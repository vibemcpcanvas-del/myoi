"""tests/test_aop_aspects.py
Comprehensive verification suite for Aspect-Oriented Programming (AOP) architecture.
Validates all 5 decoupled cross-cutting concerns:
  1. Secret Protection & Upstream Error Redaction
  2. Fail-Closed Circuit Breaker & Retry / Rate-Limiting Policy
  3. Latency, Health, and Operational Telemetry
  4. Boundary & Coordinate Invariant Validation
  5. Audit Trails & Manifest Sanitization
  6. Frontend JavaScript AOP Suite in Node.js
"""

import asyncio
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
import time
import pytest
from fastapi import HTTPException

from src.aspects import (
    AuditTrailLogger,
    CircuitBreaker,
    CircuitBreakerOpenException,
    CircuitState,
    SecretRedactor,
    TelemetryCollector,
    assert_box_boundary_invariants,
    assert_verified_coordinate_space,
    audit_manifest_export,
    circuit_breaker,
    compute_sha256,
    default_audit_logger,
    default_inference_circuit_breaker,
    default_telemetry_collector,
    enforce_coordinate_invariants,
    enforce_yolo_invariants,
    get_global_redactor,
    measure_telemetry,
    redact_secrets,
    sanitize_record_for_manifest,
)
from src.coordinate_adapter import BoundingBox, LetterboxInfo


# ==============================================================================
# 1. Secret Protection & Error Redaction Tests
# ==============================================================================

def test_secret_redactor_exact_and_patterns():
    """Verify SecretRedactor scrubs registered tokens, bearer tokens, and credentials."""
    redactor = SecretRedactor()
    dummy_key = "dummy_secret_key_abcdef123456"
    redactor.register_secret(dummy_key)

    # 1. Exact match redaction
    raw_text = f"Failed to connect using key {dummy_key} to upstream endpoint"
    sanitized = redactor.redact_text(raw_text)
    assert dummy_key not in sanitized
    assert "[REDACTED]" in sanitized

    # 2. Bearer token regex pattern
    bearer_text = "Authorization: Bearer secret_bearer_token_xyz"
    assert "secret_bearer_token_xyz" not in redactor.redact_text(bearer_text)
    assert "Bearer [REDACTED]" in redactor.redact_text(bearer_text)

    # 3. Recursive data structures
    nested_data = {
        "status": "error",
        "api_key": "some_plain_key",
        "nested": {
            "token": "nested_token_value",
            "message": f"Error with key {dummy_key}",
        },
        "list_items": [f"item with {dummy_key}", "clean item"],
    }
    cleaned = redactor.redact_data(nested_data)
    assert cleaned["api_key"] == "[REDACTED]"
    assert cleaned["nested"]["token"] == "[REDACTED]"
    assert dummy_key not in cleaned["nested"]["message"]
    assert "[REDACTED]" in cleaned["nested"]["message"]
    assert dummy_key not in cleaned["list_items"][0]


def test_redact_secrets_decorator_sync_and_async():
    """Verify @redact_secrets decorator intercepts exceptions and scrubs details."""
    redactor = SecretRedactor()
    secret = "my_private_token_999"
    redactor.register_secret(secret)

    @redact_secrets(redactor=redactor)
    def failing_sync():
        raise HTTPException(status_code=400, detail={"error": "auth", "msg": f"Secret {secret} invalid"})

    @redact_secrets(redactor=redactor)
    async def failing_async():
        raise HTTPException(status_code=401, detail=f"Unauthorized: Token {secret} rejected")

    with pytest.raises(HTTPException) as exc_info:
        failing_sync()
    assert secret not in str(exc_info.value.detail)
    assert "[REDACTED]" in exc_info.value.detail["msg"]

    with pytest.raises(HTTPException) as exc_async:
        asyncio.run(failing_async())
    assert secret not in str(exc_async.value.detail)
    assert "[REDACTED]" in str(exc_async.value.detail)


def test_secret_redactor_preserves_quotes_and_redacts_nested_lists():
    """Verify regex patterns preserve enclosing quotes and decorator redacts list detail structures."""
    redactor = SecretRedactor()
    raw = "api_key='super_secret_123', other='safe'"
    sanitized = redactor.redact_text(raw)
    assert "super_secret_123" not in sanitized
    assert "api_key='[REDACTED]'" in sanitized

    secret = "token_in_list_456"
    redactor.register_secret(secret)

    @redact_secrets(redactor=redactor)
    def failing_with_list_detail():
        raise HTTPException(
            status_code=422,
            detail=[{"loc": ["header", "authorization"], "msg": f"Invalid token {secret}"}],
        )

    with pytest.raises(HTTPException) as exc_info:
        failing_with_list_detail()
    assert secret not in str(exc_info.value.detail)
    assert "[REDACTED]" in exc_info.value.detail[0]["msg"]


# ==============================================================================
# 2. Fail-Closed Circuit Breaker Tests
# ==============================================================================

def test_circuit_breaker_fail_closed_on_auth_rejection():
    """Fail-Closed Invariant: 401 or 403 MUST immediately trip circuit to OPEN."""
    breaker = CircuitBreaker(name="test_breaker", failure_threshold=5, recovery_timeout=1.0)
    assert breaker.state == CircuitState.CLOSED
    assert breaker.can_execute() is True

    # 401 Unauthorized occurs
    breaker.record_failure(status_code=401, error_message="Unauthorized")
    assert breaker.state == CircuitState.OPEN
    assert breaker.can_execute() is False
    assert breaker.total_trips == 1

    # Calls while OPEN must be blocked
    @circuit_breaker(breaker=breaker)
    def protected_fn():
        return "ok"

    with pytest.raises(CircuitBreakerOpenException) as exc_info:
        protected_fn()
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["error"] == "circuit_breaker_open"


def test_circuit_breaker_threshold_trip_and_recovery():
    """Verify consecutive general errors trip circuit, and recovery transitions HALF_OPEN -> CLOSED."""
    breaker = CircuitBreaker(name="recover_breaker", failure_threshold=3, recovery_timeout=0.2)
    assert breaker.state == CircuitState.CLOSED

    # 1st and 2nd 500 error: still closed
    breaker.record_failure(status_code=500, error_message="Internal Error 1")
    assert breaker.state == CircuitState.CLOSED
    breaker.record_failure(status_code=500, error_message="Internal Error 2")
    assert breaker.state == CircuitState.CLOSED

    # 3rd error reaches failure_threshold -> trips to OPEN
    breaker.record_failure(status_code=500, error_message="Internal Error 3")
    assert breaker.state == CircuitState.OPEN
    assert breaker.can_execute() is False

    # Wait for recovery timeout
    time.sleep(0.25)
    # Next can_execute transitions to HALF_OPEN
    assert breaker.can_execute() is True
    assert breaker.state == CircuitState.HALF_OPEN

    # Success restores CLOSED
    breaker.record_success()
    assert breaker.state == CircuitState.CLOSED
    assert breaker.consecutive_failures == 0


def test_circuit_breaker_telemetry_reporting():
    """Verify circuit breaker provides telemetry dictionary with accurate state."""
    breaker = CircuitBreaker(name="telemetry_cb", failure_threshold=2, recovery_timeout=5.0)
    breaker.record_failure(status_code=401, error_message="Auth rejected")
    t = breaker.get_telemetry()
    assert t["name"] == "telemetry_cb"
    assert t["state"] == "OPEN"
    assert t["totalTrips"] == 1
    assert t["lastErrorMessage"] == "Auth rejected"
    assert t["cooldownRemainingSec"] > 0


def test_circuit_breaker_ignores_client_errors_and_isolates_canary():
    """Verify 400 Bad Request does not trip circuit, and HALF_OPEN permits only 1 canary request."""
    breaker = CircuitBreaker(name="robust_cb", failure_threshold=3, recovery_timeout=0.1)

    # 1. 400 Bad Request / 422 Unprocessable must NOT trip the circuit breaker
    @circuit_breaker(breaker=breaker)
    def bad_request_fn():
        raise HTTPException(status_code=400, detail="Malformed payload")

    for _ in range(5):
        with pytest.raises(HTTPException):
            bad_request_fn()

    assert breaker.state == CircuitState.CLOSED
    assert breaker.consecutive_failures == 0

    # 2. Server errors (500) trip breaker
    @circuit_breaker(breaker=breaker)
    def server_error_fn():
        raise HTTPException(status_code=500, detail="Server crashed")

    for _ in range(3):
        with pytest.raises(HTTPException):
            server_error_fn()

    assert breaker.state == CircuitState.OPEN
    assert breaker.can_execute() is False

    # Wait for cooldown
    time.sleep(0.15)

    # 3. Canary isolation: First request enters HALF_OPEN
    assert breaker.can_execute() is True
    assert breaker.state == CircuitState.HALF_OPEN
    # Second concurrent call while canary is in-flight must be rejected!
    assert breaker.can_execute() is False

    # Successful canary restores CLOSED
    breaker.record_success()
    assert breaker.state == CircuitState.CLOSED
    assert breaker.can_execute() is True


# ==============================================================================
# 3. Latency & Operational Telemetry Tests
# ==============================================================================

def test_telemetry_collector_accounting():
    """Verify TelemetryCollector records calls, counts successes/failures, and aggregates latency."""
    collector = TelemetryCollector()

    collector.record_call("test_endpoint", 12.5, success=True)
    collector.record_call("test_endpoint", 25.0, success=True)
    collector.record_call("test_endpoint", 50.0, success=False)

    metrics = collector.get_metrics()
    assert metrics["totalRequests"] == 3
    assert metrics["successfulRequests"] == 2
    assert metrics["failedRequests"] == 1
    ep_data = metrics["endpoints"]["test_endpoint"]
    assert ep_data["totalCalls"] == 3
    assert ep_data["successfulCalls"] == 2
    assert ep_data["failedCalls"] == 1
    assert ep_data["minLatencyMs"] == 12.5
    assert ep_data["maxLatencyMs"] == 50.0
    assert ep_data["avgLatencyMs"] == pytest.approx(29.17, rel=1e-2)


def test_measure_telemetry_decorator_injects_ping():
    """Verify @measure_telemetry decorator measures duration and injects pingMs into return dict."""
    collector = TelemetryCollector()

    @measure_telemetry(metric_name="timed_op", inject_ping=True, collector=collector)
    def sample_api_call():
        time.sleep(0.02)
        return {"success": True, "data": 123}

    res = sample_api_call()
    assert res["success"] is True
    assert "pingMs" in res
    assert res["pingMs"] >= 15.0  # at least 15-20ms

    metrics = collector.get_metrics()
    assert metrics["endpoints"]["timed_op"]["totalCalls"] == 1


# ==============================================================================
# 4. Boundary & Coordinate Invariant Gate Tests
# ==============================================================================

def test_coordinate_space_invariant_fail_closed():
    """Verify assert_verified_coordinate_space rejects unknown or unsupported coordinate spaces."""
    # Unknown coordinate space fails closed
    with pytest.raises(ValueError, match="Cannot transform coordinates for unverified coordinateSpace"):
        assert_verified_coordinate_space("unknown")

    with pytest.raises(ValueError, match="Unsupported coordinate space"):
        assert_verified_coordinate_space("custom_unsupported_canvas")

    # Valid spaces pass without exception
    for valid_space in ("letterbox_640", "letterbox", "original", "normalized", "stretch_640"):
        assert_verified_coordinate_space(valid_space)


def test_box_boundary_invariants_fail_closed():
    """Verify assert_box_boundary_invariants detects coordinate overflow beyond original dimensions."""
    orig_w, orig_h = 1920, 1080

    valid_box = BoundingBox(10.0, 10.0, 500.0, 400.0)
    assert_box_boundary_invariants(valid_box, orig_w, orig_h)

    # Negative coordinates
    neg_box = BoundingBox(-5.0, 10.0, 200.0, 200.0)
    with pytest.raises(ValueError, match="Box coordinates exceed frame boundaries"):
        assert_box_boundary_invariants(neg_box, orig_w, orig_h)

    # Coordinate exceeding frame width
    overflow_box = BoundingBox(100.0, 100.0, 2000.0, 500.0)
    with pytest.raises(ValueError, match="Box coordinates exceed frame boundaries"):
        assert_box_boundary_invariants(overflow_box, orig_w, orig_h)


def test_assert_box_boundary_invariants_rejects_nan_inf_and_degenerate():
    """Verify assert_box_boundary_invariants rejects NaN, Inf, zero-area, and inverted boxes."""
    orig_w, orig_h = 1920, 1080

    # NaN coordinates fail closed
    with pytest.raises(ValueError, match="not a finite number"):
        assert_box_boundary_invariants(BoundingBox(float("nan"), 10.0, 200.0, 200.0), orig_w, orig_h)

    # Inf coordinates fail closed
    with pytest.raises(ValueError, match="not a finite number"):
        assert_box_boundary_invariants(BoundingBox(10.0, float("inf"), 200.0, 200.0), orig_w, orig_h)

    # Inverted box (x2 <= x1) fails closed
    with pytest.raises(ValueError, match="degenerate or inverted"):
        assert_box_boundary_invariants(BoundingBox(200.0, 10.0, 100.0, 200.0), orig_w, orig_h)

    # Zero-area box fails closed
    with pytest.raises(ValueError, match="degenerate or inverted"):
        assert_box_boundary_invariants(BoundingBox(100.0, 100.0, 100.0, 200.0), orig_w, orig_h)


def test_enforce_yolo_invariants_rejects_bool_and_float_class_id():
    """Verify @enforce_yolo_invariants rejects non-integer class IDs such as booleans or floats."""
    from src.coordinate_adapter import to_yolo_detection

    valid_box = BoundingBox(10.0, 10.0, 200.0, 200.0)

    # Boolean class_id (True/False) must be rejected
    with pytest.raises(ValueError, match="YOLO class_id must be non-negative integer"):
        to_yolo_detection(valid_box, 1920, 1080, class_id=True)

    # Float class_id must be rejected
    with pytest.raises(ValueError, match="YOLO class_id must be non-negative integer"):
        to_yolo_detection(valid_box, 1920, 1080, class_id=1.5)

    # Negative class_id must be rejected
    with pytest.raises(ValueError, match="YOLO class_id must be non-negative integer"):
        to_yolo_detection(valid_box, 1920, 1080, class_id=-1)


# ==============================================================================
# 5. Audit Trails & Manifest Sanitization Tests
# ==============================================================================

def test_sanitize_record_for_manifest_strips_blobs():
    """Verify sanitize_record_for_manifest strips heavy base64 and blob transport fields."""
    raw_record = {
        "recordId": "rec_001",
        "sessionId": "SES_TEST",
        "imageFile": "images/test.jpg",
        "reviewStatus": "approved",
        "origBase64": "data:image/jpeg;base64,HUGE_PAYLOAD_STRING...",
        "prevBase64": "data:image/jpeg;base64,PREVIEW_PAYLOAD...",
        "origBlob": b"raw_bytes",
        "customMeta": "keep_this",
    }
    clean = sanitize_record_for_manifest(raw_record)
    assert "origBase64" not in clean
    assert "prevBase64" not in clean
    assert "origBlob" not in clean
    assert clean["recordId"] == "rec_001"
    assert clean["customMeta"] == "keep_this"


def test_audit_trail_logger_records_events():
    """Verify AuditTrailLogger creates non-repudiation audit log entries with UTC timestamps."""
    logger = AuditTrailLogger()
    entry = logger.log_event("REVIEW_APPROVED", {"recordId": "rec_123", "operator": "inspector_a"})

    assert entry["eventType"] == "REVIEW_APPROVED"
    assert "timestampUtc" in entry
    assert entry["details"]["recordId"] == "rec_123"

    events = logger.get_recent_events()
    assert len(events) == 1
    assert events[0] == entry


def test_audit_manifest_export_records_failure_event():
    """Verify @audit_manifest_export logs a failure event when export validation fails."""
    logger = AuditTrailLogger()

    @audit_manifest_export(audit_logger=logger)
    def failing_export(records):
        raise ValueError("No approved records found in dataset")

    with pytest.raises(ValueError, match="No approved records"):
        failing_export([])

    events = logger.get_recent_events()
    assert len(events) == 1
    assert events[0]["eventType"] == "YOLO_DATASET_EXPORT_FAILED"
    assert "No approved records" in events[0]["details"]["errorMessage"]


# ==============================================================================
# 6. Frontend JavaScript AOP Tests (Node.js runtime)
# ==============================================================================

def test_frontend_aspects_in_node_runtime():
    """Direct execution test of static/aspects.js in Node.js runtime verifying all 5 frontend aspects."""
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js not installed on system")

    js_file = Path(__file__).resolve().parent.parent / "static" / "aspects.js"
    assert js_file.exists()

    node_script = f"""
    const fs = require('fs');
    const code = fs.readFileSync({repr(str(js_file))}, 'utf8');
    const ctx = {{}};
    eval('(function(window){{ ' + code + ' }})(ctx)');

    const aop = ctx.SchaleAOP;
    if (!aop) throw new Error('SchaleAOP namespace missing');

    // 1. Redactor Aspect
    aop.SecretRedactor.registerSecret('dummy_secret_key_123');
    const rawMsg = "Error with key dummy_secret_key_123 and Bearer token_xyz";
    const redacted = aop.SecretRedactor.redact(rawMsg);
    if (redacted.includes('dummy_secret_key_123')) throw new Error('Redaction failed for key');
    if (redacted.includes('token_xyz')) throw new Error('Redaction failed for bearer');

    // 2. Circuit Breaker Aspect (Client error isolation & canary testing)
    const cb = new aop.CircuitBreaker({{ failureThreshold: 2, recoveryTimeoutMs: 50 }});
    if (cb.state !== 'CLOSED' || !cb.canExecute()) throw new Error('Initial CB state not CLOSED');

    // 400 Client error must NOT increment failures or trip CB
    cb.recordFailure(400, 'Bad Request');
    cb.recordFailure(400, 'Bad Request 2');
    if (cb.state !== 'CLOSED' || cb.consecutiveFailures !== 0) throw new Error('400 client error incorrectly tripped CB');

    let tripEventFired = false;
    cb.addListener((evt, data) => {{
        if (evt === 'trip') tripEventFired = true;
    }});

    // 401 must trip immediately (Fail-Closed)
    cb.recordFailure(401, 'Unauthorized');
    if (cb.state !== 'OPEN') throw new Error('401 did not trip CB to OPEN');
    if (!tripEventFired) throw new Error('Trip event not fired');
    if (cb.canExecute()) throw new Error('CB should block when OPEN');

    // 3. Telemetry Aspect
    const tel = new aop.TelemetryAspect();
    tel.record(25, true);
    tel.record(45, true);
    tel.record(60, false);
    const stats = tel.getStats();
    if (stats.total !== 3 || stats.success !== 2 || stats.failed !== 1) throw new Error('Telemetry stats incorrect');

    // 4. Review Gate Aspect (Edge cases: NaN, sub-pixel degenerate box, float classId)
    const validRec = {{
        coordinateSpace: 'letterbox_640',
        originalWidth: 1920,
        originalHeight: 1080,
        reviewedAnnotations: [
            {{ classId: 0, box: {{ x1: 100, y1: 100, x2: 400, y2: 400 }} }}
        ]
    }};
    const validRes = aop.ReviewGateAspect.validateRecordForApproval(validRec);
    if (!validRes.valid) throw new Error('Valid record rejected by gate: ' + validRes.error);

    const unknownRec = {{ coordinateSpace: 'unknown', reviewedAnnotations: [] }};
    const unknownRes = aop.ReviewGateAspect.validateRecordForApproval(unknownRec);
    if (unknownRes.valid) throw new Error('Unknown coordinateSpace should have been blocked');

    const invertedBoxRec = {{
        coordinateSpace: 'letterbox_640',
        originalWidth: 1920,
        originalHeight: 1080,
        reviewedAnnotations: [
            {{ classId: 0, box: {{ x1: 500, y1: 100, x2: 200, y2: 400 }} }}
        ]
    }};
    const invertedRes = aop.ReviewGateAspect.validateRecordForApproval(invertedBoxRec);
    if (invertedRes.valid) throw new Error('Inverted box should have been blocked');

    // Edge case 1: NaN coordinate
    const nanRec = {{
        coordinateSpace: 'letterbox_640',
        originalWidth: 1920,
        originalHeight: 1080,
        reviewedAnnotations: [
            {{ classId: 0, box: {{ x1: NaN, y1: 100, x2: 400, y2: 400 }} }}
        ]
    }};
    if (aop.ReviewGateAspect.validateRecordForApproval(nanRec).valid) throw new Error('NaN box was not blocked');

    // Edge case 2: Sub-pixel degenerate box
    const subpixelRec = {{
        coordinateSpace: 'letterbox_640',
        originalWidth: 1920,
        originalHeight: 1080,
        reviewedAnnotations: [
            {{ classId: 0, box: {{ x1: 100, y1: 100, x2: 100 + 1e-12, y2: 400 }} }}
        ]
    }};
    if (aop.ReviewGateAspect.validateRecordForApproval(subpixelRec).valid) throw new Error('Subpixel degenerate box was not blocked');

    // Edge case 3: Float classId
    const floatClassRec = {{
        coordinateSpace: 'letterbox_640',
        originalWidth: 1920,
        originalHeight: 1080,
        reviewedAnnotations: [
            {{ classId: 1.5, box: {{ x1: 100, y1: 100, x2: 400, y2: 400 }} }}
        ]
    }};
    if (aop.ReviewGateAspect.validateRecordForApproval(floatClassRec).valid) throw new Error('Float classId was not blocked');

    // 5. Audit Trail Aspect
    const auditRec = {{ reviewStatus: 'pending' }};
    aop.AuditTrailAspect.recordDecision(auditRec, 'approved', 'operator_jane');
    if (auditRec.reviewStatus !== 'approved') throw new Error('Status not updated');
    if (!auditRec.auditTrail || auditRec.auditTrail.length !== 1) throw new Error('Audit trail entry missing');

    aop.AuditTrailAspect.recordBoxEdit(auditRec, 'ADD_BOX', {{ index: 0, box: {{ x1: 10, y1: 10, x2: 50, y2: 50 }} }});
    if (auditRec.auditTrail.length !== 2 || auditRec.auditTrail[1].action !== 'ADD_BOX') {{
        throw new Error('Audit trail box edit action missing');
    }}

    console.log('JS_AOP_ALL_TESTS_PASS');
    """

    res = subprocess.run([node_bin, "-e", node_script], capture_output=True, text=True)
    assert res.returncode == 0, f"Node script failed: {res.stderr}"
    assert "JS_AOP_ALL_TESTS_PASS" in res.stdout
