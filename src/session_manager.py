"""src/session_manager.py
Session Persistence and Historical Data Retrieval Manager for SCHALE Review Center.
Enforces P0 safety, fail-closed gates, input sanitization, and data integrity invariants:
- Strict path traversal prevention (rejects '..', slashes, control chars, null bytes)
- Image payload validation (rejects 0-byte or corrupted image data)
- Bounding box boundary & coordinate invariants (numeric finite, containment within original dimensions)
- Non-negative integer class ID validation
- Atomic session writing to avoid half-written state on crash
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import shutil
from typing import Any, Dict, List, Optional, Tuple

SESSION_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]+$")
RECORD_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]+$")
MAX_SESSION_ID_LEN = 128
MAX_IMAGE_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB


def validate_session_id(session_id: str, base_dir: Path) -> Path:
    """Validates session ID against path traversal and illegal character attacks.

    Fail-Closed Invariants:
    1. Must be a non-empty string between 1 and 128 characters.
    2. Must match alphanumeric, underscore, or dash [a-zA-Z0-9_-]+.
    3. Must strictly reject '..', slashes, control chars, null bytes.
    4. Resolved directory path must reside strictly inside base_dir.
    """
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("Session ID must be a non-empty string.")

    session_id = session_id.strip()
    if len(session_id) > MAX_SESSION_ID_LEN:
        raise ValueError(
            f"Session ID exceeds maximum length of {MAX_SESSION_ID_LEN} characters."
        )

    # Check for illegal characters or path traversal markers
    if "\x00" in session_id or ".." in session_id or "/" in session_id or "\\" in session_id:
        raise ValueError(
            f"Path traversal attempt or illegal characters detected in session ID: '{session_id}'"
        )

    if not SESSION_ID_PATTERN.match(session_id):
        raise ValueError(
            f"Invalid session ID '{session_id}'. Only alphanumeric characters, underscores, and hyphens are permitted."
        )

    base_resolved = base_dir.resolve()
    target_dir = (base_dir / session_id).resolve()

    # Verify containment invariant
    if target_dir == base_resolved or base_resolved not in target_dir.parents:
        raise ValueError(
            f"Path traversal detected: target directory '{target_dir}' escapes base directory '{base_resolved}'."
        )

    return target_dir


def validate_image_payload(image_bytes: bytes, filename: str = "image.jpg") -> None:
    """Enforces non-empty payload, size limits, and magic byte validation for images.

    Fail-Closed Invariants:
    1. Payload must not be empty (0 bytes).
    2. Size must not exceed MAX_IMAGE_SIZE_BYTES.
    3. Magic bytes must indicate valid JPEG, PNG, or WebP.
    """
    if not isinstance(image_bytes, bytes) or len(image_bytes) == 0:
        raise ValueError(f"Image payload '{filename}' is empty (0 bytes).")

    if len(image_bytes) > MAX_IMAGE_SIZE_BYTES:
        raise ValueError(
            f"Image payload '{filename}' exceeds maximum allowed size ({len(image_bytes)} > {MAX_IMAGE_SIZE_BYTES} bytes)."
        )

    if len(image_bytes) < 16:
        raise ValueError(
            f"Image payload '{filename}' is too small to contain a valid image header ({len(image_bytes)} bytes)."
        )

    # Magic byte checks
    is_jpeg = image_bytes.startswith(b"\xff\xd8\xff")
    is_png = image_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    is_webp = image_bytes.startswith(b"RIFF") and len(image_bytes) >= 12 and image_bytes[8:12] == b"WEBP"

    if not (is_jpeg or is_png or is_webp):
        raise ValueError(
            f"Image payload '{filename}' has invalid or unrecognized magic bytes (must be JPEG, PNG, or WebP)."
        )


def decode_image_base64(b64_str: str, filename: str = "image.jpg") -> bytes:
    """Safely decodes base64 string or data URL, returning validated image bytes."""
    if not isinstance(b64_str, str) or not b64_str.strip():
        raise ValueError(f"Base64 image string for '{filename}' is empty.")

    clean_b64 = b64_str.strip()
    if "," in clean_b64:
        clean_b64 = clean_b64.split(",", 1)[1].strip()

    try:
        raw_bytes = base64.b64decode(clean_b64)
    except Exception as e:
        raise ValueError(f"Failed to decode base64 image for '{filename}': {e}")

    validate_image_payload(raw_bytes, filename)
    return raw_bytes


def validate_record_invariants(record: Dict[str, Any]) -> None:
    """Enforces data integrity invariants on a single frame snapshot record."""
    if not isinstance(record, dict):
        raise ValueError("Record must be a dictionary.")

    record_id = record.get("recordId")
    if not record_id or not isinstance(record_id, str) or not RECORD_ID_PATTERN.match(record_id):
        raise ValueError(f"Invalid or missing recordId: '{record_id}'")

    orig_w = record.get("originalWidth")
    orig_h = record.get("originalHeight")
    if not isinstance(orig_w, int) or not isinstance(orig_h, int) or orig_w <= 0 or orig_h <= 0:
        raise ValueError(f"Record '{record_id}' has invalid dimensions: ({orig_w}x{orig_h})")

    # Validate annotations
    annotations = record.get("reviewedAnnotations")
    if annotations is None:
        annotations = record.get("detections", [])

    if not isinstance(annotations, list):
        raise ValueError(f"Record '{record_id}' annotations must be a list.")

    eps = 1e-4
    for idx, ann in enumerate(annotations):
        if not isinstance(ann, dict):
            raise ValueError(f"Record '{record_id}' annotation [{idx}] is not an object.")

        class_id = ann.get("classId")
        if not isinstance(class_id, int) or isinstance(class_id, bool) or class_id < 0:
            raise ValueError(
                f"Record '{record_id}' annotation [{idx}] has invalid classId: {class_id} (must be non-negative integer)."
            )

        box = ann.get("box")
        if not isinstance(box, dict):
            raise ValueError(f"Record '{record_id}' annotation [{idx}] is missing 'box' object.")

        for coord_name in ("x1", "y1", "x2", "y2"):
            val = box.get(coord_name)
            if not isinstance(val, (int, float)) or not math.isfinite(val):
                raise ValueError(
                    f"Record '{record_id}' annotation [{idx}] coordinate '{coord_name}' is not a finite number: {val}"
                )

        x1 = float(box["x1"])
        y1 = float(box["y1"])
        x2 = float(box["x2"])
        y2 = float(box["y2"])

        if (x2 - x1) <= eps or (y2 - y1) <= eps:
            raise ValueError(
                f"Record '{record_id}' annotation [{idx}] box is inverted or zero-area: [x1={x1}, y1={y1}, x2={x2}, y2={y2}]"
            )

        # Allow slight boundary tolerance
        if x1 < -1.0 or y1 < -1.0 or x2 > (orig_w + 1.0) or y2 > (orig_h + 1.0):
            raise ValueError(
                f"Record '{record_id}' annotation [{idx}] box exceeds frame boundaries: [x1={x1}, y1={y1}, x2={x2}, y2={y2}] for {orig_w}x{orig_h}"
            )


class SessionManager:
    """Manages disk persistence, retrieval, and indexing of SCHALE review sessions."""

    def __init__(self, base_dir: Optional[Path] = None):
        if base_dir is None:
            repo_root = Path(__file__).resolve().parent.parent
            self.base_dir = repo_root / "data" / "sessions"
        else:
            self.base_dir = Path(base_dir).resolve()

        self.base_dir.mkdir(parents=True, exist_ok=True)

    def get_session_dir(self, session_id: str) -> Path:
        """Returns validated session directory path."""
        return validate_session_id(session_id, self.base_dir)

    def save_session(
        self,
        session_id: str,
        records: List[Dict[str, Any]],
        classes: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Atomically saves session metadata, annotations, and images to disk."""
        sess_dir = self.get_session_dir(session_id)
        images_dir = sess_dir / "images"
        previews_dir = sess_dir / "previews"
        images_dir.mkdir(parents=True, exist_ok=True)
        previews_dir.mkdir(parents=True, exist_ok=True)

        if not isinstance(records, list):
            raise ValueError("Records must be a list of frame snapshot objects.")

        cleaned_records: List[Dict[str, Any]] = []
        status_counts = {"all": len(records), "pending": 0, "approved": 0, "rejected": 0}

        for rec in records:
            validate_record_invariants(rec)
            rec_id = rec["recordId"]

            # Save clean original image
            orig_filename = f"{rec_id}_orig.jpg"
            orig_dest = images_dir / orig_filename
            has_orig_on_disk = orig_dest.exists() and orig_dest.stat().st_size > 0

            orig_b64 = rec.get("origBase64")
            if orig_b64:
                img_bytes = decode_image_base64(orig_b64, orig_filename)
                orig_dest.write_bytes(img_bytes)
                has_orig_on_disk = True
            elif not has_orig_on_disk:
                # Check if referenced imageFile exists elsewhere in session dir
                ref_file = rec.get("imageFile")
                if ref_file:
                    alt_path = sess_dir / ref_file
                    if alt_path.exists() and alt_path.stat().st_size > 0:
                        has_orig_on_disk = True
                        if alt_path != orig_dest:
                            shutil.copy2(alt_path, orig_dest)

            if not has_orig_on_disk:
                raise ValueError(
                    f"Record '{rec_id}' has neither 'origBase64' image payload nor existing image on disk."
                )

            # Save preview image if provided
            prev_filename = f"{rec_id}_prev.jpg"
            prev_dest = previews_dir / prev_filename
            prev_b64 = rec.get("prevBase64")
            if prev_b64:
                try:
                    prev_bytes = decode_image_base64(prev_b64, prev_filename)
                    prev_dest.write_bytes(prev_bytes)
                except Exception:
                    pass

            # Update status counts
            rev_status = rec.get("reviewStatus", "pending")
            if rev_status in status_counts:
                status_counts[rev_status] += 1
            else:
                status_counts["pending"] += 1

            # Prepare disk record without massive base64 strings
            rec_copy = dict(rec)
            rec_copy.pop("origBase64", None)
            rec_copy.pop("prevBase64", None)
            rec_copy.pop("origBlob", None)
            rec_copy.pop("prevBlob", None)
            rec_copy["imageFile"] = f"images/{orig_filename}"
            if prev_dest.exists():
                rec_copy["previewFile"] = f"previews/{prev_filename}"

            cleaned_records.append(rec_copy)

        # Preserve creation timestamp if existing session.json exists
        session_json_path = sess_dir / "session.json"
        now_iso = datetime.now(timezone.utc).isoformat()
        created_at_utc = now_iso

        if session_json_path.exists():
            try:
                old_data = json.loads(session_json_path.read_text(encoding="utf-8"))
                if "createdAtUtc" in old_data:
                    created_at_utc = old_data["createdAtUtc"]
            except Exception:
                pass

        session_payload = {
            "schemaVersion": "1.0.0",
            "sessionId": session_id,
            "createdAtUtc": created_at_utc,
            "updatedAtUtc": now_iso,
            "recordCount": len(cleaned_records),
            "statusCounts": status_counts,
            "classes": classes or {},
            "metadata": metadata or {},
            "records": cleaned_records,
        }

        # Atomic file write
        temp_json_path = sess_dir / "session.json.tmp"
        temp_json_path.write_text(json.dumps(session_payload, indent=2, ensure_ascii=False), encoding="utf-8")
        temp_json_path.replace(session_json_path)

        return {
            "success": True,
            "sessionId": session_id,
            "recordCount": len(cleaned_records),
            "statusCounts": status_counts,
            "createdAtUtc": created_at_utc,
            "updatedAtUtc": now_iso,
            "message": f"Session '{session_id}' saved successfully with {len(cleaned_records)} records.",
        }

    def load_session(self, session_id: str, include_base64: bool = True) -> Dict[str, Any]:
        """Loads historical session from disk and populates base64 image data URLs."""
        sess_dir = self.get_session_dir(session_id)
        session_json_path = sess_dir / "session.json"

        if not session_json_path.exists():
            raise FileNotFoundError(f"Session '{session_id}' does not exist on disk.")

        try:
            session_data = json.loads(session_json_path.read_text(encoding="utf-8"))
        except Exception as e:
            raise ValueError(f"Corrupted session.json file in '{session_id}': {e}")

        records = session_data.get("records", [])

        if include_base64:
            for rec in records:
                rec_id = rec.get("recordId")
                img_rel = rec.get("imageFile", f"images/{rec_id}_orig.jpg")
                img_path = sess_dir / img_rel
                if not img_path.exists():
                    img_path = sess_dir / "images" / f"{rec_id}_orig.jpg"

                if img_path.exists() and img_path.stat().st_size > 0:
                    raw_bytes = img_path.read_bytes()
                    mime_type = "image/jpeg"
                    if raw_bytes.startswith(b"\x89PNG"):
                        mime_type = "image/png"
                    elif raw_bytes.startswith(b"RIFF"):
                        mime_type = "image/webp"

                    b64_str = base64.b64encode(raw_bytes).decode("ascii")
                    rec["origBase64"] = f"data:{mime_type};base64,{b64_str}"

                # Preview image
                prev_rel = rec.get("previewFile", f"previews/{rec_id}_prev.jpg")
                prev_path = sess_dir / prev_rel
                if not prev_path.exists():
                    prev_path = sess_dir / "previews" / f"{rec_id}_prev.jpg"

                if prev_path.exists() and prev_path.stat().st_size > 0:
                    p_bytes = prev_path.read_bytes()
                    p_b64 = base64.b64encode(p_bytes).decode("ascii")
                    rec["prevBase64"] = f"data:image/jpeg;base64,{p_b64}"
                elif "origBase64" in rec:
                    rec["prevBase64"] = rec["origBase64"]

        return {
            "success": True,
            "sessionId": session_data.get("sessionId", session_id),
            "createdAtUtc": session_data.get("createdAtUtc"),
            "updatedAtUtc": session_data.get("updatedAtUtc"),
            "recordCount": len(records),
            "statusCounts": session_data.get("statusCounts", {
                "all": len(records),
                "pending": sum(1 for r in records if r.get("reviewStatus") == "pending"),
                "approved": sum(1 for r in records if r.get("reviewStatus") == "approved"),
                "rejected": sum(1 for r in records if r.get("reviewStatus") == "rejected"),
            }),
            "classes": session_data.get("classes", {}),
            "metadata": session_data.get("metadata", {}),
            "records": records,
        }

    def list_sessions(self) -> List[Dict[str, Any]]:
        """Discovers and summarizes all historical sessions available on disk."""
        if not self.base_dir.exists():
            return []

        results: List[Dict[str, Any]] = []

        for entry in self.base_dir.iterdir():
            if not entry.is_dir() or entry.name.startswith("."):
                continue

            session_id = entry.name
            session_json_path = entry / "session.json"

            if session_json_path.exists():
                try:
                    data = json.loads(session_json_path.read_text(encoding="utf-8"))
                    records = data.get("records", [])
                    status_counts = data.get("statusCounts")
                    if not status_counts:
                        status_counts = {
                            "all": len(records),
                            "pending": sum(1 for r in records if r.get("reviewStatus") == "pending"),
                            "approved": sum(1 for r in records if r.get("reviewStatus") == "approved"),
                            "rejected": sum(1 for r in records if r.get("reviewStatus") == "rejected"),
                        }

                    total_objects = 0
                    for r in records:
                        anns = r.get("reviewedAnnotations") or r.get("detections") or []
                        total_objects += len(anns)

                    results.append({
                        "sessionId": data.get("sessionId", session_id),
                        "createdAtUtc": data.get("createdAtUtc"),
                        "updatedAtUtc": data.get("updatedAtUtc") or data.get("createdAtUtc"),
                        "recordCount": len(records),
                        "statusCounts": status_counts,
                        "totalObjects": total_objects,
                    })
                    continue
                except Exception:
                    pass

            # Fallback for unindexed folder with images
            images_dir = entry / "images"
            if images_dir.exists() and images_dir.is_dir():
                img_files = [f for f in images_dir.glob("*_orig.*") if f.is_file()]
                if img_files:
                    mtime_iso = datetime.fromtimestamp(entry.stat().st_mtime, tz=timezone.utc).isoformat()
                    results.append({
                        "sessionId": session_id,
                        "createdAtUtc": mtime_iso,
                        "updatedAtUtc": mtime_iso,
                        "recordCount": len(img_files),
                        "statusCounts": {
                            "all": len(img_files),
                            "pending": len(img_files),
                            "approved": 0,
                            "rejected": 0,
                        },
                        "totalObjects": 0,
                    })

        # Sort newest first by updatedAtUtc or createdAtUtc
        results.sort(
            key=lambda x: str(x.get("updatedAtUtc") or x.get("createdAtUtc") or ""),
            reverse=True,
        )
        return results

    def delete_session(self, session_id: str) -> bool:
        """Permanently removes a session directory from disk."""
        sess_dir = self.get_session_dir(session_id)
        if not sess_dir.exists():
            raise FileNotFoundError(f"Session '{session_id}' not found.")

        shutil.rmtree(sess_dir)
        return True

    def get_image_file_path(self, session_id: str, filename_or_rel_path: str) -> Optional[Path]:
        """Resolves an image file path within a session with strict path containment."""
        sess_dir = self.get_session_dir(session_id)
        if not sess_dir.exists():
            return None

        # Sanitize filename / path
        rel_clean = filename_or_rel_path.replace("\\", "/").strip().lstrip("/")
        if ".." in rel_clean or "\x00" in rel_clean:
            raise ValueError(f"Path traversal detected in image filename: '{filename_or_rel_path}'")

        target_file = (sess_dir / rel_clean).resolve()
        if sess_dir.resolve() not in target_file.parents:
            raise ValueError(f"Path traversal detected in image path: '{filename_or_rel_path}'")

        if target_file.exists() and target_file.is_file():
            return target_file

        # Fallback to direct name under images/
        name_only = Path(rel_clean).name
        target_in_images = (sess_dir / "images" / name_only).resolve()
        if target_in_images.exists() and target_in_images.is_file():
            return target_in_images

        return None


# Global default session manager instance
_default_session_manager: Optional[SessionManager] = None


def get_session_manager(base_dir: Optional[Path] = None) -> SessionManager:
    """Returns singleton SessionManager instance."""
    global _default_session_manager
    if _default_session_manager is None or base_dir is not None:
        _default_session_manager = SessionManager(base_dir=base_dir)
    return _default_session_manager
