# docs/PORTING_GUIDE.md: 미래 프레임워크 및 네이티브 코어 이식 가이드 (Porting & Migration Guide)

본 문서는 `myoi` (SCHALE 전투 추적 및 실시간 탐지/데이터 수집 시스템)의 현재 프로토타입(HTML5 / Vanilla JS / Python FastAPI WGC Sidecar) 아키텍처를 향후 **React/Next.js**, **Vue/Nuxt**, 그리고 **Rust Native Core**로 이식할 때 고려해야 할 아키텍처 영향도, 변경 범위 및 구현 청사진을 정의하는 공식 기술 이식 규격서이다.

---

## 1. 현재 프로토타입 구조 및 핵심 계약 요약

현재 프로토타입은 브라우저 런타임 종속성과 운영체제 캡처 계층을 분리하는 구조로 설계되어 있습니다:
- **Frontend Core (`static/app.js`, `static/aspects.js`, `static/reviewer.js`)**:
  - AOP 기반 횡단 관심사(SecretRedactor, CircuitBreaker, Telemetry, Invariants, Audit).
  - 듀얼 캡처 모드: 네이티브 WGC(WebSocket + HTTP MJPEG 폴백) 및 브라우저 WebRTC(`getDisplayMedia`).
  - 프레임 주도 UI 노출(Frame-Driven UI Exposure): 실제 첫 유효 프레임이 수신·디코딩되어 `readyState >= 2` 및 픽셀 치수가 확립된 이후에만 작전 개시 허용.
  - 동적 창 전환(`switchWgcTarget`): 캡처 파이프라인 중단 없이 HWND 타겟을 실시간 전환하고 이전 창 프레임 캐시를 즉시 파기.
  - WebRTC 프리징 방지: `isConnectingWebRtc` 재귀 락, `selfBrowserSurface: "exclude"` 자기 참조 무한 거울 루프 차단, `vid.play()` 프로미스 거절 예외 격리.
- **Backend Core (`src/capture/`, `src/server.py`)**:
  - Zero DLL Injection Out-of-Process DWM/WGC 캡처 사이드카 (`CaptureService`, `NativeCaptureEngine`).
  - 멀티 프로토콜 배포: WebSocket(`/ws/stream`) 및 HTTP 스트림(`/api/stream`, `/api/capture/frame`).

---

## 2. React / Next.js 이식 청사진 및 영향 범위

### 2.1 이식 영향도 및 주의사항
1. **SSR(Server-Side Rendering) 및 Hydration 오류 방지**:
   - `window`, `navigator.mediaDevices`, `WebSocket`, `HTMLCanvasElement`는 브라우저 전용 API입니다.
   - Next.js (App Router / Pages Router)에서 반드시 `'use client'` 지시문 및 `next/dynamic` (`{ ssr: false }`)을 적용하여 뷰포트 컴포넌트를 클라이언트 전용으로 렌더링해야 합니다.
2. **React 상태 렌더 루프와 고빈도 캔버스 디커플링**:
   - 60 FPS 비디오/캔버스 프레임마다 React `useState`를 호출하면 컴포넌트 트리가 초당 60회 리렌더링되어 심각한 가비지 컬렉션(GC) 스터터링이 발생합니다.
   - 고빈도 프레임 파이프라인은 React State 대신 `useRef<HTMLCanvasElement>` 및 `useRef<HTMLVideoElement>`를 통해 직접 DOM 제어로 격리해야 합니다.
3. **컴포넌트 언마운트 시 리소스 누수 차단**:
   - `useEffect` cleanup 반환 함수에서 `stopOps()`를 호출하여 `stream.getTracks().forEach(t => t.stop())` 및 WebSocket/타이머 정리를 엄격히 보장해야 합니다.

### 2.2 구현 청사진 예시 (Custom Hooks)

```typescript
// hooks/useWgcCapture.ts
'use client';
import { useRef, useState, useEffect, useCallback } from 'react';

export function useWgcCapture() {
  const wsRef = useRef<WebSocket | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [isReady, setIsReady] = useState(false);
  const [currentHwnd, setCurrentHwnd] = useState<number>(0);

  const switchTarget = useCallback((targetHwnd: number) => {
    setCurrentHwnd(targetHwnd);
    setIsReady(false);
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ action: 'switch_target', target: targetHwnd }));
    }
  }, []);

  const connect = useCallback((targetHwnd: number) => {
    const ws = new WebSocket(`ws://${window.location.host}/ws/stream`);
    ws.binaryType = 'blob';
    ws.onmessage = async (e) => {
      if (typeof e.data !== 'string') {
        const bmp = await createImageBitmap(e.data);
        const cvs = canvasRef.current;
        if (cvs) {
          cvs.width = bmp.width;
          cvs.height = bmp.height;
          cvs.getContext('2d')?.drawImage(bmp, 0, 0);
        }
        bmp.close();
        setIsReady(true);
      }
    };
    wsRef.current = ws;
  }, []);

  useEffect(() => {
    return () => {
      if (wsRef.current) wsRef.current.close();
    };
  }, []);

  return { canvasRef, isReady, currentHwnd, connect, switchTarget };
}
```

---

## 3. Vue 3 / Nuxt 3 이식 청사진 및 영향 범위

### 3.1 이식 영향도 및 주의사항
1. **`<ClientOnly>` 컴포넌트 래핑**:
   - Nuxt 3 환경에서는 비디오 스트리머와 캔버스 검수기를 `<ClientOnly>` 태그로 감싸 SSR 파이프라인에서 제외합니다.
2. **Reactivity Proxy 오버헤드 방지 (`shallowRef` 사용)**:
   - `ref()`에 비트맵, 캔버스 컨텍스트, MediaStream 등 대규모 바이너리 객체를 바인딩하면 Vue의 심층 반응형 프록시(Deep Proxy)가 모든 내부 속성을 가로채 심각한 CPU 스파이크를 유발합니다.
   - 스트림과 비트맵 버퍼는 반드시 `shallowRef` 또는 일반 클래스 멤버 변수로 다루어야 합니다.
3. **라이프사이클 훅 정렬**:
   - `onMounted`에서 윈도우 목록 페치 및 캡처 준비를 수행하고, `onBeforeUnmount`에서 스트림 및 웹소켓 세션을 완전 파기합니다.

### 3.2 구현 청사진 예시 (Vue 3 Composable)

```typescript
// composables/useWebRtcScreen.ts
import { shallowRef, ref, onBeforeUnmount } from 'vue';

export function useWebRtcScreen() {
  const stream = shallowRef<MediaStream | null>(null);
  const videoEl = shallowRef<HTMLVideoElement | null>(null);
  const isConnecting = ref(false);
  const isReady = ref(false);

  const connectWebRtc = async () => {
    if (isConnecting.value) return;
    isConnecting.value = true;
    try {
      const media = await navigator.mediaDevices.getDisplayMedia({
        video: { cursor: 'always', displaySurface: 'window' },
        audio: false,
        selfBrowserSurface: 'exclude'
      });
      stream.value = media;
      if (videoEl.value) {
        videoEl.value.srcObject = media;
        videoEl.value.autoplay = true;
        videoEl.value.muted = true;
        await videoEl.value.play().catch(() => {});
        videoEl.value.onloadedmetadata = () => {
          if (videoEl.value && videoEl.value.videoWidth > 0) isReady.value = true;
        };
      }
    } finally {
      isConnecting.value = false;
    }
  };

  const stop = () => {
    stream.value?.getTracks().forEach(t => t.stop());
    stream.value = null;
    isReady.value = false;
  };

  onBeforeUnmount(stop);
  return { stream, videoEl, isReady, connectWebRtc, stop };
}
```

---

## 4. Rust Native Core 이식 청사진 및 영향 범위

### 4.1 이식 목표 및 성능 이점
- Python 백엔드 및 ctypes/GDI/PrintWindow 오버헤드를 완전히 제거하고, 순수 네이티브 초고속 아키텍처로 도약합니다.
- 초당 120+ FPS 무손실 캡처, 서브밀리초(Sub-millisecond) 지연 시간, 제로 런타임 의존성 단일 실행 바이너리(`.exe`) 배포.
- **Vanguard/BattlEye 100% 안티치트 호환(Zero DLL Injection 불변식 유지)**.

### 4.2 아키텍처 및 크레이트(Crate) 구성

| 역할 | Rust 추천 Crate | 설명 및 핵심 책임 |
| :--- | :--- | :--- |
| **WGC 캡처 엔진** | `windows` (0.58+), `windows-capture` | `Windows.Graphics.Capture`, `Direct3D11CaptureFramePool` 기반 초고속 GPU 서피스 직접 복사 |
| **웹 & 스트리밍 서버** | `axum` (0.7+), `tokio` | 비동기 고처리량 WebSocket 및 HTTP MJPEG 멀티파트 스트리머 |
| **비동기 큐 & 브로드캐스트** | `tokio::sync::broadcast` | 프레임 파이프라인 백프레셔 제어 및 다중 클라이언트 무복사 브로드캐스트 |
| **JPEG / WebP 하드웨어 인코더** | `turbojpeg`, `rav1e`, 또는 `windows::Media::Core` | CPU 병목 없는 초고속 인메모리 압축 |
| **추론 백엔드** | `ort` (ONNX Runtime Rust bindings) | DirectML / TensorRT 경유 초저지연 YOLO 추론 |

### 4.3 Rust WGC 캡처 스레드 개념 설계 (Concept Code)

```rust
// src/capture/engine.rs
use std::sync::atomic::{AtomicBool, AtomicIsize, Ordering};
use std::sync::Arc;
use tokio::sync::broadcast;
use windows::Graphics::Capture::{GraphicsCaptureItem, Direct3D11CaptureFramePool};
use windows::Win32::Foundation::HWND;

pub struct RustCaptureEngine {
    target_hwnd: Arc<AtomicIsize>,
    is_running: Arc<AtomicBool>,
    frame_sender: broadcast::Sender<Vec<u8>>,
}

impl RustCaptureEngine {
    pub fn new() -> (Self, broadcast::Receiver<Vec<u8>>) {
        let (tx, rx) = broadcast::channel(16);
        (
            Self {
                target_hwnd: Arc::new(AtomicIsize::new(0)),
                is_running: Arc::new(AtomicBool::new(false)),
                frame_sender: tx,
            },
            rx,
        )
    }

    /// 원자적 타겟 창 전환 (Dynamic Retargeting)
    pub fn switch_target(&self, new_hwnd: isize) -> Result<(), String> {
        self.target_hwnd.store(new_hwnd, Ordering::SeqCst);
        // Frame pool re-creation or GraphicsCaptureItem recreation
        Ok(())
    }
}
```

---

## 5. 결론 및 이관 체크리스트

1. **상태 불변식(Invariant) 보존**:
   - 어떤 프레임워크나 언어로 이식하더라도, "타겟 창 전환 시 이전 창 프레임 캐시 즉각 파기" 및 "첫 유효 프레임 디코딩 확인 전 실행 버튼 노출 차단" 불변식은 100% 동일하게 유지해야 합니다.
2. **에러 격리 (Fail-Closed)**:
   - 타겟 창 전환 실패 시 이전 창으로의 묵시적 폴백을 엄격히 금지하고, 클라이언트에 명시적 에러를 전달해야 합니다.
3. **WebRTC 안전 제약**:
   - WebRTC 브라우저 구현체는 항상 `selfBrowserSurface: "exclude"` 및 재귀 락을 유지하여 크롬 브라우저의 무한 거울 렌더링 프리징을 원천 차단해야 합니다.
