/**
 * static/reviewer.js
 * SCHALE Human-in-the-Loop Data Review Center & Annotation Editor.
 * Kivotos Tactical Operations System (KTOS) Triage Engine:
 * - Canvas Pan/Zoom Transform Matrix with Cursor-Centric Inverse Coordinate Mapping
 * - Keyboard Triage Shortcuts ([A] Approve, [R] Reject, [P] Pending, [[]/[]] Nav, [1]-[8] Class, [0] Reset)
 * - Chamfered Optical Reticle Bounding Boxes & Monospaced Telemetry Badges
 */

(function (window) {
    'use strict';

    class ReviewManager {
        constructor() {
            this.records = []; // Array of snapshot candidate records
            this.currentRecordIndex = -1;
            this.filterStatus = 'all'; // 'all' | 'pending' | 'approved' | 'rejected'
            this.viewMode = 'edit'; // 'orig' | 'prev' | 'edit'
            this.selectedBoxIndex = -1;
            this.isAddingBox = false;
            this.isDragging = false;
            this.dragHandle = null; // 'move', 'nw', 'ne', 'se', 'sw', 'drawing'
            this.dragStart = { x: 0, y: 0 };
            this.activeBoxInitial = null;

            // KTOS Transform Matrix: pan and zoom view state
            this.view = { scale: 1.0, panX: 0, panY: 0 };
            this.isPanning = false;
            this.panStart = { x: 0, y: 0 };
            this.spacePressed = false;

            this.COLORS = ["#eab308", "#ef4444", "#3b82f6", "#a855f7", "#22c55e", "#f97316", "#06b6d4", "#ec4899"];

            this.canvas = document.getElementById('reviewCanvas');
            this.ctx = this.canvas ? this.canvas.getContext('2d') : null;
            this.imgElement = new Image();

            this.bindEvents();
        }

        bindEvents() {
            const $ = id => document.getElementById(id);

            // Filter Tabs
            $('tabAll').onclick = () => this.setFilter('all');
            $('tabPending').onclick = () => this.setFilter('pending');
            $('tabApproved').onclick = () => this.setFilter('approved');
            $('tabRejected').onclick = () => this.setFilter('rejected');

            // View Modes
            $('btnModeOrig').onclick = () => this.setViewMode('orig');
            $('btnModePrev').onclick = () => this.setViewMode('prev');
            $('btnModeEdit').onclick = () => this.setViewMode('edit');

            // Reset View (Zoom/Pan Reset to 100%)
            const btnResetView = $('btnResetView');
            if (btnResetView) {
                btnResetView.onclick = () => this.resetView();
            }

            // Review Decisions
            $('btnApprove').onclick = () => this.setDecision('approved');
            $('btnReject').onclick = () => this.setDecision('rejected');
            $('btnPending').onclick = () => this.setDecision('pending');

            // Box Editing
            $('btnDelBox').onclick = () => this.deleteSelectedBox();
            $('btnAddBox').onclick = () => this.toggleAddBoxMode();
            $('selBoxClass').onchange = (e) => this.updateSelectedBoxClass(e.target.value);

            // Modal Controls
            $('btnCloseReview').onclick = () => {
                $('reviewModal').classList.add('hidden');
                this.spacePressed = false;
                this.isPanning = false;
            };
            $('btnOpenReview').onclick = () => {
                $('reviewModal').classList.remove('hidden');
                this.renderList();
                if (this.currentRecordIndex >= 0) this.selectRecord(this.currentRecordIndex);
                else if (this.records.length > 0) this.selectRecord(0);
            };

            // Canvas Interaction & Wheel Zoom
            if (this.canvas) {
                this.canvas.addEventListener('wheel', (e) => this.handleCanvasWheel(e), { passive: false });
                this.canvas.onmousedown = (e) => this.handleCanvasMouseDown(e);
                this.canvas.onmousemove = (e) => this.handleCanvasMouseMove(e);
                this.canvas.onmouseup = () => this.handleCanvasMouseUp();
                this.canvas.onmouseleave = () => this.handleCanvasMouseUp();
                this.canvas.oncontextmenu = (e) => {
                    if (this.spacePressed || this.isPanning) e.preventDefault();
                };
            }

            // Full Keyboard Shortcut Triage Engine
            window.addEventListener('keydown', (e) => {
                const modal = $('reviewModal');
                if (!modal || modal.classList.contains('hidden')) return;

                // Don't intercept when actively typing in an input, textarea, or dropdown
                if (document.activeElement && ['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement.tagName)) {
                    return;
                }

                // Escape: cancel adding box or close modal
                if (e.key === 'Escape') {
                    e.preventDefault();
                    if (this.isAddingBox) {
                        this.toggleAddBoxMode();
                    } else {
                        $('reviewModal').classList.add('hidden');
                        this.spacePressed = false;
                        this.isPanning = false;
                    }
                    return;
                }

                // Space Key: Pan mode toggle
                if (e.code === 'Space' && !this.spacePressed) {
                    this.spacePressed = true;
                    if (this.canvas) this.canvas.style.cursor = 'grab';
                    e.preventDefault();
                    return;
                }

                // Delete / Backspace: Delete selected box
                if (e.key === 'Delete' || e.key === 'Backspace') {
                    e.preventDefault();
                    this.deleteSelectedBox();
                    return;
                }

                const k = e.key.toUpperCase();

                // [A] Approve
                if (k === 'A') {
                    e.preventDefault();
                    this.setDecision('approved');
                    return;
                }

                // [R] Reject
                if (k === 'R') {
                    e.preventDefault();
                    this.setDecision('rejected');
                    return;
                }

                // [P] Pending
                if (k === 'P') {
                    e.preventDefault();
                    this.setDecision('pending');
                    return;
                }

                // [ or ] Filter-Aware Frame Navigation
                if (e.key === '[') {
                    e.preventDefault();
                    this.navigateFrame(-1);
                    return;
                }
                if (e.key === ']') {
                    e.preventDefault();
                    this.navigateFrame(1);
                    return;
                }

                // [0] Reset zoom & pan to 100%
                if (e.key === '0') {
                    e.preventDefault();
                    this.resetView();
                    return;
                }

                // [1] - [8] Class selection
                if (/^[1-8]$/.test(e.key)) {
                    e.preventDefault();
                    const classIdx = parseInt(e.key, 10) - 1;
                    this.handleClassShortcut(classIdx);
                    return;
                }
            });

            window.addEventListener('keyup', (e) => {
                if (e.code === 'Space') {
                    this.spacePressed = false;
                    this.isPanning = false;
                    if (this.canvas) {
                        this.canvas.style.cursor = this.isAddingBox ? 'crosshair' : 'default';
                    }
                }
            });

            window.addEventListener('blur', () => {
                this.spacePressed = false;
                this.isPanning = false;
                if (this.canvas) {
                    this.canvas.style.cursor = this.isAddingBox ? 'crosshair' : 'default';
                }
            });
        }

        getFilteredIndices() {
            const list = [];
            for (let i = 0; i < this.records.length; i++) {
                if (this.filterStatus === 'all' || this.records[i].reviewStatus === this.filterStatus) {
                    list.push(i);
                }
            }
            return list;
        }

        navigateFrame(direction) {
            const filtered = this.getFilteredIndices();
            if (filtered.length === 0) return;
            const currentPos = filtered.indexOf(this.currentRecordIndex);
            if (currentPos === -1) {
                this.selectRecord(direction > 0 ? filtered[0] : filtered[filtered.length - 1]);
                return;
            }
            const nextPos = currentPos + direction;
            if (nextPos >= 0 && nextPos < filtered.length) {
                this.selectRecord(filtered[nextPos]);
            }
        }

        resetView() {
            this.view = { scale: 1.0, panX: 0, panY: 0 };
            this.drawCanvas();
        }

        handleClassShortcut(classIdx) {
            const sel = document.getElementById('selBoxClass');
            if (!sel || !sel.options || sel.options.length === 0) return;

            const validOptions = Array.from(sel.options).filter(opt => opt.value !== '__new__');
            if (classIdx >= validOptions.length) return;

            const targetOpt = validOptions[classIdx];
            sel.value = targetOpt.value;

            if (this.selectedBoxIndex >= 0) {
                this.updateSelectedBoxClass(targetOpt.value);
            }
        }

        handleCanvasWheel(e) {
            if (!this.canvas || !this.imgElement.complete) return;
            e.preventDefault();

            const rect = this.canvas.getBoundingClientRect();
            const scaleX = (rect.width > 0) ? (this.canvas.width / rect.width) : 1;
            const scaleY = (rect.height > 0) ? (this.canvas.height / rect.height) : 1;
            const viewScale = this.view.scale || 1.0;

            const cx = (e.clientX - rect.left) * scaleX;
            const cy = (e.clientY - rect.top) * scaleY;

            const zoomFactor = e.deltaY < 0 ? 1.15 : (1 / 1.15);
            const newScale = Math.max(0.2, Math.min(10.0, viewScale * zoomFactor));

            // Cursor-centric zoom: maintain image coordinate under mouse invariant
            const imgX = (cx - this.view.panX) / viewScale;
            const imgY = (cy - this.view.panY) / viewScale;

            this.view.scale = newScale;
            this.view.panX = cx - imgX * newScale;
            this.view.panY = cy - imgY * newScale;

            this.drawCanvas();
        }

        addRecord(record) {
            this.records.push(record);
            this.updateCounts();
            if (this.currentRecordIndex === -1) {
                this.currentRecordIndex = 0;
            }
            this.renderList();
        }

        clearRecords() {
            this.records = [];
            this.currentRecordIndex = -1;
            this.selectedBoxIndex = -1;
            this.resetView();
            this.updateCounts();
            this.renderList();
            if (this.ctx && this.canvas) {
                this.ctx.setTransform(1, 0, 0, 1, 0, 0);
                this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
            }
            document.getElementById('inspectorMeta').innerText = '선택된 프레임 없음';
        }

        updateCounts() {
            const counts = { total: this.records.length, pending: 0, approved: 0, rejected: 0 };
            this.records.forEach(r => {
                if (r.reviewStatus === 'approved') counts.approved++;
                else if (r.reviewStatus === 'rejected') counts.rejected++;
                else counts.pending++;
            });

            document.getElementById('cntTotal').innerText = counts.total;
            document.getElementById('cntPending').innerText = counts.pending;
            document.getElementById('cntApproved').innerText = counts.approved;
            document.getElementById('cntRejected').innerText = counts.rejected;
            const errBox = document.getElementById('cntError');
            if (errBox && window.AppState) errBox.innerText = window.AppState.errorCount || 0;

            document.getElementById('tabCountAll').innerText = counts.total;
            document.getElementById('tabCountPending').innerText = counts.pending;
            document.getElementById('tabCountApproved').innerText = counts.approved;
            document.getElementById('tabCountRejected').innerText = counts.rejected;
        }

        setFilter(status) {
            this.filterStatus = status;
            ['tabAll', 'tabPending', 'tabApproved', 'tabRejected'].forEach(id => {
                const btn = document.getElementById(id);
                btn.className = 'px-3 py-1 rounded text-xs font-bold bg-gray-200 dark:bg-gray-800 text-gray-700 dark:text-gray-300';
            });
            const activeId = status === 'all' ? 'tabAll' : status === 'pending' ? 'tabPending' : status === 'approved' ? 'tabApproved' : 'tabRejected';
            document.getElementById(activeId).className = 'px-3 py-1 rounded text-xs font-bold bg-[var(--p)] text-white';
            this.renderList();
        }

        renderList() {
            const listEl = document.getElementById('reviewList');
            const filtered = this.records
                .map((rec, idx) => ({ rec, idx }))
                .filter(item => this.filterStatus === 'all' || item.rec.reviewStatus === this.filterStatus);

            if (filtered.length === 0) {
                listEl.innerHTML = `<div class="text-center text-xs text-gray-400 py-10 font-bold">해당 조건의 프레임이 없습니다.</div>`;
                return;
            }

            listEl.innerHTML = filtered.map(({ rec, idx }) => {
                const isSelected = idx === this.currentRecordIndex;
                const statusColor = rec.reviewStatus === 'approved' ? 'bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300' :
                                    rec.reviewStatus === 'rejected' ? 'bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300' : 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300';
                const statusLabel = rec.reviewStatus === 'approved' ? '승인' : rec.reviewStatus === 'rejected' ? '제외' : '대기';
                const imgSrc = rec.prevBase64 || rec.origBase64;
                const timeStr = rec.capturedAtUtc ? rec.capturedAtUtc.split('T')[1].slice(0, 8) : '00:00:00';

                return `
                <div class="p-2 rounded border cursor-pointer transition ${isSelected ? 'border-[var(--p)] bg-blue-50 dark:bg-blue-950/40' : 'bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-700'}" onclick="window.Reviewer.selectRecord(${idx})">
                    <div class="flex gap-2">
                        <img src="${imgSrc}" class="w-20 h-14 object-cover rounded bg-black border border-gray-300 dark:border-gray-600">
                        <div class="flex-1 flex flex-col justify-between text-[11px]">
                            <div class="flex justify-between items-center">
                                <span class="font-mono font-bold text-gray-700 dark:text-gray-300">${timeStr}</span>
                                <span class="px-1.5 py-0.5 rounded text-[10px] font-bold ${statusColor}">${statusLabel}</span>
                            </div>
                            <div class="text-[10px] text-gray-500 dark:text-gray-400 font-bold font-mono">${rec.originalWidth}x${rec.originalHeight}</div>
                            <div class="text-[10px] text-blue-600 dark:text-blue-400 font-bold">객체: ${(rec.reviewedAnnotations || rec.detections).length}건</div>
                        </div>
                    </div>
                </div>`;
            }).join('');
        }

        selectRecord(idx) {
            if (idx < 0 || idx >= this.records.length) return;
            this.currentRecordIndex = idx;
            this.selectedBoxIndex = -1;
            this.isAddingBox = false;

            const rec = this.records[idx];
            document.getElementById('inspectorMeta').innerText = 
                `[${rec.sessionId}] ${rec.originalWidth}x${rec.originalHeight} | ${rec.capturedAtUtc} | Space: ${rec.coordinateSpace}`;

            // Initialize reviewedAnnotations if not present
            if (!rec.reviewedAnnotations) {
                rec.reviewedAnnotations = JSON.parse(JSON.stringify(rec.detections || []));
            }

            this.resetView();
            this.updateClassSelectDropdown();
            this.loadAndRenderCanvas();
            this.renderList();
        }

        updateClassSelectDropdown() {
            const sel = document.getElementById('selBoxClass');
            const rec = this.records[this.currentRecordIndex];
            if (!rec) return;

            const knownClasses = {};
            const appCls = window.AppState ? window.AppState.cls : {};
            Object.keys(appCls).forEach(k => {
                knownClasses[parseInt(k, 10)] = appCls[k].name || ('Class ' + k);
            });

            const annotations = rec.reviewedAnnotations || rec.detections || [];
            annotations.forEach(a => {
                const cid = parseInt(a.classId || 0, 10);
                if (!knownClasses[cid]) {
                    knownClasses[cid] = a.className || ('Class ' + cid);
                }
            });

            const cids = Object.keys(knownClasses).map(k => parseInt(k, 10)).sort((a, b) => a - b);
            if (cids.length === 0) {
                knownClasses[0] = 'Class 0';
                cids.push(0);
            }

            let html = cids.map(cid => `<option value="${cid}">${knownClasses[cid]}</option>`).join('');
            html += '<option value="__new__">+ 새 클래스 등록...</option>';
            sel.innerHTML = html;
        }

        setViewMode(mode) {
            this.viewMode = mode;
            ['btnModeOrig', 'btnModePrev', 'btnModeEdit'].forEach(id => {
                document.getElementById(id).className = 'px-2.5 py-0.5 rounded bg-gray-200 dark:bg-gray-800 text-gray-700 dark:text-gray-300 font-bold text-xs';
            });
            const activeId = mode === 'orig' ? 'btnModeOrig' : mode === 'prev' ? 'btnModePrev' : 'btnModeEdit';
            document.getElementById(activeId).className = 'px-2.5 py-0.5 rounded bg-[var(--p)] text-white font-bold text-xs';
            this.loadAndRenderCanvas();
        }

        loadAndRenderCanvas() {
            const rec = this.records[this.currentRecordIndex];
            if (!rec) return;

            const imgSrc = (this.viewMode === 'prev' && rec.prevBase64) ? rec.prevBase64 : rec.origBase64;
            this.imgElement.onload = () => {
                this.canvas.width = rec.originalWidth;
                this.canvas.height = rec.originalHeight;
                this.drawCanvas();
            };
            this.imgElement.src = imgSrc;
        }

        drawTacticalBox(x, y, w, h, opt) {
            const { className, confidence, width, height, color, isSelected = false } = opt;
            const ctx = this.ctx;
            ctx.save();

            // 1. Box fill
            ctx.fillStyle = isSelected ? 'rgba(255, 255, 255, 0.16)' : 'rgba(0, 0, 0, 0.08)';
            ctx.fillRect(x, y, w, h);

            // 2. Base perimeter stroke
            ctx.strokeStyle = isSelected ? '#ffffff' : color;
            ctx.lineWidth = (isSelected ? 3 : 2) / this.view.scale;
            ctx.strokeRect(x, y, w, h);

            // 3. Chamfered Tactical Corner Marks
            const clen = Math.min(w / 3, h / 3, Math.max(14, Math.round(this.canvas.width / 50))) / this.view.scale;
            const ch = Math.min(clen / 2, Math.max(3, Math.round(this.canvas.width / 160))) / this.view.scale;
            ctx.strokeStyle = isSelected ? '#ffffff' : color;
            ctx.lineWidth = Math.max(2.5, 4 / this.view.scale);
            ctx.lineCap = 'square';
            ctx.lineJoin = 'miter';

            // Top-Left (Chamfered)
            ctx.beginPath();
            ctx.moveTo(x, y + clen);
            ctx.lineTo(x, y + ch);
            ctx.lineTo(x + ch, y);
            ctx.lineTo(x + clen, y);
            ctx.stroke();

            // Top-Right (Chamfered)
            ctx.beginPath();
            ctx.moveTo(x + w - clen, y);
            ctx.lineTo(x + w - ch, y);
            ctx.lineTo(x + w, y + ch);
            ctx.lineTo(x + w, y + clen);
            ctx.stroke();

            // Bottom-Right (Chamfered)
            ctx.beginPath();
            ctx.moveTo(x + w, y + h - clen);
            ctx.lineTo(x + w, y + h - ch);
            ctx.lineTo(x + w - ch, y + h);
            ctx.lineTo(x + w - clen, y + h);
            ctx.stroke();

            // Bottom-Left (Chamfered)
            ctx.beginPath();
            ctx.moveTo(x + clen, y + h);
            ctx.lineTo(x + ch, y + h);
            ctx.lineTo(x, y + h - ch);
            ctx.lineTo(x, y + h - clen);
            ctx.stroke();

            // 4. Monospaced Telemetry Badge: `${className} [${conf}%] ${w}x${h}`
            const confStr = (typeof confidence === 'number') ? `[${Math.round(confidence * 100)}%]` : '';
            const telemetryText = `${className} ${confStr ? confStr + ' ' : ''}${width}x${height}`.trim();

            const fontSize = Math.max(12, Math.round(this.canvas.width / 75)) / this.view.scale;
            ctx.font = `600 ${fontSize}px 'JetBrains Mono', 'Space Grotesk', monospace`;
            const textMetrics = ctx.measureText(telemetryText);

            const padX = 6 / this.view.scale;
            const padY = 3 / this.view.scale;
            const accentW = 4 / this.view.scale;
            const badgeW = textMetrics.width + padX * 2 + accentW;
            const badgeH = fontSize + padY * 2;
            const badgeY = (y - badgeH - (4 / this.view.scale) < 0) ? y + (4 / this.view.scale) : y - badgeH - (4 / this.view.scale);
            const badgeX = x;

            // Dark high-contrast badge background
            ctx.fillStyle = 'rgba(11, 15, 25, 0.90)';
            ctx.fillRect(badgeX, badgeY, badgeW, badgeH);

            // Left tactical color stripe
            ctx.fillStyle = color;
            ctx.fillRect(badgeX, badgeY, accentW, badgeH);

            // Subtle badge border
            ctx.strokeStyle = isSelected ? '#ffffff' : 'rgba(255, 255, 255, 0.25)';
            ctx.lineWidth = 1 / this.view.scale;
            ctx.strokeRect(badgeX, badgeY, badgeW, badgeH);

            // Telemetry Text
            ctx.fillStyle = '#ffffff';
            ctx.textBaseline = 'middle';
            ctx.fillText(telemetryText, badgeX + padX + accentW, badgeY + badgeH / 2);

            ctx.restore();
        }

        drawHandle(x, y) {
            const baseSize = Math.max(12, Math.round(this.canvas.width / 130));
            const size = baseSize / this.view.scale;
            this.ctx.fillStyle = '#ffffff';
            this.ctx.strokeStyle = '#000000';
            this.ctx.lineWidth = Math.max(1.5, size / 5);
            this.ctx.fillRect(x - size / 2, y - size / 2, size, size);
            this.ctx.strokeRect(x - size / 2, y - size / 2, size, size);
        }

        drawCanvas() {
            if (!this.ctx || !this.imgElement.complete) return;
            const rec = this.records[this.currentRecordIndex];
            if (!rec) return;

            // Reset transform to identity before clear
            this.ctx.setTransform(1, 0, 0, 1, 0, 0);
            this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);

            // Apply KTOS Pan & Zoom Transform Matrix
            this.ctx.translate(this.view.panX, this.view.panY);
            this.ctx.scale(this.view.scale, this.view.scale);

            // Draw image at (0, 0) in full resolution
            this.ctx.drawImage(this.imgElement, 0, 0);

            if (this.viewMode === 'orig') {
                this.updateZoomBadge();
                return; // clean original frame, no boxes drawn
            }

            if (this.viewMode === 'edit') {
                const boxes = rec.reviewedAnnotations || [];
                boxes.forEach((ann, i) => {
                    const isSel = i === this.selectedBoxIndex;
                    const b = ann.box;
                    const color = this.COLORS[(ann.classId || 0) % this.COLORS.length];
                    const bw = b.x2 - b.x1;
                    const bh = b.y2 - b.y1;

                    // Draw tactical chamfered bounding box with telemetry badge
                    this.drawTacticalBox(b.x1, b.y1, bw, bh, {
                        className: ann.className || ('Class ' + ann.classId),
                        confidence: ann.confidence,
                        width: Math.round(bw),
                        height: Math.round(bh),
                        color: color,
                        isSelected: isSel
                    });

                    // Draw interactive corner handles when selected
                    if (isSel) {
                        this.drawHandle(b.x1, b.y1);
                        this.drawHandle(b.x2, b.y1);
                        this.drawHandle(b.x2, b.y2);
                        this.drawHandle(b.x1, b.y2);
                    }
                });
            }

            this.updateZoomBadge();
        }

        updateZoomBadge() {
            const zoomBadge = document.getElementById('btnResetView');
            if (zoomBadge) {
                zoomBadge.innerText = `${Math.round(this.view.scale * 100)}% ⟲`;
            }
        }

        getCanvasCoordinates(e) {
            const rect = this.canvas.getBoundingClientRect();
            const scaleX = (rect.width > 0) ? (this.canvas.width / rect.width) : 1;
            const scaleY = (rect.height > 0) ? (this.canvas.height / rect.height) : 1;
            const viewScale = this.view.scale || 1.0;

            // Un-transformed coordinate on the canvas surface
            const cx = (e.clientX - rect.left) * scaleX;
            const cy = (e.clientY - rect.top) * scaleY;

            // Inverse transform mapping to original image pixel coordinates
            return {
                x: (cx - this.view.panX) / viewScale,
                y: (cy - this.view.panY) / viewScale,
            };
        }

        handleCanvasMouseDown(e) {
            if (this.viewMode !== 'edit') return;
            const rec = this.records[this.currentRecordIndex];
            if (!rec) return;

            // Middle click or Space+click triggers pan
            if (e.button === 1 || this.spacePressed) {
                this.isPanning = true;
                this.panStart = { x: e.clientX, y: e.clientY };
                if (this.canvas) this.canvas.style.cursor = 'grabbing';
                e.preventDefault();
                return;
            }

            if (e.button !== 0) return; // Only primary button for box interactions

            const pos = this.getCanvasCoordinates(e);
            this.dragStart = pos;

            if (this.isAddingBox) {
                // Begin drawing new box from click origin
                this.boxOrigin = { x: pos.x, y: pos.y };
                const sel = document.getElementById('selBoxClass');
                const selectedClassId = sel ? (parseInt(sel.value, 10) || 0) : 0;
                const selectedClassName = (sel && sel.selectedIndex >= 0 && sel.options[sel.selectedIndex])
                    ? sel.options[sel.selectedIndex].text
                    : ('Class ' + selectedClassId);
                const newAnn = {
                    classId: selectedClassId,
                    className: selectedClassName,
                    confidence: 1.0,
                    box: { x1: pos.x, y1: pos.y, x2: pos.x + 1, y2: pos.y + 1, width: 1, height: 1 }
                };
                rec.reviewedAnnotations.push(newAnn);
                this.selectedBoxIndex = rec.reviewedAnnotations.length - 1;
                this.dragHandle = 'drawing';
                this.isDragging = true;
                this.drawCanvas();
                return;
            }

            // Check if clicking existing handle or box
            const boxes = rec.reviewedAnnotations || [];
            let hitIndex = -1;
            let handleType = 'move';
            const handleRadius = Math.max(18, Math.round(this.canvas.width / 80)) / this.view.scale;

            // First check selected box handles
            if (this.selectedBoxIndex >= 0 && this.selectedBoxIndex < boxes.length) {
                const b = boxes[this.selectedBoxIndex].box;
                if (Math.hypot(pos.x - b.x1, pos.y - b.y1) < handleRadius) handleType = 'nw';
                else if (Math.hypot(pos.x - b.x2, pos.y - b.y1) < handleRadius) handleType = 'ne';
                else if (Math.hypot(pos.x - b.x2, pos.y - b.y2) < handleRadius) handleType = 'se';
                else if (Math.hypot(pos.x - b.x1, pos.y - b.y2) < handleRadius) handleType = 'sw';
                else if (pos.x >= b.x1 && pos.x <= b.x2 && pos.y >= b.y1 && pos.y <= b.y2) handleType = 'move';
                else handleType = null;

                if (handleType) {
                    hitIndex = this.selectedBoxIndex;
                }
            }

            if (hitIndex === -1) {
                // Check all boxes
                for (let i = boxes.length - 1; i >= 0; i--) {
                    const b = boxes[i].box;
                    if (pos.x >= b.x1 && pos.x <= b.x2 && pos.y >= b.y1 && pos.y <= b.y2) {
                        hitIndex = i;
                        handleType = 'move';
                        break;
                    }
                }
            }

            this.selectedBoxIndex = hitIndex;
            if (hitIndex >= 0) {
                this.isDragging = true;
                this.dragHandle = handleType;
                this.activeBoxInitial = JSON.parse(JSON.stringify(boxes[hitIndex].box));
                document.getElementById('selBoxClass').value = boxes[hitIndex].classId || 0;
            }
            this.drawCanvas();
        }

        handleCanvasMouseMove(e) {
            if (this.isPanning) {
                const rect = this.canvas.getBoundingClientRect();
                const scaleX = this.canvas.width / rect.width;
                const scaleY = this.canvas.height / rect.height;
                const dx = (e.clientX - this.panStart.x) * scaleX;
                const dy = (e.clientY - this.panStart.y) * scaleY;
                this.view.panX += dx;
                this.view.panY += dy;
                this.panStart = { x: e.clientX, y: e.clientY };
                this.drawCanvas();
                return;
            }

            if (!this.isDragging || this.selectedBoxIndex < 0) return;
            const rec = this.records[this.currentRecordIndex];
            if (!rec) return;

            const pos = this.getCanvasCoordinates(e);
            const b = rec.reviewedAnnotations[this.selectedBoxIndex].box;

            if (this.dragHandle === 'drawing' && this.boxOrigin) {
                b.x1 = Math.round(Math.max(0, Math.min(this.boxOrigin.x, pos.x)));
                b.y1 = Math.round(Math.max(0, Math.min(this.boxOrigin.y, pos.y)));
                b.x2 = Math.round(Math.min(this.canvas.width, Math.max(this.boxOrigin.x, pos.x)));
                b.y2 = Math.round(Math.min(this.canvas.height, Math.max(this.boxOrigin.y, pos.y)));
                b.width = b.x2 - b.x1;
                b.height = b.y2 - b.y1;
            } else if (this.dragHandle === 'move' && this.activeBoxInitial) {
                const dx = pos.x - this.dragStart.x;
                const dy = pos.y - this.dragStart.y;
                const init = this.activeBoxInitial;
                const w = init.x2 - init.x1;
                const h = init.y2 - init.y1;
                b.x1 = Math.round(Math.max(0, Math.min(this.canvas.width - w, init.x1 + dx)));
                b.y1 = Math.round(Math.max(0, Math.min(this.canvas.height - h, init.y1 + dy)));
                b.x2 = b.x1 + w;
                b.y2 = b.y1 + h;
                b.width = w;
                b.height = h;
            } else if (this.dragHandle === 'se') {
                b.x2 = Math.round(Math.max(b.x1 + 5, Math.min(this.canvas.width, pos.x)));
                b.y2 = Math.round(Math.max(b.y1 + 5, Math.min(this.canvas.height, pos.y)));
                b.width = b.x2 - b.x1;
                b.height = b.y2 - b.y1;
            } else if (this.dragHandle === 'nw') {
                b.x1 = Math.round(Math.min(b.x2 - 5, Math.max(0, pos.x)));
                b.y1 = Math.round(Math.min(b.y2 - 5, Math.max(0, pos.y)));
                b.width = b.x2 - b.x1;
                b.height = b.y2 - b.y1;
            } else if (this.dragHandle === 'ne') {
                b.x2 = Math.round(Math.max(b.x1 + 5, Math.min(this.canvas.width, pos.x)));
                b.y1 = Math.round(Math.min(b.y2 - 5, Math.max(0, pos.y)));
                b.width = b.x2 - b.x1;
                b.height = b.y2 - b.y1;
            } else if (this.dragHandle === 'sw') {
                b.x1 = Math.round(Math.min(b.x2 - 5, Math.max(0, pos.x)));
                b.y2 = Math.round(Math.max(b.y1 + 5, Math.min(this.canvas.height, pos.y)));
                b.width = b.x2 - b.x1;
                b.height = b.y2 - b.y1;
            }

            this.drawCanvas();
        }

        handleCanvasMouseUp() {
            if (this.isPanning) {
                this.isPanning = false;
                if (this.canvas) {
                    this.canvas.style.cursor = this.spacePressed ? 'grab' : (this.isAddingBox ? 'crosshair' : 'default');
                }
                return;
            }

            const rec = this.records[this.currentRecordIndex];

            if (this.isAddingBox) {
                this.isAddingBox = false;
                document.getElementById('btnAddBox').className = 'bg-blue-100 dark:bg-sky-950/60 text-blue-700 dark:text-sky-300 hover:bg-blue-200 dark:hover:bg-sky-900 px-2 py-1 rounded text-xs transition';
                if (rec && this.selectedBoxIndex >= 0) {
                    const b = rec.reviewedAnnotations[this.selectedBoxIndex].box;
                    if (b.width < 5 || b.height < 5) {
                        const origX = this.boxOrigin ? this.boxOrigin.x : b.x1;
                        const origY = this.boxOrigin ? this.boxOrigin.y : b.y1;
                        b.x1 = Math.max(0, Math.round(origX - 40));
                        b.y1 = Math.max(0, Math.round(origY - 40));
                        b.x2 = Math.min(this.canvas.width, b.x1 + 80);
                        b.y2 = Math.min(this.canvas.height, b.y1 + 80);
                        b.width = b.x2 - b.x1;
                        b.height = b.y2 - b.y1;
                    }

                    // AOP Aspect 5: Audit Trail box edit logging
                    if (typeof window !== 'undefined' && window.SchaleAOP && window.SchaleAOP.AuditTrailAspect) {
                        window.SchaleAOP.AuditTrailAspect.recordBoxEdit(rec, 'ADD_BOX', {
                            index: this.selectedBoxIndex,
                            box: b
                        });
                    }
                }
                this.boxOrigin = null;
            } else if (this.isDragging && this.selectedBoxIndex >= 0 && rec && rec.reviewedAnnotations) {
                const b = rec.reviewedAnnotations[this.selectedBoxIndex]?.box;
                if (b && typeof window !== 'undefined' && window.SchaleAOP && window.SchaleAOP.AuditTrailAspect) {
                    const actionName = this.dragHandle === 'move' ? 'MOVE_BOX' : 'RESIZE_BOX';
                    window.SchaleAOP.AuditTrailAspect.recordBoxEdit(rec, actionName, {
                        index: this.selectedBoxIndex,
                        box: b
                    });
                }
            }

            this.isDragging = false;
            this.dragHandle = null;
            this.activeBoxInitial = null;
            this.drawCanvas();
        }

        deleteSelectedBox() {
            const rec = this.records[this.currentRecordIndex];
            if (!rec || this.selectedBoxIndex < 0) return;

            // AOP Aspect 5: Audit Trail box deletion logging
            if (typeof window !== 'undefined' && window.SchaleAOP && window.SchaleAOP.AuditTrailAspect) {
                window.SchaleAOP.AuditTrailAspect.recordBoxEdit(rec, 'DELETE_BOX', {
                    index: this.selectedBoxIndex,
                    box: rec.reviewedAnnotations[this.selectedBoxIndex]?.box
                });
            }

            rec.reviewedAnnotations.splice(this.selectedBoxIndex, 1);
            this.selectedBoxIndex = -1;
            this.drawCanvas();
            this.renderList();
        }

        toggleAddBoxMode() {
            this.isAddingBox = !this.isAddingBox;
            const btn = document.getElementById('btnAddBox');
            btn.className = this.isAddingBox 
                ? 'bg-blue-600 text-white px-2 py-1 rounded text-xs font-bold animate-pulse'
                : 'bg-blue-100 dark:bg-sky-950/60 text-blue-700 dark:text-sky-300 hover:bg-blue-200 dark:hover:bg-sky-900 px-2 py-1 rounded text-xs transition';
        }

        updateSelectedBoxClass(classId) {
            const rec = this.records[this.currentRecordIndex];
            if (!rec || this.selectedBoxIndex < 0) return;

            const sel = document.getElementById('selBoxClass');
            if (classId === '__new__') {
                const newIdStr = prompt('새 클래스 ID를 입력하세요 (0 이상의 정수):');
                if (newIdStr === null) {
                    this.updateClassSelectDropdown();
                    return;
                }
                const newId = parseInt(newIdStr, 10);
                if (isNaN(newId) || newId < 0) {
                    alert('유효하지 않은 클래스 ID입니다. 0 이상의 정수를 입력해야 합니다.');
                    this.updateClassSelectDropdown();
                    return;
                }
                const newName = prompt('새 클래스 이름을 입력하세요:', `Class_${newId}`) || `Class_${newId}`;
                if (window.AppState) {
                    window.AppState.cls[newId] = { name: newName, on: true, alt: true, cnf: 0.25, count: 0 };
                    localStorage.setItem('schale_cls_v3', JSON.stringify(window.AppState.cls));
                }
                this.updateClassSelectDropdown();
                sel.value = String(newId);
                classId = String(newId);
            }

            const ann = rec.reviewedAnnotations[this.selectedBoxIndex];
            const oldClassId = ann.classId;
            ann.classId = parseInt(classId, 10);
            ann.className = (sel && sel.selectedIndex >= 0 && sel.options[sel.selectedIndex])
                ? sel.options[sel.selectedIndex].text
                : ('Class ' + classId);

            // AOP Aspect 5: Audit Trail class update logging
            if (typeof window !== 'undefined' && window.SchaleAOP && window.SchaleAOP.AuditTrailAspect) {
                window.SchaleAOP.AuditTrailAspect.recordBoxEdit(rec, 'UPDATE_CLASS', {
                    index: this.selectedBoxIndex,
                    previousClassId: oldClassId,
                    newClassId: ann.classId
                });
            }

            this.drawCanvas();
        }

        setDecision(status) {
            const rec = this.records[this.currentRecordIndex];
            if (!rec) return;

            if (status === 'approved') {
                // AOP Aspect 4: Boundary & Coordinate Invariant Gate
                if (typeof window !== 'undefined' && window.SchaleAOP && window.SchaleAOP.ReviewGateAspect) {
                    const check = window.SchaleAOP.ReviewGateAspect.validateRecordForApproval(rec);
                    if (!check.valid) {
                        alert(`승인 불가: ${check.error}`);
                        return;
                    }
                } else {
                    // Fallback inline verification
                    if (rec.coordinateSpace === 'unknown' || rec.coordinateTransformStatus === 'unverified') {
                        alert('승인 불가: 좌표계가 검증되지 않았거나(unknown) 변환에 실패한 프레임은 학습용 라벨로 승인할 수 없습니다.');
                        return;
                    }
                    const boxes = rec.reviewedAnnotations || [];
                    for (let i = 0; i < boxes.length; i++) {
                        const b = boxes[i].box;
                        if (b.x2 <= b.x1 || b.y2 <= b.y1 || b.x1 < 0 || b.y1 < 0 || b.x2 > rec.originalWidth || b.y2 > rec.originalHeight) {
                            alert(`승인 불가: 박스 [${i + 1}]의 좌표가 비정상입니다. (x1:${b.x1}, y1:${b.y1}, x2:${b.x2}, y2:${b.y2})`);
                            return;
                        }
                        if (typeof boxes[i].classId !== 'number' || boxes[i].classId < 0) {
                            alert(`승인 불가: 박스 [${i + 1}]의 클래스 ID(${boxes[i].classId})가 비정상입니다.`);
                            return;
                        }
                    }
                }
            }

            // AOP Aspect 5: Audit Trail Record Update
            if (typeof window !== 'undefined' && window.SchaleAOP && window.SchaleAOP.AuditTrailAspect) {
                window.SchaleAOP.AuditTrailAspect.recordDecision(rec, status);
            } else {
                rec.reviewStatus = status;
                rec.reviewedAtUtc = new Date().toISOString();
            }

            this.updateCounts();
            this.renderList();

            // Auto-advance to next record matching current filter
            const filtered = this.getFilteredIndices();
            if (filtered.length > 0) {
                if (this.filterStatus === 'all') {
                    if (this.currentRecordIndex + 1 < this.records.length) {
                        this.selectRecord(this.currentRecordIndex + 1);
                    }
                } else {
                    const nextCandidate = filtered.find(idx => idx >= this.currentRecordIndex) ?? filtered[filtered.length - 1];
                    this.selectRecord(nextCandidate);
                }
            } else {
                this.currentRecordIndex = -1;
                this.selectedBoxIndex = -1;
                this.resetView();
                if (this.ctx && this.canvas) {
                    this.ctx.setTransform(1, 0, 0, 1, 0, 0);
                    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
                }
                const meta = document.getElementById('inspectorMeta');
                if (meta) meta.innerText = '해당 조건의 프레임이 없습니다.';
            }
        }
    }

    window.ReviewManager = ReviewManager;
})(typeof window !== 'undefined' ? window : this);
