/* Thẩm Tra Mô Hình BIM - giao diện web */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (n) => (n == null ? "-" : Number(n).toLocaleString("vi-VN"));
const SEV = { ERROR: "Lỗi", WARNING: "Cảnh báo", INFO: "Thông tin" };

const S = {
  state: null, viewer: null, sceneKey: null, tab: "health", results: null, resFilter: {}, clash: null,
  clashFilter: {}, rules: null, tests: null, health: null, aiHistory: [], aiCards: {}, aiListCard: null,
  logs: [], selected: null,
};

// ====================================================================== tiện ích
async function api(path, opts = {}) {
  const o = Object.assign({ headers: {} }, opts);
  if (o.json !== undefined) { o.body = JSON.stringify(o.json); o.headers["Content-Type"] = "application/json"; }
  const r = await fetch(path, o);
  if (r.status === 401) { location.href = "/login"; throw new Error("Cần đăng nhập"); }
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = j.detail || msg; } catch (e) { /* */ }
    throw new Error(msg);
  }
  const ct = r.headers.get("content-type") || "";
  return ct.includes("json") ? r.json() : r.arrayBuffer();
}
function toast(msg, err) {
  const t = $("#toast"); t.textContent = msg; t.className = "toast on" + (err ? " err" : "");
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.className = "toast"), err ? 6000 : 3500);
}
function status(msg) { $("#status").textContent = msg; log(msg); }
function log(msg) { S.logs.push(`[${new Date().toLocaleTimeString("vi-VN")}] ${msg}`); if (S.tab === "log") renderTab(); }
function progress(on, frac) {
  const p = $("#progress"); p.classList.toggle("on", !!on);
  p.classList.toggle("indet", on && frac == null);
  $("#progressbar").style.width = frac == null ? "30%" : Math.round(frac * 100) + "%";
}
async function runJob(promise) {
  const j = await promise;
  if (j.status === "done" && !j.id) return j;
  progress(true);
  while (true) {
    await new Promise((r) => setTimeout(r, 500));
    const s = await api(`/api/jobs/${j.id}`);
    progress(true, s.total ? s.done / s.total : null);
    if (s.message) $("#status").textContent = s.message;
    if (s.status === "done") { progress(false); log(`${s.label}: xong (${s.seconds}s)`); return s; }
    if (s.status === "error") { progress(false); throw new Error(s.error); }
  }
}
function modal(title, bodyHtml, buttons) {
  $("#mtitle").textContent = title; $("#mbody").innerHTML = bodyHtml; const f = $("#mfoot"); f.innerHTML = "";
  (buttons || [{ text: "Đóng" }]).forEach((b) => {
    const el = document.createElement("button"); el.className = "btn" + (b.primary ? " primary" : ""); el.textContent = b.text;
    el.onclick = async () => { if (b.onClick && (await b.onClick()) === false) return; closeModal(); };
    f.appendChild(el);
  });
  $("#modal").classList.add("on");
}
function closeModal() { $("#modal").classList.remove("on"); }
function pickFile(accept, multiple) {
  return new Promise((res) => {
    const i = document.createElement("input"); i.type = "file"; i.accept = accept; i.multiple = !!multiple;
    i.onchange = () => res([...i.files]); i.click();
  });
}
async function upload(path, files, field = "file") {
  const fd = new FormData(); for (const f of files) fd.append(field, f);
  return api(path, { method: "POST", body: fd });
}

// ====================================================================== khởi động
async function init() {
  $("#ttv-css").textContent = ThamTraViewer.CSS;
  // chiều cao bảng dưới theo màn hình: ~30% (laptop) để khung 3D đủ lớn
  document.documentElement.style.setProperty("--dock-h", Math.max(150, Math.min(340, Math.round(window.innerHeight * 0.3))) + "px");
  S.viewer = new ThamTraViewer($("#viewer"), {
    onPick: (p) => onPick(p),
    onSelect: (ids) => onSelectChange(ids),
    onBoxMode: (on) => $("#btnBox").classList.toggle("active", on),
    infoPanel: false,            // thông tin đối tượng / xung đột hiển thị ở khung Thuộc tính bên trái
    legend: false,               // chú giải màu hiển thị ở khung bên trái
    onLegend: (items, mode) => renderLegend(items, mode),
    onClashFocus: (c) => showClashCard(c),
  });
  bindUI();
  await refreshState();
  if (S.state.models.length) { await loadScene(); }
  renderTab();
}

function bindUI() {
  // khung làm việc chỉ dùng menu của phần mềm; ô nhập chữ vẫn giữ menu trình duyệt (dán / sao chép)
  document.addEventListener("contextmenu", (e) => {
    const t = e.target;
    if (t.closest && t.closest("input, textarea, [contenteditable='true']")) return;
    if (t.closest && t.closest("#workspace, .ribbon, .titlebar, .statusbar, .modal")) e.preventDefault();
  });
  const openFiles = async (files) => {
    files = files.filter((f) => f.name.toLowerCase().endsWith(".ifc"));
    if (!files.length) return toast("Chọn file .ifc", true);
    try {
      status(`Đang tải lên ${files.length} file...`); progress(true);
      await runJob(upload("/api/models", files, "files"));
      await refreshState(); await loadScene(); renderTab();
      toast(`Đã mở ${files.length} file IFC`);
    } catch (e) { progress(false); toast(e.message, true); }
  };
  $("#fileInput").onchange = (e) => openFiles([...e.target.files]);
  $("#fileInput2").onchange = (e) => openFiles([...e.target.files]);
  const vw = $(".viewerwrap");
  ["dragenter", "dragover"].forEach((ev) => vw.addEventListener(ev, (e) => { e.preventDefault(); $("#dropmask").classList.add("on"); }));
  ["dragleave", "drop"].forEach((ev) => vw.addEventListener(ev, (e) => { e.preventDefault(); $("#dropmask").classList.remove("on"); }));
  vw.addEventListener("drop", (e) => openFiles([...e.dataTransfer.files]));

  $$("[data-dock]").forEach((b) => (b.onclick = () => showTab(b.dataset.dock)));
  $$("#docktabs [data-tab]").forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));
  $("#dockToggle").onclick = () => $(".center").classList.toggle("dock-min");
  $("#btnCheck").onclick = runCheck;
  $("#btnClash").onclick = runClash;
  $("#btnColor").onclick = () => {
    const on = S.viewer.colorMode !== "status";
    S.viewer._userColorMode = true;
    S.viewer.setColorMode(on ? "status" : (S.viewer.ifcColors ? "ifc" : "file"));
    $("#btnColor").classList.toggle("active", on);
    toast(on ? "Tô màu kết quả: đỏ = lỗi · vàng = cảnh báo · xanh = đạt (bấm lại để về màu IFC)" : "Đã về màu gốc IFC");
  };
  $("#btnIsoFail").onclick = isolateFailing;
  $("#btnShowAll").onclick = () => { S.viewer.unhideAll(); S.viewer.fitAll(); };
  $("#btnBox").onclick = () => { S.viewer.setBoxMode(!S.viewer.boxMode); $("#btnBox").classList.toggle("active", S.viewer.boxMode); };
  $("#btnHide").onclick = () => needSel() && S.viewer.hideSelected();
  $("#btnIsoSel").onclick = () => needSel() && S.viewer.isolateSelected();
  $("#btnHideOthers").onclick = () => needSel() && S.viewer.hideOthers();
  $("#btnFit").onclick = () => { S.viewer.resize(); S.viewer.fitAll(); };
  $("#btnAI").onclick = () => toggleAI();
  $("#aiClose").onclick = () => toggleAI(false);
  $("#aiSend").onclick = askAI;
  $("#aiq").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); askAI(); } });
  $("#btnSettings").onclick = openSettings;
  $("#btnProjOpen").onclick = openProjects;
  $("#btnProjNew").onclick = newProject;
  $("#startNew").onclick = newProject;
  $("#projBtn").onclick = (e) => { e.stopPropagation(); toggleProjMenu(); };
  document.addEventListener("click", (e) => { if (!e.target.closest(".projsel")) $("#projMenu").classList.remove("on"); });
  $("#btnProjSave").onclick = () => saveProject(false);
  $("#btnProjSaveAs").onclick = () => saveProject(true);
  $("#mclose").onclick = closeModal;
  // kéo đổi chiều cao bảng dưới
  let drag = null;
  $("#splitter").addEventListener("pointerdown", (e) => { drag = { y: e.clientY, h: $("#dock").offsetHeight }; e.target.setPointerCapture(e.pointerId); });
  $("#splitter").addEventListener("pointermove", (e) => {
    if (!drag) return;
    const h = Math.max(80, Math.min(window.innerHeight - 260, drag.h - (e.clientY - drag.y)));
    document.documentElement.style.setProperty("--dock-h", h + "px");
  });
  $("#splitter").addEventListener("pointerup", () => { drag = null; S.viewer.resize(); });
}

async function refreshState() {
  S.state = await api("/api/state");
  const st = S.state;
  $("#credit").textContent = `Phát triển bởi ${st.app.author} · ĐT: ${st.app.phone} · v${st.app.version}`;
  const u = st.user;
  $("#userbox").innerHTML = u ? `${u.picture ? `<img src="${esc(u.picture)}" alt="" referrerpolicy="no-referrer">` : ""}<span title="${esc(u.email)}">${esc(u.name || u.email)}</span><a href="/auth/logout">Đăng xuất</a>` : "";
  $("#projName").textContent = st.project ? st.project.name : "Chưa chọn dự án";
  $("#docname").textContent = (st.models.length ? `${st.models.length} file: ` + st.models.map((m) => m.file).join(" · ") : "") +
    (st.project && st.last_saved ? `  ·  đã lưu tự động ${st.last_saved}` : "");
  $("#addLbl").innerHTML = st.project ? "Thêm<br>file IFC" : "Mở<br>file IFC";
  $("#startAddLbl").textContent = st.project ? "Thêm file IFC vào dự án" : "Mở nhanh file IFC (không lưu)";
  $("#startProj").innerHTML = st.project && !st.models.length
    ? `<p><b>📁 ${esc(st.project.name)}</b> chưa có file nào - bấm <b>Thêm file IFC vào dự án</b> hoặc kéo thả file vào đây.</p>` : "";
  if (!st.models.length) renderRecent();
  $("#start").classList.toggle("hidden", st.models.length > 0);
  const r = st.result;
  $("#statusRight").textContent = `${st.models.length} file · ${fmt(st.models.reduce((a, m) => a + m.elements, 0))} đối tượng` +
    (r ? ` · đạt ${r.compliance.toFixed(1)}% · ${fmt(r.errors)} lỗi` : "") + (st.clash.count != null ? ` · ${fmt(st.clash.count)} xung đột` : "");
  renderBrowser(); renderAIInfo();
}

async function loadScene() {
  if (!S.state.models.some((m) => !m.error)) return;
  try {
    status("Đang dựng mô hình 3D...");
    const j = await runJob(api("/api/scene", { method: "POST" }));
    void j;
    const [meta, data] = await Promise.all([api("/api/scene/meta"), api("/api/scene/data")]);
    $("#start").classList.add("hidden");     // ẩn màn hình chào trước khi căn camera
    S.viewer.load(meta, data);
    S.sceneKey = meta;
    loadDrawingOverlay();
    status(`Mô hình 3D: ${fmt(meta.objects.length)} đối tượng`);
  } catch (e) { toast("Không dựng được 3D: " + e.message, true); }
}

// ====================================================================== khung bên trái: chú giải, xung đột
function renderLegend(items, mode) {
  const label = { ifc: "theo IFC", status: "kết quả kiểm tra", file: "theo file", class: "theo lớp IFC" }[mode] || "";
  $("#legendMode").textContent = label;
  $("#legend").innerHTML = items.length ? items.map(([c, t]) => `<div class="lg" title="${esc(t)}"><i style="background:${c}"></i>${esc(t)}</div>`).join("")
    : '<div class="muted small">—</div>';
}
function showClashCard(c) {
  const o = S.viewer.meta ? S.viewer.meta.objects : [];
  const nm = (i) => (i == null || !o[i] ? "?" : `${esc(S.viewer.meta.classes[o[i][3]])} · ${esc(o[i][4])}`);
  const html = `<div class="clashcard"><b class="t">💥 Xung đột ${esc(c.id)}</b> <span class="muted small">${esc(c.t)}</span>
    <div><b>${esc(c.m || ("Độ sâu xuyên " + c.d.toFixed(3) + " m"))}</b></div>
    <div class="small">🟪 A: ${nm(c.a)}</div><div class="small">🟦 B: ${nm(c.b)}</div></div>`;
  S.clashCard = html;
  const ex = $("#props .clashcard");
  if (ex) ex.outerHTML = html; else $("#props").insertAdjacentHTML("afterbegin", html);
}
async function waitIdle(maxMs = 600000) {
  const t0 = Date.now();
  while (Date.now() - t0 < maxMs) {
    const st = await api("/api/state");
    if (!st.busy) return true;
    $("#status").textContent = "Đang đợi tác vụ hiện tại hoàn tất...";
    await new Promise((r) => setTimeout(r, 700));
  }
  return false;
}

// ====================================================================== chọn đối tượng
function needSel() {
  if (S.viewer.getSelection().length) return true;
  toast("Chưa chọn đối tượng nào - nháy vào mô hình (Ctrl để chọn thêm) hoặc dùng Chọn vùng");
  return false;
}
function onSelectChange(ids) {
  if (ids.length > 1) {
    const refs = S.viewer.refsOf(ids);
    S.aiListCard = { title: "Đối tượng đang chọn", count: ids.length, refs, kind: "selection" };
    const meta = S.viewer.meta, by = {};
    for (const i of ids) { const c = meta.classes[meta.objects[i][3]]; by[c] = (by[c] || 0) + 1; }
    $("#props").innerHTML = `<div class="pad"><b>${fmt(ids.length)} đối tượng đang chọn</b></div><table class="kv">` +
      Object.entries(by).sort((a, b) => b[1] - a[1]).map(([c, n]) => `<tr><td>${esc(c)}</td><td>${fmt(n)}</td></tr>`).join("") +
      `</table><div class="pad"><button class="btn small" id="selList">☰ Xem danh sách</button></div>`;
    $("#selList").onclick = () => showTab("ailist");
    if (S.tab === "ailist") renderTab();
  } else if (!ids.length && S.aiListCard && S.aiListCard.kind === "selection") {
    S.aiListCard = null;
    if (S.tab === "ailist") renderTab();
  }
}
function onPick(p) {
  showProps(p.file, p.step);
  markRows(p.file, p.step);
}
async function showProps(file, step) {
  S.selected = [file, step];
  try {
    const d = await api(`/api/element?file=${encodeURIComponent(file)}&step=${step}`);
    let h = `<table class="kv">${d.identity.filter((x) => x[1]).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join("")}</table>`;
    if (d.issues.length) {
      h += `<div class="sect">Lỗi kiểm tra (${d.issues.length})</div><div>` +
        d.issues.map((i) => `<div class="issue"><span class="sev${i.sev}">[${esc(i.rule)}]</span> ${esc(i.msg)}${i.actual ? ` <span class="muted">(${esc(i.actual)})</span>` : ""}</div>`).join("") + "</div>";
    }
    if (d.clashes.length) {
      h += `<div class="sect">Xung đột (${d.clashes.length})</div><div>` +
        d.clashes.map((c) => `<div class="issue click" data-clash="${c.id}">💥 <b>${esc(c.id)}</b> ${esc(c.detail)} <span class="muted">${esc(c.test)}</span></div>`).join("") + "</div>";
    }
    for (const p of d.psets) {
      h += `<div class="sect">${esc(p.name || "(Pset không tên)")}</div><table class="kv">` +
        p.props.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${v ? esc(v) : '<span class="empty">(trống)</span>'}</td></tr>`).join("") + "</table>";
    }
    $("#props").innerHTML = (S.clashCard || "") + h;
    S.clashCard = null;
    $$("#props .sect").forEach((s) => (s.onclick = () => s.classList.toggle("closed")));
    $$("#props [data-clash]").forEach((el) => (el.onclick = () => S.viewer.focusClash(el.dataset.clash)));
  } catch (e) { $("#props").innerHTML = `<div class="pad muted">${esc(e.message)}</div>`; }
}
function focusRefs(refs, opts = {}) {
  const ids = S.viewer.resolve(refs);
  if (!ids.length) { toast("Đối tượng không có hình học 3D"); return []; }
  if (opts.isolate) S.viewer.isolate(ids);
  S.viewer.focus(ids, { silent: true });
  return ids;
}
function markRows(file, step) {
  $$("#dockbody tr.sel").forEach((r) => r.classList.remove("sel"));
  const row = $(`#dockbody tr[data-file="${CSS.escape(file)}"][data-step="${step}"]`);
  if (row) { row.classList.add("sel"); row.scrollIntoView({ block: "nearest" }); }
}

// ====================================================================== trình duyệt dự án
async function renderBrowser() {
  const st = S.state;
  let h = '<ul class="tree">';
  h += `<li><div class="row"><span class="tw">▾</span>🗂️ <b>Mô hình</b><span class="cnt">${st.models.length}</span></div><ul>`;
  if (!S.health && st.models.length) { try { S.health = await api("/api/health"); } catch (e) { S.health = { classes: [] }; } }
  for (const m of st.models) {
    const cls = (S.health ? S.health.classes : []).filter((c) => c[0] === m.file);
    h += `<li class="closed"><div class="row" data-file="${esc(m.file)}"><span class="tw">▸</span>${m.error ? "⛔" : m.warnings.length ? '<span class="warnicon">⚠</span>' : "📄"} ${esc(shortName(m.file))}` +
      `<span class="acts"><button data-act="iso">Cô lập</button><button data-act="del">Đóng</button></span><span class="cnt">${fmt(m.elements)}</span></div><ul>`;
    for (const c of cls.slice(0, 60)) {
      h += `<li><div class="row" data-file="${esc(m.file)}" data-cls="${esc(c[1])}" title="${esc(c[2])}"><span class="tw"></span>${esc(c[1])}${c[2] && c[2] !== c[1] ? ` <span class="muted small">${esc(String(c[2]).slice(0, 24))}</span>` : ""}` +
        `<span class="acts"><button data-act="hl">Tô sáng</button><button data-act="iso">Cô lập</button></span><span class="cnt">${fmt(c[3] + c[4])}</span></div></li>`;
    }
    h += "</ul></li>";
  }
  h += "</ul></li>";
  h += `<li><div class="row"><span class="tw">▾</span>✅ <b>Kiểm tra chất lượng</b></div><ul>` +
    [["health", "🩺 Sức khỏe mô hình"], ["rules", `📋 Quy tắc BEP <span class="cnt">${st.rules.enabled}/${st.rules.count}</span>`],
     ["results", `✅ Kết quả <span class="cnt">${st.result ? st.result.compliance.toFixed(1) + "%" : ""}</span>`],
     ["clash", `⚠️ Xung đột <span class="cnt">${st.clash.count != null ? fmt(st.clash.count) : ""}</span>`],
     ["inventory", "🧾 Thống kê thuộc tính"]]
      .map(([t, l]) => `<li><div class="row" data-tab="${t}"><span class="tw"></span>${l}</div></li>`).join("") + "</ul></li></ul>";
  $("#browser").innerHTML = h;
  $$("#browser .row").forEach((row) => {
    row.onclick = async (e) => {
      const act = e.target.dataset.act;
      const li = row.parentElement;
      if (row.dataset.tab) return showTab(row.dataset.tab);
      if (!act && li.querySelector("ul")) { li.classList.toggle("closed"); row.querySelector(".tw").textContent = li.classList.contains("closed") ? "▸" : "▾"; return; }
      if (act === "del") {
        if (!confirm(`Đóng file ${row.dataset.file}?`)) return;
        await api(`/api/models/${encodeURIComponent(row.dataset.file)}`, { method: "DELETE" });
        S.health = null; await refreshState(); await loadScene(); return;
      }
      const q = new URLSearchParams({ file: row.dataset.file || "", ifc_class: row.dataset.cls || "" });
      const r = await api("/api/select?" + q);
      if (act === "iso") focusRefs(r.refs, { isolate: true });
      else focusRefs(r.refs);
      status(`${fmt(r.count)} đối tượng`);
    };
  });
}
const shortName = (f) => f.replace(/\.ifc$/i, "").split(/[-_]/).slice(-2).join("-");

// ====================================================================== bảng dưới
function showTab(t) {
  S.tab = t;
  $(".center").classList.remove("dock-min");
  $$("#docktabs [data-tab]").forEach((b) => b.classList.toggle("on", b.dataset.tab === t));
  $$("[data-dock]").forEach((b) => b.classList.toggle("active", b.dataset.dock === t));
  renderTab();
}
async function renderTab() {
  $$("#docktabs [data-tab]").forEach((b) => b.classList.toggle("on", b.dataset.tab === S.tab));
  const fn = { health: tabHealth, rules: tabRules, results: tabResults, clash: tabClash, drawing: tabDrawing, inventory: tabInventory, ailist: tabAIList, log: tabLog }[S.tab];
  try { await fn($("#dockbody")); } catch (e) { $("#dockbody").innerHTML = `<div class="pad muted">${esc(e.message)}</div>`; }
}

// ---------------------------------------------------------------------- sức khỏe
async function tabHealth(el) {
  const st = S.state;
  if (!st.models.length) { el.innerHTML = '<div class="pad muted">Chưa mở file IFC.</div>'; return; }
  if (!S.health) S.health = await api("/api/health");
  const rows = st.models.map((m) => `<tr><td>${m.error ? "⛔" : m.warnings.length ? "⚠️" : "✅"} ${esc(m.file)}</td><td>${esc(m.schema)}</td><td>${esc(m.app)}</td>` +
    `<td class="n">${fmt(m.elements)}</td><td class="n">${fmt(m.parts)}</td><td class="n">${m.proxies ? `<b class="sevWARNING">${fmt(m.proxies)}</b>` : 0}</td>` +
    `<td>${esc(m.error || m.warnings.join("; "))}</td></tr>`).join("");
  const cls = S.health.classes.map((c) => `<tr class="click" data-file="${esc(c[0])}" data-cls="${esc(c[1])}"><td>${esc(shortName(c[0]))}</td>` +
    `<td class="${c[1] === "IfcBuildingElementProxy" ? "sevWARNING" : ""}">${esc(c[1])}</td><td class="muted">${esc(c[2])}</td><td class="n">${fmt(c[3])}</td><td class="n">${fmt(c[4])}</td></tr>`).join("");
  el.innerHTML = `<div class="tb"><b>Sức khỏe mô hình</b><span class="muted">· Proxy = đối tượng chưa được phân loại IFC đúng · Phần tử con = khối thuộc 1 cụm (assembly)</span></div>
    <div class="split2"><div><table class="grid"><tr><th>File</th><th>Schema</th><th>Phần mềm xuất</th><th>Đối tượng</th><th>Phần tử con</th><th>Proxy</th><th>Cảnh báo file</th></tr>${rows}</table></div>
    <div><table class="grid"><tr><th>File</th><th>Lớp IFC</th><th>PredefinedType / Loại</th><th>Số ĐT</th><th>Con</th></tr>${cls}</table></div></div>`;
  $$("tr.click", el).forEach((tr) => (tr.onclick = async () => {
    const r = await api("/api/select?" + new URLSearchParams({ file: tr.dataset.file, ifc_class: tr.dataset.cls }));
    focusRefs(r.refs); status(`${tr.dataset.cls}: ${fmt(r.count)} đối tượng`);
  }));
}

// ---------------------------------------------------------------------- quy tắc
async function tabRules(el) {
  if (!S.rules) S.rules = await api("/api/rules");
  const o = S.state.options;
  const sel = (name, val, opts) => `<select data-k="${name}">${opts.map((x) => `<option ${x === val ? "selected" : ""}>${x}</option>`).join("")}</select>`;
  const inp = (name, val, w) => `<input data-k="${name}" value="${esc(val)}" ${w ? `style="min-width:${w}px"` : ""}>`;
  const rows = S.rules.rules.map((r, i) => `<tr data-i="${i}" class="${r.enabled ? "" : "off"}"><td><input type="checkbox" data-k="enabled" ${r.enabled ? "checked" : ""}></td>` +
    `<td>${inp("id", r.id, 60)}</td><td>${inp("ifc_class", r.ifc_class, 150)}</td><td>${inp("file_filter", r.file_filter, 90)}</td><td>${sel("target", r.target, o.targets)}</td>` +
    `<td>${inp("pset", r.pset, 120)}</td><td>${inp("name", r.name, 130)}</td><td>${sel("condition", r.condition, o.conditions)}</td><td>${inp("value", r.value, 100)}</td>` +
    `<td>${sel("severity", r.severity, o.severities)}</td><td>${inp("description", r.description, 260)}</td><td>${inp("reference", r.reference, 110)}</td>` +
    `<td><button class="iconbtn" data-del="${i}" title="Xoá">🗑</button></td></tr>`).join("");
  el.innerHTML = `<div class="tb"><b>Quy tắc BEP</b><span class="muted">${esc(S.rules.source)} · ${S.rules.rules.length} quy tắc${S.rules.ids ? " · IDS: " + esc(S.rules.ids) : ""}</span>
    <span class="grow"></span>
    <button class="btn small" id="rSave">💾 Lưu thay đổi</button><button class="btn small" id="rAdd">＋ Thêm</button>
    <button class="btn small" id="rLoi">📑 Nhập Phụ lục LOI từ BEP</button><button class="btn small" id="rOpen">📂 Mở Excel/JSON/IDS</button>
    <button class="btn small" id="rDef">Bộ mẫu</button>${S.rules.ids ? '<button class="btn small" id="rNoIds">Bỏ IDS</button>' : ""}<a class="btn small" href="/api/rules/export">⬇ Excel</a></div>
    ${S.rules.warnings.length ? `<div class="tb sevWARNING">⚠ ${esc(S.rules.warnings.slice(0, 4).join(" · "))}</div>` : ""}
    <div class="scroll"><table class="grid"><tr><th>Bật</th><th>Mã</th><th>Lớp IFC áp dụng</th><th>Lọc tên file</th><th>Đối tượng KT</th><th>Property Set</th><th>Thuộc tính</th><th>Điều kiện</th><th>Giá trị</th><th>Mức độ</th><th>Mô tả yêu cầu</th><th>Tham chiếu BEP</th><th></th></tr>${rows}</table></div>`;
  const collect = () => $$("tr[data-i]", el).map((tr) => {
    const r = Object.assign({}, S.rules.rules[+tr.dataset.i]);
    $$("[data-k]", tr).forEach((x) => (r[x.dataset.k] = x.type === "checkbox" ? x.checked : x.value));
    return r;
  });
  const save = async (rules) => { S.rules = await api("/api/rules", { method: "PUT", json: { rules } }); await refreshState(); renderTab(); };
  $("#rSave").onclick = async () => { await save(collect()); toast("Đã lưu bộ quy tắc"); };
  $("#rAdd").onclick = async () => { const r = collect(); r.push({ id: `R${r.length + 1}`, enabled: true, ifc_class: "*", target: "PROPERTY", pset: "", name: "", condition: "EXISTS", value: "", severity: "ERROR", description: "", reference: "", file_filter: "", predefined_type: "", name_filter: "" }); await save(r); };
  $$("[data-del]", el).forEach((b) => (b.onclick = async () => { const r = collect(); r.splice(+b.dataset.del, 1); await save(r); }));
  $("#rDef").onclick = async () => { if (confirm("Thay bằng bộ quy tắc mẫu?")) { S.rules = await api("/api/rules/default", { method: "POST" }); await refreshState(); renderTab(); } };
  $("#rOpen").onclick = async () => { const f = await pickFile(".xlsx,.json,.ids"); if (!f.length) return; try { S.rules = await upload("/api/rules/import", f); await refreshState(); renderTab(); toast("Đã nạp " + f[0].name); } catch (e) { toast(e.message, true); } };
  if ($("#rNoIds")) $("#rNoIds").onclick = async () => { S.rules = await api("/api/rules/ids", { method: "DELETE" }); renderTab(); };
  $("#rLoi").onclick = importLOI;
}

async function importLOI() {
  const f = await pickFile(".docx,.xlsx"); if (!f.length) return;
  let d;
  try { d = await upload("/api/rules/loi/parse", f); } catch (e) { return toast(e.message, true); }
  const rows = d.components.map((c, i) => `<tr><td>${esc(c.component)}</td><td class="n">${c.count}</td><td><input data-i="${i}" data-k="ifc_class" class="inp" value="${esc(c.ifc_class)}"></td><td><input data-i="${i}" data-k="file_filter" class="inp" value="${esc(c.file_filter)}"></td></tr>`).join("");
  modal(`Gán hạng mục LOI (${d.items} thuộc tính) với mô hình`,
    `<p class="hint">Mỗi hạng mục trong BEP cần biết áp dụng cho đối tượng nào: nhập <b>Lọc tên file</b> (theo quy tắc đặt tên file của BEP) và/hoặc <b>Lớp IFC</b>. Hạng mục để '*' và trống sẽ được tạo nhưng tắt. Phần mềm đã tự đoán từ các file đang mở.</p>
     <table class="grid"><tr><th>Hạng mục (BEP)</th><th>Số TT</th><th>Lớp IFC</th><th>Lọc tên file (regex)</th></tr>${rows}</table>
     <p class="hint">File đang mở: ${esc(d.files.join(", ") || "(chưa có)")}</p>
     <label><input type="radio" name="loimode" value="replace" checked> Thay thế bộ quy tắc (giữ quy tắc chung GEN-*)</label> &nbsp; <label><input type="radio" name="loimode" value="append"> Thêm vào bộ hiện tại</label>`,
    [{ text: "Huỷ" }, { text: "Tạo quy tắc", primary: true, onClick: async () => {
      $$("#mbody input[data-i]").forEach((x) => (d.components[+x.dataset.i][x.dataset.k] = x.value));
      S.rules = await api("/api/rules/loi/apply", { method: "POST", json: { components: d.components, mode: $('input[name="loimode"]:checked').value } });
      await refreshState(); showTab("rules"); toast("Đã tạo quy tắc từ BEP - rà soát rồi bấm Chạy kiểm tra");
    } }]);
}

// ---------------------------------------------------------------------- kiểm tra & kết quả
async function runCheck() {
  if (!S.state.models.length) return toast("Hãy mở file IFC trước", true);
  try {
    if (S.tab === "rules" && S.rules) $("#rSave") && (await $("#rSave").onclick());
    await runJob(api("/api/check", { method: "POST" }));
    S.results = null; await refreshState(); await loadScene();
    showTab("results");
    const r = S.state.result; toast(`Kiểm tra xong: đạt ${r.compliance.toFixed(1)}%, ${fmt(r.errors)} lỗi, ${fmt(r.warnings)} cảnh báo`);
  } catch (e) { toast(e.message, true); }
}
async function fetchResults() {
  const q = new URLSearchParams(Object.assign({ limit: 1000 }, S.resFilter));
  S.results = await api("/api/results?" + q);
}
async function tabResults(el) {
  if (!S.results) await fetchResults();
  const R = S.results;
  if (!R.totals) { el.innerHTML = '<div class="pad muted">Chưa có kết quả. Bấm <b>▶ Chạy kiểm tra</b> trên thanh công cụ.</div>'; return; }
  const t = R.totals, f = S.resFilter;
  const opt = (arr, cur, all) => `<option value="">${all}</option>` + arr.map((x) => `<option ${x === cur ? "selected" : ""}>${esc(x)}</option>`).join("");
  const color = (p) => (p < 50 ? "#c62828" : p < 90 ? "#b26a00" : "#2e7d32");
  const rules = R.rules.map((s) => `<tr class="click ${f.rule === s.rule ? "sel" : ""}" data-rule="${esc(s.rule)}" data-rfile="${esc(s.file)}"><td>${esc(s.rule)}</td><td>${esc(s.desc)}<div class="muted small">${esc(shortName(s.file))}</div></td>` +
    `<td class="n">${fmt(s.failed)}/${fmt(s.applicable)}</td><td class="n"><span class="bar"><i style="width:${s.rate}%;background:${color(s.rate)}"></i></span>${s.rate.toFixed(0)}%</td></tr>`).join("");
  const rows = R.rows.map((i) => `<tr class="click" data-file="${esc(i.file)}" data-step="${i.step}"><td class="sev${i.sev}">${SEV[i.sev] || i.sev}</td><td>${esc(i.rule)}</td>` +
    `<td>${esc(i.cls)}</td><td>${esc(i.name)}</td><td>${esc(i.msg)}</td><td class="muted">${esc(i.actual)}</td><td class="muted small">${esc(shortName(i.file))}</td></tr>`).join("");
  el.innerHTML = `<div class="kpis"><div class="kpi"><b>${fmt(t.checked)}</b><span>Đối tượng kiểm tra</span></div><div class="kpi"><b style="color:${color(t.compliance)}">${t.compliance.toFixed(1)}%</b><span>Tỷ lệ đạt</span></div>
     <div class="kpi"><b class="sevERROR">${fmt(t.errors)}</b><span>Lỗi bắt buộc</span></div><div class="kpi"><b class="sevWARNING">${fmt(t.warnings)}</b><span>Cảnh báo</span></div>
     <div class="kpi"><b>${fmt(t.proxies)}</b><span>Proxy</span></div><span class="grow"></span><div class="muted small">${esc(R.source)}<br>${esc(R.started)}</div></div>
    <div class="tb"><select class="inp" id="fFile">${opt(R.filters.files, f.file, "Mọi file")}</select><select class="inp" id="fSev"><option value="">Mọi mức độ</option>${["ERROR", "WARNING", "INFO"].map((s) => `<option value="${s}" ${f.sev === s ? "selected" : ""}>${SEV[s]}</option>`).join("")}</select>
     <select class="inp" id="fRule">${opt(R.filters.rules, f.rule, "Mọi quy tắc")}</select><select class="inp" id="fCls">${opt(R.filters.classes, f.cls, "Mọi lớp IFC")}</select>
     <input class="inp" id="fQ" placeholder="Tìm tên, GUID, nội dung..." value="${esc(f.q || "")}"><button class="btn small" id="fClear">Bỏ lọc</button>
     <span class="grow"></span><span class="muted">${fmt(R.count)} dòng${R.count > R.rows.length ? ` (hiện ${fmt(R.rows.length)})` : ""}</span>
     <button class="btn small" id="fIso">🔎 Cô lập các đối tượng này</button><button class="btn small" id="fHl">✨ Highlight 3D</button>
     <button class="btn small" id="aiSum">📝 AI viết nhận xét</button></div>
    <div class="split2"><div><table class="grid"><tr><th>Mã</th><th>Yêu cầu</th><th>Không đạt</th><th>Tỷ lệ đạt</th></tr>${rules}</table></div>
     <div><table class="grid"><tr><th>Mức độ</th><th>Mã</th><th>Lớp IFC</th><th>Tên đối tượng</th><th>Nội dung</th><th>Giá trị</th><th>File</th></tr>${rows}</table></div></div>`;
  const apply = async () => { S.resFilter = { file: $("#fFile").value, sev: $("#fSev").value, rule: $("#fRule").value, cls: $("#fCls").value, q: $("#fQ").value }; await fetchResults(); renderTab(); };
  ["#fFile", "#fSev", "#fRule", "#fCls"].forEach((s) => ($(s).onchange = apply));
  $("#fQ").onkeydown = (e) => { if (e.key === "Enter") apply(); };
  $("#fClear").onclick = async () => { S.resFilter = {}; await fetchResults(); renderTab(); };
  $("#fIso").onclick = () => { focusRefs(R.refs, { isolate: true }); status(`Cô lập ${fmt(R.refs.length)} đối tượng`); };
  $("#fHl").onclick = () => { focusRefs(R.refs); };
  $("#aiSum").onclick = aiSummary;
  $$("tr[data-rule]", el).forEach((tr) => (tr.onclick = async () => { S.resFilter = { rule: tr.dataset.rule, file: tr.dataset.rfile }; await fetchResults(); renderTab(); }));
  $$("tr[data-step]", el).forEach((tr) => (tr.onclick = () => {
    $$("tr.sel", el).forEach((x) => x.classList.remove("sel")); tr.classList.add("sel");
    focusRefs([[tr.dataset.file, +tr.dataset.step]]); showProps(tr.dataset.file, +tr.dataset.step);
  }));
}
async function isolateFailing() {
  if (!S.state.result) return toast("Chưa chạy kiểm tra", true);
  const r = await api("/api/results?" + new URLSearchParams({ sev: "ERROR", limit: 1 }));
  S.viewer.setColorMode("status");
  focusRefs(r.refs, { isolate: true }); status(`Cô lập ${fmt(r.refs.length)} đối tượng có lỗi bắt buộc`);
}

// ---------------------------------------------------------------------- xung đột
async function tabClash(el) {
  if (!S.tests) S.tests = (await api("/api/clash/tests")).tests;
  if (S.clash === null || S.clash === undefined) S.clash = await api("/api/clash/results?" + new URLSearchParams(Object.assign({ limit: 2000 }, S.clashFilter)));
  const modes = S.state.options.clash_modes;
  const inp = (i, k, v, w) => `<input data-i="${i}" data-k="${k}" value="${esc(v)}" style="min-width:${w}px">`;
  const tests = S.tests.map((t, i) => `<tr class="${t.enabled ? "" : "off"}"><td><input type="checkbox" data-i="${i}" data-k="enabled" ${t.enabled ? "checked" : ""}></td>` +
    `<td>${esc(t.code)}</td><td>${inp(i, "name", t.name, 200)}</td><td><select data-i="${i}" data-k="mode">${modes.map((m) => `<option value="${m.k}" ${m.k === t.mode ? "selected" : ""} title="${esc(m.help)}">${m.vn}</option>`).join("")}</select></td>` +
    `<td>${inp(i, "tolerance", t.tolerance, 55)}</td><td>${inp(i, "a_name", t.a_name, 140)}</td><td>${inp(i, "b_name", t.b_name, 140)}</td>` +
    `<td>${inp(i, "a_file", t.a_file, 80)}</td><td>${inp(i, "b_file", t.b_file, 80)}</td><td class="muted small" title="${esc(t.note)}">${esc(t.note).slice(0, 90)}</td></tr>`).join("");
  const C = S.clash;
  let res = '<div class="pad muted">Chưa chạy kiểm tra xung đột. Chỉnh ma trận bên trái rồi bấm <b>💥 Chạy xung đột</b>.</div>';
  if (C && C.count != null) {
    const topt = `<option value="">Mọi phép thử (${fmt(C.total)})</option>` + C.tests.map(([t, n]) => `<option value="${esc(t)}" ${S.clashFilter.test === t ? "selected" : ""}>${esc(t)} (${n})</option>`).join("");
    const kopt = `<option value="">Mọi loại</option>` + modes.map((m) => `<option value="${m.k}" ${S.clashFilter.kind === m.k ? "selected" : ""}>${m.vn}</option>`).join("");
    res = `<div class="tb"><select class="inp" id="cT">${topt}</select><select class="inp" id="cK">${kopt}</select><span class="grow"></span>
      <a class="btn small" href="/api/clash/bcf?${new URLSearchParams(S.clashFilter)}">⬇ BCF</a></div>
      <table class="grid"><tr><th>Mã</th><th>Mô tả va chạm</th><th>Đối tượng A</th><th>Đối tượng B</th><th>Phép thử</th><th>Vị trí X, Y, Z</th></tr>` +
      C.rows.map((c) => `<tr class="click" data-cid="${c.id}"><td>${c.id}</td><td>${esc(c.detail)}</td><td>${esc(c.a)}<div class="muted small">${esc(c.ca)}</div></td><td>${esc(c.b)}<div class="muted small">${esc(c.cb)}</div></td><td class="muted small">${esc(c.test)}</td><td class="n muted small">${c.x.toFixed(2)}, ${c.y.toFixed(2)}, ${c.z.toFixed(2)}</td></tr>`).join("") + "</table>";
  }
  const view = S.clashView || (C && C.count ? "results" : "tests");
  const nOn = S.tests.filter((t) => t.enabled).length;
  const head = `<div class="tb"><button class="btn small ${view === "tests" ? "primary" : ""}" id="vTests">📋 Ma trận (${nOn}/${S.tests.length})</button>
     <button class="btn small ${view === "results" ? "primary" : ""}" id="vRes">💥 Kết quả${C && C.count != null ? ` (${fmt(C.total)})` : ""}</button><span class="grow"></span>`;
  if (view === "tests") {
    el.innerHTML = head + `<button class="btn small" id="tSave">💾 Lưu</button><button class="btn small" id="tMx">📋 Ma trận mẫu hạ tầng</button><button class="btn small" id="tPairs">Theo từng cặp file</button>
       <button class="btn small" id="tOpen">📂 Mở</button><a class="btn small" href="/api/clash/export">⬇ Excel</a><button class="btn small" id="tHelp">? 4 loại kiểm tra</button></div>
      <div class="scroll"><table class="grid"><tr><th>Bật</th><th>Mã</th><th>Tên phép thử</th><th>Loại kiểm tra</th><th>Giá trị (m)</th><th>A - từ khoá tên</th><th>B - từ khoá tên</th><th>A - lọc file</th><th>B - lọc file</th><th>Định nghĩa va chạm / căn cứ</th></tr>${tests}</table></div>`;
  } else {
    el.innerHTML = head + `</div>` + `<div class="scroll">${res}</div>`;
  }
  $("#vTests").onclick = () => { S.clashView = "tests"; renderTab(); };
  $("#vRes").onclick = async () => { if (S._saveTests && view === "tests") await S._saveTests(); S.clashView = "results"; renderTab(); };
  const collect = () => {
    const t = S.tests.map((x) => Object.assign({}, x));
    $$("[data-i]", el).forEach((x) => { const v = x.type === "checkbox" ? x.checked : x.value; t[+x.dataset.i][x.dataset.k] = x.dataset.k === "tolerance" ? parseFloat(String(v).replace(",", ".")) || 0 : v; });
    return t;
  };
  const save = async (tests) => { S.tests = (await api("/api/clash/tests", { method: "PUT", json: { tests } })).tests; await refreshState(); };
  S._saveTests = async () => { if ($$("[data-i]", el).length) await save(collect()); };
  if ($("#tSave")) $("#tSave").onclick = async () => { await save(collect()); toast("Đã lưu ma trận"); renderTab(); };
  if ($("#tMx")) $("#tMx").onclick = async () => { S.tests = (await api("/api/clash/preset/matrix", { method: "POST" })).tests; renderTab(); };
  if ($("#tPairs")) $("#tPairs").onclick = async () => { S.tests = (await api("/api/clash/preset/pairs", { method: "POST" })).tests; renderTab(); };
  if ($("#tOpen")) $("#tOpen").onclick = async () => { const f = await pickFile(".xlsx"); if (!f.length) return; try { S.tests = (await upload("/api/clash/import", f)).tests; renderTab(); } catch (e) { toast(e.message, true); } };
  if ($("#tHelp")) $("#tHelp").onclick = () => modal("Thế nào thì tính là va chạm?", modes.map((m) => `<p><b>${m.k} – ${m.vn}</b><br>${esc(m.help)}</p>`).join("") +
    "<p class='hint'>Nhóm A/B nhận diện theo từ khoá tên (tên đối tượng, tên đối tượng cha, thuộc tính Name) - tự khớp cả tên bị mất dấu khi xuất IFC (vd 'Mặt đường' ↔ 'Mt ng'), lớp IFC và lọc tên file. Giá trị trong ma trận mẫu là giá trị mẫu, cần đối chiếu QCVN 07:2023, TCVN 7957 và hồ sơ thiết kế.</p>");
  if ($("#cT")) {
    const apply = async () => { S.clashFilter = { test: $("#cT").value, kind: $("#cK").value }; S.clash = null; renderTab(); };
    $("#cT").onchange = apply; $("#cK").onchange = apply;
  }
  $$("tr[data-cid]", el).forEach((tr) => (tr.onclick = () => {
    $$("tr.sel", el).forEach((x) => x.classList.remove("sel")); tr.classList.add("sel");
    S.viewer.focusClash(tr.dataset.cid);
    const c = C.rows.find((x) => x.id === tr.dataset.cid); if (c) showProps(c.fa, c.sa);
  }));
}
async function runClash() {
  if (!S.state.models.length) return toast("Hãy mở file IFC trước", true);
  try {
    if (S.tab === "clash" && S._saveTests) await S._saveTests();
    await runJob(api("/api/clash/run", { method: "POST" }));
    S.clash = null; S.clashView = "results"; await refreshState(); await loadScene(); showTab("clash");
    toast(`Xong: ${fmt(S.state.clash.count)} xung đột`);
  } catch (e) { toast(e.message, true); }
}

// ---------------------------------------------------------------------- thống kê thuộc tính, danh sách AI, nhật ký
async function tabInventory(el) {
  if (!S.state.models.length) { el.innerHTML = '<div class="pad muted">Chưa mở file IFC.</div>'; return; }
  if (!S.health) S.health = await api("/api/health");
  const q = (S.invQ || "").toLowerCase();
  const rows = S.health.inventory.filter((r) => !q || `${r[1]} ${r[2]} ${r[3]}`.toLowerCase().includes(q)).slice(0, 3000);
  const color = (p) => (p < 50 ? "#c62828" : p < 90 ? "#b26a00" : "#2e7d32");
  el.innerHTML = `<div class="tb"><b>Mức độ điền thuộc tính</b><input class="inp" id="invQ" placeholder="Lọc lớp / Pset / thuộc tính" value="${esc(S.invQ || "")}"><span class="muted">${fmt(rows.length)} dòng · dùng để đối chiếu nhanh với BEP</span></div>
    <div class="scroll"><table class="grid"><tr><th>File</th><th>Lớp IFC</th><th>Property Set</th><th>Thuộc tính</th><th>Đã điền / Tổng</th><th>Tỷ lệ</th><th>Giá trị mẫu</th></tr>` +
    rows.map((r) => { const p = r[6] ? (100 * r[5]) / r[6] : 0; return `<tr><td class="muted small">${esc(shortName(r[0]))}</td><td>${esc(r[1])}</td><td>${esc(r[2])}</td><td>${esc(r[3])}</td><td class="n">${fmt(r[5])}/${fmt(r[6])}</td><td class="n"><span class="bar"><i style="width:${p}%;background:${color(p)}"></i></span>${p.toFixed(0)}%</td><td class="muted small">${esc(r[7])}</td></tr>`; }).join("") + "</table></div>";
  $("#invQ").onkeydown = (e) => { if (e.key === "Enter") { S.invQ = e.target.value; renderTab(); } };
}
async function tabAIList(el) {
  const c = S.aiListCard;
  if (!c) { el.innerHTML = '<div class="pad muted">Chọn nhiều đối tượng trong 3D (Ctrl + nháy, Chọn vùng, chuột phải → Chọn cùng lớp IFC) hoặc bấm "Danh sách" trên thẻ của trợ lý AI để xem danh sách ở đây.</div>'; return; }
  const meta = S.viewer.meta;
  const fidx = meta ? new Map(meta.files.map((f, i) => [f, i])) : new Map();
  const rows = c.refs.slice(0, 3000).map(([f, st]) => {
    const oi = S.viewer.objIndex ? S.viewer.objIndex.get(fidx.get(f) + ":" + st) : undefined;
    const o = oi != null ? meta.objects[oi] : null;
    return `<tr class="click" data-file="${esc(f)}" data-step="${st}"><td>${o ? esc(meta.classes[o[3]]) : ""}</td><td>${o ? esc(o[4]) : "#" + st}</td><td class="muted small">${esc(shortName(f))}</td><td class="n">${st}</td></tr>`;
  }).join("");
  el.innerHTML = `<div class="tb"><b>${esc(c.title)}</b><span class="muted">${fmt(c.count)} đối tượng</span><span class="grow"></span><button class="btn small" id="alIso">🔎 Cô lập</button><button class="btn small" id="alHl">✨ Highlight 3D</button><button class="btn small" id="alHide">🙈 Ẩn</button></div>
    <div class="scroll"><table class="grid"><tr><th>Lớp IFC</th><th>Tên</th><th>File</th><th>#</th></tr>${rows}</table></div>`;
  $("#alIso").onclick = () => focusRefs(c.refs, { isolate: true });
  $("#alHl").onclick = () => focusRefs(c.refs);
  $("#alHide").onclick = () => { S.viewer.hide(S.viewer.resolve(c.refs)); S.viewer.clearSelection(true); };
  $$("tr[data-step]", el).forEach((tr) => (tr.onclick = () => { focusRefs([[tr.dataset.file, +tr.dataset.step]]); showProps(tr.dataset.file, +tr.dataset.step); }));
}
async function tabLog(el) {
  let extra = "";
  if (S.clash && S.clash.messages) extra = "\n\n— Nhật ký xung đột —\n" + S.clash.messages.join("\n");
  el.innerHTML = `<div class="scroll"><pre style="margin:0;padding:8px 10px;white-space:pre-wrap">${esc(S.logs.join("\n") + extra)}</pre></div>`;
}

// ====================================================================== dự án
async function newProject() {
  const name = prompt("Tên dự án mới:", "");
  if (!name || !name.trim()) return;
  try {
    await waitIdle();
    await api("/api/projects/new", { method: "POST", json: { name: name.trim() } });
    S.viewer.clear(); $("#props").innerHTML = '<div class="muted pad">Chọn 1 đối tượng trong mô hình 3D hoặc trong bảng để xem thông tin.</div>';
    Object.assign(S, { results: null, clash: null, rules: null, tests: null, health: null, drawing: null, drawingRes: null, aiListCard: null, clashView: null });
    S.viewer.setOverlay(null); S.viewer.setMarkers(null);
    await refreshState(); renderTab();
    toast(`Đã tạo dự án "${name.trim()}" - thêm file IFC để bắt đầu`);
  } catch (e) { toast(e.message, true); }
}
async function toggleProjMenu() {
  const m = $("#projMenu");
  if (m.classList.contains("on")) { m.classList.remove("on"); return; }
  const d = await api("/api/projects");
  const cur = d.current ? d.current.id : null;
  m.innerHTML = `<button class="pm" data-a="new"><b>＋ Tạo dự án mới</b></button><hr>` +
    (d.projects.length ? d.projects.map((p) => `<button class="pm ${p.id === cur ? "cur" : ""}" data-id="${p.id}"><span>${p.id === cur ? "● " : ""}<b>${esc(p.name)}</b></span>
      <small>${p.files.length} file · ${esc(p.updated)}${p.has_result ? " · đã kiểm tra" : ""}${p.has_clash ? " · có xung đột" : ""}</small></button>`).join("")
      : '<div class="pad muted small">Chưa có dự án nào.</div>') +
    `<hr><button class="pm" data-a="manage">🗂️ Quản lý dự án (tải về, nhập, xoá)...</button>`;
  m.classList.add("on");
  $$("[data-id]", m).forEach((b) => (b.onclick = () => { m.classList.remove("on"); if (b.dataset.id !== cur) loadProject(b.dataset.id); }));
  m.querySelector('[data-a="new"]').onclick = () => { m.classList.remove("on"); newProject(); };
  m.querySelector('[data-a="manage"]').onclick = () => { m.classList.remove("on"); openProjects(); };
}
async function renderRecent() {
  try {
    const d = await api("/api/projects");
    const cur = d.current ? d.current.id : null;
    const list = d.projects.filter((p) => p.id !== cur).slice(0, 6);
    $("#startRecent").innerHTML = list.length ? `<div class="recent"><h3>Dự án gần đây</h3>` + list.map((p) => `<button data-id="${p.id}"><b>📁 ${esc(p.name)}</b>
      <small>${p.files.length} file · ${esc(p.updated)}</small></button>`).join("") + "</div>" : "";
    $$("#startRecent [data-id]").forEach((b) => (b.onclick = () => loadProject(b.dataset.id)));
  } catch (e) { /* */ }
}
async function saveProject(asNew) {
  if (!S.state.models.length && !S.state.project) return toast("Chưa có mô hình để lưu - hoặc bấm ＋ Dự án mới", true);
  let name = S.state.project ? S.state.project.name : "";
  if (asNew || !S.state.project) {
    name = prompt("Tên dự án:", name || (S.state.models[0].file.replace(/\.ifc$/i, "")));
    if (!name) return;
  }
  try {
    await runJob(api("/api/projects/save", { method: "POST", json: { name, as_new: !!asNew } }));
    await refreshState(); toast(`Đã lưu dự án "${S.state.project.name}"`);
  } catch (e) { toast(e.message, true); }
}
async function openProjects() {
  const d = await api("/api/projects");
  const rows = d.projects.map((p) => `<tr><td><b>${esc(p.name)}</b><div class="muted small">${esc(p.files.slice(0, 4).join(", "))}${p.files.length > 4 ? "…" : ""}</div></td>
    <td class="n">${p.files.length}</td><td class="muted small">${esc(p.updated)}</td><td class="n">${p.size_mb} MB</td>
    <td>${p.has_result ? "✅" : ""}${p.has_clash ? " 💥" : ""}</td>
    <td style="white-space:nowrap"><button class="btn small primary" data-open="${p.id}">Mở</button> <a class="btn small" href="/api/projects/${p.id}/download">⬇</a> <button class="btn small" data-del="${p.id}">🗑</button></td></tr>`).join("");
  modal("Dự án đã lưu", `${d.projects.length ? `<table class="grid"><tr><th>Dự án</th><th>File</th><th>Cập nhật</th><th>Dung lượng</th><th>Kết quả</th><th></th></tr>${rows}</table>`
      : '<p class="muted">Chưa có dự án nào. Mở file IFC rồi bấm 💾 Lưu.</p>'}
    <p class="hint">Dự án lưu file IFC, bộ quy tắc, ma trận va chạm, kết quả kiểm tra, xung đột, đối chiếu bản vẽ và cài đặt. ⬇ tải về file .ttproj để chuyển sang máy khác.</p>
    <label class="btn small"><input type="file" id="projUpload" accept=".ttproj,.zip" hidden>📤 Nhập file .ttproj</label>`, [{ text: "Đóng" }]);
  $$("#mbody [data-open]").forEach((b) => (b.onclick = async () => { closeModal(); await loadProject(b.dataset.open); }));
  $$("#mbody [data-del]").forEach((b) => (b.onclick = async () => {
    if (!confirm("Xoá dự án này khỏi máy chủ?")) return;
    await api(`/api/projects/${b.dataset.del}`, { method: "DELETE" }); closeModal(); openProjects();
  }));
  $("#projUpload").onchange = async (e) => {
    try { const r = await upload("/api/projects/upload", [...e.target.files]); closeModal(); await loadProject(r.id); }
    catch (err) { toast(err.message, true); }
  };
}
async function loadProject(pid) {
  try {
    await waitIdle();
    await runJob(api(`/api/projects/${pid}/open`, { method: "POST" }));
    Object.assign(S, { results: null, clash: null, rules: null, tests: null, health: null, drawing: null, aiListCard: null, clashView: null });
    S.viewer.setOverlay(null); S.viewer.setMarkers(null);
    await refreshState();
    if (S.state.models.length) { await loadScene(); await loadDrawingOverlay(); } else S.viewer.clear();
    renderTab();
    toast(`Đã mở dự án "${S.state.project.name}"`);
  } catch (e) { toast(e.message, true); }
}

// ====================================================================== đối chiếu bản vẽ 2D
const DSTAT_COLOR = { "Khớp": [0.2, 0.65, 0.35], "Khác tên": [0.55, 0.4, 0.85], "Lệch cao độ": [0.95, 0.6, 0.1],
  "Chỉ có trên bản vẽ": [0.85, 0.15, 0.15], "Chỉ có trong mô hình": [0.1, 0.5, 0.95] };
async function loadDrawingOverlay() {
  try {
    const info = await api("/api/drawing");
    if (!info.file || !S.viewer.meta) { S.viewer.setOverlay(null); S.viewer.setMarkers(null); return; }
    const buf = await api("/api/drawing/overlay");
    S.viewer.setOverlay(new Float32Array(buf));
    S.viewer.setOverlayVisible(S.drawOverlayOn !== false);
    const r = await api("/api/drawing/result");
    drawingMarkers(r.rows || []);
  } catch (e) { /* chưa có bản vẽ */ }
}
function drawingMarkers(rows) {
  const meta = S.viewer.meta; if (!meta) return;
  const o = meta.origin, zTop = S.viewer.bounds ? S.viewer.bounds.max.z + 0.5 : 0;
  S.viewer.setMarkers(rows.map((r) => ({ x: r.x - o[0], y: r.y - o[1], z: (r.top_model ?? r.top_drawing ?? zTop + o[2]) - o[2] + 0.3,
    c: DSTAT_COLOR[r.status] || [0.5, 0.5, 0.5] })));
}
async function tabDrawing(el) {
  if (!S.drawing) S.drawing = await api("/api/drawing");
  const D = S.drawing;
  if (!D.file) {
    el.innerHTML = `<div class="pad"><b>Đối chiếu mô hình với bản vẽ 2D</b>
      <p class="muted">Nạp bản vẽ mặt bằng (DXF${D.converter ? " hoặc DWG" : ""}) cùng hệ toạ độ với mô hình (VN-2000). Phần mềm ghép ký hiệu trên bản vẽ (vd hố ga) với đối tượng trong mô hình theo vị trí,
      đọc nhãn (tên, cao độ mặt, cao độ đáy) để so sánh; nét bản vẽ được phủ lên mô hình 3D để xem độ khớp.</p>
      ${D.converter ? "" : '<p class="hint">⚠ Máy chủ chưa có công cụ chuyển DWG: hãy xuất DXF từ AutoCAD/ZWCAD (Save As → DXF), hoặc cài ODA File Converter (miễn phí) / LibreDWG (nguồn mở).</p>'}
      <label class="btn primary"><input type="file" id="dwFile" accept=".dxf,.dwg" hidden>📂 Nạp bản vẽ</label></div>`;
    $("#dwFile").onchange = uploadDrawing;
    return;
  }
  const c = Object.assign({}, D.config || {});
  const f = (k, label, w = 160, hint = "") => `<label class="small" title="${esc(hint)}">${label}<br><input class="inp" data-c="${k}" value="${esc(c[k] ?? "")}" style="width:${w}px"></label>`;
  const lrows = D.layers.slice(0, 200).map((l) => {
    const chk = (k) => { try { return c[k] && new RegExp(c[k], "i").test(l.name) ? "checked" : ""; } catch (e) { return ""; } };
    return `<tr><td>${esc(l.name)}</td><td class="n">${fmt(l.points)}</td><td class="n">${fmt(l.texts)}</td><td class="n">${fmt(l.segments)}</td>
      <td><input type="checkbox" data-l="point_layers" data-n="${esc(l.name)}" ${chk("point_layers")}></td>
      <td><input type="checkbox" data-l="label_layers" data-n="${esc(l.name)}" ${chk("label_layers")}></td>
      <td><input type="checkbox" data-l="overlay_layers" data-n="${esc(l.name)}" ${chk("overlay_layers")}></td></tr>`;
  }).join("");
  let res = '<div class="pad muted">Chọn layer ký hiệu / nhãn, nhóm đối tượng mô hình rồi bấm ▶ Đối chiếu.</div>';
  const R = S.drawingRes;
  if (R && R.summary) {
    const s = R.summary, t = s.top, b = s.bottom;
    const st = Object.entries(R.statuses).map(([k, n]) => `<option ${S.dwStatus === k ? "selected" : ""} value="${esc(k)}">${esc(k)} (${n})</option>`).join("");
    res = `<div class="kpis"><div class="kpi"><b>${fmt(s.matched)}/${fmt(s.model_points)}</b><span>Ghép theo vị trí (≤${s.match_radius} m)</span></div>
      <div class="kpi"><b style="color:#c62828">${fmt(s.only_drawing)}</b><span>Chỉ có trên bản vẽ</span></div><div class="kpi"><b style="color:#1565c0">${fmt(s.only_model)}</b><span>Chỉ có trong mô hình</span></div>
      <div class="kpi"><b>${fmt(s.name_mismatch)}</b><span>Khác tên</span></div>
      <div class="kpi"><b>${t.median ?? "-"} m</b><span>Trung vị |Δ mặt| · >0,1: ${t[">0.1"]} · >0,3: ${t[">0.3"]} · >1: ${t[">1.0"]}</span></div>
      <div class="kpi"><b>${b.median ?? "-"} m</b><span>Trung vị |Δ đáy| · >0,3: ${b[">0.3"]} · >1: ${b[">1.0"]}</span></div>
      <div class="kpi"><b>${s.dxy.median ?? "-"} m</b><span>Lệch vị trí trung vị (max ${s.dxy.max ?? "-"})</span></div></div>
      <div class="tb"><select class="inp" id="dwSt"><option value="">Mọi trạng thái</option>${st}</select><span class="muted">${fmt(R.count)} dòng</span><span class="grow"></span>
      ${Object.entries(DSTAT_COLOR).map(([k, v]) => `<span class="chip"><i style="display:inline-block;width:9px;height:9px;border-radius:50%;background:rgb(${v.map((x) => Math.round(x * 255))})"></i> ${k}</span>`).join("")}
      <a class="btn small" href="/api/drawing/report.xlsx">⬇ Excel</a></div>
      <table class="grid"><tr><th>Trạng thái</th><th>Tên IFC</th><th>Tên BV</th><th>Lệch VT (m)</th><th>Mặt IFC</th><th>Mặt BV</th><th>Δ mặt</th><th>Đáy IFC</th><th>Đáy BV</th><th>Δ đáy</th><th>Nhãn bản vẽ</th></tr>` +
      R.rows.map((r, i) => `<tr class="click" data-i="${i}"><td>${esc(r.status)}</td><td>${esc(r.name_model)}</td><td>${esc(r.name_drawing)}</td><td class="n">${r.dxy ?? ""}</td>
        <td class="n">${r.top_model ?? ""}</td><td class="n">${r.top_drawing ?? ""}</td><td class="n ${Math.abs(r.d_top || 0) > s.elev_tol ? "sevERROR" : ""}">${r.d_top ?? ""}</td>
        <td class="n">${r.bottom_model ?? ""}</td><td class="n">${r.bottom_drawing ?? ""}</td><td class="n ${Math.abs(r.d_bottom || 0) > s.elev_tol ? "sevWARNING" : ""}">${r.d_bottom ?? ""}</td>
        <td class="muted small">${esc((r.label || "").replace(/\n/g, " / ")).slice(0, 80)}</td></tr>`).join("") + "</table>";
  }
  el.innerHTML = `<div class="tb"><b>📐 ${esc(D.file)}</b><span class="muted">${fmt(D.layers.length)} layer</span><span class="grow"></span>
      <label class="small"><input type="checkbox" id="dwOv" ${S.drawOverlayOn !== false ? "checked" : ""}> Hiện nét bản vẽ trên 3D</label>
      <button class="btn small" id="dwTop">⬇ Nhìn bằng</button><label class="btn small"><input type="file" id="dwFile" accept=".dxf,.dwg" hidden>📂 Bản vẽ khác</label>
      <button class="btn small primary" id="dwRun">▶ Đối chiếu</button></div>
    <div class="split2" style="grid-template-columns: minmax(330px, 34%) minmax(0, 1fr)"><div>
      <div class="tb" style="flex-wrap:wrap;gap:8px">${f("name", "Đối tượng", 90)}${f("model_file", "Mô hình: lọc file", 120, "regex tên file IFC")}${f("model_class", "Lớp IFC", 170)}${f("model_name", "Từ khoá tên", 100)}
        ${f("match_radius", "Bán kính ghép (m)", 70)}${f("label_radius", "Bán kính nhãn (m)", 70)}${f("elev_tol", "Dung sai cao độ (m)", 70)}
        ${f("scale", "Tỷ lệ BV", 60, "0.001 nếu bản vẽ vẽ theo mm")}${f("dx", "Dịch X", 60)}${f("dy", "Dịch Y", 60)}${f("name_regex", "Mẫu tên (regex)", 170)}</div>
      <table class="grid"><tr><th>Layer</th><th>Ký hiệu</th><th>Chữ</th><th>Nét</th><th title="Layer chứa ký hiệu đối tượng (block/đường tròn)">KH</th><th title="Layer chứa nhãn tên/cao độ">Nhãn</th><th title="Hiện nét lên mô hình 3D">3D</th></tr>${lrows}</table>
    </div><div>${res}</div></div>`;
  const collect = () => {
    const cfg = Object.assign({}, c);
    $$("[data-c]", el).forEach((x) => (cfg[x.dataset.c] = x.value));
    for (const k of ["point_layers", "label_layers", "overlay_layers"]) {
      const names = $$(`[data-l="${k}"]:checked`, el).map((x) => "^" + x.dataset.n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "$");
      cfg[k] = names.join("|");
    }
    return cfg;
  };
  $("#dwFile").onchange = uploadDrawing;
  $("#dwTop").onclick = () => S.viewer.viewTop();
  $("#dwOv").onchange = (e) => { S.drawOverlayOn = e.target.checked; S.viewer.setOverlayVisible(e.target.checked); };
  $("#dwRun").onclick = async () => {
    try {
      await runJob(api("/api/drawing/compare", { method: "POST", json: { config: collect() } }));
      S.drawing = null; S.drawingRes = await api("/api/drawing/result"); S.dwStatus = "";
      await loadDrawingOverlay(); renderTab();
      const s = S.drawingRes.summary; toast(`Ghép ${s.matched}/${s.model_points} · chỉ BV ${s.only_drawing} · chỉ MH ${s.only_model} · lệch mặt >0,3 m: ${s.top[">0.3"]}`);
    } catch (e) { toast(e.message, true); }
  };
  if ($("#dwSt")) $("#dwSt").onchange = async (e) => { S.dwStatus = e.target.value; S.drawingRes = await api("/api/drawing/result?status=" + encodeURIComponent(e.target.value)); renderTab(); };
  $$("tr[data-i]", el).forEach((tr) => (tr.onclick = () => {
    const r = S.drawingRes.rows[+tr.dataset.i], o = S.viewer.meta ? S.viewer.meta.origin : [0, 0, 0];
    $$("tr.sel", el).forEach((x) => x.classList.remove("sel")); tr.classList.add("sel");
    if (r.step != null && r.file) { focusRefs([[r.file, r.step]]); showProps(r.file, r.step); }
    else S.viewer.focusPoint(r.x - o[0], r.y - o[1], (r.top_drawing ?? (S.viewer.bounds.max.z + o[2])) - o[2], 30);
  }));
  if (!S.drawingRes && D.summary) { S.drawingRes = await api("/api/drawing/result"); renderTab(); }
}
async function uploadDrawing(e) {
  const files = [...e.target.files]; if (!files.length) return;
  try {
    await runJob(upload("/api/drawing/upload", files));
    S.drawing = null; S.drawingRes = null; await loadDrawingOverlay(); showTab("drawing"); toast("Đã nạp bản vẽ - kiểm tra layer gợi ý rồi bấm ▶ Đối chiếu");
  } catch (err) { toast(err.message, true); }
}

// ====================================================================== trợ lý AI
function toggleAI(on) {
  const ws = $("#workspace");
  const open = on === undefined ? !ws.classList.contains("ai-open") : on;
  ws.classList.toggle("ai-open", open);
  $("#btnAI").classList.toggle("active", open);
  setTimeout(() => S.viewer.resize(), 50);
  if (open && !$("#aimsgs").children.length) {
    addMsg("bot", "Xin chào! Hãy hỏi tôi về mô hình bằng tiếng Việt. Tôi dùng công cụ của phần mềm để truy xuất và đếm trực tiếp trên mô hình trước khi trả lời.");
  }
  renderSugg();
}
function renderAIInfo() {
  const s = S.state.settings;
  $("#aiinfo").innerHTML = `Model: <select class="inp" id="aiModel">${["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"].map((m) => `<option ${m === s.ai_model ? "selected" : ""}>${m}</option>`).join("")}</select>
    ${s.ai_key ? '<span style="color:#2e7d32">● đã có API key</span>' : '<button class="btn small" id="aiKey">Nhập API key</button>'}`;
  $("#aiModel").onchange = async (e) => { S.state = await api("/api/settings", { method: "PUT", json: { ai_model: e.target.value } }); };
  if ($("#aiKey")) $("#aiKey").onclick = openSettings;
}
function renderSugg() {
  const st = S.state;
  const sug = ["Mô hình có những loại đối tượng nào?", "Đối tượng nào chưa có thông tin vật liệu?"];
  if (st.result) sug.push("Tóm tắt kết quả kiểm tra", "Quy tắc nào không đạt nhiều nhất?");
  if (st.clash.count) sug.push("Có bao nhiêu xung đột giữa ống TNM và ống TNT?");
  sug.push("Có bao nhiêu hố ga?");
  $("#aisugg").innerHTML = sug.map((s) => `<button>${esc(s)}</button>`).join("");
  $$("#aisugg button").forEach((b) => (b.onclick = () => { $("#aiq").value = b.textContent; askAI(); }));
}
function addMsg(role, text, cards) {
  const m = document.createElement("div"); m.className = "msg " + role; m.textContent = text;
  for (const c of cards || []) {
    S.aiCards[c.id + "_" + S.aiHistory.length] = c;
    const el = document.createElement("div"); el.className = "card";
    el.innerHTML = `<div class="ct">${fmt(c.count)} ${c.kind === "clashes" ? "đối tượng liên quan" : "phần tử"}</div><div class="cs">${esc(c.title)}</div>
      <div class="cb"><button class="btn small" data-a="list">☰ Danh sách (${fmt(c.count)})</button><button class="btn small" data-a="hl">✨ Highlight 3D</button><button class="btn small" data-a="iso">🔎 Cô lập</button></div>`;
    el.querySelector('[data-a="list"]').onclick = () => { S.aiListCard = c; showTab("ailist"); };
    el.querySelector('[data-a="hl"]').onclick = () => { S.viewer.clearIsolate(); focusRefs(c.refs); };
    el.querySelector('[data-a="iso"]').onclick = () => focusRefs(c.refs, { isolate: true });
    m.appendChild(el);
  }
  $("#aimsgs").appendChild(m); $("#aimsgs").scrollTop = 1e9;
  return m;
}
async function askAI() {
  const q = $("#aiq").value.trim(); if (!q) return;
  if (!S.state.models.length) return toast("Hãy mở file IFC trước", true);
  if (!S.state.settings.ai_key) { openSettings(); return toast("Cần nhập API key Claude", true); }
  $("#aiq").value = ""; addMsg("user", q);
  const wait = addMsg("bot thinking", "Đang truy vấn mô hình...");
  try {
    const r = await api("/api/ai/chat", { method: "POST", json: { question: q, history: S.aiHistory } });
    wait.remove(); addMsg("bot", r.answer, r.cards);
    S.aiHistory.push({ role: "user", text: q }, { role: "assistant", text: r.answer });
    if (r.cards.length) focusRefs(r.cards[r.cards.length - 1].refs);
  } catch (e) { wait.remove(); addMsg("bot", "⚠ " + e.message); }
}
async function aiSummary() {
  if (!S.state.settings.ai_key) { openSettings(); return toast("Cần nhập API key Claude", true); }
  status("AI đang viết nhận xét thẩm tra..."); progress(true);
  try {
    const r = await api("/api/ai/summary", { method: "POST" }); progress(false);
    modal("Nhận xét thẩm tra (AI hỗ trợ - cần chuyên gia xác nhận)", `<textarea id="sumText" style="width:100%;height:50vh">${esc(r.text)}</textarea><p class="hint">Nhận xét được đưa vào báo cáo Excel/HTML.</p>`,
      [{ text: "Đóng" }, { text: "Lưu vào báo cáo", primary: true, onClick: async () => { await api("/api/ai/summary", { method: "PUT", json: { text: $("#sumText").value } }); toast("Đã lưu"); } }]);
  } catch (e) { progress(false); toast(e.message, true); }
}

// ====================================================================== cài đặt
function openSettings() {
  const s = S.state.settings;
  modal("Cài đặt", `
    <label class="f">Cụm đối tượng (cha - con)</label>
    ${[["auto", "Tự động (khuyến nghị): cha chỉ là nhóm → kiểm tra các con; cha có thuộc tính → chỉ kiểm tra cha"], ["parents", "Chỉ đối tượng cha / độc lập"], ["all", "Tất cả"]]
      .map(([v, t]) => `<div><label><input type="radio" name="pm" value="${v}" ${s.part_mode === v ? "checked" : ""}> ${t}</label></div>`).join("")}
    <label class="f"><input type="checkbox" id="setCls" ${s.classify ? "checked" : ""}> Kiểm tra tên đối tượng ↔ lớp IFC (phát hiện sai chủng loại)</label>
    <label class="f">API key Claude (cho trợ lý AI)</label>
    <input class="inp" id="setKey" type="password" style="width:100%" placeholder="${s.ai_key ? "đã có - để trống nếu không đổi" : "sk-ant-..."}">
    <div class="hint">Chỉ giữ trong bộ nhớ phiên làm việc trên máy chủ, không ghi ra đĩa. Có thể đặt sẵn biến môi trường ANTHROPIC_API_KEY trên máy chủ. AI chỉ nhận dữ liệu tóm tắt (tên lớp, tên, thuộc tính, thống kê), không nhận file IFC.</div>`,
    [{ text: "Huỷ" }, { text: "Lưu", primary: true, onClick: async () => {
      const body = { part_mode: $('input[name="pm"]:checked').value, classify: $("#setCls").checked };
      if ($("#setKey").value.trim()) body.api_key = $("#setKey").value.trim();
      S.state = await api("/api/settings", { method: "PUT", json: body }); renderAIInfo(); toast("Đã lưu cài đặt");
    } }]);
}

window.addEventListener("DOMContentLoaded", () => init().catch((e) => toast(e.message, true)));
