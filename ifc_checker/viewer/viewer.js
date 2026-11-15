/* Thẩm Tra Mô Hình BIM - trình xem 3D (three.js r149, không cần build).
 * Dùng chung cho bản web (Streamlit component) và bản desktop (máy chủ cục bộ).
 *   const v = new ThamTraViewer(container, {onPick: fn});
 *   v.load(meta, arrayBuffer); v.focus([objIdx...]); v.focusClash("C00001");
 */
(function () {
  "use strict";
  const T = window.THREE;

  const PALETTE = [
    [0.27, 0.51, 0.71], [0.87, 0.52, 0.2], [0.35, 0.66, 0.42], [0.62, 0.42, 0.74], [0.85, 0.37, 0.53],
    [0.4, 0.68, 0.73], [0.73, 0.65, 0.29], [0.55, 0.45, 0.35], [0.45, 0.5, 0.62], [0.7, 0.3, 0.3],
    [0.3, 0.55, 0.55], [0.6, 0.6, 0.35],
  ];
  const STATUS = { 2: [0.86, 0.24, 0.24], 1: [0.93, 0.64, 0.16], 0: [0.55, 0.72, 0.6], "-1": [0.74, 0.76, 0.8] };
  const STATUS_TXT = { 2: "Lỗi", 1: "Cảnh báo", 0: "Đạt", "-1": "Không kiểm tra" };
  const SEL = [0.1, 0.45, 1.0];

  function el(tag, attrs, html) {
    const e = document.createElement(tag);
    if (attrs) for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (html !== undefined) e.innerHTML = html;
    return e;
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  class ThamTraViewer {
    constructor(container, opts) {
      this.c = container;
      this.opts = opts || {};
      this.meta = null;
      this.colorMode = "status";
      this.hideOk = false;
      this.isolate = true;
      this.showClash = true;
      this.selected = [];
      this.activeClash = null;
      this.isolateSet = null;
      this._buildDom();
      this._initThree();
    }

    // ------------------------------------------------------------------ DOM
    _buildDom() {
      const c = this.c;
      c.classList.add("ttv");
      c.innerHTML = "";
      this.canvasWrap = el("div", { class: "ttv-canvas" });
      c.appendChild(this.canvasWrap);
      const bar = el("div", { class: "ttv-bar" });
      bar.innerHTML =
        '<select data-k="mode" title="Tô màu theo">' +
        '<option value="status">Màu: kết quả kiểm tra</option><option value="file">Màu: theo file</option>' +
        '<option value="class">Màu: theo lớp IFC</option></select>' +
        '<label><input type="checkbox" data-k="hideOk"> Ẩn đối tượng đạt</label>' +
        '<label><input type="checkbox" data-k="isolate" checked> Làm mờ phần còn lại</label>' +
        '<label><input type="checkbox" data-k="clash" checked> Điểm xung đột</label>' +
        '<button data-k="fit" title="Xem toàn cảnh">⤢ Toàn cảnh</button>';
      c.appendChild(bar);
      bar.querySelector('[data-k="mode"]').onchange = (e) => { this.colorMode = e.target.value; this._recolor(); this._legend(); };
      bar.querySelector('[data-k="hideOk"]').onchange = (e) => { this.hideOk = e.target.checked; this._rebuildIndex(); };
      bar.querySelector('[data-k="isolate"]').onchange = (e) => { this.isolate = e.target.checked; this._applyIsolate(); };
      bar.querySelector('[data-k="clash"]').onchange = (e) => { this.showClash = e.target.checked; if (this.clashMesh) this.clashMesh.visible = this.showClash; this._render(); };
      bar.querySelector('[data-k="fit"]').onclick = () => this.fitAll();
      this.info = el("div", { class: "ttv-info" });
      this.info.style.display = "none";
      c.appendChild(this.info);
      this.legendEl = el("div", { class: "ttv-legend" });
      c.appendChild(this.legendEl);
      this.status = el("div", { class: "ttv-status" }, "Đang tải mô hình...");
      c.appendChild(this.status);
      this.help = el("div", { class: "ttv-help" }, "Chuột trái: xoay · Chuột phải/Shift: di chuyển · Lăn: thu phóng · Nháy: chọn · Nháy đúp: đặt tâm xoay");
      c.appendChild(this.help);
    }

    // ------------------------------------------------------------------ three
    _initThree() {
      const w = this.canvasWrap.clientWidth || 800, h = this.canvasWrap.clientHeight || 600;
      this.renderer = new T.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
      this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      this.renderer.setSize(w, h);
      this.renderer.setClearColor(0xeef1f5, 1);
      this.canvasWrap.appendChild(this.renderer.domElement);
      this.scene = new T.Scene();
      this.camera = new T.PerspectiveCamera(45, w / h, 0.1, 1e6);
      this.camera.up.set(0, 0, 1);
      this.camera.position.set(100, -100, 100);
      this.target = new T.Vector3();
      this.scene.add(new T.HemisphereLight(0xffffff, 0x8a8f99, 0.85));
      const dl = new T.DirectionalLight(0xffffff, 0.55);
      dl.position.set(0.5, -0.8, 1.0);
      this.scene.add(dl);
      this.camLight = new T.DirectionalLight(0xffffff, 0.25);
      this.scene.add(this.camLight);
      this._controls();
      window.addEventListener("resize", () => this.resize());
      if (window.ResizeObserver) new ResizeObserver(() => this.resize()).observe(this.canvasWrap);
    }

    resize() {
      const w = this.canvasWrap.clientWidth, h = this.canvasWrap.clientHeight;
      if (!w || !h) return;
      this.renderer.setSize(w, h);
      this.camera.aspect = w / h;
      this.camera.updateProjectionMatrix();
      this._render();
    }

    _render() {
      if (this._raf) return;
      this._raf = requestAnimationFrame(() => {
        this._raf = 0;
        this.camera.lookAt(this.target);
        this.camLight.position.copy(this.camera.position);
        this.renderer.render(this.scene, this.camera);
      });
    }

    _controls() {
      const dom = this.renderer.domElement;
      let mode = null, sx = 0, sy = 0, moved = false;
      dom.addEventListener("contextmenu", (e) => e.preventDefault());
      dom.addEventListener("pointerdown", (e) => {
        mode = e.button === 2 || e.button === 1 || e.shiftKey ? "pan" : "rot";
        sx = e.clientX; sy = e.clientY; moved = false;
        dom.setPointerCapture(e.pointerId);
      });
      dom.addEventListener("pointermove", (e) => {
        if (!mode) return;
        const dx = e.clientX - sx, dy = e.clientY - sy;
        if (Math.abs(dx) + Math.abs(dy) > 2) moved = true;
        sx = e.clientX; sy = e.clientY;
        const off = this.camera.position.clone().sub(this.target);
        if (mode === "rot") {
          // hệ Z-up: xoay theo góc phương vị / thiên đỉnh quanh tâm
          const r = off.length();
          let az = Math.atan2(off.y, off.x), el_ = Math.acos(Math.min(1, Math.max(-1, off.z / r)));
          az -= dx * 0.006;
          el_ = Math.min(Math.PI - 0.01, Math.max(0.01, el_ - dy * 0.006));
          off.set(r * Math.sin(el_) * Math.cos(az), r * Math.sin(el_) * Math.sin(az), r * Math.cos(el_));
          this.camera.position.copy(this.target).add(off);
        } else {
          const dist = off.length();
          const scale = (2 * dist * Math.tan((this.camera.fov * Math.PI) / 360)) / this.canvasWrap.clientHeight;
          const right = new T.Vector3().crossVectors(this.camera.getWorldDirection(new T.Vector3()), this.camera.up).normalize();
          const up = new T.Vector3().crossVectors(right, this.camera.getWorldDirection(new T.Vector3())).normalize();
          const delta = right.multiplyScalar(-dx * scale).add(up.multiplyScalar(dy * scale));
          this.camera.position.add(delta);
          this.target.add(delta);
        }
        this._render();
      });
      dom.addEventListener("pointerup", (e) => {
        if (mode && !moved && e.button === 0) this._pick(e, false);
        mode = null;
      });
      dom.addEventListener("dblclick", (e) => this._pick(e, true));
      dom.addEventListener("wheel", (e) => {
        e.preventDefault();
        const f = Math.pow(1.0015, e.deltaY);
        const off = this.camera.position.clone().sub(this.target).multiplyScalar(f);
        this.camera.position.copy(this.target).add(off);
        this._render();
      }, { passive: false });
    }

    // ------------------------------------------------------------------ load
    load(meta, buffer) {
      this.meta = meta;
      const pos = new Float32Array(buffer, 0, meta.nverts * 3);
      const idx = new Uint32Array(buffer, meta.posBytes, meta.ntris * 3);
      this.fullIndex = idx;
      const g = new T.BufferGeometry();
      g.setAttribute("position", new T.BufferAttribute(pos, 3));
      this.colors = new Float32Array(meta.nverts * 3);
      g.setAttribute("color", new T.BufferAttribute(this.colors, 3));
      this.geom = g;
      this.mat = new T.MeshPhongMaterial({ vertexColors: true, flatShading: true, side: T.DoubleSide, shininess: 12 });
      if (this.mesh) this.scene.remove(this.mesh);
      this.mesh = new T.Mesh(g, this.mat);
      this.scene.add(this.mesh);
      this.hlGeom = new T.BufferGeometry();
      this.hlGeom.setAttribute("position", g.getAttribute("position"));
      this.hlGeom.setAttribute("color", g.getAttribute("color"));
      this.hlMesh = new T.Mesh(this.hlGeom, new T.MeshPhongMaterial({ vertexColors: true, flatShading: true, side: T.DoubleSide, emissive: 0x111111, polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1 }));
      this.hlMesh.renderOrder = 5;
      this.hlMesh.visible = false;
      this.scene.add(this.hlMesh);
      // lớp "x-quang": luôn vẽ trên cùng để thấy đối tượng bị che khuất (vd hố ga nằm dưới mặt đường)
      this.xrGeom = new T.BufferGeometry();
      this.xrGeom.setAttribute("position", g.getAttribute("position"));
      this.xrGeom.setAttribute("color", g.getAttribute("color"));
      this.xrMesh = new T.Mesh(this.xrGeom, new T.MeshPhongMaterial({ vertexColors: true, flatShading: true, side: T.DoubleSide, depthTest: false, transparent: true, opacity: 0.92 }));
      this.xrMesh.renderOrder = 9;
      this.xrMesh.visible = false;
      this.scene.add(this.xrMesh);
      // bảng tra (file, step) -> chỉ số đối tượng; cha -> các con (đối tượng cha không có hình học)
      this.objIndex = new Map();
      this.childIndex = new Map();
      meta.objects.forEach((o, i) => {
        this.objIndex.set(o[0] + ":" + o[1], i);
        if (o[11] != null && o[11] >= 0) {
          const k = o[0] + ":" + o[11];
          if (!this.childIndex.has(k)) this.childIndex.set(k, []);
          this.childIndex.get(k).push(i);
        }
      });
      this.isolateSet = null;
      this._recolor();
      this._rebuildIndex();
      this._buildClashMarkers();
      this._computeBounds();
      this.fitAll();
      this._legend();
      const n = meta.objects.length;
      this.status.textContent = `${n.toLocaleString()} đối tượng · ${meta.ntris.toLocaleString()} tam giác` +
        (meta.clashes.length ? ` · ${meta.clashes.length.toLocaleString()} xung đột` : "");
    }

    _computeBounds() {
      const b = new T.Box3();
      for (const o of this.meta.objects) {
        const bb = o[10];
        b.expandByPoint(new T.Vector3(bb[0], bb[1], bb[2]));
        b.expandByPoint(new T.Vector3(bb[3], bb[4], bb[5]));
      }
      this.bounds = b;
      const size = b.getSize(new T.Vector3()).length() || 100;
      this.camera.near = Math.max(0.01, size / 20000);
      this.camera.far = size * 20;
      this.camera.updateProjectionMatrix();
    }

    _objColor(o) {
      if (this.colorMode === "file") return PALETTE[o[0] % PALETTE.length];
      if (this.colorMode === "class") return PALETTE[(o[3] * 5) % PALETTE.length];
      return STATUS[o[5]] || STATUS["-1"];
    }

    _recolor() {
      if (!this.meta) return;
      const col = this.colors;
      const ca = this.activeClash ? this.activeClash.a : -1, cb = this.activeClash ? this.activeClash.b : -1;
      const sel = this.activeClash ? new Set() : new Set(this.selected);
      this.meta.objects.forEach((o, i) => {
        let c = this._objColor(o);
        if (i === ca) c = [0.85, 0.12, 0.75];       // đối tượng A: tím hồng
        else if (i === cb) c = [0.12, 0.5, 0.95];   // đối tượng B: xanh
        else if (sel.has(i)) c = SEL;
        const v0 = o[6], vn = o[7];
        for (let k = v0 * 3, e = (v0 + vn) * 3; k < e; k += 3) { col[k] = c[0]; col[k + 1] = c[1]; col[k + 2] = c[2]; }
      });
      this.geom.getAttribute("color").needsUpdate = true;
      this._render();
    }

    _rebuildIndex() {
      if (!this.meta) return;
      const objs = this.meta.objects, full = this.fullIndex;
      const keep = [];
      let total = 0;
      const sel = new Set(this.selected);
      objs.forEach((o, i) => {
        if (this.isolateSet && !this.isolateSet.has(i)) return;
        if (this.hideOk && o[5] <= 0 && !sel.has(i)) return;
        keep.push(i); total += o[9];
      });
      const idx = new Uint32Array(total * 3);
      const triObj = new Uint32Array(total);
      let p = 0, t = 0;
      for (const i of keep) {
        const o = objs[i];
        idx.set(full.subarray(o[8] * 3, (o[8] + o[9]) * 3), p);
        triObj.fill(i, t, t + o[9]);
        p += o[9] * 3; t += o[9];
      }
      this.triObj = triObj;
      this.geom.setIndex(new T.BufferAttribute(idx, 1));
      this.geom.computeBoundingSphere();
      this._render();
    }

    _buildClashMarkers() {
      if (this.clashMesh) { this.scene.remove(this.clashMesh); this.clashMesh = null; }
      const cl = this.meta.clashes || [];
      const size = this.meta.objects.length ? this._sceneSize() : 100;
      this.clashRadius = Math.max(0.3, Math.min(5, size / 800));
      if (!this.activeMarker) {
        const g = new T.BufferGeometry();
        g.setAttribute("position", new T.BufferAttribute(new Float32Array(3), 3));
        this.activeMarker = new T.Points(g, new T.PointsMaterial({ color: 0xff0000, size: 22, sizeAttenuation: false, depthTest: false, map: this._dotTexture(true), transparent: true }));
        this.activeMarker.renderOrder = 11;
        this.activeMarker.visible = false;
        this.activeMarker.frustumCulled = false;
        this.scene.add(this.activeMarker);
      }
      if (!cl.length) return;
      const pos = new Float32Array(cl.length * 3);
      cl.forEach((c, i) => { pos[i * 3] = c.p[0]; pos[i * 3 + 1] = c.p[1]; pos[i * 3 + 2] = c.p[2]; });
      const g = new T.BufferGeometry();
      g.setAttribute("position", new T.BufferAttribute(pos, 3));
      const pts = new T.Points(g, new T.PointsMaterial({ color: 0xe0115f, size: 9, sizeAttenuation: false, depthTest: false, map: this._dotTexture(false), transparent: true }));
      pts.renderOrder = 10;
      pts.frustumCulled = false;
      pts.visible = this.showClash;
      this.clashMesh = pts;
      this.scene.add(pts);
    }

    _dotTexture(ring) {
      const c = document.createElement("canvas");
      c.width = c.height = 64;
      const x = c.getContext("2d");
      x.beginPath();
      x.arc(32, 32, 26, 0, Math.PI * 2);
      if (ring) { x.lineWidth = 9; x.strokeStyle = "#fff"; x.stroke(); x.lineWidth = 5; x.strokeStyle = "#fff"; }
      else { x.fillStyle = "#fff"; x.fill(); x.lineWidth = 6; x.strokeStyle = "rgba(255,255,255,.9)"; x.stroke(); }
      const t = new T.CanvasTexture(c);
      return t;
    }

    _sceneSize() {
      const b = new T.Box3();
      for (const o of this.meta.objects) { const bb = o[10]; b.expandByPoint(new T.Vector3(bb[0], bb[1], bb[2])); b.expandByPoint(new T.Vector3(bb[3], bb[4], bb[5])); }
      return b.getSize(new T.Vector3()).length() || 100;
    }

    // ------------------------------------------------------------------ camera
    _flyToBox(box) {
      const center = box.getCenter(new T.Vector3());
      const size = Math.max(box.getSize(new T.Vector3()).length(), 1);
      const dir = this.camera.position.clone().sub(this.target).normalize();
      if (!isFinite(dir.x) || dir.lengthSq() < 0.5) dir.set(0.6, -0.6, 0.5).normalize();
      const dist = (size * 0.75) / Math.tan((this.camera.fov * Math.PI) / 360);
      this.target.copy(center);
      this.camera.position.copy(center).add(dir.multiplyScalar(dist));
      this._render();
    }

    fitAll() { if (this.bounds) this._flyToBox(this.isolateSet ? this._boxOf([...this.isolateSet]) : this.bounds); }

    // ------------------------------------------------------------------ API cho ứng dụng
    /** refs: [[file, step], ...] -> chỉ số đối tượng 3D (đối tượng không có hình học thì lấy các con) */
    resolve(refs) {
      if (!this.meta) return [];
      const fidx = new Map(this.meta.files.map((f, i) => [f, i]));
      const out = new Set();
      for (const [f, st] of refs || []) {
        const fi = fidx.get(f);
        if (fi == null) continue;
        const k = fi + ":" + st;
        if (this.objIndex.has(k)) out.add(this.objIndex.get(k));
        else for (const c of this.childIndex.get(k) || []) out.add(c);
      }
      return [...out];
    }

    isolate(ids) {
      this.isolateSet = ids && ids.length ? new Set(ids) : null;
      this._rebuildIndex();
      if (this.isolateSet) this._flyToBox(this._boxOf(ids));
      else this.fitAll();
    }

    clearIsolate() { this.isolate(null); }

    setColorMode(m) {
      this.colorMode = m;
      const sel = this.c.querySelector('[data-k="mode"]');
      if (sel) sel.value = m;
      this._recolor();
      this._legend();
    }

    clearSelection() {
      this.selected = [];
      this.activeClash = null;
      if (this.activeMarker) this.activeMarker.visible = false;
      this._highlight([], []);
      this._recolor();
      this.info.style.display = "none";
    }

    _boxOf(ids) {
      const b = new T.Box3();
      for (const i of ids) {
        const bb = this.meta.objects[i][10];
        b.expandByPoint(new T.Vector3(bb[0], bb[1], bb[2]));
        b.expandByPoint(new T.Vector3(bb[3], bb[4], bb[5]));
      }
      return b;
    }

    // ------------------------------------------------------------------ chọn / tô sáng
    focus(ids, opts) {
      if (!this.meta) return;
      ids = (ids || []).filter((i) => i != null && i >= 0 && i < this.meta.objects.length);
      this.selected = ids;
      this.activeClash = null;
      if (this.activeMarker) this.activeMarker.visible = false;
      this._highlight([], ids);
      if (this.hideOk) this._rebuildIndex();
      this._recolor();
      if (ids.length) {
        this._flyToBox(this._boxOf(ids));
        this._showInfo(ids[0], ids.length);
      } else this.info.style.display = "none";
      if (!(opts && opts.silent) && this.opts.onSelect) this.opts.onSelect(ids);
    }

    focusClash(id) {
      if (!this.meta) return;
      const c = (this.meta.clashes || []).find((x) => x.id === id);
      if (!c) return;
      this.activeClash = c;
      const ids = [c.a, c.b].filter((x) => x != null);
      this.selected = ids;
      this._highlight(c.b != null ? [c.b] : [], c.a != null ? [c.a] : []);
      this._recolor();
      const r = this.clashRadius || 1;
      const ap = this.activeMarker.geometry.getAttribute("position");
      ap.setXYZ(0, c.p[0], c.p[1], c.p[2]);
      ap.needsUpdate = true;
      this.activeMarker.visible = true;
      // khung nhìn bao 2 đối tượng va chạm, giới hạn quanh điểm va chạm
      const pt = new T.Vector3(c.p[0], c.p[1], c.p[2]);
      let box = new T.Box3().setFromCenterAndSize(pt, new T.Vector3(10, 10, 10));
      if (c.a != null) {
        const ba = this._boxOf([c.a]);
        if (ba.getSize(new T.Vector3()).length() < 60) box = ba.expandByPoint(pt).expandByScalar(4);
      }
      this._flyToBox(box);
      this._applyIsolate();
      const o = this.meta.objects;
      const name = (i) => (i == null ? "?" : `${esc(this.meta.classes[o[i][3]])} - ${esc(o[i][4])}<br><small>${esc(this.meta.files[o[i][0]])}</small>`);
      this.info.innerHTML = `<b>Xung đột ${esc(c.id)}</b> <span class="ttv-x">✕</span><div class="ttv-k">${esc(c.t)}</div>` +
        `<div><b>${esc(c.m || ("Độ sâu xuyên " + c.d.toFixed(3) + " m"))}</b></div><hr><div><i class="ttv-sw" style="background:#d91fbf"></i>A: ${name(c.a)}</div>` +
        `<hr><div><i class="ttv-sw" style="background:#1f80f2"></i>B: ${name(c.b)}</div>`;
      this.info.style.display = "block";
      this.info.querySelector(".ttv-x").onclick = () => (this.info.style.display = "none");
    }

    _subIndex(ids) {
      let total = 0;
      for (const i of ids) total += this.meta.objects[i][9];
      const idx = new Uint32Array(total * 3);
      let p = 0;
      for (const i of ids) {
        const o = this.meta.objects[i];
        idx.set(this.fullIndex.subarray(o[8] * 3, (o[8] + o[9]) * 3), p);
        p += o[9] * 3;
      }
      return new T.BufferAttribute(idx, 1);
    }

    _highlight(opaqueIds, xrayIds) {
      opaqueIds = opaqueIds || [];
      xrayIds = xrayIds || [];
      this.hlMesh.visible = opaqueIds.length > 0;
      if (opaqueIds.length) { this.hlGeom.setIndex(this._subIndex(opaqueIds)); this.hlGeom.computeBoundingSphere(); }
      this.xrMesh.visible = xrayIds.length > 0;
      if (xrayIds.length) { this.xrGeom.setIndex(this._subIndex(xrayIds)); this.xrGeom.computeBoundingSphere(); }
      this._applyIsolate();
      this._render();
    }

    _applyIsolate() {
      const on = this.isolate && this.selected.length > 0;
      this.mat.transparent = on;
      this.mat.opacity = on ? 0.18 : 1;
      this.mat.depthWrite = !on;
      this.mat.needsUpdate = true;
      this._render();
    }

    _showInfo(i, n) {
      const o = this.meta.objects[i];
      const iss = (this.meta.issues && this.meta.issues[String(i)]) || [];
      this.info.innerHTML =
        `<b>${esc(this.meta.classes[o[3]])}</b> <span class="ttv-x">✕</span>` +
        `<div>${esc(o[4]) || "<i>(không tên)</i>"}</div>` +
        `<div class="ttv-k">${esc(this.meta.files[o[0]])} · #${o[1]}</div>` +
        `<div class="ttv-k">GlobalId: <code>${esc(o[2])}</code></div>` +
        `<div>Trạng thái: <b class="st${o[5]}">${STATUS_TXT[o[5]]}</b>${n > 1 ? ` · ${n} phần` : ""}</div>` +
        (iss.length ? "<ul>" + iss.map((m) => `<li>${esc(m)}</li>`).join("") + "</ul>" : "");
      this.info.style.display = "block";
      this.info.querySelector(".ttv-x").onclick = () => (this.info.style.display = "none");
    }

    _pick(e, setPivot) {
      if (!this.mesh) return;
      const rect = this.renderer.domElement.getBoundingClientRect();
      const ndc = new T.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
      const rc = new T.Raycaster();
      rc.setFromCamera(ndc, this.camera);
      const hits = rc.intersectObject(this.mesh, false);
      if (!hits.length) { if (!setPivot) this.focus([], { silent: true }); return; }
      const h = hits[0];
      if (setPivot) {
        const off = this.camera.position.clone().sub(this.target);
        this.target.copy(h.point);
        this.camera.position.copy(h.point).add(off);
        this._render();
        return;
      }
      const oi = this.triObj[h.faceIndex];
      this.selected = [oi];
      this.activeClash = null;
      if (this.activeMarker) this.activeMarker.visible = false;
      this._highlight([], [oi]);
      this._recolor();
      this._showInfo(oi, 1);
      const o = this.meta.objects[oi];
      if (this.opts.onPick) this.opts.onPick({ index: oi, file: this.meta.files[o[0]], step: o[1], guid: o[2] });
    }

    _legend() {
      if (!this.meta) return;
      let items = [];
      if (this.colorMode === "status") items = [2, 1, 0, -1].map((k) => [STATUS[k], STATUS_TXT[k]]);
      else if (this.colorMode === "file") items = this.meta.files.map((f, i) => [PALETTE[i % PALETTE.length], f]);
      else items = this.meta.classes.slice(0, 14).map((c, i) => [PALETTE[(i * 5) % PALETTE.length], c]);
      const rgb = (c) => `rgb(${Math.round(c[0] * 255)},${Math.round(c[1] * 255)},${Math.round(c[2] * 255)})`;
      this.legendEl.innerHTML = items.map(([c, t]) => `<div><i style="background:${rgb(c)}"></i>${esc(t)}</div>`).join("") +
        (this.meta.clashes.length ? `<div><i style="background:#e0115f;border-radius:50%"></i>Điểm xung đột</div>` : "");
    }
  }

  ThamTraViewer.CSS = `
  .ttv{position:relative;width:100%;height:100%;overflow:hidden;font:12px/1.4 "Segoe UI",system-ui,sans-serif;color:#1a202c;background:#eef1f5}
  .ttv-canvas{position:absolute;inset:0}
  .ttv-canvas canvas{display:block;width:100%;height:100%;touch-action:none}
  .ttv-bar{position:absolute;top:8px;left:8px;right:8px;display:flex;flex-wrap:wrap;gap:6px;align-items:center;pointer-events:none}
  .ttv-bar>*{pointer-events:auto;background:rgba(255,255,255,.92);border:1px solid #cfd6df;border-radius:6px;padding:3px 7px;font:inherit;color:inherit}
  .ttv-bar button{cursor:pointer}
  .ttv-info{position:absolute;top:44px;right:8px;width:min(330px,60%);max-height:62%;overflow:auto;background:rgba(255,255,255,.96);border:1px solid #cfd6df;border-radius:8px;padding:8px 10px;box-shadow:0 2px 10px rgba(0,0,0,.12)}
  .ttv-info ul{margin:6px 0 0 16px;padding:0}.ttv-info li{margin:2px 0;color:#9b1c1c}
  .ttv-info hr{border:0;border-top:1px solid #e2e8f0;margin:6px 0}
  .ttv-x{float:right;cursor:pointer;color:#64748b}
  .ttv-sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px}
  .ttv-k{color:#64748b;font-size:11px;word-break:break-all}
  .st2{color:#c0392b}.st1{color:#b7791f}.st0{color:#2f855a}
  .ttv-legend{position:absolute;left:8px;bottom:28px;background:rgba(255,255,255,.9);border:1px solid #cfd6df;border-radius:6px;padding:5px 8px;max-height:40%;overflow:auto}
  .ttv-legend i{display:inline-block;width:10px;height:10px;margin-right:6px;border-radius:2px;vertical-align:-1px}
  .ttv-status{position:absolute;right:8px;bottom:6px;color:#475569;font-size:11px;background:rgba(255,255,255,.8);padding:1px 6px;border-radius:4px}
  .ttv-help{position:absolute;left:8px;bottom:6px;color:#64748b;font-size:10.5px}
  @media (max-width:600px){.ttv-help{display:none}}
  `;
  window.ThamTraViewer = ThamTraViewer;
})();
