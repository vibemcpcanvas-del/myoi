"""src/aspects/telemetry.py
Aspect 3: Latency, Health, and Operational Telemetry.
Decouples operational metrics collection, high-resolution latency timing, and health accounting.
"""

from collections import deque
from dataclasses import dataclass, field
import functools
import time
from typing import Any, Callable, Deque, Dict, List, Optional
from fastapi import HTTPException

from src.aspects.base import is_coroutine_callable


@dataclass
class EndpointMetrics:
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    last_latency_ms: float = 0.0
    latencies: Deque[float] = field(default_factory=lambda: deque(maxlen=200))

    def record(self, duration_ms: float, success: bool):
        self.total_calls += 1
        if success:
            self.successful_calls += 1
        else:
            self.failed_calls += 1
        self.last_latency_ms = duration_ms
        self.latencies.append(duration_ms)

    def summary(self) -> Dict[str, Any]:
        l_list = list(self.latencies)
        avg_lat = round(sum(l_list) / len(l_list), 2) if l_list else 0.0
        min_lat = round(min(l_list), 2) if l_list else 0.0
        max_lat = round(max(l_list), 2) if l_list else 0.0
        p95_lat = round(sorted(l_list)[int(len(l_list) * 0.95)], 2) if len(l_list) >= 20 else max_lat

        return {
            "totalCalls": self.total_calls,
            "successfulCalls": self.successful_calls,
            "failedCalls": self.failed_calls,
            "lastLatencyMs": round(self.last_latency_ms, 2),
            "avgLatencyMs": avg_lat,
            "minLatencyMs": min_lat,
            "maxLatencyMs": max_lat,
            "p95LatencyMs": p95_lat,
        }


class TelemetryCollector:
    """In-memory telemetry collector for real-time health and operational metrics."""

    def __init__(self):
        self.started_at: float = time.time()
        self.endpoints: Dict[str, EndpointMetrics] = {}

    def get_or_create(self, name: str) -> EndpointMetrics:
        if name not in self.endpoints:
            self.endpoints[name] = EndpointMetrics()
        return self.endpoints[name]

    def record_call(self, endpoint_name: str, duration_ms: float, success: bool = True):
        em = self.get_or_create(endpoint_name)
        em.record(duration_ms, success)

    def get_metrics(self) -> Dict[str, Any]:
        now = time.time()
        uptime_sec = round(now - self.started_at, 1)

        total_all = sum(em.total_calls for em in self.endpoints.values())
        success_all = sum(em.successful_calls for em in self.endpoints.values())
        failed_all = sum(em.failed_calls for em in self.endpoints.values())

        return {
            "uptimeSeconds": uptime_sec,
            "totalRequests": total_all,
            "successfulRequests": success_all,
            "failedRequests": failed_all,
            "endpoints": {k: v.summary() for k, v in self.endpoints.items()},
        }


# Global telemetry singleton
default_telemetry_collector = TelemetryCollector()


def measure_telemetry(metric_name: str = "default", inject_ping: bool = False, collector: Optional[TelemetryCollector] = None) -> Callable:
    """AOP Decorator: Measures execution duration, updates telemetry metrics,
    and optionally injects `pingMs` into return dictionary.
    """
    tc = collector or default_telemetry_collector

    def decorator(func: Callable) -> Callable:
        if is_coroutine_callable(func):

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                start = time.perf_counter()
                success = False
                try:
                    result = await func(*args, **kwargs)
                    success = True
                    elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
                    tc.record_call(metric_name, elapsed_ms, success=True)
                    if inject_ping and isinstance(result, dict) and "pingMs" not in result:
                        result["pingMs"] = elapsed_ms
                    return result
                except Exception as ex:
                    elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
                    tc.record_call(metric_name, elapsed_ms, success=False)
                    raise ex

            return async_wrapper
        else:

            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                start = time.perf_counter()
                try:
                    result = func(*args, **kwargs)
                    elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
                    tc.record_call(metric_name, elapsed_ms, success=True)
                    if inject_ping and isinstance(result, dict) and "pingMs" not in result:
                        result["pingMs"] = elapsed_ms
                    return result
                except Exception as ex:
                    elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
                    tc.record_call(metric_name, elapsed_ms, success=False)
                    raise ex

            return sync_wrapper

    return decorator
