"""tests/test_yolo_export.py
Comprehensive unit tests for Ultralytics YOLO detect export, session split,
and leakage prevention (Requirements 10.10, 10.11, 10.12).
"""

import io
import json
import zipfile
import pytest
import yaml

from src.yolo_exporter import validate_and_build_yolo_dataset, compute_sha256


def create_dummy_jpeg(color: int) -> bytes:
    from PIL import Image
    img = Image.new("RGB", (1920, 1080), color=(color, color, color))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_10_unapproved_and_rejected_images_strictly_excluded_from_zip():
    """Requirement 10.10: Verify that unreviewed (pending) and rejected images are completely excluded from the dataset ZIP."""
    img1 = create_dummy_jpeg(50)
    img2 = create_dummy_jpeg(100)
    img3 = create_dummy_jpeg(150)

    image_data_map = {
        "images/rec_approved.jpg": img1,
        "images/rec_pending.jpg": img2,
        "images/rec_rejected.jpg": img3,
    }

    records = [
        {
            "recordId": "rec_1",
            "sessionId": "SES_A",
            "imageFile": "images/rec_approved.jpg",
            "reviewStatus": "approved",
            "originalWidth": 1920,
            "originalHeight": 1080,
            "detections": [
                {"classId": 0, "className": "Sensei", "confidence": 0.95, "box": {"x1": 100, "y1": 100, "x2": 400, "y2": 500}}
            ],
        },
        {
            "recordId": "rec_2",
            "sessionId": "SES_A",
            "imageFile": "images/rec_pending.jpg",
            "reviewStatus": "pending",  # Pending
            "originalWidth": 1920,
            "originalHeight": 1080,
            "detections": [
                {"classId": 0, "className": "Sensei", "confidence": 0.80, "box": {"x1": 200, "y1": 200, "x2": 600, "y2": 700}}
            ],
        },
        {
            "recordId": "rec_3",
            "sessionId": "SES_B",
            "imageFile": "images/rec_rejected.jpg",
            "reviewStatus": "rejected",  # Rejected
            "originalWidth": 1920,
            "originalHeight": 1080,
            "detections": [
                {"classId": 1, "className": "Target", "confidence": 0.90, "box": {"x1": 300, "y1": 300, "x2": 800, "y2": 800}}
            ],
        },
    ]

    zip_bytes, report = validate_and_build_yolo_dataset(records, image_data_map)

    assert report["approvedCount"] == 1
    assert report["pendingCount"] == 1
    assert report["rejectedCount"] == 1

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        namelist = zf.namelist()
        # Verify approved image is present
        assert any("rec_approved.jpg" in name for name in namelist)
        # Verify pending and rejected images are NOT present
        assert not any("rec_pending.jpg" in name for name in namelist)
        assert not any("rec_rejected.jpg" in name for name in namelist)


def test_10_export_fails_closed_when_zero_approved_images():
    """Requirement 10.10: Zero approved images must raise ValueError and block ZIP generation."""
    records = [
        {"recordId": "rec_1", "reviewStatus": "pending", "detections": []},
        {"recordId": "rec_2", "reviewStatus": "rejected", "detections": []},
    ]
    with pytest.raises(ValueError, match="Zero approved images found"):
        validate_and_build_yolo_dataset(records, {})


def test_12_multi_session_train_val_split_and_no_leakage():
    """Requirement 10.12: Verify multi-session partition into train and val without session leakage."""
    img_a1 = create_dummy_jpeg(10)
    img_a2 = create_dummy_jpeg(20)
    img_b1 = create_dummy_jpeg(30)

    image_data_map = {
        "images/a1.jpg": img_a1,
        "images/a2.jpg": img_a2,
        "images/b1.jpg": img_b1,
    }

    records = [
        {
            "recordId": "r1",
            "sessionId": "SES_01",
            "imageFile": "images/a1.jpg",
            "reviewStatus": "approved",
            "originalWidth": 1920,
            "originalHeight": 1080,
            "detections": [{"classId": 0, "className": "Class0", "box": {"x1": 10, "y1": 10, "x2": 100, "y2": 100}}],
        },
        {
            "recordId": "r2",
            "sessionId": "SES_01",
            "imageFile": "images/a2.jpg",
            "reviewStatus": "approved",
            "originalWidth": 1920,
            "originalHeight": 1080,
            "detections": [{"classId": 0, "className": "Class0", "box": {"x1": 20, "y1": 20, "x2": 200, "y2": 200}}],
        },
        {
            "recordId": "r3",
            "sessionId": "SES_02",
            "imageFile": "images/b1.jpg",
            "reviewStatus": "approved",
            "originalWidth": 1920,
            "originalHeight": 1080,
            "detections": [{"classId": 0, "className": "Class0", "box": {"x1": 30, "y1": 30, "x2": 300, "y2": 300}}],
        },
    ]

    zip_bytes, report = validate_and_build_yolo_dataset(records, image_data_map)
    assert report["trainCount"] >= 1
    assert report["valCount"] >= 1

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        namelist = zf.namelist()
        # Ensure train and val folders exist
        train_images = [n for n in namelist if n.startswith("images/train/")]
        val_images = [n for n in namelist if n.startswith("images/val/")]
        assert len(train_images) > 0
        assert len(val_images) > 0

        # Verify data.yaml contents
        yaml_text = zf.read("data.yaml").decode("utf-8")
        parsed_yaml = yaml.safe_load(yaml_text)
        assert parsed_yaml["train"] == "images/train"
        assert parsed_yaml["val"] == "images/val"
        assert 0 in parsed_yaml["names"]


def test_12_duplicate_frame_hash_detection_blocks_leakage():
    """Requirement 10.12: If identical frame hash exists across train and val, export MUST block."""
    identical_bytes = create_dummy_jpeg(88)

    image_data_map = {
        "images/train_frame.jpg": identical_bytes,
        "images/val_frame.jpg": identical_bytes,  # Duplicate content in different session
    }

    records = [
        {
            "recordId": "r1",
            "sessionId": "SES_01",
            "imageFile": "images/train_frame.jpg",
            "reviewStatus": "approved",
            "originalWidth": 1920,
            "originalHeight": 1080,
            "detections": [{"classId": 0, "className": "Target", "box": {"x1": 10, "y1": 10, "x2": 100, "y2": 100}}],
        },
        {
            "recordId": "r2",
            "sessionId": "SES_02",
            "imageFile": "images/val_frame.jpg",
            "reviewStatus": "approved",
            "originalWidth": 1920,
            "originalHeight": 1080,
            "detections": [{"classId": 0, "className": "Target", "box": {"x1": 10, "y1": 10, "x2": 100, "y2": 100}}],
        },
    ]

    with pytest.raises(ValueError, match="Data leakage detected! Identical frame hash"):
        validate_and_build_yolo_dataset(records, image_data_map)


def test_fixed_class_mapping_preserves_class_ids_without_reordering():
    """Requirement 7 & Bug Fix: Class IDs must NOT be dynamically re-mapped or swapped."""
    img1 = create_dummy_jpeg(11)
    img2 = create_dummy_jpeg(22)

    image_data_map = {
        "images/sensei.jpg": img1,
        "images/enemy.jpg": img2,
    }

    # Class 3 (Boss) appears first, Class 1 (Minion) appears second
    records = [
        {
            "recordId": "rec_boss",
            "sessionId": "SES_01",
            "imageFile": "images/sensei.jpg",
            "reviewStatus": "approved",
            "originalWidth": 1920,
            "originalHeight": 1080,
            "detections": [{"classId": 3, "className": "Boss", "confidence": 0.95, "box": {"x1": 100, "y1": 100, "x2": 400, "y2": 400}}],
        },
        {
            "recordId": "rec_minion",
            "sessionId": "SES_02",
            "imageFile": "images/enemy.jpg",
            "reviewStatus": "approved",
            "originalWidth": 1920,
            "originalHeight": 1080,
            "detections": [{"classId": 1, "className": "Minion", "confidence": 0.85, "box": {"x1": 200, "y1": 200, "x2": 500, "y2": 500}}],
        },
    ]

    fixed_classes = {1: {"name": "Minion"}, 3: {"name": "Boss"}}
    zip_bytes, report = validate_and_build_yolo_dataset(records, image_data_map, classes=fixed_classes)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        data_yaml = yaml.safe_load(zf.read("data.yaml").decode("utf-8"))
        # Class 1 must be Minion, Class 3 must be Boss
        assert data_yaml["names"][1] == "Minion"
        assert data_yaml["names"][3] == "Boss"

        # Check label text lines
        boss_label = zf.read("labels/train/sensei.txt").decode("utf-8").strip()
        assert boss_label.startswith("3 ")  # Must NOT be 0!

        minion_label = zf.read("labels/val/enemy.txt").decode("utf-8").strip()
        assert minion_label.startswith("1 ")  # Must NOT be 0 or 1 swapped!


def test_unknown_coordinate_space_strictly_blocked_from_export():
    """Requirement 10.4 & Bug Fix: Records with coordinateSpace: 'unknown' must fail closed."""
    img = create_dummy_jpeg(33)
    records = [
        {
            "recordId": "rec_unknown_coord",
            "sessionId": "SES_01",
            "imageFile": "images/unknown.jpg",
            "coordinateSpace": "unknown",
            "reviewStatus": "approved",
            "originalWidth": 1920,
            "originalHeight": 1080,
            "detections": [{"classId": 0, "className": "Target", "box": {"x1": 10, "y1": 10, "x2": 100, "y2": 100}}],
        }
    ]
    with pytest.raises(ValueError, match="coordinateSpace is unverified"):
        validate_and_build_yolo_dataset(records, {"images/unknown.jpg": img})


def test_out_of_bounds_bounding_box_strictly_blocked_from_export():
    """Requirement 7 & Bug Fix: Bounding box exceeding image bounds must raise ValueError."""
    img = create_dummy_jpeg(44)
    records = [
        {
            "recordId": "rec_oob",
            "sessionId": "SES_01",
            "imageFile": "images/oob.jpg",
            "reviewStatus": "approved",
            "originalWidth": 1920,
            "originalHeight": 1080,
            "detections": [{"classId": 0, "className": "Target", "box": {"x1": -50, "y1": 10, "x2": 100, "y2": 100}}],
        }
    ]
    with pytest.raises(ValueError, match="Box coordinates exceed frame boundaries"):
        validate_and_build_yolo_dataset(records, {"images/oob.jpg": img})


def test_manifest_sanitization_and_jsonl_presence():
    """Requirement 5.2, 7 & Bug Fix: Manifest must strip base64 blobs and include manifest.jsonl."""
    img = create_dummy_jpeg(55)
    records = [
        {
            "recordId": "rec_clean",
            "sessionId": "SES_01",
            "imageFile": "images/clean.jpg",
            "reviewStatus": "approved",
            "originalWidth": 1920,
            "originalHeight": 1080,
            "origBase64": "data:image/jpeg;base64,SHOULD_BE_STRIPPED",
            "prevBase64": "data:image/jpeg;base64,SHOULD_BE_STRIPPED_TOO",
            "detections": [{"classId": 0, "className": "Target", "box": {"x1": 50, "y1": 50, "x2": 200, "y2": 200}}],
        }
    ]

    zip_bytes, report = validate_and_build_yolo_dataset(records, {"images/clean.jpg": img})
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        # Check manifest.json
        manifest_raw = zf.read("manifest.json").decode("utf-8")
        manifest_data = json.loads(manifest_raw)
        assert "SHOULD_BE_STRIPPED" not in manifest_raw
        assert manifest_data["exportedAtUtc"] != ""
        rec_in_manifest = manifest_data["approvedRecords"][0]
        assert "origBase64" not in rec_in_manifest
        assert "prevBase64" not in rec_in_manifest

        # Check manifest.jsonl
        assert "manifest.jsonl" in zf.namelist()
        jsonl_raw = zf.read("manifest.jsonl").decode("utf-8").strip()
        assert "SHOULD_BE_STRIPPED" not in jsonl_raw
        line_data = json.loads(jsonl_raw)
        assert line_data["recordId"] == "rec_clean"

        # Check empty val directory exists
        namelist = zf.namelist()
        assert "images/val/" in namelist
        assert "labels/val/" in namelist
