"""src/aspects/audit.py
Aspect 5: Audit Trails & Manifest Sanitization.
Decouples export manifest payload sanitization, cryptographic audit seals, and review audit logging.
"""

from datetime import datetime, timezone
import functools
import hashlib
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


EXCLUDED_TRANSPORT_FIELDS: Set[str] = {
    "origBase64",
    "prevBase64",
    "origBlob",
    "prevBlob",
}


def sanitize_record_for_manifest(record: Dict[str, Any]) -> Dict[str, Any]:
    """Pure transformation aspect: Strips heavy ephemeral base64/blob transport payloads
    from records before serialization into manifest.json and manifest.jsonl.
    """
    return {k: v for k, v in record.items() if k not in EXCLUDED_TRANSPORT_FIELDS}


def compute_sha256(data: bytes) -> str:
    """Cryptographic hash calculation for non-repudiation and duplicate detection."""
    return hashlib.sha256(data).hexdigest()


class AuditTrailLogger:
    """Maintains an in-memory audit log of review actions and export operations."""

    def __init__(self):
        self.logs: List[Dict[str, Any]] = []

    def log_event(self, event_type: str, details: Dict[str, Any]) -> Dict[str, Any]:
        entry = {
            "timestampUtc": datetime.now(timezone.utc).isoformat(),
            "eventType": event_type,
            "details": details,
        }
        self.logs.append(entry)
        return entry

    def get_recent_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self.logs[-limit:]


# Global audit logger instance
default_audit_logger = AuditTrailLogger()


def audit_manifest_export(audit_logger: Optional[AuditTrailLogger] = None) -> Callable:
    """AOP Decorator: Intercepts dataset generation to record audit events,
    enforcing audit trail non-repudiation.
    """
    al = audit_logger or default_audit_logger

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(manifest_records: List[Dict[str, Any]], *args, **kwargs):
            total_records = len(manifest_records)
            try:
                zip_bytes, report = func(manifest_records, *args, **kwargs)
                # Record successful audit event
                al.log_event(
                    event_type="YOLO_DATASET_EXPORTED",
                    details={
                        "totalRecords": total_records,
                        "approvedCount": report.get("approvedCount", 0),
                        "trainCount": report.get("trainCount", 0),
                        "valCount": report.get("valCount", 0),
                        "zipSizeBytes": len(zip_bytes),
                        "exportedAtUtc": datetime.now(timezone.utc).isoformat(),
                    },
                )
                return zip_bytes, report
            except Exception as ex:
                al.log_event(
                    event_type="YOLO_DATASET_EXPORT_FAILED",
                    details={
                        "totalRecords": total_records,
                        "errorType": type(ex).__name__,
                        "errorMessage": str(ex),
                        "attemptedAtUtc": datetime.now(timezone.utc).isoformat(),
                    },
                )
                raise ex

        return wrapper

    return decorator
