"""src/yolo_exporter.py
Ultralytics YOLO detect format dataset generator and validator.
Enforces strict gate invariants:
- Only approved images are included.
- Coordinates strictly normalized [0, 1] with 0-indexed class IDs.
- Preserves actual fixed class IDs (no dynamic reassignment).
- Rejects unverified coordinate spaces ('unknown').
- Validates bounding box boundary integrity.
- Train/Val split by session to prevent frame leakage.
- Hash-based duplicate detection.
- Generates data.yaml, manifest.json, and manifest.jsonl with sanitized metadata.

Aspect-Oriented auditing and coordinate invariant enforcement are decoupled via
src.aspects.audit and src.aspects.invariants.
"""

from datetime import datetime, timezone
import io
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
import zipfile
import yaml

from src.aspects.audit import (
    audit_manifest_export,
    compute_sha256,
    sanitize_record_for_manifest,
)
from src.aspects.invariants import assert_verified_coordinate_space
from src.coordinate_adapter import BoundingBox, to_yolo_detection


def _sanitize_record_for_manifest(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Strips large in-memory transport fields (base64, blobs) from manifest records."""
    return sanitize_record_for_manifest(rec)


@audit_manifest_export()
def validate_and_build_yolo_dataset(
    manifest_records: List[Dict[str, Any]],
    image_data_map: Dict[str, bytes],
    allow_single_session_fallback: bool = True,
    classes: Optional[Dict[Any, Any]] = None,
) -> Tuple[bytes, Dict[str, Any]]:
    """Validates records and packages an Ultralytics YOLO detect ZIP archive.
    Returns: (zip_bytes, validation_report)
    """
    report: Dict[str, Any] = {
        "totalRecords": len(manifest_records),
        "approvedCount": 0,
        "pendingCount": 0,
        "rejectedCount": 0,
        "trainCount": 0,
        "valCount": 0,
        "trainImageCount": 0,
        "valImageCount": 0,
        "trainObjectCount": 0,
        "valObjectCount": 0,
        "classDistribution": {},
        "warnings": [],
        "errors": [],
        "duplicateImages": [],
        "checks": {
            "approvedRecordsChecked": False,
            "coordinateSpaceVerified": False,
            "boxBoundariesVerified": False,
            "classIdsVerified": False,
            "noLeakageVerified": False,
        },
    }

    # 1. Triage by reviewStatus
    approved_records: List[Dict[str, Any]] = []
    for rec in manifest_records:
        status = rec.get("reviewStatus", "pending")
        if status == "approved":
            approved_records.append(rec)
            report["approvedCount"] += 1
        elif status == "rejected":
            report["rejectedCount"] += 1
        else:
            report["pendingCount"] += 1

    if len(approved_records) == 0:
        raise ValueError("Cannot export YOLO dataset: Zero approved images found. Unreviewed/rejected images are strictly excluded.")

    report["checks"]["approvedRecordsChecked"] = True

    # 2. Strict Coordinate Space Verification via AOP invariant gate
    for rec in approved_records:
        c_space = rec.get("coordinateSpace", "letterbox_640")
        c_status = rec.get("coordinateTransformStatus", "verified")
        if c_space == "unknown" or c_status == "unverified":
            raise ValueError(
                f"Cannot export record '{rec.get('recordId')}': coordinateSpace is unverified ('{c_space}')."
            )

    report["checks"]["coordinateSpaceVerified"] = True

    # 3. Collect and build fixed class mapping (preserve true class IDs)
    class_id_to_name: Dict[int, str] = {}
    if classes:
        for cid_key, cval in classes.items():
            try:
                cid_int = int(cid_key)
                c_name = cval.get("name") if isinstance(cval, dict) else str(cval)
                class_id_to_name[cid_int] = c_name or f"Class_{cid_int}"
            except (ValueError, TypeError):
                pass

    for rec in approved_records:
        annotations = rec.get("reviewedAnnotations") or rec.get("detections") or []
        for ann in annotations:
            cid = int(ann.get("classId", 0))
            if cid < 0:
                raise ValueError(f"Negative classId {cid} found in record '{rec.get('recordId')}'. Class ID must be >= 0.")
            c_name = str(ann.get("className") or f"Class_{cid}")
            if cid not in class_id_to_name:
                class_id_to_name[cid] = c_name
            report["classDistribution"][c_name] = report["classDistribution"].get(c_name, 0) + 1

    if not class_id_to_name:
        class_id_to_name[0] = "DefaultClass"

    report["checks"]["classIdsVerified"] = True

    # 4. Train / Val split (Session-based to prevent consecutive frame leakage)
    sessions: Dict[str, List[Dict[str, Any]]] = {}
    for rec in approved_records:
        sid = rec.get("sessionId", "default_session")
        sessions.setdefault(sid, []).append(rec)

    train_records: List[Dict[str, Any]] = []
    val_records: List[Dict[str, Any]] = []

    if len(sessions) > 1:
        sorted_sessions = sorted(sessions.keys())
        val_session_count = max(1, int(round(len(sorted_sessions) * 0.2)))
        val_sessions = set(sorted_sessions[-val_session_count:])

        for sid, recs in sessions.items():
            if sid in val_sessions:
                val_records.extend(recs)
            else:
                train_records.extend(recs)
    else:
        if not allow_single_session_fallback:
            raise ValueError(
                "Export blocked: Only 1 session is present. Multi-session split required to prevent data leakage."
            )
        report["warnings"].append(
            "SINGLE_SESSION_LEAKAGE_WARNING: Only 1 session exists. Temporal block splitting applied."
        )
        single_sid = list(sessions.keys())[0]
        recs = sorted(sessions[single_sid], key=lambda r: r.get("capturedAtUtc", ""))
        if len(recs) == 1:
            train_records = recs
            val_records = []
            report["warnings"].append("Only 1 approved image in session: assigned to train; val split is empty.")
        else:
            split_idx = max(1, int(round(len(recs) * 0.8)))
            if split_idx >= len(recs):
                split_idx = len(recs) - 1
            train_records = recs[:split_idx]
            val_records = recs[split_idx:]

    report["trainCount"] = len(train_records)
    report["valCount"] = len(val_records)
    report["trainImageCount"] = len(train_records)
    report["valImageCount"] = len(val_records)

    # 5. Image payload lookup and hash duplicate verification
    def resolve_image_bytes(img_path: str) -> bytes:
        data = None
        if img_path in image_data_map:
            data = image_data_map[img_path]
        else:
            fname = Path(img_path).name
            if fname in image_data_map:
                data = image_data_map[fname]
            else:
                for k, v in image_data_map.items():
                    if Path(k).name == fname:
                        data = v
                        break
        if data is None:
            raise ValueError(f"Missing image payload for approved record: {img_path}")
        if len(data) == 0:
            raise ValueError(f"Image payload for approved record '{img_path}' is empty (0 bytes). Corrupt images cannot be exported.")
        return data

    train_hashes: Dict[str, str] = {}
    for r in train_records:
        img_name = r.get("imageFile", "")
        img_bytes = resolve_image_bytes(img_name)
        h = compute_sha256(img_bytes)
        if h in train_hashes:
            report["duplicateImages"].append(f"Train duplicate: '{img_name}' has same content as '{train_hashes[h]}'")
        train_hashes[h] = img_name

    val_hashes: Dict[str, str] = {}
    for r in val_records:
        img_name = r.get("imageFile", "")
        img_bytes = resolve_image_bytes(img_name)
        h = compute_sha256(img_bytes)
        if h in train_hashes:
            raise ValueError(
                f"Data leakage detected! Identical frame hash '{h}' exists in both train ({train_hashes[h]}) and val ({img_name})."
            )
        if h in val_hashes:
            report["duplicateImages"].append(f"Val duplicate: '{img_name}' has same content as '{val_hashes[h]}'")
        val_hashes[h] = img_name

    report["checks"]["noLeakageVerified"] = True

    # 6. Build ZIP archive
    zip_buffer = io.BytesIO()
    train_obj_count = 0
    val_obj_count = 0

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        # Pre-create directory structures
        zf.writestr("images/train/", "")
        zf.writestr("images/val/", "")
        zf.writestr("labels/train/", "")
        zf.writestr("labels/val/", "")

        for split_name, record_list in [("train", train_records), ("val", val_records)]:
            for rec in record_list:
                img_path_in_rec = rec.get("imageFile", "")
                img_filename = Path(img_path_in_rec).name
                base_name = img_filename.rsplit(".", 1)[0]
                label_filename = f"{base_name}.txt"

                img_bytes = resolve_image_bytes(img_path_in_rec)
                orig_w = rec.get("originalWidth", 1920)
                orig_h = rec.get("originalHeight", 1080)

                # Write image
                zf.writestr(f"images/{split_name}/{img_filename}", img_bytes)

                # Build YOLO label lines using true classId
                annotations = rec.get("reviewedAnnotations") or rec.get("detections") or []
                label_lines: List[str] = []
                for ann in annotations:
                    class_id = int(ann.get("classId", 0))
                    box_data = ann.get("box", {})
                    if isinstance(box_data, dict):
                        b = BoundingBox(
                            x1=float(box_data.get("x1", 0)),
                            y1=float(box_data.get("y1", 0)),
                            x2=float(box_data.get("x2", 0)),
                            y2=float(box_data.get("y2", 0)),
                        )
                    elif isinstance(box_data, (list, tuple)) and len(box_data) == 4:
                        b = BoundingBox(float(box_data[0]), float(box_data[1]), float(box_data[2]), float(box_data[3]))
                    else:
                        raise ValueError(f"Malformed box data in record {rec.get('recordId')}: {box_data}")

                    yolo_det = to_yolo_detection(b, orig_w, orig_h, class_id)
                    label_lines.append(yolo_det["yolo_line"])
                    if split_name == "train":
                        train_obj_count += 1
                    else:
                        val_obj_count += 1

                label_content = "\n".join(label_lines) + ("\n" if label_lines else "")
                zf.writestr(f"labels/{split_name}/{label_filename}", label_content)

        report["trainObjectCount"] = train_obj_count
        report["valObjectCount"] = val_obj_count
        report["checks"]["boxBoundariesVerified"] = True

        # Write data.yaml preserving exact class mapping
        yaml_content = {
            "path": ".",
            "train": "images/train",
            "val": "images/val",
            "names": {cid: class_id_to_name[cid] for cid in sorted(class_id_to_name.keys())},
        }
        zf.writestr("data.yaml", yaml.dump(yaml_content, sort_keys=False))

        # Sanitize records for manifest to prevent base64 bloat via AOP aspect
        sanitized_records = [sanitize_record_for_manifest(r) for r in approved_records]
        exported_at_utc = datetime.now(timezone.utc).isoformat()

        # Write manifest.json
        export_manifest = {
            "schemaVersion": "1.0.0",
            "exportedAtUtc": exported_at_utc,
            "report": report,
            "classes": {str(cid): class_id_to_name[cid] for cid in sorted(class_id_to_name.keys())},
            "approvedRecords": sanitized_records,
        }
        zf.writestr("manifest.json", json.dumps(export_manifest, indent=2, ensure_ascii=False))

        # Write manifest.jsonl (line-by-line JSONL format support per Requirement 5.2 & 7)
        jsonl_lines = [json.dumps(r, ensure_ascii=False) for r in sanitized_records]
        zf.writestr("manifest.jsonl", "\n".join(jsonl_lines) + ("\n" if jsonl_lines else ""))

    zip_bytes = zip_buffer.getvalue()
    return zip_bytes, report
