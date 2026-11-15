/**
 * static/aspects.js
 * Frontend Aspect-Oriented Programming (AOP) Engine for SCHALE Tactical Tracking.
 * Decouples 5 core cross-cutting concerns:
 *   1. Secret Redactor Aspect (sanitizes error details & strings)
 *   2. Fail-Closed Circuit Breaker Aspect (isolates loop on 401/503 errors)
 *   3. Operational Telemetry Aspect (tracks latency, ping, and request counters)
 *   4. Review Gate & Coordinate Invariant Aspect (enforces coordinate and box invariants)
 *   5. Audit Trail Aspect (tracks review decision history and timestamps)
 */

(function (window) {
    'use strict';

    // --- Concern 1: Secret Redactor Aspect ---
    const SecretRedactor = {
        registeredSecrets: new Set(),

        registerSecret(secret) {
            if (secret && typeof secret === 'string' && secret.trim()) {
                this.registeredSecrets.add(secret.trim());
            }
        },

        PATTERNS: [
            /Bearer\s+[A-Za-z0-9_\-\.]+/gi,
            /(api[_-]?key|token|secret|key)\s*[:=\s]\s*['"]?[A-Za-z0-9_\-\.]+['"]?/gi,
            /ul_[A-Za-z0-9]+/gi,
        ],

        redact(text) {
            if (!text || typeof text !== 'string') return text;
            let result = text;

            // Registered secrets exact replacement
            for (const sec of this.registeredSecrets) {
                if (sec && result.includes(sec)) {
                    result = result.split(sec).join('[REDACTED]');
                }
            }

            // Pattern-based redaction
            for (const pat of this.PATTERNS) {
                result = result.replace(pat, '[REDACTED]');
            }

            return result;
        },

        redactError(err) {
            if (!err) return err;
            if (typeof err === 'string') return this.redact(err);
            if (err.message) {
                err.message = this.redact(err.message);
            }
            return err;
        }
    };

    // --- Concern 2: Fail-Closed Circuit Breaker Aspect ---
    class CircuitBreaker {
        constructor(options = {}) {
            this.name = options.name || 'frontend_api_circuit';
            this.failureThreshold = options.failureThreshold || 3;
            this.recoveryTimeoutMs = options.recoveryTimeoutMs || 15000;
            this.halfOpenMaxCanary = options.halfOpenMaxCanary || 1;
            this.state = 'CLOSED'; // 'CLOSED' | 'OPEN' | 'HALF_OPEN'
            this.consecutiveFailures = 0;
            this.totalTrips = 0;
            this.lastFailureTime = 0;
            this.halfOpenInFlight = 0;
            this.listeners = [];
        }

        addListener(fn) {
            if (typeof fn === 'function') this.listeners.push(fn);
        }

        notifyListeners(event, data) {
            this.listeners.forEach(fn => {
                try { fn(event, data); } catch (e) { console.error('CB listener error:', e); }
            });
        }

        canExecute() {
            const now = Date.now();
            if (this.state === 'CLOSED') return true;
            if (this.state === 'OPEN') {
                if (now - this.lastFailureTime >= this.recoveryTimeoutMs) {
                    this.state = 'HALF_OPEN';
                    this.halfOpenInFlight = 1;
                    this.notifyListeners('state_change', { state: this.state });
                    return true;
                }
                return false;
            }
            if (this.state === 'HALF_OPEN') {
                if (this.halfOpenInFlight < this.halfOpenMaxCanary) {
                    this.halfOpenInFlight++;
                    return true;
                }
                return false;
            }
            return false;
        }

        recordSuccess() {
            this.consecutiveFailures = 0;
            this.halfOpenInFlight = 0;
            if (this.state !== 'CLOSED') {
                this.state = 'CLOSED';
                this.notifyListeners('state_change', { state: this.state });
            }
        }

        recordFailure(statusCode, message) {
            // Client errors (400, 404, 413, 422, etc.) must not trip upstream circuit
            if (statusCode && statusCode >= 400 && statusCode < 500 && statusCode !== 401 && statusCode !== 403) {
                if (this.state === 'HALF_OPEN' && this.halfOpenInFlight > 0) {
                    this.halfOpenInFlight--;
                }
                return false;
            }

            this.lastFailureTime = Date.now();
            this.consecutiveFailures++;
            this.halfOpenInFlight = 0;

            // Fail-closed invariant: 401 (Auth) or 503 immediately trips circuit
            const isFatal = (statusCode === 401 || statusCode === 403 || statusCode === 503);

            if (this.state === 'HALF_OPEN' || isFatal || this.consecutiveFailures >= this.failureThreshold) {
                if (this.state !== 'OPEN') {
                    this.totalTrips++;
                }
                this.state = 'OPEN';
                this.notifyListeners('trip', {
                    statusCode,
                    message: SecretRedactor.redact(message),
                    consecutiveFailures: this.consecutiveFailures,
                    totalTrips: this.totalTrips,
                });
                this.notifyListeners('state_change', { state: this.state });
            }
            return true;
        }

        reset() {
            this.state = 'CLOSED';
            this.consecutiveFailures = 0;
            this.halfOpenInFlight = 0;
            this.notifyListeners('state_change', { state: this.state });
        }

        async wrapFetch(url, options = {}) {
            if (!this.canExecute()) {
                const remainingSec = Math.max(0, ((this.recoveryTimeoutMs - (Date.now() - this.lastFailureTime)) / 1000)).toFixed(1);
                const msg = `Circuit Breaker '${this.name}' is OPEN. Requests blocked for ${remainingSec}s.`;
                throw new Error(msg);
            }

            try {
                const response = await fetch(url, options);
                if (!response.ok) {
                    const errData = await response.clone().json().catch(() => ({}));
                    const errMsg = errData.detail?.message || `HTTP ${response.status}`;
                    this.recordFailure(response.status, errMsg);
                } else {
                    this.recordSuccess();
                }
                return response;
            } catch (networkErr) {
                this.recordFailure(502, networkErr.message);
                throw networkErr;
            }
        }
    }

    // --- Concern 3: Operational Telemetry Aspect ---
    class TelemetryAspect {
        constructor() {
            this.totalRequests = 0;
            this.successfulRequests = 0;
            this.failedRequests = 0;
            this.latencies = [];
            this.maxHistory = 100;
            this.subscribers = [];
        }

        subscribe(fn) {
            if (typeof fn === 'function') this.subscribers.push(fn);
        }

        record(durationMs, success = true) {
            this.totalRequests++;
            if (success) this.successfulRequests++;
            else this.failedRequests++;

            this.latencies.push(durationMs);
            if (this.latencies.length > this.maxHistory) {
                this.latencies.shift();
            }

            const stats = this.getStats();
            this.subscribers.forEach(sub => {
                try { sub(stats); } catch (e) { /* ignore */ }
            });
        }

        getStats() {
            const count = this.latencies.length;
            const avg = count ? Math.round(this.latencies.reduce((a, b) => a + b, 0) / count) : 0;
            const last = count ? this.latencies[count - 1] : 0;
            return {
                total: this.totalRequests,
                success: this.successfulRequests,
                failed: this.failedRequests,
                lastPingMs: last,
                avgPingMs: avg,
            };
        }
    }

    // --- Concern 4: Boundary & Coordinate Invariant Gate Aspect ---
    const ReviewGateAspect = {
        VALID_COORDINATE_SPACES: ['letterbox_640', 'letterbox', 'original', 'normalized', 'stretch_640'],

        validateRecordForApproval(record) {
            if (!record) {
                return { valid: false, error: 'Record is null or undefined.' };
            }

            // Invariant 1: Unknown coordinate space rejection
            const coordSpace = record.coordinateSpace || 'letterbox_640';
            if (coordSpace === 'unknown' || record.coordinateTransformStatus === 'unverified') {
                return {
                    valid: false,
                    error: '좌표계가 검증되지 않았거나(unknown) 변환에 실패한 프레임은 승인할 수 없습니다.'
                };
            }

            if (!this.VALID_COORDINATE_SPACES.includes(coordSpace)) {
                return {
                    valid: false,
                    error: `지원되지 않는 좌표계입니다: '${coordSpace}'`
                };
            }

            // Invariant 2: Bounding box geometric and boundary constraints
            const origW = record.originalWidth || 1920;
            const origH = record.originalHeight || 1080;
            const boxes = record.reviewedAnnotations || record.detections || [];

            for (let i = 0; i < boxes.length; i++) {
                const ann = boxes[i];
                const b = ann.box;
                if (!b) {
                    return { valid: false, error: `박스 [${i + 1}]의 좌표 데이터가 누락되었습니다.` };
                }

                // Strictly verify coordinates are finite numbers (rejects NaN, Inf, undefined, null)
                if (![b.x1, b.y1, b.x2, b.y2].every(v => typeof v === 'number' && Number.isFinite(v))) {
                    return {
                        valid: false,
                        error: `박스 [${i + 1}]의 좌표값이 유효한 유한 숫자가 아닙니다: [${b.x1}, ${b.y1}, ${b.x2}, ${b.y2}]`
                    };
                }

                const eps = 1e-4;
                if ((b.x2 - b.x1) <= eps || (b.y2 - b.y1) <= eps) {
                    return {
                        valid: false,
                        error: `박스 [${i + 1}]의 가로/세로 크기가 비정상(0 이하 또는 퇴화 박스)입니다: [${b.x1}, ${b.y1}, ${b.x2}, ${b.y2}]`
                    };
                }

                if (b.x1 < -eps || b.y1 < -eps || b.x2 > (origW + eps) || b.y2 > (origH + eps)) {
                    return {
                        valid: false,
                        error: `박스 [${i + 1}]의 좌표가 원본 해상도 (${origW}x${origH}) 경계를 초과했습니다: [x1:${b.x1}, y1:${b.y1}, x2:${b.x2}, y2:${b.y2}]`
                    };
                }

                if (typeof ann.classId !== 'number' || !Number.isInteger(ann.classId) || ann.classId < 0) {
                    return {
                        valid: false,
                        error: `박스 [${i + 1}]의 클래스 ID(${ann.classId})는 0 이상의 정수여야 합니다.`
                    };
                }
            }

            return { valid: true };
        }
    };

    // --- Concern 5: Audit Trail Aspect ---
    const AuditTrailAspect = {
        recordDecision(record, newStatus, operator = 'human_reviewer') {
            if (!record) return;
            const now = new Date().toISOString();
            const prevStatus = record.reviewStatus || 'pending';

            record.reviewStatus = newStatus;
            record.reviewedAtUtc = now;

            if (!record.auditTrail || !Array.isArray(record.auditTrail)) {
                record.auditTrail = [];
            }

            record.auditTrail.push({
                action: 'SET_DECISION',
                previousStatus: prevStatus,
                newStatus: newStatus,
                timestampUtc: now,
                operator: operator,
            });
        },

        recordBoxEdit(record, actionType, details) {
            if (!record) return;
            if (!record.auditTrail || !Array.isArray(record.auditTrail)) {
                record.auditTrail = [];
            }
            record.auditTrail.push({
                action: actionType,
                details: details,
                timestampUtc: new Date().toISOString(),
            });
        }
    };

    // Assemble SchaleAOP namespace
    const SchaleAOP = {
        SecretRedactor,
        CircuitBreaker,
        defaultCircuitBreaker: new CircuitBreaker({ name: 'schale_main_breaker' }),
        TelemetryAspect,
        defaultTelemetry: new TelemetryAspect(),
        ReviewGateAspect,
        AuditTrailAspect,
    };

    window.SchaleAOP = SchaleAOP;
})(typeof window !== 'undefined' ? window : this);
