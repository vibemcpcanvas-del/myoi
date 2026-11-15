/**
 * static/coordinate_adapter.js
 * Aspect-ratio preserving letterbox and coordinate space adapter for SCHALE Tactical Tracking.
 * Ensures stream aspect ratio preservation and coordinate consistency.
 */

(function (window) {
    'use strict';

    function calculateLetterbox(origW, origH, targetW = 640, targetH = 640) {
        if (origW <= 0 || origH <= 0 || targetW <= 0 || targetH <= 0) {
            throw new Error(`Invalid dimensions for letterbox: orig=(${origW}, ${origH}), target=(${targetW}, ${targetH})`);
        }
        const scale = Math.min(targetW / origW, targetH / origH);
        const scaledW = Math.round(origW * scale);
        const scaledH = Math.round(origH * scale);
        const padX = (targetW - scaledW) / 2.0;
        const padY = (targetH - scaledH) / 2.0;

        return {
            origW,
            origH,
            targetW,
            targetH,
            scale,
            padX,
            padY,
            scaledW,
            scaledH,
        };
    }

    function transformBoxToOriginal(box, coordSpace, letterbox) {
        if (coordSpace === 'unknown') {
            throw new Error("Cannot transform coordinates for unverified coordinateSpace: 'unknown'.");
        }

        let ox1, oy1, ox2, oy2;

        if (coordSpace === 'letterbox_640' || coordSpace === 'letterbox') {
            ox1 = (box.x1 - letterbox.padX) / letterbox.scale;
            oy1 = (box.y1 - letterbox.padY) / letterbox.scale;
            ox2 = (box.x2 - letterbox.padX) / letterbox.scale;
            oy2 = (box.y2 - letterbox.padY) / letterbox.scale;
        } else if (coordSpace === 'normalized') {
            ox1 = box.x1 * letterbox.origW;
            oy1 = box.y1 * letterbox.origH;
            ox2 = box.x2 * letterbox.origW;
            oy2 = box.y2 * letterbox.origH;
        } else if (coordSpace === 'original') {
            ox1 = box.x1;
            oy1 = box.y1;
            ox2 = box.x2;
            oy2 = box.y2;
        } else if (coordSpace === 'stretch_640') {
            ox1 = box.x1 * (letterbox.origW / letterbox.targetW);
            oy1 = box.y1 * (letterbox.origH / letterbox.targetH);
            ox2 = box.x2 * (letterbox.origW / letterbox.targetW);
            oy2 = box.y2 * (letterbox.origH / letterbox.targetH);
        } else {
            throw new Error(`Unsupported coordinate space: '${coordSpace}'`);
        }

        const minX = Math.min(ox1, ox2);
        const maxX = Math.max(ox1, ox2);
        const minY = Math.min(oy1, oy2);
        const maxY = Math.max(oy1, oy2);

        // Clamping to image boundaries
        const clampedX1 = Math.max(0, Math.min(letterbox.origW, minX));
        const clampedY1 = Math.max(0, Math.min(letterbox.origH, minY));
        const clampedX2 = Math.max(0, Math.min(letterbox.origW, maxX));
        const clampedY2 = Math.max(0, Math.min(letterbox.origH, maxY));

        const w = clampedX2 - clampedX1;
        const h = clampedY2 - clampedY1;

        if (w <= 0 || h <= 0) {
            throw new Error(`Transformed box is degenerate: [${clampedX1}, ${clampedY1}, ${clampedX2}, ${clampedY2}]`);
        }

        return {
            x1: Math.round(clampedX1 * 100) / 100,
            y1: Math.round(clampedY1 * 100) / 100,
            x2: Math.round(clampedX2 * 100) / 100,
            y2: Math.round(clampedY2 * 100) / 100,
            width: Math.round(w * 100) / 100,
            height: Math.round(h * 100) / 100,
        };
    }

    function toYoloDetection(box, origW, origH, classId) {
        if (classId < 0) {
            throw new Error(`YOLO classId must be >= 0, got ${classId}`);
        }
        if (origW <= 0 || origH <= 0) {
            throw new Error(`Invalid original image dimensions: (${origW}, ${origH})`);
        }
        const w = box.x2 - box.x1;
        const h = box.y2 - box.y1;
        if (w <= 0 || h <= 0) {
            throw new Error(`Invalid box dimensions for YOLO: width=${w}, height=${h}`);
        }

        const xCenter = ((box.x1 + box.x2) / 2.0) / origW;
        const yCenter = ((box.y1 + box.y2) / 2.0) / origH;
        const normW = w / origW;
        const normH = h / origH;

        if (xCenter < 0 || xCenter > 1 || yCenter < 0 || yCenter > 1 || normW <= 0 || normW > 1 || normH <= 0 || normH > 1) {
            throw new Error(`Normalized coordinates out of bounds: xCenter=${xCenter}, yCenter=${yCenter}, w=${normW}, h=${normH}`);
        }

        return {
            classId: classId,
            xCenter: Number(xCenter.toFixed(6)),
            yCenter: Number(yCenter.toFixed(6)),
            width: Number(normW.toFixed(6)),
            height: Number(normH.toFixed(6)),
            yoloLine: `${classId} ${xCenter.toFixed(6)} ${yCenter.toFixed(6)} ${normW.toFixed(6)} ${normH.toFixed(6)}`
        };
    }

    window.CoordinateAdapter = {
        calculateLetterbox,
        transformBoxToOriginal,
        toYoloDetection,
    };
})(typeof window !== 'undefined' ? window : this);
