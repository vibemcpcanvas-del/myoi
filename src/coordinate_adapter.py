"""src/coordinate_adapter.py
Coordinate adapter and aspect-ratio preserving letterbox utilities.
Handles transformations between original stream frames, letterbox inference canvases,
and normalized YOLO detection coordinates.
Aspect-Oriented invariant verification is decoupled via src.aspects.invariants.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from src.aspects.invariants import enforce_coordinate_invariants, enforce_yolo_invariants


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


@enforce_coordinate_invariants
def transform_box_to_original(
    box: BoundingBox,
    coord_space: str,
    letterbox: LetterboxInfo,
) -> BoundingBox:
    """Transforms bounding box coordinates from given coordinate space to original frame pixels.
    Fails closed if coordinate space is unknown or unverified via AOP invariant gate.
    """
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

    return result_box


@enforce_yolo_invariants
def to_yolo_detection(
    box: BoundingBox,
    orig_w: int,
    orig_h: int,
    class_id: int,
) -> Dict[str, Any]:
    """Converts pixel bounding box on the original frame into normalized YOLO detect format.
    Boundary verification and normalization checks are intercepted by AOP invariants aspect.
    """
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

    line = f"{class_id} {x_center:.6f} {y_center:.6f} {norm_w:.6f} {norm_h:.6f}"

    return {
        "class_id": class_id,
        "x_center": round(x_center, 6),
        "y_center": round(y_center, 6),
        "width": round(norm_w, 6),
        "height": round(norm_h, 6),
        "yolo_line": line,
    }
