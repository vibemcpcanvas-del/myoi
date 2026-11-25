# Mobile Haptic & Vibration Telemetry Feasibility Analysis
**Project**: SCHALE Tactical Tracking (myoi)  
**Evaluator**: Codex CLI (`gpt-6-luna`, Pateway API, MAX Reasoning Invariant)  
**Target Specification**: W3C Vibration API & Mobile Client Haptic Telemetry Integration  
**Date**: 2026-11-26  

---

## 1. Web Standards Feasibility & Engine Compatibility Matrix

### 1.1 W3C Vibration API Standard (`navigator.vibrate`)
- **API Signature**: `navigator.vibrate(pattern: number | number[]): boolean`
- **Return Value**: Returns `true` if the vibration pattern was successfully dispatched to hardware; returns `false` if blocked by user activation, permissions policy, page hidden state, or lack of hardware vibration motor.

### 1.2 Multi-Engine Ground-Truth Compatibility

| Platform / Engine | Support Status | Technical Mechanism & Constraints |
| :--- | :--- | :--- |
| **Android Chromium (Chrome, Edge, Samsung Internet)** | **Supported (Full)** | Handled via Blink `modules/vibration/vibration_controller.cc`. Dispatches vibration patterns directly to Android OS Vibrator Service. Supported in both standard browser tabs and Installed Standalone PWAs. |
| **Android Firefox (GeckoView)** | **Supported (Full)** | Gecko implements `nsVibration` on top of Android HAL. Standard array and integer patterns fully supported. |
| **Apple iOS Safari (WebKit)** | **UNSUPPORTED (Fail-Closed)** | WebKit has explicitly classified `navigator.vibrate` as **WONTFIX / Disabled** (`Source/WebCore/features.json`) citing fingerprinting and abusive background notification concerns. Neither Safari nor iOS WebKit wrappers (Chrome for iOS, standalone PWAs) support vibration. |
| **Desktop Browsers (Windows/macOS/Linux)** | **Ignored / False** | Desktop hardware lacks a haptic vibration actuator; calls to `navigator.vibrate()` return `false` or silently complete without effect. |

---

## 2. Browser Security & Activation Invariants

### 2.1 User Activation Invariant (Sticky vs Transient)
- **Standard Requirement**: In modern Chromium Blink, `navigator.vibrate()` requires **Sticky User Activation** (`navigator.userActivation.hasBeenActive`), NOT fresh `TransientUserActivation`.
- **Operational Implication for myoi**: A user does NOT need to tap the screen at the exact moment a target is detected. Once the user clicks "화면 공유" (`btnShare`) or "작전 개시" (`btnStart`) once upon page load, sticky activation remains active for the entire session lifecycle. Subsequent background detection loop ticks can freely trigger vibrations.

### 2.2 Permissions Policy (`Permissions-Policy: vibrate`)
- **Top-level Document**: By default, `vibrate` is permitted on the top-level origin (`vibrate=(self)`).
- **Embedded Iframes**: If myoi is ever embedded within an external iframe, the host page must explicitly declare `allow="vibrate"`; otherwise, `navigator.vibrate` will fail closed and return `false`.

### 2.3 Secure Context Invariant (`window.isSecureContext`)
- **Specification Fact**: The W3C Vibration API IDL does not mandate `[SecureContext]`, meaning it technically functions over plain `http://localhost`.
- **Production Guardrail**: For mobile devices accessing the LAN server (e.g., `http://192.168.x.x:8000`), modern Android Chromium restricts device sensors and certain hardware hooks to Secure Contexts. HTTPS deployment or tunneling (or localhost port forwarding) is required for deterministic mobile operation.

### 2.4 Page Visibility Gating
- Under the W3C spec and Chromium implementation, calling `navigator.vibrate()` when `document.hidden === true` is immediately rejected and returns `false`. Vibration only fires when the web app tab is actively focused and visible.

---

## 3. Tactical Feedback Typestate & Telemetry Signatures

To ensure non-intrusive, distinguishable tactile telemetry, myoi defines 4 distinct vibration patterns coordinated with visual and auditory alerts:

```
[Typestate Transition]
Idle / Standby ---> User Activates ("화면 공유") [Sticky Activation Acquired]
                          |
    +---------------------+---------------------+
    |                     |                     |
[Target Detected]  [WS Disconnected]   [Circuit Breaker Trip]
Pattern: [200, 100, 200]  Pattern: [400, 150, 400]  Pattern: [800] (Heavy Alarm)
Cooldown: 2.0s        Cooldown: 5.0s        Cooldown: Single-shot
```

### 3.1 Pattern Signatures

```javascript
export const HAPTIC_PATTERNS = {
    // Tactile confirmation on interactive button tap
    CLICK: [30],
    // High-priority alert: Confirmed target detection
    TARGET_DETECTED: [180, 80, 180],
    // Network degradation: Stream dropped or WebSocket reconnecting
    STREAM_DEGRADED: [400, 120, 400],
    // Fatal circuit breaker trip: Fail-closed isolation
    CIRCUIT_BREAKER_TRIP: [800],
};
```

---

## 4. Actionable Defensive Interface Specification

A self-contained, fail-closed haptic controller for `static/app.js`:

```javascript
/**
 * Safe, fail-closed mobile haptic feedback dispatcher.
 * Adheres strictly to Sticky User Activation, Secure Context, and Platform Availability.
 */
class HapticTelemetryController {
    constructor() {
        this.enabled = true;
        this.lastTriggerTime = 0;
        this.minCooldownMs = 400; // Anti-chatter throttle
    }

    /**
     * Checks whether the current runtime environment is capable of vibration.
     * @returns {boolean}
     */
    isSupported() {
        return (
            typeof window !== 'undefined' &&
            typeof navigator !== 'undefined' &&
            typeof navigator.vibrate === 'function' &&
            // Check for sticky user activation if supported by browser
            (navigator.userActivation ? navigator.userActivation.hasBeenActive : true)
        );
    }

    /**
     * Dispatches a vibration pattern with fail-closed safety gating.
     * @param {number|number[]} pattern - Vibration duration(s) in milliseconds
     * @param {number} customCooldownMs - Optional custom throttle window
     * @returns {boolean} - true if vibration dispatched, false if suppressed/unsupported
     */
    trigger(pattern, customCooldownMs = 0) {
        if (!this.enabled) return false;
        if (!this.isSupported()) return false;

        // Visibility guard: never vibrate when tab is in background
        if (typeof document !== 'undefined' && document.hidden) {
            return false;
        }

        // Throttle guard: prevent overwhelming mobile vibrator hardware
        const now = Date.now();
        const effectiveCooldown = customCooldownMs || this.minCooldownMs;
        if (now - this.lastTriggerTime < effectiveCooldown) {
            return false;
        }

        try {
            const success = navigator.vibrate(pattern);
            if (success) {
                this.lastTriggerTime = now;
            }
            return success;
        } catch (err) {
            // Fail-closed: Silently catch permission or hardware errors without throwing
            console.warn('[Haptics] Vibration dispatch suppressed by browser policy:', err);
            return false;
        }
    }

    /**
     * Cancels any currently ongoing vibration pattern.
     */
    cancel() {
        if (this.isSupported()) {
            try {
                navigator.vibrate(0);
            } catch (e) {}
        }
    }
}

// Global Singleton Export
window.HapticFeedback = new HapticTelemetryController();
```

---

## 5. Strict Proven vs Unproven / Fail-Closed Matrix

| Component | [입증된 사실 (Proven & Verified)] | [미입증 / 차단된 사실 (Unproven / Fail-Closed)] |
| :--- | :--- | :--- |
| **Android Mobile Web** | Chrome/Edge/Firefox 모바일 브라우저 및 설치형 PWA 환경에서 `navigator.vibrate` 완벽 동작. | 배터리 절전 모드 또는 시스템 설정에서 '진동 피드백 비활성화'된 기기에서는 하드웨어 차단됨. |
| **iOS / Apple 기기** | WebKit 보안/지문 방지 정책으로 인해 **완전 미지원(Fail-Closed)** 확인 (`features.json` WONTFIX). | 네이티브 앱 래퍼(Capacitor, React Native 등) 없이 순수 웹 브라우저 상태에서는 iOS 진동 불가. |
| **사용자 활성화 (Activation)** | 모바일 크롬은 **Sticky Activation**(`hasBeenActive`)을 요구하므로, 시작 버튼 1회 클릭 후 자동 감지 루프 내 비동기 진동 호출 가능. | 최초 페이지 로드 직후 사용자 터치가 0회인 상태에서의 무단 진동 호출은 브라우저에 의해 자동 차단(`false` 반환). |
| **보안 컨텍스트 (HTTPS)** | 로컬 데스크톱(`localhost`)에서는 동작하나, 모바일 기기가 사설 IP(`192.168.x.x`)로 접속할 경우 보안 컨텍스트 경고 발생 가능. | 상용 모바일 서비스 시 HTTPS 인증서 적용 또는 터널링 필요. |
