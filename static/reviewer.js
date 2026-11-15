/**
 * static/reviewer.js
 * SCHALE Human-in-the-Loop Data Review Center & Annotation Editor.
 * Allows visual inspection, interactive box adjustment, and approval/rejection gates.
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
            this.dragHandle = null; // 'move' or 'nw', 'ne', 'se', 'sw'
            this.dragStart = { x: 0, y: 0 };
            this.activeBoxInitial = null;

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

            // Review Decisions
            $('btnApprove').onclick = () => this.setDecision('approved');
            $('btnReject').onclick = () => this.setDecision('rejected');
            $('btnPending').onclick = () => this.setDecision('pending');

            // Box Editing
            $('btnDelBox').onclick = () => this.deleteSelectedBox();
            $('btnAddBox').onclick = () => this.toggleAddBoxMode();
            $('selBoxClass').onchange = (e) => this.updateSelectedBoxClass(e.target.value);

            // Modal Controls
            $('btnCloseReview').onclick = () => $('reviewModal').classList.add('hidden');
            $('btnOpenReview').onclick = () => {
                $('reviewModal').classList.remove('hidden');
                this.renderList();
                if (this.currentRecordIndex >= 0) this.selectRecord(this.currentRecordIndex);
                else if (this.records.length > 0) this.selectRecord(0);
            };

            // Canvas Interaction
            if (this.canvas) {
                this.canvas.onmousedown = (e) => this.handleCanvasMouseDown(e);
                this.canvas.onmousemove = (e) => this.handleCanvasMouseMove(e);
                this.canvas.onmouseup = () => this.handleCanvasMouseUp();
                this.canvas.onmouseleave = () => this.handleCanvasMouseUp();
            }

            // Keyboard shortcut: Delete or Backspace removes selected box
            window.addEventListener('keydown', (e) => {
                if (e.key === 'Delete' || e.key === 'Backspace') {
                    const modal = $('reviewModal');
                    if (modal && !modal.classList.contains('hidden')) {
                        if (document.activeElement && ['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement.tagName)) {
                            return;
                        }
                        this.deleteSelectedBox();
                    }
                }
            });
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
            this.updateCounts();
            this.renderList();
            if (this.ctx && this.canvas) {
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
                btn.className = 'px-3 py-1 rounded text-xs font-bold bg-gray-200 text-gray-700';
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
                const statusColor = rec.reviewStatus === 'approved' ? 'bg-green-100 text-green-800' :
                                    rec.reviewStatus === 'rejected' ? 'bg-red-100 text-red-800' : 'bg-amber-100 text-amber-800';
                const statusLabel = rec.reviewStatus === 'approved' ? '승인' : rec.reviewStatus === 'rejected' ? '제외' : '대기';
                const imgSrc = rec.prevBase64 || rec.origBase64;
                const timeStr = rec.capturedAtUtc ? rec.capturedAtUtc.split('T')[1].slice(0, 8) : '00:00:00';

                return `
                <div class="p-2 rounded border cursor-pointer transition ${isSelected ? 'border-[var(--p)] bg-blue-50' : 'bg-white hover:bg-gray-100'}" onclick="window.Reviewer.selectRecord(${idx})">
                    <div class="flex gap-2">
                        <img src="${imgSrc}" class="w-20 h-14 object-cover rounded bg-black border">
                        <div class="flex-1 flex flex-col justify-between text-[11px]">
                            <div class="flex justify-between items-center">
                                <span class="font-mono font-bold text-gray-700">${timeStr}</span>
                                <span class="px-1.5 py-0.5 rounded text-[10px] font-bold ${statusColor}">${statusLabel}</span>
                            </div>
                            <div class="text-[10px] text-gray-500 font-bold">${rec.originalWidth}x${rec.originalHeight}</div>
                            <div class="text-[10px] text-blue-600 font-bold">객체: ${(rec.reviewedAnnotations || rec.detections).length}건</div>
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
                knownClasses[parseInt(k)] = appCls[k].name || ('Class ' + k);
            });

            const annotations = rec.reviewedAnnotations || rec.detections || [];
            annotations.forEach(a => {
                const cid = parseInt(a.classId || 0);
                if (!knownClasses[cid]) {
                    knownClasses[cid] = a.className || ('Class ' + cid);
                }
            });

            const cids = Object.keys(knownClasses).map(k => parseInt(k)).sort((a, b) => a - b);
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
                document.getElementById(id).className = 'px-2.5 py-0.5 rounded bg-gray-200 font-bold text-xs';
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

        drawCanvas() {
            if (!this.ctx || !this.imgElement.complete) return;
            const rec = this.records[this.currentRecordIndex];
            if (!rec) return;

            this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
            this.ctx.drawImage(this.imgElement, 0, 0);

            if (this.viewMode === 'orig') {
                return; // clean original frame, no boxes drawn
            }

            if (this.viewMode === 'edit') {
                const boxes = rec.reviewedAnnotations || [];
                boxes.forEach((ann, i) => {
                    const isSel = i === this.selectedBoxIndex;
                    const b = ann.box;
                    const color = this.COLORS[(ann.classId || 0) % this.COLORS.length];

                    this.ctx.strokeStyle = isSel ? '#ffffff' : color;
                    this.ctx.lineWidth = isSel ? Math.max(3, Math.round(this.canvas.width / 400)) : Math.max(2, Math.round(this.canvas.width / 600));
                    this.ctx.strokeRect(b.x1, b.y1, b.x2 - b.x1, b.y2 - b.y1);

                    // Box Fill with slight alpha when selected
                    if (isSel) {
                        this.ctx.fillStyle = 'rgba(255, 255, 255, 0.18)';
                        this.ctx.fillRect(b.x1, b.y1, b.x2 - b.x1, b.y2 - b.y1);
                        // Draw corner handles
                        this.drawHandle(b.x1, b.y1);
                        this.drawHandle(b.x2, b.y1);
                        this.drawHandle(b.x2, b.y2);
                        this.drawHandle(b.x1, b.y2);
                    }

                    // Label badge
                    const label = `${ann.className || 'C' + ann.classId} ${(ann.confidence ? (ann.confidence * 100).toFixed(0) + '%' : '')}`;
                    this.ctx.fillStyle = color;
                    const fontSize = Math.max(14, Math.round(this.canvas.width / 80));
                    this.ctx.font = `bold ${fontSize}px sans-serif`;
                    this.ctx.fillText(label, b.x1, Math.max(fontSize + 4, b.y1 - 6));
                });
            }
        }

        drawHandle(x, y) {
            const size = Math.max(10, Math.round(this.canvas.width / 140));
            this.ctx.fillStyle = '#ffffff';
            this.ctx.strokeStyle = '#000000';
            this.ctx.lineWidth = Math.max(1.5, size / 5);
            this.ctx.fillRect(x - size / 2, y - size / 2, size, size);
            this.ctx.strokeRect(x - size / 2, y - size / 2, size, size);
        }

        getCanvasCoordinates(e) {
            const rect = this.canvas.getBoundingClientRect();
            const scaleX = this.canvas.width / rect.width;
            const scaleY = this.canvas.height / rect.height;
            return {
                x: (e.clientX - rect.left) * scaleX,
                y: (e.clientY - rect.top) * scaleY,
            };
        }

        handleCanvasMouseDown(e) {
            if (this.viewMode !== 'edit') return;
            const rec = this.records[this.currentRecordIndex];
            if (!rec) return;

            const pos = this.getCanvasCoordinates(e);
            this.dragStart = pos;

            if (this.isAddingBox) {
                // Begin drawing new box from click origin
                this.boxOrigin = { x: pos.x, y: pos.y };
                const selectedClassId = parseInt(document.getElementById('selBoxClass').value) || 0;
                const selectedClassName = document.getElementById('selBoxClass').selectedOptions[0]?.text || ('Class ' + selectedClassId);
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
            const handleRadius = Math.max(18, Math.round(this.canvas.width / 80));

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
            if (this.isAddingBox) {
                this.isAddingBox = false;
                document.getElementById('btnAddBox').className = 'bg-blue-100 text-blue-700 hover:bg-blue-200 px-2 py-1 rounded text-xs';
                const rec = this.records[this.currentRecordIndex];
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
                }
                this.boxOrigin = null;
            }
            this.isDragging = false;
            this.dragHandle = null;
            this.activeBoxInitial = null;
            this.drawCanvas();
        }

        deleteSelectedBox() {
            const rec = this.records[this.currentRecordIndex];
            if (!rec || this.selectedBoxIndex < 0) return;
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
                : 'bg-blue-100 text-blue-700 hover:bg-blue-200 px-2 py-1 rounded text-xs';
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
                const newId = parseInt(newIdStr);
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
            ann.classId = parseInt(classId);
            ann.className = sel.selectedOptions[0]?.text || ('Class ' + classId);
            this.drawCanvas();
        }

        setDecision(status) {
            const rec = this.records[this.currentRecordIndex];
            if (!rec) return;

            if (status === 'approved') {
                // Invariant 1: Unverified coordinate space strictly blocked
                if (rec.coordinateSpace === 'unknown' || rec.coordinateTransformStatus === 'unverified') {
                    alert('승인 불가: 좌표계가 검증되지 않았거나(unknown) 변환에 실패한 프레임은 학습용 라벨로 승인할 수 없습니다.');
                    return;
                }

                // Invariant 2: Box boundaries and degeneracy validation
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

            rec.reviewStatus = status;
            rec.reviewedAtUtc = new Date().toISOString();
            this.updateCounts();
            this.renderList();

            // Auto-advance to next record if available
            if (this.currentRecordIndex + 1 < this.records.length) {
                this.selectRecord(this.currentRecordIndex + 1);
            }
        }
    }

    window.ReviewManager = ReviewManager;
})(typeof window !== 'undefined' ? window : this);
