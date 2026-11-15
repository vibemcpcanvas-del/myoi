"""src/aspects/__init__.py
Aspect-Oriented Programming (AOP) module for SCHALE Tactical Tracking.
Decouples 5 core cross-cutting concerns:
1. Secret Protection & Upstream Error Redaction
2. Fail-Closed Circuit Breaker & Retry / Rate-Limiting Policy
3. Latency, Health, and Operational Telemetry
4. Boundary & Coordinate Invariant Validation
5. Audit Trails & Manifest Sanitization
"""

from src.aspects.base import JoinPoint, is_coroutine_callable
from src.aspects.redaction import (
    SecretRedactor,
    get_global_redactor,
    redact_secrets,
    SecretRedactionMiddleware,
)
from src.aspects.circuit_breaker import (
    CircuitState,
    CircuitBreaker,
    CircuitBreakerOpenException,
    default_inference_circuit_breaker,
    circuit_breaker,
)
from src.aspects.telemetry import (
    EndpointMetrics,
    TelemetryCollector,
    default_telemetry_collector,
    measure_telemetry,
)
from src.aspects.invariants import (
    VALID_COORDINATE_SPACES,
    assert_verified_coordinate_space,
    assert_box_boundary_invariants,
    enforce_coordinate_invariants,
    enforce_yolo_invariants,
)
from src.aspects.audit import (
    sanitize_record_for_manifest,
    compute_sha256,
    AuditTrailLogger,
    default_audit_logger,
    audit_manifest_export,
)

__all__ = [
    "JoinPoint",
    "is_coroutine_callable",
    "SecretRedactor",
    "get_global_redactor",
    "redact_secrets",
    "SecretRedactionMiddleware",
    "CircuitState",
    "CircuitBreaker",
    "CircuitBreakerOpenException",
    "default_inference_circuit_breaker",
    "circuit_breaker",
    "EndpointMetrics",
    "TelemetryCollector",
    "default_telemetry_collector",
    "measure_telemetry",
    "VALID_COORDINATE_SPACES",
    "assert_verified_coordinate_space",
    "assert_box_boundary_invariants",
    "enforce_coordinate_invariants",
    "enforce_yolo_invariants",
    "sanitize_record_for_manifest",
    "compute_sha256",
    "AuditTrailLogger",
    "default_audit_logger",
    "audit_manifest_export",
]
