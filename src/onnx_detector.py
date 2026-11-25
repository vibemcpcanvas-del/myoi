"""src/onnx_detector.py
Local ONNX Runtime YOLO Inference Engine for SCHALE Tactical Tracking.
Supports direct local inference on .onnx weight files without external cloud calls.
Enforces fail-closed validation on model paths, weights, and tensor outputs.
"""

from __future__ import annotations

import ast
import io
from pathlib import Path
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

try:
    import onnxruntime as ort
    HAS_ONNXRUNTIME = True
except ImportError:
    ort = None  # type: ignore
    HAS_ONNXRUNTIME = False


MODEL_FILENAME_PATTERN = re.compile(r"^[a-zA-Z0-9_\-\.]+\.onnx$", re.IGNORECASE)
MAX_ONNX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB


def sanitize_onnx_filename(filename: str, models_dir: Path) -> Path:
    """Validates an ONNX model filename against path traversal and extension rules."""
    if not isinstance(filename, str) or not filename.strip():
        raise ValueError("Model filename must be a non-empty string.")
    name = filename.strip()
    if "\x00" in name or ".." in name or "/" in name or "\\" in name:
        raise ValueError(f"Illegal characters or path traversal detected in model filename: '{name}'")
    if not MODEL_FILENAME_PATTERN.match(name):
        raise ValueError(f"Invalid ONNX model filename (must end with .onnx): '{name}'")
    base = models_dir.resolve()
    resolved = (base / name).resolve()
    try:
        resolved.relative_to(base)
    except ValueError:
        raise ValueError(f"Model path escapes models directory: '{name}'")
    return resolved


def _parse_names_metadata(raw_names: Optional[str]) -> Dict[int, str]:
    """Parses Ultralytics/YOLO ONNX 'names' metadata dictionary string."""
    if not raw_names:
        return {}
    try:
        parsed = ast.literal_eval(raw_names)
        if isinstance(parsed, dict):
            return {int(k): str(v) for k, v in parsed.items()}
        if isinstance(parsed, list):
            return {i: str(v) for i, v in enumerate(parsed)}
    except Exception:
        pass
    return {}


def _nms_numpy(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> List[int]:
    """Pure-NumPy Non-Maximum Suppression on [x1, y1, x2, y2] boxes."""
    if len(boxes) == 0:
        return []
    x1 = boxes[:, 0]
    y1 = boxes[:, 1]
    x2 = boxes[:, 2]
    y2 = boxes[:, 3]
    areas = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
    order = scores.argsort()[::-1]

    keep: List[int] = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        w = np.maximum(0.0, xx2 - xx1)
        h = np.maximum(0.0, yy2 - yy1)
        inter = w * h
        denom = areas[i] + areas[order[1:]] - inter
        iou = np.where(denom > 0.0, inter / denom, 0.0)
        inds = np.where(iou <= iou_threshold)[0]
        order = order[inds + 1]
    return keep


class OnnxYoloDetector:
    """Thread-safe local ONNX Runtime inference engine for YOLO detection models."""

    def __init__(self, models_dir: Path):
        self.models_dir = Path(models_dir).resolve()
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._session: Optional[Any] = None
        self._active_model_name: Optional[str] = None
        self._input_name: str = "images"
        self._input_size: Tuple[int, int] = (640, 640)  # (H, W)
        self._class_names: Dict[int, str] = {}

    @property
    def is_available(self) -> bool:
        return HAS_ONNXRUNTIME

    @property
    def active_model_name(self) -> Optional[str]:
        with self._lock:
            return self._active_model_name

    @property
    def is_loaded(self) -> bool:
        with self._lock:
            return self._session is not None and self._active_model_name is not None

    def list_models(self) -> List[Dict[str, Any]]:
        """Lists all valid .onnx model files inside models_dir."""
        self.models_dir.mkdir(parents=True, exist_ok=True)
        models: List[Dict[str, Any]] = []
        for p in sorted(self.models_dir.iterdir(), key=lambda x: x.name.lower()):
            if p.is_file() and p.suffix.lower() == ".onnx":
                try:
                    sanitize_onnx_filename(p.name, self.models_dir)
                    st = p.stat()
                    models.append({
                        "name": p.name,
                        "sizeBytes": st.st_size,
                        "sizeMb": round(st.st_size / (1024 * 1024), 2),
                        "modifiedAt": st.st_mtime,
                        "active": p.name == self._active_model_name,
                    })
                except ValueError:
                    continue
        return models

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            providers = ort.get_available_providers() if HAS_ONNXRUNTIME else []
            return {
                "onnxAvailable": HAS_ONNXRUNTIME,
                "loaded": self.is_loaded,
                "activeModel": self._active_model_name,
                "inputSize": [self._input_size[1], self._input_size[0]],
                "providers": providers,
                "modelsDir": str(self.models_dir),
                "models": self.list_models(),
            }

    def unload_model(self) -> Dict[str, Any]:
        with self._lock:
            self._session = None
            self._active_model_name = None
            self._class_names = {}
        return {"success": True, "loaded": False, "activeModel": None}

    def load_model(self, model_name: str) -> Dict[str, Any]:
        """Loads an ONNX model from models_dir into an InferenceSession."""
        if not HAS_ONNXRUNTIME:
            raise RuntimeError("onnxruntime is not installed in the Python environment.")
        model_path = sanitize_onnx_filename(model_name, self.models_dir)
        if not model_path.exists():
            raise FileNotFoundError(f"ONNX model not found: '{model_name}'")

        with self._lock:
            if self._active_model_name == model_path.name and self._session is not None:
                return {
                    "success": True,
                    "loaded": True,
                    "activeModel": self._active_model_name,
                    "inputSize": [self._input_size[1], self._input_size[0]],
                    "classNames": self._class_names,
                }

            providers = ort.get_available_providers()
            preferred = [p for p in ("DmlExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider") if p in providers]
            if not preferred:
                preferred = ["CPUExecutionProvider"]

            session = ort.InferenceSession(str(model_path), providers=preferred)
            inputs = session.get_inputs()
            if not inputs:
                raise ValueError(f"Invalid ONNX model '{model_name}': no input tensors found.")

            inp = inputs[0]
            input_name = inp.name
            shape = inp.shape
            h, w = 640, 640
            if len(shape) == 4:
                sh, sw = shape[2], shape[3]
                if isinstance(sh, int) and 32 <= sh <= 4096:
                    h = sh
                if isinstance(sw, int) and 32 <= sw <= 4096:
                    w = sw

            meta = session.get_modelmeta()
            custom_map = getattr(meta, "custom_metadata_map", {}) or {}
            class_names = _parse_names_metadata(custom_map.get("names"))

            self._session = session
            self._active_model_name = model_path.name
            self._input_name = input_name
            self._input_size = (h, w)
            self._class_names = class_names

            return {
                "success": True,
                "loaded": True,
                "activeModel": self._active_model_name,
                "inputSize": [w, h],
                "classNames": class_names,
                "providers": session.get_providers(),
            }

    def save_uploaded_model(self, filename: str, payload: bytes) -> Dict[str, Any]:
        """Validates and saves an uploaded .onnx file into models_dir."""
        if not payload or len(payload) == 0:
            raise ValueError("Uploaded ONNX file is empty (0 bytes).")
        if len(payload) > MAX_ONNX_UPLOAD_BYTES:
            raise ValueError(f"Uploaded ONNX file exceeds maximum size of {MAX_ONNX_UPLOAD_BYTES} bytes.")
        dest_path = sanitize_onnx_filename(filename, self.models_dir)
        tmp_path = dest_path.with_suffix(".onnx.tmp")
        try:
            tmp_path.write_bytes(payload)
            tmp_path.replace(dest_path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
        return self.load_model(dest_path.name)

    def predict(
        self,
        image_bytes: bytes,
        conf: float = 0.25,
        iou: float = 0.45,
        imgsz: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Runs local ONNX inference on image bytes and returns Ultralytics-compatible JSON."""
        if not HAS_ONNXRUNTIME:
            raise RuntimeError("onnxruntime is not installed.")
        if not image_bytes:
            raise ValueError("Empty image payload for ONNX inference.")

        with self._lock:
            if self._session is None or self._active_model_name is None:
                raise RuntimeError("No ONNX model is currently loaded. Select or upload a .onnx file first.")
            session = self._session
            input_name = self._input_name
            target_h, target_w = self._input_size
            class_names = dict(self._class_names)
            active_name = self._active_model_name

        t0 = time.perf_counter()
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        orig_w, orig_h = img.size

        # Letterbox resize to (target_w, target_h) if dimensions differ
        scale = min(target_w / float(orig_w), target_h / float(orig_h))
        new_w = max(1, int(round(orig_w * scale)))
        new_h = max(1, int(round(orig_h * scale)))
        pad_x = (target_w - new_w) // 2
        pad_y = (target_h - new_h) // 2

        if (orig_w, orig_h) == (target_w, target_h):
            canvas = img
            scale = 1.0
            pad_x, pad_y = 0, 0
        else:
            resized = img.resize((new_w, new_h), Image.Resampling.BILINEAR)
            canvas = Image.new("RGB", (target_w, target_h), (114, 114, 114))
            canvas.paste(resized, (pad_x, pad_y))

        arr = np.asarray(canvas, dtype=np.float32) / 255.0
        tensor = np.transpose(arr, (2, 0, 1))[np.newaxis, ...]  # (1, 3, H, W)
        t_pre = (time.perf_counter() - t0) * 1000.0

        t1 = time.perf_counter()
        outputs = session.run(None, {input_name: tensor})
        t_inf = (time.perf_counter() - t1) * 1000.0

        t2 = time.perf_counter()
        results = self._postprocess(
            outputs=outputs,
            conf_thres=conf,
            iou_thres=iou,
            pad_x=pad_x,
            pad_y=pad_y,
            scale=scale,
            orig_w=orig_w,
            orig_h=orig_h,
            class_names=class_names,
        )
        t_post = (time.perf_counter() - t2) * 1000.0

        return {
            "success": True,
            "coordinateSpace": "letterbox_640" if (orig_w == 640 and orig_h == 640) else "original",
            "inferenceWidth": target_w,
            "inferenceHeight": target_h,
            "speed": {
                "preprocess": round(t_pre, 2),
                "inference": round(t_inf, 2),
                "postprocess": round(t_post, 2),
            },
            "results": results,
            "metadata": {
                "engine": "onnx_local",
                "model": active_name,
            },
        }

    def _postprocess(
        self,
        outputs: List[np.ndarray],
        conf_thres: float,
        iou_thres: float,
        pad_x: int,
        pad_y: int,
        scale: float,
        orig_w: int,
        orig_h: int,
        class_names: Dict[int, str],
    ) -> List[Dict[str, Any]]:
        if not outputs:
            return []
        pred = np.asarray(outputs[0])
        if pred.ndim == 3:
            pred = pred[0]
        if pred.ndim != 2 or pred.shape[0] == 0 or pred.shape[1] == 0:
            return []

        # Case 1: End-to-end NMS-embedded ONNX output [N, 6] -> [x1, y1, x2, y2, conf, cls]
        if pred.shape[1] == 6 and pred.shape[0] <= 1000:
            results: List[Dict[str, Any]] = []
            for row in pred:
                score = float(row[4])
                if score < conf_thres:
                    continue
                cls_id = int(round(float(row[5])))
                x1 = max(0.0, min(float(orig_w), (float(row[0]) - pad_x) / scale))
                y1 = max(0.0, min(float(orig_h), (float(row[1]) - pad_y) / scale))
                x2 = max(0.0, min(float(orig_w), (float(row[2]) - pad_x) / scale))
                y2 = max(0.0, min(float(orig_h), (float(row[3]) - pad_y) / scale))
                if x2 <= x1 or y2 <= y1:
                    continue
                results.append({
                    "class": cls_id,
                    "name": class_names.get(cls_id, f"C{cls_id}"),
                    "confidence": round(score, 4),
                    "box": {"x1": round(x1, 2), "y1": round(y1, 2), "x2": round(x2, 2), "y2": round(y2, 2)},
                    "source": "onnx_local",
                })
            return results

        # Case 2: Standard Ultralytics YOLOv8/v11 output [4 + num_classes, num_anchors]
        # Transpose if channels are on axis 0 and anchors on axis 1 (e.g., (84, 8400))
        if pred.shape[0] < pred.shape[1] and pred.shape[0] >= 5:
            pred = pred.T  # -> (num_anchors, 4 + num_classes)

        if pred.shape[1] < 5:
            return []

        boxes_xywh = pred[:, :4]
        class_scores = pred[:, 4:]

        cls_ids = np.argmax(class_scores, axis=1)
        scores = class_scores[np.arange(len(class_scores)), cls_ids]

        mask = scores >= conf_thres
        if not np.any(mask):
            return []

        boxes_xywh = boxes_xywh[mask]
        scores = scores[mask]
        cls_ids = cls_ids[mask]

        # Convert cx, cy, w, h -> x1, y1, x2, y2
        cx = boxes_xywh[:, 0]
        cy = boxes_xywh[:, 1]
        bw = boxes_xywh[:, 2]
        bh = boxes_xywh[:, 3]
        x1 = (cx - bw / 2.0 - pad_x) / scale
        y1 = (cy - bh / 2.0 - pad_y) / scale
        x2 = (cx + bw / 2.0 - pad_x) / scale
        y2 = (cy + bh / 2.0 - pad_y) / scale

        x1 = np.clip(x1, 0.0, float(orig_w))
        y1 = np.clip(y1, 0.0, float(orig_h))
        x2 = np.clip(x2, 0.0, float(orig_w))
        y2 = np.clip(y2, 0.0, float(orig_h))

        boxes_xyxy = np.stack([x1, y1, x2, y2], axis=1)

        # Class-aware NMS via coordinate offset
        max_dim = max(orig_w, orig_h, 1024) * 2.0
        offsets = cls_ids.astype(np.float32) * max_dim
        boxes_for_nms = boxes_xyxy + offsets[:, np.newaxis]
        keep = _nms_numpy(boxes_for_nms, scores, iou_thres)

        results = []
        for idx in keep:
            bx1, by1, bx2, by2 = boxes_xyxy[idx]
            if bx2 <= bx1 or by2 <= by1:
                continue
            cid = int(cls_ids[idx])
            results.append({
                "class": cid,
                "name": class_names.get(cid, f"C{cid}"),
                "confidence": round(float(scores[idx]), 4),
                "box": {
                    "x1": round(float(bx1), 2),
                    "y1": round(float(by1), 2),
                    "x2": round(float(bx2), 2),
                    "y2": round(float(by2), 2),
                },
                "source": "onnx_local",
            })
        return results


_detector_instance: Optional[OnnxYoloDetector] = None
_detector_lock = threading.Lock()


def get_onnx_detector(models_dir: Optional[Path] = None) -> OnnxYoloDetector:
    """Singleton accessor for OnnxYoloDetector."""
    global _detector_instance
    with _detector_lock:
        target_dir = (models_dir or (Path(__file__).resolve().parent.parent / "models")).resolve()
        if _detector_instance is None or _detector_instance.models_dir != target_dir:
            _detector_instance = OnnxYoloDetector(models_dir=target_dir)
        return _detector_instance
