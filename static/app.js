/**
 * static/app.js
 * SCHALE Tactical Tracking - Main Application Logic
 * Integrates stream capture, aspect-preserving letterbox inference,
 * snapshot collection, and human review verification.
 */

(function () {
    'use strict';

    const $ = id => document.getElementById(id) || (id === 'chkHideStream' ? document.getElementById('chkHideVid') : null) || (id === 'chkHideVid' ? document.getElementById('chkHideStream') : null);
    const toast = (msg, err) => {
        const d = document.createElement('div');
        d.className = `toast ${err ? 'border-red-500 text-red-600' : 'text-gray-800'}`;
        d.innerHTML = msg;
        $('toastBox').appendChild(d);
        setTimeout(() => {
            if (typeof d.remove === 'function') {
                d.remove();
            } else if (d.parentNode) {
                d.parentNode.removeChild(d);
            }
        }, 3500);
    };
    window.toast = toast;

    const COLORS = ["#eab308", "#ef4444", "#3b82f6", "#a855f7", "#22c55e", "#f97316", "#06b6d4", "#ec4899"];
    const NET_SIZE = 640;

    // Application state
    window.AppState = {
        stream: null,
        timer: null,
        abortController: null,
        isRunning: false,
        isInferenceInFlight: false,
        errorCount: 0,
        cls: JSON.parse(localStorage.getItem('schale_cls_v3') || '{}'),
        lastTTS: {},
        lastBeep: 0,
        sessionId: generateSessionId(),
        lastSnapshotTime: 0,
        // Native WGC Capture Sidecar State
        captureMode: 'wgc', // 'wgc' (default / recommended) or 'webrtc'
        wgcWs: null,
        wgcSessionGen: 0,
        httpFallbackActive: false,
        fallbackTimer: null,
        wsRetryTimer: null,
        wsRetryCount: 0,
        wgcReady: false,
        wgcStalled: false,
        lastFrameArrival: 0,
        wgcCanvas: $('wgcCanvas'),
        wgcCtx: $('wgcCanvas') ? $('wgcCanvas').getContext('2d') : null,
        wgcHwnd: 0,
        wgcTargetHwnd: 0,
        isConnectingWebRtc: false,
        webrtcReady: false,
        isDecodingFrame: false,
        // Dual Inference Engine Toggles (Cloud API vs Local ONNX)
        useCloud: localStorage.getItem('schale_use_cloud') !== 'false',
        useOnnx: localStorage.getItem('schale_use_onnx') === 'true',
        onnxModel: localStorage.getItem('schale_onnx_model') || '',
        // Bounded collection data proxy
        get collectedData() {
            return (window.Reviewer && window.Reviewer.records) ? window.Reviewer.records : [];
        },
        set collectedData(val) {
            if (window.Reviewer) window.Reviewer.records = val;
        },
        // Offscreen canvases
        pCvs: document.createElement('canvas'), // 640x640 letterbox canvas for inference
        origCvs: document.createElement('canvas'), // full resolution clean original canvas
        prevCvs: document.createElement('canvas'), // full resolution preview canvas
    };
    const state = window.AppState;
    state.pCvs.width = state.pCvs.height = NET_SIZE;
    const pCtx = state.pCvs.getContext('2d');
    const origCtx = state.origCvs.getContext('2d');
    const prevCtx = state.prevCvs.getContext('2d');

    // Initialize Reviewer
    window.Reviewer = new window.ReviewManager();

    // AOP Aspect Integration: Circuit Breaker Listener
    if (typeof window !== 'undefined' && window.SchaleAOP && window.SchaleAOP.defaultCircuitBreaker) {
        window.SchaleAOP.defaultCircuitBreaker.addListener((evt, data) => {
            if (evt === 'trip') {
                state.isRunning = false;
                $('btnStart').classList.remove('hidden');
                $('btnStop').classList.add('hidden');
                toast(`서킷 브레이커 차단 (${data.statusCode}): API 키 권한 또는 서버 상태를 확인하십시오.`, true);
                $('statusTxt').innerHTML = `<span class="text-red-600 font-black">🚨 서킷 브레이커 작동 (${data.statusCode}): ${data.message} (작전 자동 격리)</span>`;
            }
        });
    }

    function generateSessionId() {
        const d = new Date();
        const pad = n => String(n).padStart(2, '0');
        return `SES_${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
    }

    window.generateSessionId = generateSessionId;
    window.AppState = state;
    if (window.Reviewer) {
        window.Reviewer.currentSessionId = state.sessionId;
        window.Reviewer.updateSessionBadge();
    }

    $('txtSessionId').innerText = state.sessionId;

    // --- Day/Night Ops Dual-Theme Engine ---
    function initThemeToggle() {
        const btn = $('themeToggleBtn');
        const icon = $('themeIcon');
        const label = $('themeLabel');
        if (!btn) return;

        function syncThemeUI() {
            const isDark = document.documentElement.classList.contains('dark');
            if (icon) icon.innerText = isDark ? '🌙' : '☀️';
            if (label) label.innerText = isDark ? 'NIGHT OPS' : 'DAY OPS';
        }

        btn.onclick = () => {
            const isDark = document.documentElement.classList.toggle('dark');
            try {
                localStorage.setItem('schale_theme', isDark ? 'dark' : 'light');
            } catch (e) {
                console.warn('Theme preference storage error:', e);
            }
            syncThemeUI();
        };

        syncThemeUI();
    }
    initThemeToggle();

    // --- Tactical HUD & Chamfered Bounding Box Utilities ---
    function hexToRgba(hex, alpha) {
        if (!hex || typeof hex !== 'string') return `rgba(18, 116, 196, ${alpha})`;
        const s = hex.trim();
        if (s.startsWith('rgb')) {
            if (s.startsWith('rgba')) return s.replace(/[\d\.]+\)$/g, `${alpha})`);
            return s.replace('rgb', 'rgba').replace(')', `, ${alpha})`);
        }
        let c = s.replace('#', '');
        if (c.length === 3) c = c.split('').map(x => x + x).join('');
        const num = parseInt(c, 16);
        if (isNaN(num)) return `rgba(18, 116, 196, ${alpha})`;
        return `rgba(${(num >> 16) & 255}, ${(num >> 8) & 255}, ${num & 255}, ${alpha})`;
    }

    function drawTacticalBox(cCtx, dx, dy, dw, dh, opt) {
        const { className, confidence, width, height, color, isSelected = false, renderScale = 640 } = opt;
        cCtx.save();

        // 1. Subtle semi-transparent box fill
        cCtx.fillStyle = isSelected ? 'rgba(255, 255, 255, 0.16)' : hexToRgba(color, 0.08);
        cCtx.fillRect(dx, dy, dw, dh);

        // 2. Base perimeter stroke
        cCtx.strokeStyle = isSelected ? '#ffffff' : color;
        cCtx.lineWidth = isSelected ? Math.max(2.5, renderScale / 350) : Math.max(1.5, renderScale / 500);
        cCtx.strokeRect(dx, dy, dw, dh);

        // 3. Chamfered Tactical Corner Marks (Optical Reticles)
        const clen = Math.min(dw / 3, dh / 3, Math.max(8, Math.round(renderScale / 50)));
        const ch = Math.min(clen / 2, Math.max(2, Math.round(renderScale / 160)));
        cCtx.strokeStyle = isSelected ? '#ffffff' : color;
        cCtx.lineWidth = Math.max(2.5, renderScale / 240);
        cCtx.lineCap = 'square';
        cCtx.lineJoin = 'miter';

        // Top-Left (Chamfered Corner Mark)
        cCtx.beginPath();
        cCtx.moveTo(dx, dy + clen);
        cCtx.lineTo(dx, dy + ch);
        cCtx.lineTo(dx + ch, dy);
        cCtx.lineTo(dx + clen, dy);
        cCtx.stroke();

        // Top-Right (Chamfered Corner Mark)
        cCtx.beginPath();
        cCtx.moveTo(dx + dw - clen, dy);
        cCtx.lineTo(dx + dw - ch, dy);
        cCtx.lineTo(dx + dw, dy + ch);
        cCtx.lineTo(dx + dw, dy + clen);
        cCtx.stroke();

        // Bottom-Right (Chamfered Corner Mark)
        cCtx.beginPath();
        cCtx.moveTo(dx + dw, dy + dh - clen);
        cCtx.lineTo(dx + dw, dy + dh - ch);
        cCtx.lineTo(dx + dw - ch, dy + dh);
        cCtx.lineTo(dx + dw - clen, dy + dh);
        cCtx.stroke();

        // Bottom-Left (Chamfered Corner Mark)
        cCtx.beginPath();
        cCtx.moveTo(dx + clen, dy + dh);
        cCtx.lineTo(dx + ch, dy + dh);
        cCtx.lineTo(dx, dy + dh - ch);
        cCtx.lineTo(dx, dy + dh - clen);
        cCtx.stroke();

        // 4. Monospaced Telemetry Badge: `${className} [${conf}%] ${w}x${h}`
        const confVal = Math.round((typeof confidence === 'number' ? confidence : 1) * 100);
        const telemetryText = `${className} [${confVal}%] ${width}x${height}`;

        const fontSize = Math.max(10, Math.round(renderScale / 60));
        cCtx.font = `600 ${fontSize}px 'JetBrains Mono', 'Space Grotesk', monospace`;
        const textMetrics = cCtx.measureText(telemetryText);

        const padX = Math.max(5, Math.round(fontSize * 0.45));
        const padY = Math.max(3, Math.round(fontSize * 0.25));
        const badgeW = textMetrics.width + padX * 2 + 4; // 4px accent bar
        const badgeH = fontSize + padY * 2;
        const badgeY = (dy - badgeH - 3 < 0) ? dy + 3 : dy - badgeH - 3;
        const badgeX = dx;

        // Dark high-contrast badge background
        cCtx.fillStyle = 'rgba(11, 15, 25, 0.88)';
        cCtx.fillRect(badgeX, badgeY, badgeW, badgeH);

        // Left tactical color stripe
        cCtx.fillStyle = color;
        cCtx.fillRect(badgeX, badgeY, 3, badgeH);

        // Subtle badge border
        cCtx.strokeStyle = isSelected ? '#ffffff' : 'rgba(255, 255, 255, 0.2)';
        cCtx.lineWidth = 1;
        cCtx.strokeRect(badgeX, badgeY, badgeW, badgeH);

        // Telemetry Text
        cCtx.fillStyle = '#ffffff';
        cCtx.textBaseline = 'middle';
        cCtx.fillText(telemetryText, badgeX + padX + 3, badgeY + badgeH / 2);

        cCtx.restore();
    }

    // --- Web Audio API (Beep Sound Generator) ---
    let audioCtx;
    const playBeep = () => {
        try {
            if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
            if (audioCtx.state === 'suspended') audioCtx.resume();

            const osc = audioCtx.createOscillator();
            const gain = audioCtx.createGain();

            osc.type = 'triangle';
            osc.frequency.setValueAtTime(880, audioCtx.currentTime);
            osc.frequency.exponentialRampToValueAtTime(440, audioCtx.currentTime + 0.15);

            gain.gain.setValueAtTime(0.12, audioCtx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.15);

            osc.connect(gain);
            gain.connect(audioCtx.destination);
            osc.start();
            osc.stop(audioCtx.currentTime + 0.15);
        } catch (e) {
            console.warn("Audio playback not allowed:", e);
        }
    };

    // --- UI Event Bindings ---
    $('iConf').oninput = e => $('vConf').innerText = Number(e.target.value).toFixed(2);
    if ($('iIou')) $('iIou').oninput = e => { if ($('vIou')) $('vIou').innerText = Number(e.target.value).toFixed(2); };
    $('iFrame').oninput = e => $('vFrame').innerText = e.target.value;
    $('iCooldown').oninput = e => $('vCooldown').innerText = Number(e.target.value).toFixed(1) + 's';
    // --- WebSocket Circuit Breaker (Zero-Console-Flood Invariant) ---
    const wsCircuitBreaker = {
        state: 'CLOSED', // 'CLOSED', 'OPEN', 'HALF_OPEN'
        failures: 0,
        maxFailures: 3,
        lastFailureTime: 0,
        resetTimeout: 30000,
        recordFailure() {
            this.failures++;
            this.lastFailureTime = Date.now();
            if (this.failures >= this.maxFailures) {
                this.state = 'OPEN';
            }
        },
        recordSuccess() {
            this.failures = 0;
            this.state = 'CLOSED';
        },
        isOpen() {
            if (this.state === 'OPEN') {
                if (Date.now() - this.lastFailureTime > this.resetTimeout) {
                    this.state = 'HALF_OPEN';
                    return false;
                }
                return true;
            }
            return false;
        },
        reset() {
            this.failures = 0;
            this.state = 'CLOSED';
            this.lastFailureTime = 0;
        }
    };
    window.wsCircuitBreaker = wsCircuitBreaker;

    // --- Stream Concealment (스트리밍 화면 가리기) Bug Fix ---
    // Critical: Do NOT set display: none, visibility: hidden, or opacity: 0 on video/canvas elements!
    // Chromium/WebRTC throttles frame decoding to 0 FPS when elements are hidden or 0 opacity,
    // which causes canvas.drawImage to output black frames and completely breaks AI detection.
    const updateConcealState = (conceal) => {
        const overlay = $('streamConcealOverlay');
        if (overlay) {
            const isActive = state.stream || state.wgcWs || state.httpFallbackActive || state.isRunning;
            overlay.style.display = (conceal && isActive) ? 'flex' : 'none';
        }
        // Ensure underlying video & WGC canvas maintain full hardware decoding composition
        if ($('vid')) $('vid').style.opacity = '1';
        if ($('wgcCanvas')) $('wgcCanvas').style.opacity = '1';
    };

    const chkHide = $('chkHideVid') || $('chkHideStream');
    if (chkHide) {
        chkHide.onchange = e => updateConcealState(e.target.checked);
    }
    // Global access for testing and automation
    window.chkHideStream = chkHide;
    window.chkHideVid = chkHide;
    window.updateStreamConcealment = updateConcealState;

    // Data Collection Consent Confirmation
    $('chkCollect').onchange = (e) => {
        if (e.target.checked) {
            const ok = confirm(
                "【SCHALE 데이터 수집 안내 및 동의】\n\n" +
                "1. 스트림의 전체 원본 해상도(100% 원본 비율) 프레임이 메모리에 수집됩니다.\n" +
                "2. 자동 예측은 정답 라벨이 아니며, 검수 센터에서 사람이 직접 승인한 프레임만 학습 데이터셋(YOLO ZIP)에 포함됩니다.\n" +
                "3. 수집된 프레임은 브라우저 다운로드/내보내기로 관리되며 외부 서버로 무단 자동 업로드되지 않습니다.\n\n" +
                "데이터 수집을 활성화하시겠습니까?"
            );
            if (!ok) {
                e.target.checked = false;
                toast("데이터 수집이 취소되었습니다.");
                return;
            }
            toast("학습 데이터 수집 활성화됨");
        } else {
            toast("데이터 수집 비활성화됨");
        }
    };

    $('btnReset').onclick = () => {
        state.cls = {};
        saveCls();
        renderCls();
        toast("클래스 목록 초기화됨");
    };

    $('btnNewSession').onclick = async () => {
        if (window.Reviewer) {
            if (window.Reviewer.autoSaveTimer) {
                clearTimeout(window.Reviewer.autoSaveTimer);
                window.Reviewer.autoSaveTimer = null;
            }
            if (window.Reviewer.records.length > 0) {
                await window.Reviewer.saveCurrentSessionToServer(true);
            }
        }
        state.sessionId = generateSessionId();
        if (window.Reviewer) {
            window.Reviewer.currentSessionId = state.sessionId;
            window.Reviewer.clearRecords();
            window.Reviewer.updateSessionBadge();
        }
        $('txtSessionId').innerText = state.sessionId;
        toast(`새 세션 시작: ${state.sessionId}`);
    };

    $('btnClearSession').onclick = () => {
        if (confirm("현재 세션에 수집된 프레임을 모두 버리시겠습니까?")) {
            window.Reviewer.clearRecords();
            state.sessionId = generateSessionId();
            if (window.Reviewer) {
                window.Reviewer.currentSessionId = state.sessionId;
                window.Reviewer.updateSessionBadge();
            }
            $('txtSessionId').innerText = state.sessionId;
            toast("세션 및 수집 데이터 초기화됨");
        }
    };

    // --- Class Configuration Management ---
    const saveCls = () => localStorage.setItem('schale_cls_v3', JSON.stringify(state.cls));
    const renderCls = () => {
        const box = $('classList');
        const keys = Object.keys(state.cls);
        if (!keys.length) {
            box.innerHTML = '<div class="pt-2">API 감지 시 클래스가 자동 등록됩니다.</div>';
            return;
        }

        box.innerHTML = keys.sort((a, b) => a - b).map(k => {
            const c = state.cls[k];
            return `
            <div class="bg-gray-50 border p-2 rounded text-xs flex flex-col gap-1.5" data-id="${k}">
                <div class="flex justify-between font-bold border-b pb-1">
                    <span style="color:${COLORS[k % COLORS.length]}">[${k}] ${c.name}</span>
                    <label class="ba-check flex items-center"><input type="checkbox" class="t-on" ${c.on ? 'checked' : ''}><span class="chk m-0 w-[14px] h-[14px]"></span></label>
                </div>
                <div class="flex justify-between items-center ${c.on ? '' : 'opacity-30 pointer-events-none'}">
                    <label class="ba-check flex items-center"><input type="checkbox" class="t-alt" ${c.alt ? 'checked' : ''}><span class="chk w-[14px] h-[14px]"></span><span class="text-[10px]">경고</span></label>
                    <div class="flex items-center gap-1">
                        <span class="text-[10px] text-gray-500">Conf:</span>
                        <input type="number" class="t-cnf w-12 border p-0.5 text-center text-xs" step="0.05" min="0.01" max="1" value="${c.cnf}">
                    </div>
                </div>
            </div>`;
        }).join('');
    };
    window.renderCls = renderCls;

    $('classList').onchange = e => {
        const p = e.target.closest('[data-id]');
        if (!p) return;
        const id = p.dataset.id;
        if (e.target.classList.contains('t-on')) state.cls[id].on = e.target.checked;
        if (e.target.classList.contains('t-alt')) state.cls[id].alt = e.target.checked;
        if (e.target.classList.contains('t-cnf')) state.cls[id].cnf = parseFloat(e.target.value);
        saveCls();
        renderCls();
    };

    // --- Frame Source Resolution (Native WGC Canvas vs WebRTC Video) ---
    const getFrameSource = () => {
        if (state.captureMode === 'wgc') {
            const cvs = state.wgcCanvas || $('wgcCanvas');
            if (!state.wgcReady || !cvs || cvs.width <= 0 || cvs.height <= 0) return null;
            return {
                element: cvs,
                width: cvs.width,
                height: cvs.height
            };
        } else {
            if (!state.stream) return null;
            const vid = $('vid');
            if (!vid || vid.readyState < 2 || !vid.videoWidth || !vid.videoHeight) return null;
            return {
                element: vid,
                width: vid.videoWidth,
                height: vid.videoHeight
            };
        }
    };
    window.getFrameSource = getFrameSource;

    // --- Dynamic Window Retargeting ---
    const switchWgcTarget = async (newHwnd) => {
        state.wgcTargetHwnd = newHwnd;
        state.wgcHwnd = newHwnd;

        const sel = $('selWgcWindow') || $('wgcWindowSelect');
        if (sel && String(sel.value) !== String(newHwnd)) {
            sel.value = String(newHwnd);
        }

        const isConnected = !!(state.wgcWs || state.httpFallbackActive);
        if (!isConnected) {
            return;
        }

        // Invalidate ready state until fresh frame arrives from newly targeted window
        state.wgcReady = false;
        $('statusTxt').innerHTML = `<span class="text-blue-500 font-medium">⚡ 대상 창 전환 중... (HWND: ${newHwnd})</span>`;

        // Clear canvas to prevent frame leakage from previous window
        const cvs = state.wgcCanvas || $('wgcCanvas');
        if (cvs && typeof cvs.getContext === 'function') {
            const ctx = cvs.getContext('2d');
            if (ctx) ctx.clearRect(0, 0, cvs.width, cvs.height);
        }

        const wsOpen = (typeof WebSocket !== 'undefined' && WebSocket.OPEN !== undefined) ? WebSocket.OPEN : 1;
        if (state.wgcWs && (state.wgcWs.readyState === wsOpen || state.wgcWs.readyState === 1)) {
            state.wgcWs.send(JSON.stringify({
                action: 'switch_target',
                target: newHwnd
            }));
        } else if (state.wgcWs && state.wgcWs.readyState === 0) {
            console.log("[WGC] WebSocket connecting; target HWND queued:", newHwnd);
        } else if (state.httpFallbackActive) {
            try {
                const res = await fetch('/api/capture/switch', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ target: newHwnd })
                });
                const data = await res.json();
                if (!res.ok || !data.success) {
                    toast(`창 전환 실패: ${data.detail?.message || data.error || '대상 창을 찾을 수 없습니다.'}`, true);
                    stopOps();
                } else {
                    toast(`대상 창 전환 완료: ${data.targetTitle || '선택 창'}`);
                }
            } catch (e) {
                toast(`창 전환 요청 실패: ${e.message}`, true);
                stopOps();
            }
        }
    };
    window.switchWgcTarget = switchWgcTarget;

    // --- Native WGC Window Enumeration & Tab Controls ---
    const refreshWgcWindows = async () => {
        const sel = $('selWgcWindow') || $('wgcWindowSelect');
        if (!sel) return;
        try {
            const res = await fetch('/api/capture/windows');
            if (res.ok) {
                const data = await res.json();
                const windows = data.windows || [];
                const currentVal = (state.wgcTargetHwnd !== undefined && state.wgcTargetHwnd !== null) ? state.wgcTargetHwnd : sel.value;
                sel.innerHTML = '<option value="0">🖥️ 전체 데스크톱 (Screen Desktop)</option>';
                windows.forEach(w => {
                    const opt = document.createElement('option');
                    opt.value = w.hwnd;
                    const cleanTitle = (w.title || '').replace(/[\r\n]+/g, ' ').slice(0, 35);
                    opt.textContent = `🎮 [${w.process_name}] ${cleanTitle} (${w.width}x${w.height})`;
                    sel.appendChild(opt);
                });
                if (currentVal !== undefined && Array.from(sel.options).some(o => String(o.value) === String(currentVal))) {
                    sel.value = currentVal;
                }
            }
        } catch (e) {
            console.warn("Failed to fetch capturable windows:", e);
        }
    };
    window.refreshWgcWindows = refreshWgcWindows;

    const setCaptureMode = (mode) => {
        if (state.isRunning || state.stream || state.wgcWs || state.httpFallbackActive || state.isConnectingWebRtc) {
            stopOps();
        }
        state.captureMode = mode;
        const tabWgc = $('tabSourceWgc');
        const tabRtc = $('tabSourceWebRTC');
        const wgcControls = $('wgcControls');
        const vid = $('vid');
        const wgcCanvas = $('wgcCanvas');
        const btnShare = $('btnShare');

        if (mode === 'wgc') {
            if (tabWgc) tabWgc.className = "px-2.5 py-1 rounded bg-[var(--p)] text-white shadow-sm transition-all";
            if (tabRtc) tabRtc.className = "px-2.5 py-1 rounded bg-gray-200 dark:bg-gray-700 text-gray-600 dark:text-gray-300 transition-all";
            if (wgcControls) wgcControls.classList.remove('hidden');
            if (btnShare) btnShare.querySelector('span').innerText = "⚡ 네이티브 연결";
            if (vid) vid.classList.add('hidden');
            if (wgcCanvas) wgcCanvas.classList.remove('hidden');
            refreshWgcWindows();
        } else {
            if (tabRtc) tabRtc.className = "px-2.5 py-1 rounded bg-[var(--p)] text-white shadow-sm transition-all";
            if (tabWgc) tabWgc.className = "px-2.5 py-1 rounded bg-gray-200 dark:bg-gray-700 text-gray-600 dark:text-gray-300 transition-all";
            if (wgcControls) wgcControls.classList.add('hidden');
            if (btnShare) btnShare.querySelector('span').innerText = "🌐 화면 공유";
            if (wgcCanvas) wgcCanvas.classList.add('hidden');
            if (vid) vid.classList.remove('hidden');
        }
    };
    window.setCaptureMode = setCaptureMode;

    if ($('tabSourceWgc')) $('tabSourceWgc').onclick = () => setCaptureMode('wgc');
    if ($('tabSourceWebRTC')) $('tabSourceWebRTC').onclick = () => setCaptureMode('webrtc');
    if ($('btnRefreshWindows')) $('btnRefreshWindows').onclick = refreshWgcWindows;
    const selWgcElem = $('selWgcWindow') || $('wgcWindowSelect');
    if (selWgcElem) selWgcElem.onchange = (e) => switchWgcTarget(e.target.value);

    // --- Native WGC Capture Multi-Protocol Connection & HTTP Fallback ---
    const startHttpFallbackCapture = async (targetHwnd) => {
        if (state.httpFallbackActive) return;
        state.httpFallbackActive = true;
        $('statusTxt').innerHTML = '<span class="text-blue-500 font-medium">⚡ HTTP 스트림 프로토콜 활성화 중...</span>';

        try {
            const res = await fetch('/api/capture/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    target: targetHwnd,
                    fps: 20,
                    quality: 85
                })
            });
            if (!state.httpFallbackActive) return; // user cancelled/stopped while starting
            const data = await res.json();
            if (!data.success) {
                toast(`네이티브 캡처 시작 실패: ${data.error || '대상 창을 찾을 수 없습니다.'}`, true);
                stopOps();
                return;
            }

            const btnShare = $('btnShare');
            if (btnShare && btnShare.querySelector('span')) {
                btnShare.querySelector('span').innerText = "연결 해제";
            }
            $('vid').classList.add('hidden');
            $('wgcCanvas').classList.remove('hidden');
            if (chkHide && chkHide.checked) {
                updateConcealState(true);
            }
            $('statusTxt').innerHTML = '<span class="text-blue-500 font-bold">⚡ HTTP 스트림 프로토콜 활성화... 프레임 수신 대기 중</span>';
            toast("HTTP 대체 프로토콜로 네이티브 캡처가 연결되었습니다.");

            let pollingInFlight = false;
            let consecutiveHttpErrors = 0;
            const pollFrame = async () => {
                if (!state.httpFallbackActive) return;
                if (pollingInFlight) {
                    state.fallbackTimer = setTimeout(pollFrame, 25);
                    return;
                }
                pollingInFlight = true;
                try {
                    const frameRes = await fetch(`/api/capture/frame?t=${Date.now()}`);
                    if (!state.httpFallbackActive) return;
                    if (frameRes.ok) {
                        consecutiveHttpErrors = 0;
                        const blob = await frameRes.blob();
                        const bmp = await createImageBitmap(blob);
                        try {
                            const cvs = state.wgcCanvas || $('wgcCanvas');
                            if (cvs) {
                                const MAX_CANVAS_W = 1920;
                                const MAX_CANVAS_H = 1080;
                                let targetW = bmp.width;
                                let targetH = bmp.height;
                                if (targetW > MAX_CANVAS_W || targetH > MAX_CANVAS_H) {
                                    const scale = Math.min(MAX_CANVAS_W / targetW, MAX_CANVAS_H / targetH);
                                    targetW = Math.round(targetW * scale);
                                    targetH = Math.round(targetH * scale);
                                }
                                if (cvs.width !== targetW || cvs.height !== targetH) {
                                    cvs.width = targetW;
                                    cvs.height = targetH;
                                    $('resolutionInfo').innerText = `해상도: ${targetW}x${targetH} (${bmp.width}x${bmp.height} HTTP Fallback)`;
                                }
                                const ctx = cvs.getContext('2d');
                                ctx.drawImage(bmp, 0, 0, targetW, targetH);
                            }
                        } finally {
                            bmp.close();
                        }
                        state.wgcReady = true;
                        state.wgcStalled = false;
                        state.lastFrameArrival = Date.now();
                        $('overlayStandby').style.display = 'none';
                        $('btnStart').classList.remove('hidden');
                        if (!state.isRunning) {
                            $('statusTxt').innerHTML = '<span class="text-emerald-500 font-bold">⚡ 네이티브 연결됨 (HTTP 스트림 수신 중)</span>';
                        }
                    } else if (frameRes.status === 503) {
                        state.wgcStalled = true;
                    } else {
                        consecutiveHttpErrors++;
                    }
                } catch (e) {
                    consecutiveHttpErrors++;
                    console.warn("HTTP capture frame polling warning:", e);
                } finally {
                    pollingInFlight = false;
                    if (state.httpFallbackActive) {
                        const delay = consecutiveHttpErrors > 5 ? 1000 : 40; // ~25 FPS with backoff on repeated errors
                        state.fallbackTimer = setTimeout(pollFrame, delay);
                    }
                }
            };
            pollFrame();
        } catch (err) {
            console.error("HTTP Fallback start error:", err);
            toast(`HTTP 스트림 연결 실패: ${err.message}`, true);
            stopOps();
        }
    };
    window.startHttpFallbackCapture = startHttpFallbackCapture;

    const handleWsFailure = (targetHwnd) => {
        if (state.wgcWs) {
            try {
                state.wgcWs.onopen = null;
                state.wgcWs.onmessage = null;
                state.wgcWs.onerror = null;
                state.wgcWs.onclose = null;
                state.wgcWs.close();
            } catch (e) {}
            state.wgcWs = null;
        }
        wsCircuitBreaker.recordFailure();
        state.wsRetryCount++;

        const maxRetries = wsCircuitBreaker.maxFailures;
        if (state.wsRetryCount < maxRetries && !wsCircuitBreaker.isOpen()) {
            const backoff = Math.min(600 * Math.pow(2, state.wsRetryCount - 1), 3000);
            $('statusTxt').innerHTML = `<span class="text-amber-500 font-medium">⚠️ WebSocket 재연결 대기 중... (${state.wsRetryCount}/${maxRetries}, ${backoff}ms)</span>`;
            clearTimeout(state.wsRetryTimer);
            state.wsRetryTimer = setTimeout(() => {
                if (state.captureMode === 'wgc' && !state.wgcWs && !state.httpFallbackActive) {
                    connectNativeWgc(targetHwnd);
                }
            }, backoff);
        } else {
            console.warn("[WGC] WebSocket 서킷 브레이커 트립됨. HTTP 대체 스트림으로 자동 전환합니다.");
            $('statusTxt').innerHTML = '<span class="text-blue-500 font-medium">⚡ WebSocket 미지원 환경 감지 -> HTTP 스트림으로 자동 전환 중...</span>';
            startHttpFallbackCapture(targetHwnd);
        }
    };

    const connectNativeWgc = (targetHwnd) => {
        state.wgcHwnd = targetHwnd;

        // If WebSocket circuit breaker is OPEN, skip directly to HTTP fallback
        if (wsCircuitBreaker.isOpen()) {
            console.warn("[WGC] WebSocket 서킷 브레이커 OPEN: HTTP 대체 프로토콜로 즉시 전환합니다.");
            startHttpFallbackCapture(targetHwnd);
            return;
        }

        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${location.host}/ws/stream`;
        $('statusTxt').innerHTML = `<span class="text-blue-500">🔵 네이티브 WGC 사이드카 연결 중... (${state.wsRetryCount + 1}/3)</span>`;

        state.wgcSessionGen = (state.wgcSessionGen || 0) + 1;
        const currentSessionId = state.wgcSessionGen;
        let connectionEstablished = false;
        let failureHandled = false;
        let wsHandshakeTimer = null;

        const onConnectionFailed = (reason) => {
            if (failureHandled || connectionEstablished) return;
            if (state.wgcSessionGen !== currentSessionId) return;
            failureHandled = true;
            if (wsHandshakeTimer) {
                clearTimeout(wsHandshakeTimer);
                wsHandshakeTimer = null;
            }
            console.warn(`[WGC] WebSocket 연결 실패 (${reason}). 서킷 브레이커 카운트 증가.`);
            handleWsFailure(targetHwnd);
        };

        try {
            const ws = new WebSocket(wsUrl);
            ws.binaryType = 'blob';

            wsHandshakeTimer = setTimeout(() => {
                if (!connectionEstablished) {
                    try {
                        ws.onopen = null;
                        ws.onmessage = null;
                        ws.onerror = null;
                        ws.onclose = null;
                        ws.close();
                    } catch (e) {}
                    onConnectionFailed("핸드셰이크 타임아웃");
                }
            }, 3000);

            ws.onopen = () => {
                if (state.wgcSessionGen !== currentSessionId) {
                    try { ws.close(); } catch (e) {}
                    return;
                }
                connectionEstablished = true;
                if (wsHandshakeTimer) clearTimeout(wsHandshakeTimer);
                wsCircuitBreaker.recordSuccess();
                state.wsRetryCount = 0;

                ws.send(JSON.stringify({
                    action: 'start',
                    target: (state.wgcTargetHwnd !== undefined && state.wgcTargetHwnd !== null) ? state.wgcTargetHwnd : targetHwnd,
                    fps: 20,
                    quality: 85
                }));
                $('overlayStandby').style.display = 'none';
                const btnShare = $('btnShare');
                if (btnShare && btnShare.querySelector('span')) {
                    btnShare.querySelector('span').innerText = "연결 해제";
                }
                $('vid').classList.add('hidden');
                $('wgcCanvas').classList.remove('hidden');
                if (chkHide && chkHide.checked) {
                    updateConcealState(true);
                }
            };

            ws.onmessage = async (e) => {
                if (state.wgcSessionGen !== currentSessionId) return;
                if (typeof e.data === 'string') {
                    try {
                        const msg = JSON.parse(e.data);
                        if (msg.type === 'capture_state') {
                            if (msg.state === 'PAUSED_MINIMIZED') {
                                $('statusTxt').innerHTML = '<span class="text-amber-500 font-bold">⏸️ 대상 창이 최소화되었습니다 (캡처 대기 중)</span>';
                                state.wgcStalled = true;
                            } else if (msg.state === 'DEGRADED') {
                                $('statusTxt').innerHTML = `<span class="text-amber-500 font-bold">⚠️ 캡처 성능 저하 (${msg.error || '지연'})</span>`;
                                state.wgcStalled = true;
                            } else if (msg.state === 'FAILED') {
                                $('statusTxt').innerHTML = `<span class="text-rose-500 font-bold">🚨 캡처 실패: ${msg.error || '창 소실'}</span>`;
                                state.wgcStalled = true;
                            } else if (msg.state === 'CAPTURING') {
                                if (state.wgcStalled) {
                                    state.wgcStalled = false;
                                    if (state.isRunning) {
                                        $('statusTxt').innerText = "실시간 감지 및 추적 중...";
                                    }
                                }
                            }
                        } else if (msg.type === 'start_response') {
                            if (!msg.data || !msg.data.success) {
                                toast(`네이티브 캡처 실패: ${msg.data?.error || msg.data?.message || '대상 창을 찾을 수 없습니다.'}`, true);
                                stopOps();
                            } else {
                                toast("네이티브 WGC 캡처 연결 성공");
                                $('statusTxt').innerHTML = '<span class="text-blue-500 font-bold">⚡ 네이티브 WGC 세션 확립... 프레임 수신 대기 중</span>';
                            }
                        } else if (msg.type === 'switch_response') {
                            if (!msg.data || !msg.data.success) {
                                toast(`네이티브 창 전환 실패: ${msg.data?.error || msg.data?.message || '대상 창을 찾을 수 없습니다.'}`, true);
                                stopOps();
                            } else {
                                toast(`대상 창 전환 완료: ${msg.data.targetTitle || '선택 창'}`);
                                $('statusTxt').innerHTML = `<span class="text-blue-500 font-bold">⚡ 대상 창 전환됨: ${msg.data.targetTitle || '선택 창'}</span>`;
                            }
                        } else if (msg.type === 'target_switched') {
                            console.log(`[WGC] Target switched notification: HWND ${msg.targetHwnd}`);
                        }
                    } catch (err) {
                        console.warn("WGC WS text parse error:", err);
                    }
                    return;
                }

                // Binary Frame Processing
                if (state.isDecodingFrame) {
                    // Frame-dropping backpressure guard: discard frame if previous frame is still decoding
                    return;
                }
                state.isDecodingFrame = true;
                try {
                    const bmp = await createImageBitmap(e.data);
                    try {
                        const cvs = state.wgcCanvas || $('wgcCanvas');
                        if (cvs) {
                            const MAX_CANVAS_W = 1920;
                            const MAX_CANVAS_H = 1080;
                            let targetW = bmp.width;
                            let targetH = bmp.height;
                            if (targetW > MAX_CANVAS_W || targetH > MAX_CANVAS_H) {
                                const scale = Math.min(MAX_CANVAS_W / targetW, MAX_CANVAS_H / targetH);
                                targetW = Math.round(targetW * scale);
                                targetH = Math.round(targetH * scale);
                            }
                            if (cvs.width !== targetW || cvs.height !== targetH) {
                                cvs.width = targetW;
                                cvs.height = targetH;
                                $('resolutionInfo').innerText = `해상도: ${targetW}x${targetH} (${bmp.width}x${bmp.height} WGC)`;
                            }
                            const ctx = cvs.getContext('2d');
                            ctx.drawImage(bmp, 0, 0, targetW, targetH);
                        }
                        state.wgcReady = true;
                        state.wgcStalled = false;
                        state.lastFrameArrival = Date.now();
                        $('overlayStandby').style.display = 'none';
                        $('btnStart').classList.remove('hidden');
                        if (!state.isRunning) {
                            $('statusTxt').innerHTML = '<span class="text-emerald-500 font-bold">⚡ 네이티브 연결됨 (실시간 프레임 수신 중)</span>';
                        }
                    } finally {
                        bmp.close();
                    }
                } catch (err) {
                    console.warn("WGC frame decode warning:", err);
                } finally {
                    state.isDecodingFrame = false;
                }
            };

            ws.onerror = (err) => {
                if (!connectionEstablished) {
                    onConnectionFailed("핸드셰이크 오류");
                } else {
                    console.error("WGC WebSocket error:", err);
                    toast("WGC 사이드카 통신 오류", true);
                }
            };

            ws.onclose = () => {
                if (!connectionEstablished) {
                    onConnectionFailed("연결 거절/종료");
                    return;
                }
                if (state.wgcWs === ws) {
                    state.wgcWs = null;
                    state.wgcReady = false;
                    state.wgcStalled = false;
                    const concealOverlay = $('streamConcealOverlay');
                    if (concealOverlay) concealOverlay.style.display = 'none';
                    // Auto-fallback to HTTP stream if disconnected unexpectedly in WGC mode
                    if (state.captureMode === 'wgc' && !state.httpFallbackActive) {
                        console.warn("[WGC] WebSocket 스트림 연결 끊김. HTTP 대체 스트림으로 자동 복구합니다.");
                        $('statusTxt').innerHTML = '<span class="text-amber-500 font-medium">⚡ WebSocket 스트림 연결 끊김 -> HTTP 스트림으로 자동 복구 중...</span>';
                        startHttpFallbackCapture(state.wgcHwnd || 0);
                    } else {
                        stopOps();
                    }
                }
            };

            state.wgcWs = ws;
        } catch (err) {
            onConnectionFailed("WebSocket 인스턴스 생성 예외");
        }
    };
    window.connectNativeWgc = connectNativeWgc;

    // --- WebRTC Screen Capture Connection ---
    const connectWebRtc = async () => {
        if (state.isConnectingWebRtc) {
            console.warn("[WebRTC] Connection already in progress, ignoring duplicate call.");
            return;
        }
        state.isConnectingWebRtc = true;

        try {
            if (state.stream) {
                state.stream.getTracks().forEach(t => t.stop());
                state.stream = null;
            }

            // Display media options with recursion guard to avoid infinite hall-of-mirrors freeze
            const displayMediaOptions = {
                video: {
                    cursor: "always",
                    displaySurface: "window"
                },
                audio: false,
                selfBrowserSurface: "exclude",
                surfaceSwitching: "exclude",
                systemAudio: "exclude"
            };

            if (!navigator || !navigator.mediaDevices || typeof navigator.mediaDevices.getDisplayMedia !== 'function') {
                toast("현재 환경에서는 WebRTC 화면 공유를 지원하지 않습니다.", true);
                stopOps();
                return;
            }

            let stream;
            try {
                stream = await navigator.mediaDevices.getDisplayMedia(displayMediaOptions);
            } catch (constraintErr) {
                // If user cancelled (NotAllowedError / AbortError), do not re-prompt with fallback
                if (constraintErr.name === 'NotAllowedError' || constraintErr.name === 'AbortError') {
                    throw constraintErr;
                }
                // Fallback for browsers not supporting selfBrowserSurface / displaySurface constraints
                stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
            }

            // If user switched mode away from WebRTC while screen-share dialog was open, discard stream immediately
            if (state.captureMode !== 'webrtc') {
                if (stream) {
                    stream.getTracks().forEach(t => t.stop());
                }
                return;
            }

            if (!stream) {
                toast("화면 공유 스트림을 가져오지 못했습니다.", true);
                return;
            }

            state.stream = stream;
            const vid = $('vid');
            if (!vid) {
                toast("비디오 요소를 찾을 수 없습니다.", true);
                stopOps();
                return;
            }

            // Clean up previous event listeners on vid
            vid.onloadedmetadata = null;
            vid.oncanplay = null;
            vid.onplaying = null;

            vid.autoplay = true;
            vid.playsInline = true;
            vid.muted = true;
            vid.srcObject = stream;

            // Safe play() with rejection guard to prevent browser pipeline stalls
            try {
                if (typeof vid.play === 'function') {
                    const playPromise = vid.play();
                    if (playPromise !== undefined) {
                        await playPromise.catch(playErr => {
                            console.warn("[WebRTC] vid.play() interrupted or rejected:", playErr);
                        });
                    }
                }
            } catch (playErr) {
                console.warn("[WebRTC] vid.play() caught exception:", playErr);
            }

            const track = stream.getVideoTracks()[0];
            if (track) {
                track.onended = () => {
                    console.log("[WebRTC] Stream track ended by user/system.");
                    stopOps();
                };
            }

            $('overlayStandby').style.display = 'none';
            const btnShare = $('btnShare');
            if (btnShare && btnShare.querySelector('span')) {
                btnShare.querySelector('span').innerText = "연결 해제";
            }

            // Frame-driven UI exposure: wait for actual video dimensions & readyState >= 2 before enabling start
            state.webrtcReady = false;
            const checkVideoReady = () => {
                if (vid.videoWidth > 0 && vid.videoHeight > 0 && (vid.readyState === undefined || vid.readyState >= 2)) {
                    vid.onloadedmetadata = null;
                    vid.oncanplay = null;
                    vid.onplaying = null;
                    const vw = vid.videoWidth;
                    const vh = vid.videoHeight;
                    $('resolutionInfo').innerText = `해상도: ${vw}x${vh} (WebRTC 원본 유지)`;
                    $('btnStart').classList.remove('hidden');
                    state.webrtcReady = true;
                    if (!state.isRunning) {
                        $('statusTxt').innerHTML = '<span class="text-emerald-500 font-bold">🌐 WebRTC 연결됨 (화면 공유 활성)</span>';
                    }
                    return true;
                }
                return false;
            };

            if (!checkVideoReady()) {
                vid.onloadedmetadata = checkVideoReady;
                vid.oncanplay = checkVideoReady;
                vid.onplaying = checkVideoReady;
            }

            if (chkHide && chkHide.checked) {
                updateConcealState(true);
            }
            toast("WebRTC 화면 공유가 연결되었습니다.");
        } catch (err) {
            if (err.name === 'NotAllowedError' || err.name === 'AbortError') {
                toast("화면 공유가 취소되었습니다.", true);
            } else {
                console.error("[WebRTC] Screen share failed:", err);
                toast(`화면 공유 오류: ${err.message}`, true);
            }
            stopOps();
        } finally {
            state.isConnectingWebRtc = false;
        }
    };
    window.connectWebRtc = connectWebRtc;

    // --- Screen Share / Operation Control ---
    $('btnShare').onclick = async () => {
        if (state.captureMode === 'wgc') {
            // Native WGC Capture Mode
            const isConnected = !!(state.wgcWs || state.httpFallbackActive);
            if (!isConnected) {
                const sel = $('selWgcWindow');
                const targetHwnd = sel ? sel.value : 0;
                connectNativeWgc(targetHwnd);
            } else {
                stopOps();
            }
        } else {
            // WebRTC Mode
            if (!state.stream) {
                await connectWebRtc();
            } else {
                stopOps();
            }
        }
    };

    const stopOps = () => {
        if (window.Reviewer && typeof window.Reviewer.flushUnsavedRecordsOnExit === 'function') {
            window.Reviewer.flushUnsavedRecordsOnExit();
        }
        state.wgcSessionGen = (state.wgcSessionGen || 0) + 1;
        state.isRunning = false;
        state.isInferenceInFlight = false;
        state.isConnectingWebRtc = false;
        state.webrtcReady = false;
        clearTimeout(state.timer);
        clearTimeout(state.wsRetryTimer);
        clearTimeout(state.fallbackTimer);
        state.wsRetryCount = 0;

        if (state.abortController) {
            state.abortController.abort();
            state.abortController = null;
        }

        if (state.stream) {
            state.stream.getTracks().forEach(t => t.stop());
            state.stream = null;
        }

        if (state.wgcWs) {
            try {
                state.wgcWs.onopen = null;
                state.wgcWs.onmessage = null;
                state.wgcWs.onerror = null;
                state.wgcWs.onclose = null;
                state.wgcWs.send(JSON.stringify({ action: "stop" }));
                state.wgcWs.close();
            } catch (e) {}
            state.wgcWs = null;
        }

        if (state.httpFallbackActive) {
            state.httpFallbackActive = false;
        }

        if (state.captureMode === 'wgc') {
            try {
                if (typeof fetch === 'function') {
                    fetch('/api/capture/stop', { method: 'POST' }).catch(() => {});
                }
            } catch (e) {}
        }

        state.wgcReady = false;
        state.wgcStalled = false;
        state.isDecodingFrame = false;
        const concealOverlay = $('streamConcealOverlay');
        if (concealOverlay) concealOverlay.style.display = 'none';

        if (window.Reviewer && window.Reviewer.records.length > 0) {
            window.Reviewer.saveCurrentSessionToServer(true);
        }

        const vid = $('vid');
        if (vid) {
            vid.onloadedmetadata = null;
            vid.oncanplay = null;
            vid.onplaying = null;
            try {
                if (typeof vid.pause === 'function') vid.pause();
            } catch (e) {}
            vid.srcObject = null;
        }
        const overlayStandby = $('overlayStandby');
        if (overlayStandby) overlayStandby.style.display = 'flex';
        const btnShare = $('btnShare');
        if (btnShare && btnShare.querySelector('span')) {
            btnShare.querySelector('span').innerText = state.captureMode === 'wgc' ? "⚡ 네이티브 연결" : "🌐 화면 공유";
        }
        $('btnStart').classList.add('hidden');
        $('btnStop').classList.add('hidden');
        $('statusTxt').innerText = "연결이 해제되었습니다.";
        $('resolutionInfo').innerText = "해상도: 대기 중";
        $('videoWrap').classList.remove('alert');
        $('alertBanner').style.opacity = '0';

        const cvs = $('cvs');
        if (cvs && typeof cvs.getContext === 'function') {
            const ctx = cvs.getContext('2d');
            if (ctx) ctx.clearRect(0, 0, cvs.width, cvs.height);
        }

        const wgcCanvas = state.wgcCanvas || $('wgcCanvas');
        if (wgcCanvas && typeof wgcCanvas.getContext === 'function') {
            const wgcCtx = wgcCanvas.getContext('2d');
            if (wgcCtx) wgcCtx.clearRect(0, 0, wgcCanvas.width, wgcCanvas.height);
        }
    };
    window.stopOps = stopOps;

    $('btnStart').onclick = () => {
        const source = getFrameSource();
        if (!source) {
            toast(state.captureMode === 'wgc' ? "네이티브 캡처를 먼저 연결하세요." : "화면 공유를 먼저 시작하세요.", true);
            return;
        }
        state.isRunning = true;
        $('btnStart').classList.add('hidden');
        $('btnStop').classList.remove('hidden');
        toast("클라우드 자동 작전 개시");
        loop();
    };

    $('btnStop').onclick = () => {
        state.isRunning = false;
        clearTimeout(state.timer);
        if (state.abortController) {
            state.abortController.abort();
            state.abortController = null;
        }
        $('btnStart').classList.remove('hidden');
        $('btnStop').classList.add('hidden');
        $('statusTxt').innerText = "작전 중지됨.";
        $('videoWrap').classList.remove('alert');
        $('alertBanner').style.opacity = '0';
        const cvs = $('cvs');
        if (cvs && typeof cvs.getContext === 'function') {
            const ctx = cvs.getContext('2d');
            if (ctx) ctx.clearRect(0, 0, cvs.width, cvs.height);
        }

        const wgcCanvas = state.wgcCanvas || $('wgcCanvas');
        if (wgcCanvas && typeof wgcCanvas.getContext === 'function') {
            const wgcCtx = wgcCanvas.getContext('2d');
            if (wgcCtx) wgcCtx.clearRect(0, 0, wgcCanvas.width, wgcCanvas.height);
        }
    };
    window.stopOps = stopOps;

    // --- Inference & Drawing Loop ---
    const loop = async () => {
        if (!state.isRunning) return;
        if (state.isInferenceInFlight) return;

        if (state.wgcStalled) {
            if (state.isRunning) {
                state.timer = setTimeout(loop, 300);
            }
            return;
        }

        // Stale watchdog: verify fresh frames arrived within 1.5 seconds in native WGC mode
        if (state.captureMode === 'wgc' && state.lastFrameArrival && (Date.now() - state.lastFrameArrival > 1500)) {
            $('statusTxt').innerHTML = '<span class="text-amber-500 font-bold">⚠️ 프레임 지연 감지 (대기 중...)</span>';
            if (state.isRunning) {
                state.timer = setTimeout(loop, 300);
            }
            return;
        }

        // 0. Check if both inference engines are disabled
        if (!state.useCloud && !state.useOnnx) {
            $('statusTxt').innerHTML = '<span class="text-slate-500 font-bold">⏸️ 추론 엔진 꺼짐 (클라우드 OFF · 로컬 ONNX OFF — 모니터링 대기)</span>';
            const cvs = $('cvs');
            if (cvs && typeof cvs.getContext === 'function') {
                const ctx = cvs.getContext('2d');
                if (ctx) ctx.clearRect(0, 0, cvs.width, cvs.height);
            }
            if (state.isRunning) {
                state.timer = setTimeout(loop, 350);
            }
            return;
        }

        const source = getFrameSource();
        if (!source || !source.width || !source.height) {
            $('statusTxt').innerHTML = '<span class="text-amber-600">🟡 프레임 소스 준비 대기 중...</span>';
            if (state.isRunning) {
                state.timer = setTimeout(loop, 200);
            }
            return;
        }

        const vw = source.width;
        const vh = source.height;

        state.isInferenceInFlight = true;

        // 1. Calculate aspect-preserving letterbox for inference
        const letterbox = window.CoordinateAdapter.calculateLetterbox(vw, vh, NET_SIZE, NET_SIZE);

        // Draw letterbox onto 640x640 canvas (neutral padding)
        pCtx.fillStyle = '#727272';
        pCtx.fillRect(0, 0, NET_SIZE, NET_SIZE);
        pCtx.drawImage(source.element, letterbox.padX, letterbox.padY, letterbox.scaledW, letterbox.scaledH);

        // 2. Prepare inference payload
        const blob = await new Promise(r => state.pCvs.toBlob(r, 'image/jpeg', 0.85));
        if (!blob || blob.size === 0) {
            toast("프레임 인코딩 실패 (0 바이트)", true);
            state.isInferenceInFlight = false;
            if (state.isRunning) {
            state.timer = setTimeout(loop, 200);
        }
            return;
        }

        const fd = new FormData();
        fd.append("file", blob, "frame.jpg");
        fd.append("imgsz", NET_SIZE);
        fd.append("coordinateSpace", "letterbox_640");
        fd.append("useCloud", state.useCloud ? "true" : "false");
        fd.append("useOnnx", state.useOnnx ? "true" : "false");
        if (state.useOnnx && state.onnxModel) {
            fd.append("onnxModel", state.onnxModel);
        }
        if ($('iConf')) {
            fd.append("conf", $('iConf').value);
        }
        if ($('iIou')) {
            fd.append("iou", $('iIou').value);
        }

        state.abortController = new AbortController();
        const st = Date.now();

        try {
            // Call proxy via AOP Circuit Breaker Aspect
            const apiFetcher = (typeof window !== 'undefined' && window.SchaleAOP && window.SchaleAOP.defaultCircuitBreaker)
                ? (url, opts) => window.SchaleAOP.defaultCircuitBreaker.wrapFetch(url, opts)
                : fetch;

            const res = await apiFetcher("/api/predict", {
                method: "POST",
                body: fd,
                signal: state.abortController.signal
            });

            if (!state.isRunning) return; // discarded after stop

            if (!res.ok) {
                const errData = await res.json().catch(() => ({}));
                let errMsg = errData.detail?.message || `HTTP ${res.status}`;
                if (typeof window !== 'undefined' && window.SchaleAOP && window.SchaleAOP.SecretRedactor) {
                    errMsg = window.SchaleAOP.SecretRedactor.redact(errMsg);
                }

                if (res.status === 401 || res.status === 503) {
                    // Fatal Auth Error: Fail-Closed Circuit Breaker - Stop operation immediately to prevent loop spamming
                    state.isRunning = false;
                    $('btnStart').classList.remove('hidden');
                    $('btnStop').classList.add('hidden');
                    toast(`인증 실패 (${res.status}): API 키 권한을 확인하십시오. 작전이 자동 중지되었습니다.`, true);
                    $('statusTxt').innerHTML = `<span class="text-red-600 font-black">🚨 인증 실패 (${res.status}): ${errMsg} (작전 자동 중단)</span>`;
                    return;
                }
                throw new Error(errMsg);
            }

            const data = await res.json();
            const elapsed = Date.now() - st;

            // AOP Aspect 3: Telemetry recording
            if (typeof window !== 'undefined' && window.SchaleAOP && window.SchaleAOP.defaultTelemetry) {
                window.SchaleAOP.defaultTelemetry.record(elapsed, true);
            }

            $('statusTxt').innerHTML = `<span class="text-green-600">🟢 추론 성공 (Ping: ${elapsed}ms)</span>`;

            // Coordinate Space Badge update
            const coordBadge = $('coordSpaceBadge');
            if (coordBadge) {
                if (data.coordinateSpace === 'unknown') {
                    coordBadge.className = 'text-xs font-mono bg-red-100 text-red-800 px-2.5 py-1 rounded-full font-bold animate-pulse';
                    coordBadge.innerText = '좌표 기준 미확인 [라벨 생성 차단]';
                } else {
                    coordBadge.className = 'text-xs font-mono bg-blue-100 text-blue-800 px-2.5 py-1 rounded-full font-bold';
                    coordBadge.innerText = `${data.coordinateSpace || 'Letterbox 640'} [Verified]`;
                }
            }

            const rawResults = data.results || [];
            let needRender = false;

            // Register detected classes
            rawResults.forEach(r => {
                if (!state.cls[r.class]) {
                    state.cls[r.class] = {
                        name: r.name || `C${r.class}`,
                        on: true,
                        alt: true,
                        cnf: parseFloat($('iConf').value),
                        count: 0
                    };
                    needRender = true;
                }
            });
            if (needRender) {
                saveCls();
                renderCls();
            }

            // 3. Align Canvas Overlay to Contain-Mode Video Rect
            const videoWrap = $('videoWrap');
            const wrapW = videoWrap.clientWidth;
            const wrapH = videoWrap.clientHeight;
            const cvs = $('cvs');
            if (cvs.width !== wrapW || cvs.height !== wrapH) {
                cvs.width = wrapW;
                cvs.height = wrapH;
            }

            const ctx = cvs.getContext('2d');
            ctx.clearRect(0, 0, wrapW, wrapH);

            // Compute video containment viewport inside wrap
            const streamAspect = vw / vh;
            const wrapAspect = wrapW / wrapH;
            let renderW, renderH, offsetX, offsetY;

            if (wrapAspect > streamAspect) {
                // Pillared horizontally
                renderH = wrapH;
                renderW = wrapH * streamAspect;
                offsetX = (wrapW - renderW) / 2;
                offsetY = 0;
            } else {
                // Letterboxed vertically
                renderW = wrapW;
                renderH = wrapW / streamAspect;
                offsetX = 0;
                offsetY = (wrapH - renderH) / 2;
            }

            const renderScaleX = renderW / vw;
            const renderScaleY = renderH / vh;

            const thFrames = parseInt($('iFrame').value);
            let hasAlert = false;
            const seen = new Set();
            const now = Date.now();
            const validDetectionsForCollection = [];

            rawResults.forEach(r => {
                const c = state.cls[r.class];
                if (!c || !c.on || r.confidence < c.cnf) return;

                seen.add(r.class);
                c.count = (c.count || 0) + 1;
                const isConf = c.count >= thFrames;

                // Transform box coordinates to original frame pixel space
                let origBox;
                try {
                    origBox = window.CoordinateAdapter.transformBoxToOriginal(
                        r.box,
                        data.coordinateSpace || "letterbox_640",
                        letterbox
                    );
                } catch (e) {
                    console.warn("Coordinate conversion skipped:", e);
                    return;
                }

                if (isConf) {
                    validDetectionsForCollection.push({
                        classId: r.class,
                        className: c.name,
                        confidence: r.confidence,
                        box: origBox
                    });
                }

                if (isConf && c.alt) {
                    hasAlert = true;

                    // Beep alert (Global cooldown 2s)
                    if ($('chkBeep').checked && (now - state.lastBeep > 2000)) {
                        state.lastBeep = now;
                        playBeep();
                    }

                    // TTS alert (Per-class cooldown 5s)
                    if ($('chkTTS').checked && (now - (state.lastTTS[r.class] || 0) > 5000)) {
                        state.lastTTS[r.class] = now;
                        const u = new SpeechSynthesisUtterance(`경고. ${c.name}.`);
                        u.lang = 'ko-KR';
                        u.rate = 1.2;
                        speechSynthesis.speak(u);
                    }
                }

                // Draw overlay box on display viewport
                const color = COLORS[r.class % COLORS.length];
                const dx = offsetX + origBox.x1 * renderScaleX;
                const dy = offsetY + origBox.y1 * renderScaleY;
                const dw = origBox.width * renderScaleX;
                const dh = origBox.height * renderScaleY;

                drawTacticalBox(ctx, dx, dy, dw, dh, {
                    className: c.name,
                    confidence: r.confidence,
                    width: Math.round(origBox.width),
                    height: Math.round(origBox.height),
                    color: color,
                    renderScale: wrapW
                });
            });

            // Reset counts for unseen classes
            Object.keys(state.cls).forEach(k => {
                if (!seen.has(Number(k))) state.cls[k].count = 0;
            });

            $('videoWrap').classList.toggle('alert', hasAlert);
            $('alertBanner').style.opacity = hasAlert ? '1' : '0';

            // 4. Data Collection Snapshot Trigger
            if ($('chkCollect').checked && validDetectionsForCollection.length > 0) {
                const cooldownSec = parseFloat($('iCooldown').value);
                const cooldownMs = cooldownSec * 1000;

                if (now - state.lastSnapshotTime >= cooldownMs) {
                    state.lastSnapshotTime = now;
                    $('cooldownIndicator').innerText = "스냅샷 캡처 중...";
                    await captureSnapshot(vw, vh, validDetectionsForCollection);
                    $('cooldownIndicator').innerText = `쿨다운 중 (${cooldownSec}s)`;
                } else {
                    const remaining = ((cooldownMs - (now - state.lastSnapshotTime)) / 1000).toFixed(1);
                    $('cooldownIndicator').innerText = `중복 억제 활성: 잔여 쿨다운 ${remaining}s`;
                }
            } else if ($('chkCollect').checked) {
                $('cooldownIndicator').innerText = "수집 조건 대기 중 (신뢰도/연속프레임 충족 시)";
            }

        } catch (e) {
            if (e.name !== 'AbortError') {
                state.errorCount++;
                const errBox = $('cntError');
                if (errBox) errBox.innerText = state.errorCount;

                const safeMsg = (typeof window !== 'undefined' && window.SchaleAOP && window.SchaleAOP.SecretRedactor)
                    ? window.SchaleAOP.SecretRedactor.redact(e.message)
                    : e.message;
                $('statusTxt').innerHTML = `<span class="text-red-500">🔴 에러: ${safeMsg}</span>`;

                // AOP Aspect 3: Telemetry recording for failure
                const elapsed = Date.now() - st;
                if (typeof window !== 'undefined' && window.SchaleAOP && window.SchaleAOP.defaultTelemetry) {
                    window.SchaleAOP.defaultTelemetry.record(elapsed, false);
                }
            }
        } finally {
            state.isInferenceInFlight = false;
            state.abortController = null;
        }

        if (state.isRunning) {
            state.timer = setTimeout(loop, 200);
        }
    };

    // --- High-Resolution Snapshot Capture ---
    async function captureSnapshot(vw, vh, detections) {
        try {
            // 1. Capture clean unannotated original frame
            state.origCvs.width = vw;
            state.origCvs.height = vh;
            const src = getFrameSource();
            const srcEl = src ? src.element : $('vid');
            origCtx.drawImage(srcEl, 0, 0, vw, vh);
            const origBase64 = state.origCvs.toDataURL('image/jpeg', 0.95);

            // 2. Capture preview frame with boxes for review convenience
            state.prevCvs.width = vw;
            state.prevCvs.height = vh;
            prevCtx.drawImage(state.origCvs, 0, 0);

            detections.forEach(d => {
                const b = d.box;
                const color = COLORS[(d.classId || 0) % COLORS.length];
                drawTacticalBox(prevCtx, b.x1, b.y1, b.width, b.height, {
                    className: d.className,
                    confidence: d.confidence,
                    width: Math.round(b.width),
                    height: Math.round(b.height),
                    color: color,
                    renderScale: vw
                });
            });

            const prevBase64 = state.prevCvs.toDataURL('image/jpeg', 0.85);

            const recId = `rec_${Date.now()}_${Math.random().toString(36).substr(2, 6)}`;
            const timestampUtc = new Date().toISOString();

            const record = {
                schemaVersion: "1.0.0",
                recordId: recId,
                sessionId: state.sessionId,
                imageFile: `images/${recId}_orig.jpg`,
                previewFile: `previews/${recId}_prev.jpg`,
                capturedAtUtc: timestampUtc,
                originalWidth: vw,
                originalHeight: vh,
                inferenceWidth: NET_SIZE,
                inferenceHeight: NET_SIZE,
                model: "ultralytics-yolo-v8",
                coordinateSpace: "letterbox_640",
                coordinateTransformStatus: "verified",
                detections: detections,
                reviewedAnnotations: JSON.parse(JSON.stringify(detections)),
                reviewStatus: "pending",
                reviewedAtUtc: null,
                reviewerId: "Sensei",
                origBlob: null,
                prevBlob: null,
                origBase64: origBase64,
                prevBase64: prevBase64,
            };

            window.Reviewer.addRecord(record);
            if (window.Reviewer && typeof window.Reviewer.saveCurrentSessionToServer === 'function') {
                // Persist immediately to local disk so files survive sudden session close
                window.Reviewer.saveCurrentSessionToServer(true);
            }
            toast(`스냅샷 수집 및 로컬 보존 완료 (${vw}x${vh})`);
        } catch (err) {
            state.errorCount++;
            const errBox = $('cntError');
            if (errBox) errBox.innerText = state.errorCount;
            console.error("Snapshot capture error:", err);
            toast("스냅샷 수집 실패", true);
        }
    }

    // --- YOLO ZIP Export Modal & Download ---
    $('btnExportZip').onclick = () => {
        const records = window.Reviewer.records;
        const approved = records.filter(r => r.reviewStatus === 'approved');
        const pending = records.filter(r => r.reviewStatus === 'pending');
        const rejected = records.filter(r => r.reviewStatus === 'rejected');

        // Sessions count
        const sessions = new Set(approved.map(r => r.sessionId));

        // Coordinate space and boundary checks
        let hasUnverifiedCoordSpace = false;
        let hasOutOfBoundsBox = false;
        let totalObjects = 0;

        approved.forEach(r => {
            if (r.coordinateSpace === 'unknown' || r.coordinateTransformStatus === 'unverified') {
                hasUnverifiedCoordSpace = true;
            }
            const annotations = r.reviewedAnnotations || r.detections || [];
            annotations.forEach(a => {
                totalObjects++;
                const b = a.box;
                if (!b || b.x1 < 0 || b.y1 < 0 || b.x2 > r.originalWidth || b.y2 > r.originalHeight || b.x2 <= b.x1 || b.y2 <= b.y1) {
                    hasOutOfBoundsBox = true;
                }
            });
        });

        // Class distribution
        const classDist = {};
        approved.forEach(r => {
            (r.reviewedAnnotations || r.detections || []).forEach(a => {
                const name = a.className || `Class ${a.classId}`;
                classDist[name] = (classDist[name] || 0) + 1;
            });
        });

        // Approximate train/val split preview
        let trainImgCount = 0, valImgCount = 0;
        if (sessions.size > 1) {
            valImgCount = Math.max(1, Math.round(approved.length * 0.2));
            trainImgCount = approved.length - valImgCount;
        } else if (approved.length > 1) {
            trainImgCount = Math.max(1, Math.round(approved.length * 0.8));
            valImgCount = approved.length - trainImgCount;
        } else {
            trainImgCount = approved.length;
            valImgCount = 0;
        }

        const summaryBox = $('exportSummary');
        summaryBox.innerHTML = `
        <div class="bg-gray-50 border rounded p-3 space-y-2">
            <div class="flex justify-between font-bold">
                <span>승인 완료 (데이터셋 포함):</span>
                <span class="text-green-600">${approved.length}건 (객체 ${totalObjects}개)</span>
            </div>
            <div class="flex justify-between font-bold text-gray-500">
                <span>검수 대기 (제외):</span>
                <span>${pending.length}건</span>
            </div>
            <div class="flex justify-between font-bold text-gray-500">
                <span>반려/제외 (제외):</span>
                <span>${rejected.length}건</span>
            </div>
            <div class="border-t pt-1 flex justify-between font-bold">
                <span>예상 분할 (Train / Val):</span>
                <span>${trainImgCount}장 / ${valImgCount}장</span>
            </div>
            <div class="flex justify-between font-bold text-gray-600">
                <span>고유 세션 수:</span>
                <span>${sessions.size}개</span>
            </div>
        </div>

        ${hasUnverifiedCoordSpace ? `
        <div class="bg-red-50 border border-red-300 text-red-900 rounded p-2 text-[11px] font-bold">
            🚨 내보내기 차단: 좌표계가 검증되지 않은(unknown) 프레임이 승인 목록에 포함되어 있습니다. 검수 센터에서 제외 처리해야 합니다.
        </div>` : ''}

        ${hasOutOfBoundsBox ? `
        <div class="bg-red-50 border border-red-300 text-red-900 rounded p-2 text-[11px] font-bold">
            🚨 내보내기 차단: 프레임 경계를 벗어나거나 크기가 0 이하인 비정상 박스가 존재합니다. 검수 센터에서 수정 또는 제외하세요.
        </div>` : ''}

        ${sessions.size <= 1 && !hasUnverifiedCoordSpace && !hasOutOfBoundsBox ? `
        <div class="bg-amber-50 border border-amber-300 text-amber-900 rounded p-2 text-[11px] font-bold">
            ⚠️ 단일 세션 경고: 세션이 1개뿐입니다. 인접 프레임 유사성 누수를 방지하기 위해 시계열 블록 분할(80% Train, 20% Val)이 적용됩니다.
        </div>` : ''}

        ${sessions.size > 1 && !hasUnverifiedCoordSpace && !hasOutOfBoundsBox ? `
        <div class="bg-blue-50 border border-blue-200 text-blue-900 rounded p-2 text-[11px] font-bold">
            ✔ 다중 세션 분할: 세션 단위로 Train/Val이 완벽히 분리되어 프레임 누수가 방지됩니다.
        </div>` : ''}

        <div class="border rounded p-2">
            <div class="font-bold text-xs mb-1">클래스별 객체 수 (고정 매핑 보존):</div>
            <div class="space-y-0.5 text-[11px]">
                ${Object.keys(classDist).length ? Object.entries(classDist).map(([k, v]) => `
                    <div class="flex justify-between"><span>${k}</span><span class="font-bold">${v}개</span></div>
                `).join('') : '<div class="text-gray-400">승인된 객체 없음</div>'}
            </div>
        </div>
        `;

        if (approved.length === 0 || hasUnverifiedCoordSpace || hasOutOfBoundsBox) {
            $('btnConfirmExport').disabled = true;
            $('btnConfirmExport').classList.add('opacity-50', 'pointer-events-none');
        } else {
            $('btnConfirmExport').disabled = false;
            $('btnConfirmExport').classList.remove('opacity-50', 'pointer-events-none');
        }

        $('exportModal').classList.remove('hidden');
    };

    $('btnCloseExport').onclick = () => $('exportModal').classList.add('hidden');
    $('btnCancelExport').onclick = () => $('exportModal').classList.add('hidden');

    $('btnConfirmExport').onclick = async () => {
        // Ensure unsaved session changes are persisted to disk so exporter disk fallback succeeds
        if (window.Reviewer && typeof window.Reviewer.saveCurrentSessionToServer === 'function') {
            await window.Reviewer.saveCurrentSessionToServer(true);
        }
        const approved = window.Reviewer.records.filter(r => r.reviewStatus === 'approved');
        if (approved.length === 0) {
            toast("승인 완료된 프레임이 없습니다.", true);
            return;
        }

        $('btnConfirmExport').disabled = true;
        $('btnConfirmExport').querySelector('span').innerText = "ZIP 생성 중...";

        try {
            // Build payload for server-side exporter
            const imagesMap = {};
            approved.forEach(r => {
                if (r.origBase64) {
                    imagesMap[r.imageFile] = r.origBase64;
                }
            });

            const res = await fetch("/api/export-yolo", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    sessionId: (window.Reviewer && window.Reviewer.currentSessionId) || state.sessionId,
                    records: approved,
                    images: imagesMap,
                    classes: state.cls,
                })
            });

            if (!res.ok) {
                const errJson = await res.json().catch(() => ({}));
                throw new Error(errJson.detail?.message || `HTTP ${res.status}`);
            }

            const blob = await res.blob();
            const downloadUrl = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = downloadUrl;
            a.download = `schale_yolo_dataset_${Date.now()}.zip`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(downloadUrl);

            $('exportModal').classList.add('hidden');
            toast("YOLO 데이터셋 ZIP 다운로드 완료!");
        } catch (e) {
            alert(`ZIP 생성 실패: ${e.message}`);
        } finally {
            $('btnConfirmExport').disabled = false;
            $('btnConfirmExport').querySelector('span').innerText = "ZIP 다운로드 실행";
        }
    };

    // --- Dual Inference Engine Controls (Cloud & Local ONNX) ---
    function updateInferenceEngineBadge() {
        const badge = $('inferenceEngineBadge');
        if (!badge) return;
        if (state.useCloud && state.useOnnx) {
            badge.className = 'text-[10px] font-mono font-bold px-2 py-0.5 rounded bg-purple-100 dark:bg-purple-950/60 text-purple-700 dark:text-purple-300';
            badge.innerText = '⚡ 하이브리드 (Cloud + ONNX)';
        } else if (state.useCloud) {
            badge.className = 'text-[10px] font-mono font-bold px-2 py-0.5 rounded bg-sky-100 dark:bg-sky-950/60 text-sky-700 dark:text-sky-300';
            badge.innerText = '☁️ 클라우드 단독';
        } else if (state.useOnnx) {
            badge.className = 'text-[10px] font-mono font-bold px-2 py-0.5 rounded bg-emerald-100 dark:bg-emerald-950/60 text-emerald-700 dark:text-emerald-300';
            badge.innerText = '🧠 로컬 ONNX 단독';
        } else {
            badge.className = 'text-[10px] font-mono font-bold px-2 py-0.5 rounded bg-gray-200 dark:bg-gray-800 text-gray-600 dark:text-gray-400';
            badge.innerText = '⏸️ 추론 모두 꺼짐';
        }
    }

    async function refreshOnnxModels() {
        const sel = $('selOnnxModel');
        const txtStatus = $('txtOnnxStatus');
        if (!sel) return;
        try {
            const res = await fetch('/api/models/onnx');
            if (!res.ok) return;
            const data = await res.json();
            const models = data.models || [];
            const active = data.activeModel || state.onnxModel || '';

            sel.innerHTML = '<option value="">-- .onnx 가중치 선택 (models/) --</option>';
            models.forEach(m => {
                const opt = document.createElement('option');
                opt.value = m.name;
                opt.textContent = `${m.name} (${m.sizeMb} MB)`;
                sel.appendChild(opt);
            });

            if (active && models.some(m => m.name === active)) {
                sel.value = active;
                state.onnxModel = active;
                if (txtStatus) txtStatus.innerText = data.loaded ? `로드됨 (${active})` : `선택됨 (${active})`;
            } else {
                if (txtStatus) txtStatus.innerText = models.length === 0 ? '파일 없음' : '미선택';
            }
        } catch (err) {
            console.warn('[ONNX] Failed to list models:', err);
        }
    }

    async function loadSelectedOnnxModel(modelName) {
        const txtStatus = $('txtOnnxStatus');
        state.onnxModel = modelName || '';
        localStorage.setItem('schale_onnx_model', state.onnxModel);
        try {
            const res = await fetch('/api/models/onnx/load', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ modelName: modelName || null }),
            });
            const data = await res.json();
            if (!res.ok) {
                throw new Error(data.detail?.message || `HTTP ${res.status}`);
            }
            if (data.loaded) {
                if (txtStatus) txtStatus.innerText = `로드됨 (${data.activeModel})`;
                toast(`로컬 ONNX 모델 로드 완료: ${data.activeModel}`);
            } else {
                if (txtStatus) txtStatus.innerText = '미선택';
            }
        } catch (err) {
            if (txtStatus) txtStatus.innerText = '로드 실패';
            toast(`ONNX 모델 로드 실패: ${err.message}`, true);
        }
    }

    if ($('chkUseCloud')) {
        $('chkUseCloud').checked = state.useCloud;
        $('chkUseCloud').onchange = (e) => {
            state.useCloud = !!e.target.checked;
            localStorage.setItem('schale_use_cloud', String(state.useCloud));
            updateInferenceEngineBadge();
            toast(`클라우드 AI 호출: ${state.useCloud ? '켜짐 (ON)' : '꺼짐 (OFF)'}`);
        };
    }

    if ($('chkUseOnnx')) {
        $('chkUseOnnx').checked = state.useOnnx;
        $('chkUseOnnx').onchange = async (e) => {
            state.useOnnx = !!e.target.checked;
            localStorage.setItem('schale_use_onnx', String(state.useOnnx));
            updateInferenceEngineBadge();
            toast(`로컬 ONNX 가중치 실행: ${state.useOnnx ? '켜짐 (ON)' : '꺼짐 (OFF)'}`);
            if (state.useOnnx && state.onnxModel) {
                await loadSelectedOnnxModel(state.onnxModel);
            }
        };
    }

    if ($('selOnnxModel')) {
        $('selOnnxModel').onchange = (e) => loadSelectedOnnxModel(e.target.value);
    }
    if ($('btnRefreshOnnx')) {
        $('btnRefreshOnnx').onclick = () => refreshOnnxModels();
    }
    if ($('inpOnnxUpload')) {
        $('inpOnnxUpload').onchange = async (e) => {
            const file = e.target.files && e.target.files[0];
            if (!file) return;
            const fd = new FormData();
            fd.append('file', file);
            try {
                toast(`ONNX 가중치 업로드 중: ${file.name}...`);
                const res = await fetch('/api/models/onnx/upload', { method: 'POST', body: fd });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail?.message || `HTTP ${res.status}`);
                state.onnxModel = data.activeModel;
                localStorage.setItem('schale_onnx_model', state.onnxModel);
                await refreshOnnxModels();
                if ($('chkUseOnnx') && !state.useOnnx) {
                    $('chkUseOnnx').checked = true;
                    state.useOnnx = true;
                    localStorage.setItem('schale_use_onnx', 'true');
                    updateInferenceEngineBadge();
                }
                toast(`로컬 ONNX 업로드 및 활성화 완료: ${data.activeModel}`);
            } catch (err) {
                toast(`ONNX 업로드 실패: ${err.message}`, true);
            } finally {
                e.target.value = '';
            }
        };
    }

    updateInferenceEngineBadge();
    refreshOnnxModels();

    // Initial render and page lifecycle cleanup
    renderCls();
    if (typeof setCaptureMode === 'function') {
        setCaptureMode('wgc');
    }
    if (typeof refreshWgcWindows === 'function') {
        refreshWgcWindows();
    }
    if (typeof window.addEventListener === 'function') {
        window.addEventListener('pagehide', () => {
            if (window.Reviewer && typeof window.Reviewer.flushUnsavedRecordsOnExit === 'function') {
                window.Reviewer.flushUnsavedRecordsOnExit();
            }
        });
        window.addEventListener('beforeunload', stopOps);
    }
    if (typeof document !== 'undefined' && typeof document.addEventListener === 'function') {
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'hidden' && window.Reviewer && typeof window.Reviewer.flushUnsavedRecordsOnExit === 'function') {
                window.Reviewer.flushUnsavedRecordsOnExit();
            }
        });
    }
    document.body.onclick = () => {
        if (speechSynthesis.getVoices().length === 0) speechSynthesis.getVoices();
    };
})();
