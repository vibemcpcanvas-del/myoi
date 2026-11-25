# GOALS.md

## Project Purpose
- Deliver SCHALE Tactical Tracking (myoi): High-throughput streaming frame capture (Windows Graphics Capture / WebRTC fallback), hybrid Cloud/ONNX YOLO inference, real-time tactical overlays, and dataset annotation review.

## Desired Outcomes & Success Conditions
- Deterministic verification (`python scripts/verify_workspace.py --strict`) passes on every commit.
- Standalone portable Windows executable (`dist/myoi.exe`) compiled and verified.
- Sub-50ms local ONNX detection pipeline with coordinate transformations preserved.

## Scope & Explicit Non-Goals
- **In Scope**: Local server, frontend static assets, WGC sidecar, ONNX detector, and portable PyInstaller executable.
- **Non-Goals**: Committing model binary weights (.onnx/.pt) to git, bypassing user data collection consent, unverified coordinate scaling.

## Security, Reliability & Recovery Constraints
- Zero-trust input validation on multipart payloads, file paths, and WebSocket frames.
- Circuit breaker fails closed on upstream HTTP 401/403 or server stalls.
- No plaintext credentials stored in source code; environment variable `INFERENCE_API_KEY` only.
