"""src/coordinate_adapter.py
Coordinate adapter and aspect-ratio preserving letterbox utilities.
Handles transformations between original stream frames, letterbox inference canvases,
and normalized YOLO detection coordinates.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class LetterboxInfo:
    orig_w: int
    orig_h: int
    target_w: int
    target_h: int
    scale: float
    pad_x: float
    pad_y: float
    scaled_w: int
    scaled_h: int


@dataclass(frozen=True)
class BoundingBox:
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1

    def is_valid(self) -> bool:
        return self.width > 0 and self.height > 0


def calculate_letterbox(
    orig_w: int,
    orig_h: int,
    target_w: int = 640,
    target_h: int = 640,
) -> LetterboxInfo:
    """Computes scale factor and padding for aspect-ratio preserving letterboxing."""
    if orig_w <= 0 or orig_h <= 0 or target_w <= 0 or target_h <= 0:
        raise ValueError(f"Invalid dimensions: orig=({orig_w}, {orig_h}), target=({target_w}, {target_h})")

    scale = min(target_w / orig_w, target_h / orig_h)
    scaled_w = int(round(orig_w * scale))
    scaled_h = int(round(orig_h * scale))
    pad_x = (target_w - scaled_w) / 2.0
    pad_y = (target_h - scaled_h) / 2.0

    return LetterboxInfo(
        orig_w=orig_w,
        orig_h=orig_h,
        target_w=target_w,
        target_h=target_h,
        scale=scale,
        pad_x=pad_x,
        pad_y=pad_y,
        scaled_w=scaled_w,
        scaled_h=scaled_h,
    )


def transform_box_to_original(
    box: BoundingBox,
    coord_space: str,
    letterbox: LetterboxInfo,
) -> BoundingBox:
    """Transforms bounding box coordinates from given coordinate space to original frame pixels.
    Fails closed if coordinate space is unknown or unverified.
    """
    if coord_space == "unknown":
        raise ValueError("Cannot transform coordinates for unverified coordinateSpace: 'unknown'.")

    if coord_space == "letterbox_640" or coord_space == "letterbox":
        ox1 = (box.x1 - letterbox.pad_x) / letterbox.scale
        oy1 = (box.y1 - letterbox.pad_y) / letterbox.scale
        ox2 = (box.x2 - letterbox.pad_x) / letterbox.scale
        oy2 = (box.y2 - letterbox.pad_y) / letterbox.scale
    elif coord_space == "normalized":
        ox1 = box.x1 * letterbox.orig_w
        oy1 = box.y1 * letterbox.orig_h
        ox2 = box.x2 * letterbox.orig_w
        oy2 = box.y2 * letterbox.orig_h
    elif coord_space == "original":
        ox1, oy1, ox2, oy2 = box.x1, box.y1, box.x2, box.y2
    elif coord_space == "stretch_640":
        # Legacy non-letterbox stretching (deprecated, documented)
        ox1 = box.x1 * (letterbox.orig_w / letterbox.target_w)
        oy1 = box.y1 * (letterbox.orig_h / letterbox.target_h)
        ox2 = box.x2 * (letterbox.orig_w / letterbox.target_w)
        oy2 = box.y2 * (letterbox.orig_h / letterbox.target_h)
    else:
        raise ValueError(f"Unsupported coordinate space: '{coord_space}'")

    # Order coordinates properly if inverted
    min_x, max_x = min(ox1, ox2), max(ox1, ox2)
    min_y, max_y = min(oy1, oy2), max(oy1, oy2)

    # Clamp to original frame boundaries
    clamped_x1 = max(0.0, min(float(letterbox.orig_w), min_x))
    clamped_y1 = max(0.0, min(float(letterbox.orig_h), min_y))
    clamped_x2 = max(0.0, min(float(letterbox.orig_w), max_x))
    clamped_y2 = max(0.0, min(float(letterbox.orig_h), max_y))

    result_box = BoundingBox(
        x1=round(clamped_x1, 2),
        y1=round(clamped_y1, 2),
        x2=round(clamped_x2, 2),
        y2=round(clamped_y2, 2),
    )

    if not result_box.is_valid():
        raise ValueError(
            f"Transformed bounding box is degenerate or zero-area: ({result_box.x1}, {result_box.y1}, {result_box.x2}, {result_box.y2})"
        )

    return result_box


def to_yolo_detection(
    box: BoundingBox,
    orig_w: int,
    orig_h: int,
    class_id: int,
) -> Dict[str, Any]:
    """Converts pixel bounding box on the original frame into normalized YOLO detect format.
    Formula:
      x_center = ((x1 + x2) / 2) / image_width
      y_center = ((y1 + y2) / 2) / image_height
      width    = (x2 - x1) / image_width
      height   = (y2 - y1) / image_height
    """
    if class_id < 0:
        raise ValueError(f"YOLO class_id must be non-negative integer (>= 0), got {class_id}")
    if orig_w <= 0 or orig_h <= 0:
        raise ValueError(f"Original image dimensions must be positive, got ({orig_w}, {orig_h})")
    if not box.is_valid():
        raise ValueError(f"Invalid box for YOLO conversion: width={box.width}, height={box.height}")

    # Boundary verification: box must not exceed original frame boundaries
    eps = 1e-4
    if box.x1 < -eps or box.y1 < -eps or box.x2 > (orig_w + eps) or box.y2 > (orig_h + eps):
        raise ValueError(
            f"Box coordinates exceed frame boundaries: [x1={box.x1}, y1={box.y1}, x2={box.x2}, y2={box.y2}] for frame {orig_w}x{orig_h}"
        )

    # Clamping for tiny floating-point precision differences
    clamped_x1 = max(0.0, min(float(orig_w), box.x1))
    clamped_y1 = max(0.0, min(float(orig_h), box.y1))
    clamped_x2 = max(0.0, min(float(orig_w), box.x2))
    clamped_y2 = max(0.0, min(float(orig_h), box.y2))
    clamped_w = clamped_x2 - clamped_x1
    clamped_h = clamped_y2 - clamped_y1

    if clamped_w <= 0 or clamped_h <= 0:
        raise ValueError(f"Degenerate clamped box for YOLO conversion: ({clamped_x1}, {clamped_y1}, {clamped_x2}, {clamped_y2})")

    x_center = ((clamped_x1 + clamped_x2) / 2.0) / orig_w
    y_center = ((clamped_y1 + clamped_y2) / 2.0) / orig_h
    norm_w = clamped_w / orig_w
    norm_h = clamped_h / orig_h

    # Numerical boundary verification
    for name, val in [("x_center", x_center), ("y_center", y_center)]:
        if not (0.0 <= val <= 1.0):
            raise ValueError(f"YOLO {name} out of bounds [0, 1]: {val}")
    for name, val in [("width", norm_w), ("height", norm_h)]:
        if not (0.0 < val <= 1.0):
            raise ValueError(f"YOLO normalized {name} out of bounds (0, 1]: {val}")

    if (x_center - norm_w / 2.0) < -eps or (x_center + norm_w / 2.0) > (1.0 + eps):
        raise ValueError(f"YOLO normalized horizontal span out of bounds [0, 1]: [{x_center - norm_w/2.0}, {x_center + norm_w/2.0}]")
    if (y_center - norm_h / 2.0) < -eps or (y_center + norm_h / 2.0) > (1.0 + eps):
        raise ValueError(f"YOLO normalized vertical span out of bounds [0, 1]: [{y_center - norm_h/2.0}, {y_center + norm_h/2.0}]")

    line = f"{class_id} {x_center:.6f} {y_center:.6f} {norm_w:.6f} {norm_h:.6f}"

    return {
        "class_id": class_id,
        "x_center": round(x_center, 6),
        "y_center": round(y_center, 6),
        "width": round(norm_w, 6),
        "height": round(norm_h, 6),
        "yolo_line": line,
    }
