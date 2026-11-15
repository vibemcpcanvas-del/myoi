"""tests/test_coordinate_adapter.py
Comprehensive unit tests for coordinate adapter and letterbox transformation (WBS-02).
Covers Test Items 1, 2, 3, 4, and 11 from the specification.
"""

import pytest
from src.coordinate_adapter import (
    BoundingBox,
    LetterboxInfo,
    calculate_letterbox,
    to_yolo_detection,
    transform_box_to_original,
)


def test_1_landscape_1920x1080_letterbox_and_box_restoration():
    """Requirement 10.1: 1920x1080 landscape video aspect ratio and box position verification."""
    orig_w, orig_h = 1920, 1080
    info = calculate_letterbox(orig_w, orig_h, target_w=640, target_h=640)

    # Scale should be 640 / 1920 = 1/3
    assert info.scale == pytest.approx(640 / 1920, rel=1e-4)
    assert info.scaled_w == 640
    assert info.scaled_h == 360  # 1080 * (1/3)
    assert info.pad_x == 0.0
    assert info.pad_y == pytest.approx((640 - 360) / 2.0, rel=1e-4)  # 140.0

    # Suppose an object in the original 1920x1080 is at (960, 540, 1440, 810) - exact center/quadrant
    orig_expected_box = BoundingBox(960.0, 540.0, 1440.0, 810.0)
    # On the 640x640 letterbox canvas:
    # bx1 = 960 * (1/3) + 0 = 320
    # by1 = 540 * (1/3) + 140 = 180 + 140 = 320
    # bx2 = 1440 * (1/3) + 0 = 480
    # by2 = 810 * (1/3) + 140 = 270 + 140 = 410
    infer_box = BoundingBox(320.0, 320.0, 480.0, 410.0)

    restored = transform_box_to_original(infer_box, "letterbox_640", info)
    assert restored.x1 == pytest.approx(orig_expected_box.x1, abs=1.0)
    assert restored.y1 == pytest.approx(orig_expected_box.y1, abs=1.0)
    assert restored.x2 == pytest.approx(orig_expected_box.x2, abs=1.0)
    assert restored.y2 == pytest.approx(orig_expected_box.y2, abs=1.0)


def test_2_aspect_640x480_letterbox_and_box_restoration():
    """Requirement 10.2: 640x480 (4:3) video letterbox and box position verification."""
    orig_w, orig_h = 640, 480
    info = calculate_letterbox(orig_w, orig_h, target_w=640, target_h=640)

    # 4:3 -> scale is min(640/640, 640/480) = 1.0
    assert info.scale == 1.0
    assert info.scaled_w == 640
    assert info.scaled_h == 480
    assert info.pad_x == 0.0
    assert info.pad_y == 80.0  # (640 - 480) / 2

    # A box on original at (100, 100, 300, 300)
    orig_box = BoundingBox(100.0, 100.0, 300.0, 300.0)
    # on letterbox canvas: (100, 100 + 80 = 180, 300, 300 + 80 = 380)
    infer_box = BoundingBox(100.0, 180.0, 300.0, 380.0)

    restored = transform_box_to_original(infer_box, "letterbox_640", info)
    assert restored.x1 == pytest.approx(orig_box.x1, abs=0.5)
    assert restored.y1 == pytest.approx(orig_box.y1, abs=0.5)
    assert restored.x2 == pytest.approx(orig_box.x2, abs=0.5)
    assert restored.y2 == pytest.approx(orig_box.y2, abs=0.5)


def test_3_portrait_1080x1920_letterbox_and_box_restoration():
    """Requirement 10.3: 1080x1920 vertical video aspect ratio and box position verification."""
    orig_w, orig_h = 1080, 1920
    info = calculate_letterbox(orig_w, orig_h, target_w=640, target_h=640)

    # Vertical 9:16 -> scale = 640 / 1920 = 1/3
    assert info.scale == pytest.approx(640 / 1920, rel=1e-4)
    assert info.scaled_h == 640
    assert info.scaled_w == 360  # 1080 * (1/3)
    assert info.pad_y == 0.0
    assert info.pad_x == pytest.approx((640 - 360) / 2.0, rel=1e-4)  # 140.0

    # Box on original frame at (270, 480, 810, 1440)
    orig_box = BoundingBox(270.0, 480.0, 810.0, 1440.0)
    # on letterbox: (270*(1/3) + 140 = 230, 480*(1/3) = 160, 810*(1/3) + 140 = 410, 1440*(1/3) = 480)
    infer_box = BoundingBox(230.0, 160.0, 410.0, 480.0)

    restored = transform_box_to_original(infer_box, "letterbox_640", info)
    assert restored.x1 == pytest.approx(orig_box.x1, abs=1.0)
    assert restored.y1 == pytest.approx(orig_box.y1, abs=1.0)
    assert restored.x2 == pytest.approx(orig_box.x2, abs=1.0)
    assert restored.y2 == pytest.approx(orig_box.y2, abs=1.0)


def test_4_coordinate_space_modes_and_unknown_blocking():
    """Requirement 10.4: Verify coordinate space adapters and ensure 'unknown' is strictly blocked."""
    orig_w, orig_h = 1920, 1080
    info = calculate_letterbox(orig_w, orig_h)

    # 1. 'unknown' coordinate space MUST raise ValueError and fail closed
    with pytest.raises(ValueError, match="Cannot transform coordinates for unverified coordinateSpace"):
        transform_box_to_original(BoundingBox(10, 10, 50, 50), "unknown", info)

    # 2. 'normalized' coordinate space
    norm_box = BoundingBox(0.1, 0.2, 0.5, 0.6)
    restored_norm = transform_box_to_original(norm_box, "normalized", info)
    assert restored_norm.x1 == pytest.approx(192.0)
    assert restored_norm.y1 == pytest.approx(216.0)
    assert restored_norm.x2 == pytest.approx(960.0)
    assert restored_norm.y2 == pytest.approx(648.0)

    # 3. 'original' coordinate space
    orig_input = BoundingBox(50.0, 60.0, 400.0, 500.0)
    restored_orig = transform_box_to_original(orig_input, "original", info)
    assert restored_orig.x1 == 50.0
    assert restored_orig.y1 == 60.0
    assert restored_orig.x2 == 400.0
    assert restored_orig.y2 == 500.0

    # 4. Out-of-bounds box is clamped safely
    out_of_bounds = BoundingBox(-50.0, -20.0, 2500.0, 1500.0)
    clamped = transform_box_to_original(out_of_bounds, "original", info)
    assert clamped.x1 == 0.0
    assert clamped.y1 == 0.0
    assert clamped.x2 == 1920.0
    assert clamped.y2 == 1080.0


def test_11_yolo_normalization_and_zero_indexed_class():
    """Requirement 10.11: YOLO label coordinates normalized (0~1) and class_id 0-indexed."""
    orig_w, orig_h = 1920, 1080

    # Valid box: x1=480, y1=270, x2=1440, y2=810
    # x_center = (480 + 1440)/2 / 1920 = 960 / 1920 = 0.5
    # y_center = (270 + 810)/2 / 1080 = 540 / 1080 = 0.5
    # width = (1440 - 480) / 1920 = 960 / 1920 = 0.5
    # height = (810 - 270) / 1080 = 540 / 1080 = 0.5
    box = BoundingBox(480.0, 270.0, 1440.0, 810.0)
    yolo_res = to_yolo_detection(box, orig_w, orig_h, class_id=0)

    assert yolo_res["class_id"] == 0
    assert yolo_res["x_center"] == 0.500000
    assert yolo_res["y_center"] == 0.500000
    assert yolo_res["width"] == 0.500000
    assert yolo_res["height"] == 0.500000
    assert yolo_res["yolo_line"] == "0 0.500000 0.500000 0.500000 0.500000"

    # Verify class_id < 0 is rejected
    with pytest.raises(ValueError, match="class_id must be non-negative"):
        to_yolo_detection(box, orig_w, orig_h, class_id=-1)

    # Verify degenerate box is rejected
    degenerate_box = BoundingBox(100.0, 100.0, 100.0, 100.0)
    with pytest.raises(ValueError, match="Invalid box for YOLO conversion"):
        to_yolo_detection(degenerate_box, orig_w, orig_h, class_id=1)
