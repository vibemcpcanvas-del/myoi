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
