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
  const STATUS = { 2: [0.86, 0.24, 0.24], 1: [0.93, 0.64, 0.16], 0: [0.36, 0.66, 0.45], "-1": [0.6, 0.64, 0.7] };
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
      this.fadeOthers = true;   // làm mờ phần còn lại khi có đối tượng được chọn
      this.showClash = true;
      this.selected = [];
      this.activeClash = null;
      this.isolateSet = null;
      this.hiddenSet = new Set();
      this.boxMode = false;
      // LOD động theo kích thước trên màn hình (điểm ảnh)
      this.lod = true;
      this.lodHidePx = 1.5;     // nhỏ hơn -> ẩn
      this.lodProxyPx = 12;     // nhỏ hơn -> vẽ khối hộp đơn giản
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
        '<label title="Xem xa: đối tượng nhỏ trên màn hình được vẽ đơn giản hoá / tạm ẩn; zoom gần hiện đầy đủ chi tiết"><input type="checkbox" data-k="lod" checked> LOD tự động</label>' +
        '<button data-k="fit" title="Xem toàn cảnh">⤢ Toàn cảnh</button>';
      c.appendChild(bar);
      bar.querySelector('[data-k="mode"]').onchange = (e) => { this._userColorMode = true; this.colorMode = e.target.value; this._recolor(); this._legend(); };
      bar.querySelector('[data-k="hideOk"]').onchange = (e) => { this.hideOk = e.target.checked; this._rebuildIndex(); };
      bar.querySelector('[data-k="isolate"]').onchange = (e) => { this.fadeOthers = e.target.checked; this._applyIsolate(); };
      bar.querySelector('[data-k="clash"]').onchange = (e) => { this.showClash = e.target.checked; if (this.clashMesh) this.clashMesh.visible = this.showClash; this._render(); };
      bar.querySelector('[data-k="fit"]').onclick = () => this.fitAll();
      bar.querySelector('[data-k="lod"]').onchange = (e) => { this.lod = e.target.checked; this._rebuildIndex(); };
      this.info = el("div", { class: "ttv-info" });
      this.info.style.display = "none";
      c.appendChild(this.info);
      this.legendEl = el("div", { class: "ttv-legend" });
      c.appendChild(this.legendEl);
      this.status = el("div", { class: "ttv-status" }, "Đang tải mô hình...");
      c.appendChild(this.status);
      this.help = el("div", { class: "ttv-help" }, "Trái: xoay · Phải/Shift: di chuyển · Lăn: thu phóng · Nháy: chọn (Ctrl: chọn thêm) · Alt+kéo: chọn vùng · Chuột phải: menu · H ẩn · I cô lập · U hiện tất cả");
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
      window.addEventListener("keydown", (e) => {
        const t = e.target;
        if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
        if (!this.meta || e.ctrlKey || e.metaKey || e.altKey) return;
        const k = e.key.toLowerCase();
        if (k === "f" || e.key === "Home") this.fitAll();
        else if (k === "h") this.hideSelected();
        else if (k === "i") this.isolateSelected();
        else if (k === "u") this.unhideAll();
        else if (k === "z") this.zoomSelection();
        else if (e.key === "Escape") this.clearSelection(true);
      });
      if (window.ResizeObserver) new ResizeObserver(() => this.resize()).observe(this.canvasWrap);
    }

    resize() {
      const w = this.canvasWrap.clientWidth, h = this.canvasWrap.clientHeight;
      if (!w || !h) return;
      this.renderer.setSize(w, h);
      this.camera.aspect = w / h;
      this.camera.updateProjectionMatrix();
      if (this._autoFit && this.bounds) this.fitAll();
      this._render();
    }

    _render() {
      if (this._raf) return;
      this._raf = requestAnimationFrame(() => {
        this._raf = 0;
        this.camera.lookAt(this.target);
        this.camLight.position.copy(this.camera.position);
        this.renderer.render(this.scene, this.camera);
        if (this.meta) {
          clearTimeout(this._lodT);
          this._lodT = setTimeout(() => { if (this._camSig() !== this._lodSig) this._rebuildIndex(); }, 160);
        }
      });
    }

    _camSig() {
      const p = this.camera.position, t = this.target;
      const q = (v) => Math.round(v * 20) / 20;
      return [p.x, p.y, p.z, t.x, t.y, t.z].map(q).join(",") + "|" + this.canvasWrap.clientWidth + "x" + this.canvasWrap.clientHeight;
    }

    _controls() {
      const dom = this.renderer.domElement;
      let mode = null, sx = 0, sy = 0, moved = false;
      dom.addEventListener("contextmenu", (e) => e.preventDefault());
      let bx = 0, by = 0, btn = 0;
      dom.addEventListener("pointerdown", (e) => {
        this._autoFit = false;
        this._hideMenu();
        btn = e.button;
        if (e.button === 0 && (this.boxMode || e.altKey)) mode = "box";
        else mode = e.button === 2 || e.button === 1 || e.shiftKey ? "pan" : "rot";
        sx = bx = e.clientX; sy = by = e.clientY; moved = false;
        dom.setPointerCapture(e.pointerId);
      });
      dom.addEventListener("pointermove", (e) => {
        if (!mode) return;
        if (mode === "box") {
          if (Math.abs(e.clientX - bx) + Math.abs(e.clientY - by) > 3) moved = true;
          this._drawBox(bx, by, e.clientX, e.clientY);
          return;
        }
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
        if (mode === "box") {
          this._boxEl.style.display = "none";
          if (moved) this._boxSelect(bx, by, e.clientX, e.clientY, e.ctrlKey || e.metaKey);
          else this._pick(e, false);
        } else if (mode && !moved && btn === 0) this._pick(e, false);
        else if (mode && !moved && btn === 2) this._contextMenu(e);
        mode = null;
      });
      dom.addEventListener("dblclick", (e) => this._pick(e, true));
      dom.addEventListener("wheel", (e) => {
        e.preventDefault();
        this._autoFit = false;
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
      this.hiddenSet = new Set();
      // tâm & bán kính bao của từng đối tượng (cho LOD và loại bỏ ngoài khung nhìn)
      const n0 = meta.objects.length;
      this.centers = new Float32Array(n0 * 3);
      this.radii = new Float32Array(n0);
      meta.objects.forEach((o, i) => {
        const b = o[10];
        this.centers[i * 3] = (b[0] + b[3]) / 2; this.centers[i * 3 + 1] = (b[1] + b[4]) / 2; this.centers[i * 3 + 2] = (b[2] + b[5]) / 2;
        this.radii[i] = Math.max(0.01, Math.hypot(b[3] - b[0], b[4] - b[1], b[5] - b[2]) / 2);
      });
      if (this.proxyMesh) { this.scene.remove(this.proxyMesh); this.proxyMesh.geometry.dispose(); }
      this.proxyMat = new T.MeshPhongMaterial({ flatShading: true, shininess: 8 });
      this.proxyMesh = new T.InstancedMesh(new T.BoxGeometry(1, 1, 1), this.proxyMat, Math.max(1, n0));
      this.proxyMesh.setColorAt(0, new T.Color(1, 1, 1));
      this.proxyMesh.count = 0;
      this.proxyMesh.frustumCulled = false;
      this.scene.add(this.proxyMesh);
      this.proxyIds = new Uint32Array(0);
      // chưa có kết quả kiểm tra -> tô màu theo file cho dễ phân biệt
      if (!this._userColorMode) this.setColorMode(meta.objects.some((o) => o[5] >= 0) ? "status" : "file");
      this._recolor();
      this._rebuildIndex();
      this._buildClashMarkers();
      this._computeBounds();
      this._autoFit = true;
      this.resize();
      this.fitAll();
      // bố cục trang có thể còn thay đổi (ẩn màn hình chào, bảng dưới...) -> căn lại vài lần đầu
      [80, 300, 800].forEach((ms) => setTimeout(() => { if (this._autoFit) { this.resize(); this.fitAll(); } }, ms));
      this._legend();
      const n = meta.objects.length;
      this.baseStatus = `${n.toLocaleString()} đối tượng · ${meta.ntris.toLocaleString()} tam giác` +
        (meta.clashes.length ? ` · ${meta.clashes.length.toLocaleString()} xung đột` : "");
      this._updateStatus();
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
      this._proxyColors();
      this._render();
    }

    _proxyColors() {
      if (!this.proxyMesh || !this.proxyIds.length) return;
      const col = this.colors, c = new T.Color();
      for (let k = 0; k < this.proxyIds.length; k++) {
        const v = this.meta.objects[this.proxyIds[k]][6] * 3;
        this.proxyMesh.setColorAt(k, c.setRGB(col[v], col[v + 1], col[v + 2]));
      }
      if (this.proxyMesh.instanceColor) this.proxyMesh.instanceColor.needsUpdate = true;
    }

    _updateStatus() {
      if (!this.baseStatus) return;
      const L = this.lodStats;
      this.status.textContent = this.baseStatus + (this.lod && L
        ? ` · đang vẽ ${L.tris.toLocaleString()} tam giác (${L.full.toLocaleString()} chi tiết, ${L.proxy.toLocaleString()} giản lược, ${(L.hidden + L.culled).toLocaleString()} ẩn)`
        : "") +
        (this.selected && this.selected.length ? ` · chọn ${this.selected.length.toLocaleString()}` : "") +
        (this.hiddenSet && this.hiddenSet.size ? ` · ẩn ${this.hiddenSet.size.toLocaleString()}` : "") +
        (this.isolateSet ? ` · cô lập ${this.isolateSet.size.toLocaleString()}` : "");
    }

    _rebuildIndex() {
      if (!this.meta) return;
      const objs = this.meta.objects, full = this.fullIndex;
      const keep = [], proxies = [];
      let total = 0, hidden = 0, culled = 0;
      const sel = new Set(this.selected);
      const cam = this.camera;
      cam.lookAt(this.target);
      cam.updateMatrixWorld();
      const fr = new T.Frustum().setFromProjectionMatrix(new T.Matrix4().multiplyMatrices(cam.projectionMatrix, cam.matrixWorldInverse));
      const k = (this.canvasWrap.clientHeight || 600) / 2 / Math.tan((cam.fov * Math.PI) / 360);
      const cp = cam.position, sph = new T.Sphere(), C = this.centers, R = this.radii;
      const useLod = this.lod && C;
      objs.forEach((o, i) => {
        if (this.isolateSet && !this.isolateSet.has(i)) return;
        if (this.hiddenSet.has(i)) return;
        if (this.hideOk && o[5] <= 0 && !sel.has(i)) return;
        if (useLod && !sel.has(i)) {
          const dx = C[i * 3] - cp.x, dy = C[i * 3 + 1] - cp.y, dz = C[i * 3 + 2] - cp.z;
          const d = Math.max(1e-3, Math.sqrt(dx * dx + dy * dy + dz * dz));
          sph.center.set(C[i * 3], C[i * 3 + 1], C[i * 3 + 2]);
          sph.radius = R[i] + d * 0.35;               // nới rộng để không "hụt" mép khi xoay/kéo
          if (!fr.intersectsSphere(sph)) { culled++; return; }
          const px = (R[i] / d) * k;                  // bán kính trên màn hình (điểm ảnh)
          if (px < this.lodHidePx) { hidden++; return; }
          if (px < this.lodProxyPx && o[9] > 12) { proxies.push(i); return; }
        }
        keep.push(i); total += o[9];
      });
      this._lodSig = this._camSig();
      // khối hộp đơn giản cho đối tượng nhỏ trên màn hình
      const m4 = new T.Matrix4(), q = new T.Quaternion(), sc = new T.Vector3(), pos = new T.Vector3();
      this.proxyIds = Uint32Array.from(proxies);
      proxies.forEach((i, j) => {
        const b = objs[i][10];
        sc.set(Math.max(0.05, b[3] - b[0]), Math.max(0.05, b[4] - b[1]), Math.max(0.05, b[5] - b[2]));
        pos.set((b[0] + b[3]) / 2, (b[1] + b[4]) / 2, (b[2] + b[5]) / 2);
        this.proxyMesh.setMatrixAt(j, m4.compose(pos, q, sc));
      });
      this.proxyMesh.count = proxies.length;
      this.proxyMesh.instanceMatrix.needsUpdate = true;
      this._proxyColors();
      this.lodStats = { full: keep.length, proxy: proxies.length, hidden, culled, tris: total + proxies.length * 12 };
      this._updateStatus();
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
    /** Lấy mẫu toạ độ đỉnh (của các đối tượng 'ids', hoặc toàn bộ) để căn camera theo hình thật. */
    _samplePoints(ids, max = 40000) {
      const pos = this.geom && this.geom.getAttribute("position");
      if (!pos) return null;
      const a = pos.array, out = [];
      if (!ids) {
        const n = pos.count, step = Math.max(1, Math.floor(n / max));
        for (let i = 0; i < n; i += step) out.push(a[i * 3], a[i * 3 + 1], a[i * 3 + 2]);
      } else {
        let total = 0;
        for (const i of ids) total += this.meta.objects[i][7];
        const step = Math.max(1, Math.floor(total / max));
        for (const i of ids) {
          const o = this.meta.objects[i];
          for (let k = o[6]; k < o[6] + o[7]; k += step) out.push(a[k * 3], a[k * 3 + 1], a[k * 3 + 2]);
        }
      }
      return out.length ? out : null;
    }

    _flyToBox(box, pts) {
      const center = box.getCenter(new T.Vector3());
      const size = Math.max(box.getSize(new T.Vector3()).length(), 1);
      const dir = this.camera.position.clone().sub(this.target).normalize();
      if (!isFinite(dir.x) || dir.lengthSq() < 0.5) dir.set(0.6, -0.6, 0.5).normalize();
      // căn khít: chiếu 8 góc hộp bao lên mặt phẳng camera, chọn khoảng cách nhỏ nhất để thấy trọn (lề 8%)
      const vfov = (this.camera.fov * Math.PI) / 180;
      const tanV = Math.tan(vfov / 2), tanH = tanV * Math.max(0.2, this.camera.aspect || 1);
      const fwd = dir.clone().negate();
      let right = new T.Vector3().crossVectors(fwd, this.camera.up);
      if (right.lengthSq() < 1e-6) right.set(1, 0, 0);
      right.normalize();
      const up = new T.Vector3().crossVectors(right, fwd).normalize();
      let dist = 0;
      const fitPoint = (x, y, z) => {
        const vx = x - center.x, vy = y - center.y, vz = z - center.z;
        const zc = vx * dir.x + vy * dir.y + vz * dir.z;
        const px = Math.abs(vx * right.x + vy * right.y + vz * right.z);
        const py = Math.abs(vx * up.x + vy * up.y + vz * up.z);
        dist = Math.max(dist, zc + px / tanH, zc + py / tanV);
      };
      if (pts && pts.length) {
        // căn theo hình thật: dịch tâm về giữa vùng hình chiếu rồi tính khoảng cách
        let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
        for (let i = 0; i < pts.length; i += 3) {
          const vx = pts[i] - center.x, vy = pts[i + 1] - center.y, vz = pts[i + 2] - center.z;
          const px = vx * right.x + vy * right.y + vz * right.z, py = vx * up.x + vy * up.y + vz * up.z;
          if (px < minX) minX = px; if (px > maxX) maxX = px; if (py < minY) minY = py; if (py > maxY) maxY = py;
        }
        center.add(right.clone().multiplyScalar((minX + maxX) / 2)).add(up.clone().multiplyScalar((minY + maxY) / 2));
        for (let i = 0; i < pts.length; i += 3) fitPoint(pts[i], pts[i + 1], pts[i + 2]);
        dist *= 1.06;
      } else {
        for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y]) for (const z of [box.min.z, box.max.z]) fitPoint(x, y, z);
        dist *= 1.08;
      }
      dist = Math.max(dist, size * 0.05);
      this.target.copy(center);
      this.camera.position.copy(center).add(dir.multiplyScalar(dist));
      this._render();
    }

    fitAll() {
      if (!this.bounds) return;
      const ids = this.isolateSet ? [...this.isolateSet] : null;
      this._flyToBox(ids ? this._boxOf(ids) : this.bounds, this._samplePoints(ids));
    }

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

    clearSelection(notify) {
      this.selected = [];
      if (notify && this.opts.onSelect) setTimeout(() => this.opts.onSelect([]), 0);
      this.activeClash = null;
      if (this.activeMarker) this.activeMarker.visible = false;
      this._highlight([], []);
      this._recolor();
      this.info.style.display = "none";
      this._updateStatus();
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
      this._updateStatus();
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
      const on = this.fadeOthers && this.selected.length > 0;
      this.mat.transparent = on;
      this.mat.opacity = on ? 0.18 : 1;
      if (this.proxyMat) { this.proxyMat.transparent = on; this.proxyMat.opacity = on ? 0.18 : 1; this.proxyMat.depthWrite = !on; this.proxyMat.needsUpdate = true; }
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
      const targets = this.proxyMesh && this.proxyMesh.count ? [this.mesh, this.proxyMesh] : [this.mesh];
      const hits = rc.intersectObjects(targets, false);
      if (!hits.length) { if (!setPivot && !(e.ctrlKey || e.metaKey)) this.clearSelection(true); return; }
      const h = hits[0];
      if (setPivot) {
        const off = this.camera.position.clone().sub(this.target);
        this.target.copy(h.point);
        this.camera.position.copy(h.point).add(off);
        this._render();
        return;
      }
      const oi = h.object === this.proxyMesh ? this.proxyIds[h.instanceId] : this.triObj[h.faceIndex];
      if (e.ctrlKey || e.metaKey) {
        const set = new Set(this.selected);
        if (set.has(oi)) set.delete(oi); else set.add(oi);
        this.select([...set]);
      } else this.select([oi]);
    }

    // ------------------------------------------------------------------ chọn / ẩn / cô lập
    /** Đặt tập đối tượng đang chọn (không di chuyển camera trừ khi opts.zoom). */
    select(ids, opts = {}) {
      ids = [...new Set(ids || [])].filter((i) => i >= 0 && i < this.meta.objects.length);
      this.selected = ids;
      this.activeClash = null;
      if (this.activeMarker) this.activeMarker.visible = false;
      this._highlight([], ids.length <= 20000 ? ids : ids.slice(0, 20000));
      this._recolor();
      if (ids.length === 1) this._showInfo(ids[0], 1);
      else if (ids.length > 1) {
        const by = {};
        for (const i of ids) { const c = this.meta.classes[this.meta.objects[i][3]]; by[c] = (by[c] || 0) + 1; }
        this.info.innerHTML = `<b>${ids.length.toLocaleString()} đối tượng đã chọn</b> <span class="ttv-x">✕</span><ul style="color:inherit">` +
          Object.entries(by).sort((a, b) => b[1] - a[1]).slice(0, 8).map(([c, n]) => `<li style="color:inherit">${esc(c)}: ${n.toLocaleString()}</li>`).join("") +
          `</ul><div class="ttv-k">H ẩn · I cô lập · Z phóng tới · Esc bỏ chọn · chuột phải: thêm lệnh</div>`;
        this.info.style.display = "block";
        this.info.querySelector(".ttv-x").onclick = () => (this.info.style.display = "none");
      } else this.info.style.display = "none";
      if (opts.zoom && ids.length) this._flyToBox(this._boxOf(ids), this._samplePoints(ids, 20000));
      this._updateStatus();
      if (ids.length === 1 && this.opts.onPick) {
        const o = this.meta.objects[ids[0]];
        this.opts.onPick({ index: ids[0], file: this.meta.files[o[0]], step: o[1], guid: o[2] });
      }
      if (this.opts.onSelect && !opts.silent) this.opts.onSelect(ids);
    }

    getSelection() { return [...this.selected]; }
    refsOf(ids) { return ids.map((i) => { const o = this.meta.objects[i]; return [this.meta.files[o[0]], o[1]]; }); }

    hide(ids) { for (const i of ids) this.hiddenSet.add(i); this._rebuildIndex(); this._updateStatus(); }
    hideSelected() { if (!this.selected.length) return; const ids = this.selected; this.clearSelection(true); this.hide(ids); }
    hideOthers() {
      if (!this.selected.length) return;
      const keep = new Set(this.selected);
      this.meta.objects.forEach((o, i) => { if (!keep.has(i)) this.hiddenSet.add(i); });
      this._rebuildIndex(); this._updateStatus();
    }
    isolateSelected() { if (this.selected.length) this.isolate(this.selected); this._updateStatus(); }
    unhideAll() { this.hiddenSet.clear(); this.isolateSet = null; this._rebuildIndex(); this._updateStatus(); }
    zoomSelection() { if (this.selected.length) this._flyToBox(this._boxOf(this.selected), this._samplePoints(this.selected, 20000)); else this.fitAll(); }
    selectSame(kind) {
      if (!this.selected.length) return;
      const objs = this.meta.objects, col = kind === "class" ? 3 : 0;
      const vals = new Set(this.selected.map((i) => objs[i][col]));
      const ids = [];
      objs.forEach((o, i) => { if (vals.has(o[col]) && !this.hiddenSet.has(i) && (!this.isolateSet || this.isolateSet.has(i))) ids.push(i); });
      this.select(ids);
    }
    // ------------------------------------------------------------------ bản vẽ 2D phủ lên mô hình
    /** segs: Float32Array (x1,y1,x2,y2) toạ độ tương đối gốc cảnh; vẽ ở cao độ z (mặc định: trên đỉnh mô hình). */
    setOverlay(segs, opts = {}) {
      if (this.overlay) { this.scene.remove(this.overlay); this.overlay.geometry.dispose(); this.overlay = null; }
      if (!segs || !segs.length) { this._render(); return; }
      const z = opts.z != null ? opts.z : (this.bounds ? this.bounds.max.z + 0.3 : 0);
      const n = segs.length / 4, pos = new Float32Array(n * 6);
      for (let i = 0; i < n; i++) {
        pos[i * 6] = segs[i * 4]; pos[i * 6 + 1] = segs[i * 4 + 1]; pos[i * 6 + 2] = z;
        pos[i * 6 + 3] = segs[i * 4 + 2]; pos[i * 6 + 4] = segs[i * 4 + 3]; pos[i * 6 + 5] = z;
      }
      const g = new T.BufferGeometry();
      g.setAttribute("position", new T.BufferAttribute(pos, 3));
      this.overlay = new T.LineSegments(g, new T.LineBasicMaterial({ color: opts.color || 0x1b2a41, transparent: true, opacity: 0.75, depthTest: false }));
      this.overlay.renderOrder = 8;
      this.overlay.frustumCulled = false;
      this.scene.add(this.overlay);
      this._render();
    }

    setOverlayVisible(on) { if (this.overlay) { this.overlay.visible = !!on; this._render(); } }

    /** pts: [{x,y,z,c:[r,g,b]}] toạ độ tương đối gốc cảnh - điểm đánh dấu kết quả đối chiếu bản vẽ */
    setMarkers(pts) {
      if (this.markers) { this.scene.remove(this.markers); this.markers.geometry.dispose(); this.markers = null; }
      if (!pts || !pts.length) { this._render(); return; }
      const pos = new Float32Array(pts.length * 3), col = new Float32Array(pts.length * 3);
      pts.forEach((p, i) => { pos.set([p.x, p.y, p.z], i * 3); col.set(p.c, i * 3); });
      const g = new T.BufferGeometry();
      g.setAttribute("position", new T.BufferAttribute(pos, 3));
      g.setAttribute("color", new T.BufferAttribute(col, 3));
      this.markers = new T.Points(g, new T.PointsMaterial({ size: 11, sizeAttenuation: false, vertexColors: true, depthTest: false,
        map: this._dotTexture(false), transparent: true }));
      this.markers.renderOrder = 12;
      this.markers.frustumCulled = false;
      this.scene.add(this.markers);
      this._render();
    }

    /** Bay tới 1 điểm (toạ độ tương đối gốc cảnh), khung nhìn rộng r mét */
    focusPoint(x, y, z, r = 25) {
      this._autoFit = false;
      const c = new T.Vector3(x, y, z);
      this._flyToBox(new T.Box3().setFromCenterAndSize(c, new T.Vector3(r, r, r)));
      if (this.activeMarker) {
        const ap = this.activeMarker.geometry.getAttribute("position");
        ap.setXYZ(0, x, y, z); ap.needsUpdate = true; this.activeMarker.visible = true;
      }
      this._render();
    }

    /** Nhìn bằng (từ trên xuống) - thuận tiện so với bản vẽ mặt bằng */
    viewTop() {
      this._autoFit = false;
      const off = this.camera.position.clone().sub(this.target);
      const d = off.length();
      this.camera.position.set(this.target.x, this.target.y - d * 0.001, this.target.z + d);
      this._render();
    }

    setBoxMode(on) { this.boxMode = !!on; this.renderer.domElement.style.cursor = this.boxMode ? "crosshair" : ""; }

    _drawBox(x0, y0, x1, y1) {
      if (!this._boxEl) {
        this._boxEl = el("div", { class: "ttv-box" });
        this.c.appendChild(this._boxEl);
      }
      const r = this.c.getBoundingClientRect();
      Object.assign(this._boxEl.style, { display: "block", left: Math.min(x0, x1) - r.left + "px", top: Math.min(y0, y1) - r.top + "px",
        width: Math.abs(x1 - x0) + "px", height: Math.abs(y1 - y0) + "px" });
    }

    _boxSelect(x0, y0, x1, y1, add) {
      const rect = this.renderer.domElement.getBoundingClientRect();
      const toN = (x, y) => [((x - rect.left) / rect.width) * 2 - 1, -((y - rect.top) / rect.height) * 2 + 1];
      const [ax, ay] = toN(Math.min(x0, x1), Math.max(y0, y1)), [bx, by] = toN(Math.max(x0, x1), Math.min(y0, y1));
      this.camera.updateMatrixWorld();
      const v = new T.Vector3(), C = this.centers, ids = add ? new Set(this.selected) : new Set();
      this.meta.objects.forEach((o, i) => {
        if (this.hiddenSet.has(i) || (this.isolateSet && !this.isolateSet.has(i))) return;
        v.set(C[i * 3], C[i * 3 + 1], C[i * 3 + 2]).project(this.camera);
        if (v.z < 1 && v.x >= ax && v.x <= bx && v.y >= ay && v.y <= by) ids.add(i);
      });
      this.select([...ids]);
    }

    _hideMenu() { if (this._menu) this._menu.style.display = "none"; }

    _contextMenu(e) {
      // chuột phải lên đối tượng chưa chọn -> chọn đối tượng đó trước
      const before = this.selected.length;
      if (!before) this._pick({ clientX: e.clientX, clientY: e.clientY, ctrlKey: false }, false);
      if (!this._menu) { this._menu = el("div", { class: "ttv-menu" }); this.c.appendChild(this._menu); }
      const has = this.selected.length > 0, anyHidden = this.hiddenSet.size > 0 || !!this.isolateSet;
      const items = [
        ["zoom", "Phóng tới đối tượng chọn", "Z", has], ["hide", "Ẩn đối tượng chọn", "H", has],
        ["iso", "Cô lập đối tượng chọn", "I", has], ["others", "Ẩn các đối tượng khác", "", has], null,
        ["sameClass", "Chọn cùng lớp IFC", "", has], ["sameFile", "Chọn cùng file", "", has],
        ["box", this.boxMode ? "Tắt chọn vùng" : "Chọn vùng (kéo khung)", "Alt+kéo", true], null,
        ["show", "Hiện tất cả", "U", anyHidden], ["clear", "Bỏ chọn", "Esc", has], ["fit", "Toàn cảnh", "F", true],
      ];
      this._menu.innerHTML = items.map((it) => it ? `<button data-a="${it[0]}" ${it[3] ? "" : "disabled"}><span>${it[1]}</span><kbd>${it[2]}</kbd></button>` : "<hr>").join("");
      const r = this.c.getBoundingClientRect();
      Object.assign(this._menu.style, { display: "block", left: Math.min(e.clientX - r.left, r.width - 230) + "px", top: Math.min(e.clientY - r.top, r.height - 330) + "px" });
      this._menu.querySelectorAll("button").forEach((b) => (b.onclick = () => {
        this._hideMenu();
        ({ zoom: () => this.zoomSelection(), hide: () => this.hideSelected(), iso: () => this.isolateSelected(),
           others: () => this.hideOthers(), sameClass: () => this.selectSame("class"), sameFile: () => this.selectSame("file"),
           box: () => { this.setBoxMode(!this.boxMode); if (this.opts.onBoxMode) this.opts.onBoxMode(this.boxMode); },
           show: () => this.unhideAll(), clear: () => this.clearSelection(true), fit: () => this.fitAll() })[b.dataset.a]();
      }));
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
  .ttv-box{position:absolute;display:none;border:1.5px dashed #1a73ff;background:rgba(26,115,255,.08);pointer-events:none;z-index:5}
  .ttv-menu{position:absolute;display:none;z-index:20;min-width:220px;background:#fff;border:1px solid #cfd6df;border-radius:8px;box-shadow:0 6px 24px rgba(0,0,0,.18);padding:4px}
  .ttv-menu button{display:flex;width:100%;justify-content:space-between;gap:16px;border:0;background:none;padding:6px 10px;border-radius:5px;cursor:pointer;font:inherit;color:inherit;text-align:left}
  .ttv-menu button:hover:not(:disabled){background:#eef3fb}
  .ttv-menu button:disabled{color:#a0aab8;cursor:default}
  .ttv-menu kbd{color:#7a8798;font:11px/1.6 inherit}
  .ttv-menu hr{border:0;border-top:1px solid #e6ebf1;margin:3px 0}
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
