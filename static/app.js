/**
 * static/app.js
 * SCHALE Tactical Tracking - Main Application Logic
 * Integrates stream capture, aspect-preserving letterbox inference,
 * snapshot collection, and human review verification.
 */

(function () {
    'use strict';

    const $ = id => document.getElementById(id);
    const toast = (msg, err) => {
        const d = document.createElement('div');
        d.className = `toast ${err ? 'border-red-500 text-red-600' : 'text-gray-800'}`;
        d.innerHTML = msg;
        $('toastBox').appendChild(d);
        setTimeout(() => d.remove(), 3500);
    };

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
    $('iFrame').oninput = e => $('vFrame').innerText = e.target.value;
    $('iCooldown').oninput = e => $('vCooldown').innerText = Number(e.target.value).toFixed(1) + 's';
    $('chkHideVid').onchange = e => { $('vid').style.opacity = e.target.checked ? '0' : '1'; };

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

    $('btnNewSession').onclick = () => {
        state.sessionId = generateSessionId();
        $('txtSessionId').innerText = state.sessionId;
        toast(`새 세션 시작: ${state.sessionId}`);
    };

    $('btnClearSession').onclick = () => {
        if (confirm("현재 세션에 수집된 프레임을 모두 버리시겠습니까?")) {
            window.Reviewer.clearRecords();
            state.sessionId = generateSessionId();
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

    // --- Screen Share / Operation Control ---
    $('btnShare').onclick = async () => {
        if (!state.stream) {
            try {
                state.stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
                $('vid').srcObject = state.stream;
                $('overlayStandby').style.display = 'none';
                $('btnShare').querySelector('span').innerText = "연결 해제";
                $('btnStart').classList.remove('hidden');

                const track = state.stream.getVideoTracks()[0];
                track.onended = stopOps;

                $('vid').onloadedmetadata = () => {
                    const vw = $('vid').videoWidth;
                    const vh = $('vid').videoHeight;
                    $('resolutionInfo').innerText = `해상도: ${vw}x${vh} (원본 유지)`;
                };
            } catch (e) {
                toast("화면 공유 취소됨", true);
            }
        } else {
            state.stream.getTracks().forEach(t => t.stop());
            stopOps();
        }
    };

    const stopOps = () => {
        state.isRunning = false;
        state.isInferenceInFlight = false;
        clearTimeout(state.timer);
        if (state.abortController) {
            state.abortController.abort();
            state.abortController = null;
        }

        if (state.stream) {
            state.stream.getTracks().forEach(t => t.stop());
            state.stream = null;
        }

        $('vid').srcObject = null;
        $('overlayStandby').style.display = 'flex';
        $('btnShare').querySelector('span').innerText = "화면 공유";
        $('btnStart').classList.add('hidden');
        $('btnStop').classList.add('hidden');
        $('statusTxt').innerText = "연결이 해제되었습니다.";
        $('resolutionInfo').innerText = "해상도: 대기 중";
        $('videoWrap').classList.remove('alert');
        $('alertBanner').style.opacity = '0';

        const cvs = $('cvs');
        cvs.getContext('2d').clearRect(0, 0, cvs.width, cvs.height);
    };

    $('btnStart').onclick = () => {
        if (!state.stream) {
            toast("화면 공유를 먼저 시작하세요.", true);
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
        cvs.getContext('2d').clearRect(0, 0, cvs.width, cvs.height);
    };

    // --- Inference & Drawing Loop ---
    const loop = async () => {
        if (!state.isRunning || !state.stream) return;
        if (state.isInferenceInFlight) return;

        const vw = $('vid').videoWidth;
        const vh = $('vid').videoHeight;

        if (!vw || !vh) {
            $('statusTxt').innerHTML = '<span class="text-amber-600">🟡 비디오 메타데이터 준비 대기 중...</span>';
            if (state.isRunning) {
            state.timer = setTimeout(loop, 200);
        }
            return;
        }

        state.isInferenceInFlight = true;

        // 1. Calculate aspect-preserving letterbox for inference
        const letterbox = window.CoordinateAdapter.calculateLetterbox(vw, vh, NET_SIZE, NET_SIZE);

        // Draw letterbox onto 640x640 canvas (neutral padding)
        pCtx.fillStyle = '#727272';
        pCtx.fillRect(0, 0, NET_SIZE, NET_SIZE);
        pCtx.drawImage($('vid'), letterbox.padX, letterbox.padY, letterbox.scaledW, letterbox.scaledH);

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
            origCtx.drawImage($('vid'), 0, 0, vw, vh);
            const origBlob = await new Promise(r => state.origCvs.toBlob(r, 'image/jpeg', 0.95));
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

            const prevBlob = await new Promise(r => state.prevCvs.toBlob(r, 'image/jpeg', 0.85));
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
                origBlob: origBlob,
                prevBlob: prevBlob,
                origBase64: origBase64,
                prevBase64: prevBase64,
            };

            window.Reviewer.addRecord(record);
            toast(`스냅샷 수집 완료 (${vw}x${vh})`);
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
                imagesMap[r.imageFile] = r.origBase64;
            });

            const res = await fetch("/api/export-yolo", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
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

    // Initial render and page lifecycle cleanup
    renderCls();
    window.addEventListener('beforeunload', stopOps);
    document.body.onclick = () => {
        if (speechSynthesis.getVoices().length === 0) speechSynthesis.getVoices();
    };
})();
