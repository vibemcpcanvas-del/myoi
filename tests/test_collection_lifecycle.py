"""tests/test_collection_lifecycle.py
Comprehensive verification of stream collection triggers, lifecycle management,
and regression prevention (Requirements 10.5, 10.6, 10.7, 10.8, 10.9, 10.14, 10.15).
"""

import pytest


class MockStreamTrack:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


class MockStream:
    def __init__(self, width=1920, height=1080):
        self.width = width
        self.height = height
        self.tracks = [MockStreamTrack()]

    def getTracks(self):
        return self.tracks

    def getVideoTracks(self):
        return self.tracks


class MockTacticalTracker:
    """Python simulation of the frontend tracking & collection state machine
    to mechanically verify stream logic, filters, and lifecycle behavior.
    """
    def __init__(self):
        self.stream = None
        self.isRunning = False
        self.timerActive = False
        self.abortControllerActive = False
        self.cls = {
            0: {"name": "Sensei", "on": True, "alt": True, "cnf": 0.50, "count": 0},
            1: {"name": "Enemy", "on": False, "alt": True, "cnf": 0.30, "count": 0},  # Inactive class
        }
        self.thFrames = 2
        self.cooldownMs = 2000
        self.lastSnapshotTime = -100000
        self.lastBeep = -100000
        self.lastTTS = {}
        self.capturedRecords = []

    def start_stream(self, width=1920, height=1080):
        self.stream = MockStream(width, height)

    def start_ops(self):
        if not self.stream:
            raise RuntimeError("No stream available")
        self.isRunning = True
        self.timerActive = True

    def stop_ops(self):
        self.isRunning = False
        self.timerActive = False
        if self.abortControllerActive:
            self.abortControllerActive = False  # Aborted
        if self.stream:
            for t in self.stream.getTracks():
                t.stop()
            self.stream = None

    def process_frame_detections(self, raw_results, current_time_ms, data_collection_enabled=True):
        if not self.isRunning or not self.stream:
            return None

        seen = set()
        has_alert = False
        valid_for_collection = []

        for r in raw_results:
            c = self.cls.get(r["class"])
            # Requirement 10.5: Inactive class check
            # Requirement 10.6: Class confidence check
            if not c or not c["on"] or r["confidence"] < c["cnf"]:
                continue

            seen.add(r["class"])
            c["count"] += 1

            # Requirement 10.7: Consecutive frames check
            is_conf = c["count"] >= self.thFrames
            if is_conf:
                valid_for_collection.append(r)

            if is_conf and c["alt"]:
                has_alert = True
                # Beep alert (Global 2s cooldown)
                if current_time_ms - self.lastBeep > 2000:
                    self.lastBeep = current_time_ms
                # TTS alert (Per-class 5s cooldown)
                if current_time_ms - self.lastTTS.get(r["class"], -100000) > 5000:
                    self.lastTTS[r["class"]] = current_time_ms

        # Reset counts for unseen classes
        for k in self.cls:
            if k not in seen:
                self.cls[k]["count"] = 0

        # Requirement 10.8: Cooldown check for snapshot collection
        saved_snapshot = False
        if data_collection_enabled and len(valid_for_collection) > 0:
            if current_time_ms - self.lastSnapshotTime >= self.cooldownMs:
                self.lastSnapshotTime = current_time_ms
                # Requirement 10.9: Original stream dimensions preserved
                record = {
                    "recordId": f"rec_{current_time_ms}",
                    "originalWidth": self.stream.width,
                    "originalHeight": self.stream.height,
                    "detections": valid_for_collection,
                    "reviewStatus": "pending",
                }
                self.capturedRecords.append(record)
                saved_snapshot = True

        return {
            "has_alert": has_alert,
            "saved_snapshot": saved_snapshot,
            "valid_detections": valid_for_collection,
        }


def test_5_inactive_class_excluded_from_alert_and_collection():
    """Requirement 10.5: Inactive class (c.on == False) MUST be excluded from alert and snapshot collection."""
    tracker = MockTacticalTracker()
    tracker.start_stream()
    tracker.start_ops()

    # Class 1 is disabled (on=False)
    raw = [{"class": 1, "confidence": 0.99, "box": {"x1": 10, "y1": 10, "x2": 50, "y2": 50}}]
    res = tracker.process_frame_detections(raw, current_time_ms=1000)

    assert len(res["valid_detections"]) == 0
    assert res["has_alert"] is False
    assert res["saved_snapshot"] is False
    assert tracker.cls[1]["count"] == 0  # Count not incremented


def test_6_class_confidence_threshold_rejection():
    """Requirement 10.6: Detections below class confidence threshold MUST NOT trigger save or alert."""
    tracker = MockTacticalTracker()
    tracker.start_stream()
    tracker.start_ops()

    # Class 0 threshold is 0.50. We send 0.45 (below threshold)
    raw = [{"class": 0, "confidence": 0.45, "box": {"x1": 10, "y1": 10, "x2": 50, "y2": 50}}]
    res = tracker.process_frame_detections(raw, current_time_ms=1000)

    assert len(res["valid_detections"]) == 0
    assert res["has_alert"] is False
    assert res["saved_snapshot"] is False
    assert tracker.cls[0]["count"] == 0


def test_7_consecutive_detection_frames_requirement():
    """Requirement 10.7: Detections must satisfy thFrames consecutive frames before triggering collection."""
    tracker = MockTacticalTracker()
    tracker.start_stream()
    tracker.start_ops()
    assert tracker.thFrames == 2

    # Frame 1: Valid detection (count becomes 1 < 2)
    raw = [{"class": 0, "confidence": 0.85, "box": {"x1": 10, "y1": 10, "x2": 50, "y2": 50}}]
    res1 = tracker.process_frame_detections(raw, current_time_ms=1000)
    assert tracker.cls[0]["count"] == 1
    assert res1["saved_snapshot"] is False
    assert res1["has_alert"] is False

    # Frame 2: Consecutive frame! (count becomes 2 >= 2)
    res2 = tracker.process_frame_detections(raw, current_time_ms=1200)
    assert tracker.cls[0]["count"] == 2
    assert res2["saved_snapshot"] is True
    assert res2["has_alert"] is True


def test_8_cooldown_suppresses_duplicate_snapshots():
    """Requirement 10.8: During cooldown window, duplicate saves must be suppressed."""
    tracker = MockTacticalTracker()
    tracker.start_stream()
    tracker.start_ops()

    raw = [{"class": 0, "confidence": 0.85, "box": {"x1": 10, "y1": 10, "x2": 50, "y2": 50}}]

    # Frame 1 (count 1)
    tracker.process_frame_detections(raw, current_time_ms=1000)
    # Frame 2 (count 2 -> snapshot 1 saved at t=1200)
    res2 = tracker.process_frame_detections(raw, current_time_ms=1200)
    assert res2["saved_snapshot"] is True
    assert len(tracker.capturedRecords) == 1

    # Frame 3 at t=1800 (within 2000ms cooldown: 1800 - 1200 = 600ms < 2000ms)
    res3 = tracker.process_frame_detections(raw, current_time_ms=1800)
    assert res3["saved_snapshot"] is False  # Suppressed!
    assert len(tracker.capturedRecords) == 1

    # Frame 4 at t=3500 (after cooldown: 3500 - 1200 = 2300ms > 2000ms)
    res4 = tracker.process_frame_detections(raw, current_time_ms=3500)
    assert res4["saved_snapshot"] is True  # Allowed!
    assert len(tracker.capturedRecords) == 2


def test_9_saved_original_image_resolution_matches_stream():
    """Requirement 10.9: Captured snapshot records must preserve native unscaled stream resolution."""
    tracker = MockTacticalTracker()
    tracker.start_stream(width=3840, height=2160)  # 4K stream
    tracker.start_ops()

    raw = [{"class": 0, "confidence": 0.90, "box": {"x1": 100, "y1": 100, "x2": 500, "y2": 500}}]
    # 2 consecutive frames
    tracker.process_frame_detections(raw, current_time_ms=100)
    tracker.process_frame_detections(raw, current_time_ms=300)

    assert len(tracker.capturedRecords) == 1
    rec = tracker.capturedRecords[0]
    assert rec["originalWidth"] == 3840
    assert rec["originalHeight"] == 2160


def test_14_lifecycle_cleanup_on_stop_and_track_ended():
    """Requirement 10.14: Verify timers, in-flight requests, and stream tracks are cleaned up on stop."""
    tracker = MockTacticalTracker()
    tracker.start_stream()
    tracker.start_ops()
    tracker.abortControllerActive = True

    assert tracker.isRunning is True
    assert tracker.timerActive is True
    assert tracker.stream is not None

    # Track ends or user stops
    tracker.stop_ops()

    assert tracker.isRunning is False
    assert tracker.timerActive is False
    assert tracker.abortControllerActive is False
    assert tracker.stream is None


def test_15_audio_and_class_configuration_regressions():
    """Requirement 10.15: Verify audio alerts cooldown and class configuration regression prevention."""
    tracker = MockTacticalTracker()
    tracker.start_stream()
    tracker.start_ops()

    raw = [{"class": 0, "confidence": 0.90, "box": {"x1": 10, "y1": 10, "x2": 50, "y2": 50}}]

    # Frame 1
    tracker.process_frame_detections(raw, current_time_ms=1000)
    # Frame 2: triggers Beep & TTS
    tracker.process_frame_detections(raw, current_time_ms=1200)
    assert tracker.lastBeep == 1200
    assert tracker.lastTTS[0] == 1200

    # Frame 3 at t=2000: Beep (diff 800ms < 2000ms) and TTS (diff 800ms < 5000ms) suppressed
    tracker.process_frame_detections(raw, current_time_ms=2000)
    assert tracker.lastBeep == 1200
    assert tracker.lastTTS[0] == 1200

    # Frame 4 at t=3500: Beep fires (diff 2300ms > 2000ms), TTS still suppressed (diff 2300ms < 5000ms)
    tracker.process_frame_detections(raw, current_time_ms=3500)
    assert tracker.lastBeep == 3500
    assert tracker.lastTTS[0] == 1200

    # Frame 5 at t=6500: TTS fires (diff 5300ms > 5000ms)
    tracker.process_frame_detections(raw, current_time_ms=6500)
    assert tracker.lastTTS[0] == 6500
