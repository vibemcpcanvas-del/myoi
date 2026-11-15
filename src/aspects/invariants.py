"""src/aspects/invariants.py
Aspect 4: Boundary & Coordinate Invariant Validation.
Decouples mathematical invariants, coordinate space verification, and domain boundary enforcement.
"""

import functools
import math
from typing import Any, Callable, Dict, Optional, Set, Tuple


VALID_COORDINATE_SPACES: Set[str] = {
    "letterbox_640",
    "letterbox",
    "original",
    "normalized",
    "stretch_640",
}


def assert_verified_coordinate_space(coord_space: str) -> None:
    """Fail-Closed Invariant: Rejects unknown or unverified coordinate spaces."""
    if not coord_space or coord_space == "unknown":
        raise ValueError("Cannot transform coordinates for unverified coordinateSpace: 'unknown'.")
    if coord_space not in VALID_COORDINATE_SPACES:
        raise ValueError(f"Unsupported coordinate space: '{coord_space}'")


def assert_box_boundary_invariants(box: Any, orig_w: int, orig_h: int, eps: float = 1e-4) -> None:
    """Enforces bounding box spatial containment, finite numeric coordinates,
    and non-degeneracy within frame dimensions.
    """
    if not hasattr(box, "x1") or not hasattr(box, "x2") or not hasattr(box, "y1") or not hasattr(box, "y2"):
        raise ValueError(f"Invalid box object: {box}")

    # 1. Finite numeric invariant: strictly reject NaN, Inf, or non-numeric types
    for name, val in (("x1", box.x1), ("y1", box.y1), ("x2", box.x2), ("y2", box.y2)):
        if not (isinstance(val, (int, float)) and math.isfinite(val)):
            raise ValueError(f"Box coordinate '{name}' is not a finite number: {val}")

    # 2. Geometric non-degeneracy invariant: width and height must be strictly positive (> eps)
    if hasattr(box, "is_valid") and not box.is_valid():
        raise ValueError(
            f"Invalid box for YOLO conversion: degenerate or inverted box ({box.x1}, {box.y1}, {box.x2}, {box.y2})"
        )

    if (box.x2 - box.x1) <= eps or (box.y2 - box.y1) <= eps:
        raise ValueError(
            f"Invalid box for YOLO conversion: degenerate or inverted box [x1={box.x1}, y1={box.y1}, x2={box.x2}, y2={box.y2}]"
        )

    # 3. Spatial boundary containment invariant: coordinates must reside within [0, orig_dimension]
    if box.x1 < -eps or box.y1 < -eps or box.x2 > (orig_w + eps) or box.y2 > (orig_h + eps):
        raise ValueError(
            f"Box coordinates exceed frame boundaries: [x1={box.x1}, y1={box.y1}, x2={box.x2}, y2={box.y2}] for frame {orig_w}x{orig_h}"
        )


def enforce_coordinate_invariants(func: Callable) -> Callable:
    """AOP Decorator: Intercepts coordinate transformation to enforce space verification
    and boundary containment fail-closed.
    """

    @functools.wraps(func)
    def wrapper(box: Any, coord_space: str, letterbox: Any, *args, **kwargs):
        # Pre-condition advice: Verify coordinate space
        assert_verified_coordinate_space(coord_space)

        # Pre-condition advice: Verify input box has finite numeric coordinates
        if hasattr(box, "x1") and hasattr(box, "x2") and hasattr(box, "y1") and hasattr(box, "y2"):
            for name, val in (("x1", box.x1), ("y1", box.y1), ("x2", box.x2), ("y2", box.y2)):
                if not (isinstance(val, (int, float)) and math.isfinite(val)):
                    raise ValueError(f"Input box coordinate '{name}' is not a finite number: {val}")

        # Execute transformation
        result_box = func(box, coord_space, letterbox, *args, **kwargs)

        # Post-condition advice: Verify valid non-degenerate box
        if hasattr(result_box, "is_valid") and not result_box.is_valid():
            raise ValueError(
                f"Transformed bounding box is degenerate or zero-area: ({result_box.x1}, {result_box.y1}, {result_box.x2}, {result_box.y2})"
            )

        return result_box

    return wrapper


def enforce_yolo_invariants(func: Callable) -> Callable:
    """AOP Decorator: Intercepts YOLO detection conversion to enforce non-negative integer class IDs,
    positive frame bounds, boundary containment, and normalized [0, 1] span invariants.
    """

    @functools.wraps(func)
    def wrapper(box: Any, orig_w: int, orig_h: int, class_id: int, *args, **kwargs):
        # Pre-condition advice: Validate class_id (must be integer >= 0, not bool)
        if not isinstance(class_id, int) or isinstance(class_id, bool) or class_id < 0:
            raise ValueError(f"YOLO class_id must be non-negative integer (>= 0), got {class_id}")
        if not (isinstance(orig_w, (int, float)) and isinstance(orig_h, (int, float)) and orig_w > 0 and orig_h > 0):
            raise ValueError(f"Original image dimensions must be positive, got ({orig_w}, {orig_h})")

        # Boundary and geometric containment check
        assert_box_boundary_invariants(box, int(orig_w), int(orig_h))

        # Execute YOLO detection conversion
        result = func(box, orig_w, orig_h, class_id, *args, **kwargs)

        # Post-condition advice: Verify normalized coordinates [0, 1]
        for coord_key in ("x_center", "y_center"):
            val = result.get(coord_key, 0.0)
            if not (0.0 <= val <= 1.0):
                raise ValueError(f"YOLO {coord_key} out of bounds [0, 1]: {val}")

        for dim_key in ("width", "height"):
            val = result.get(dim_key, 0.0)
            if not (0.0 < val <= 1.0):
                raise ValueError(f"YOLO normalized {dim_key} out of bounds (0, 1]: {val}")

        # Post-condition advice: Verify normalized horizontal and vertical spans
        eps = 1e-4
        xc, yc = result.get("x_center", 0.0), result.get("y_center", 0.0)
        nw, nh = result.get("width", 0.0), result.get("height", 0.0)
        if (xc - nw / 2.0) < -eps or (xc + nw / 2.0) > (1.0 + eps):
            raise ValueError(f"YOLO normalized horizontal span out of bounds [0, 1]: [{xc - nw/2.0}, {xc + nw/2.0}]")
        if (yc - nh / 2.0) < -eps or (yc + nh / 2.0) > (1.0 + eps):
            raise ValueError(f"YOLO normalized vertical span out of bounds [0, 1]: [{yc - nh/2.0}, {yc + nh/2.0}]")

        return result

    return wrapper
