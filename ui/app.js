let cfg = null;
let currentName = "example";
let selected = null;       // {kind, index}
let selectedPort = null;   // port id
let presets = [];          // 设备尺寸表里的模块预设（data/设备尺寸.xlsx）
let presetMeta = null;     // {source, source_kind, warnings, markers}
let resultState = { solutions: [], page: 0, pageSize: 4, sort: "cost-asc", version: 0 };

/* 作业流：每个作业单独一份日志缓冲，互不覆盖 */
const jobs = new Map();    // id -> stream
let activeJobId = null;    // 当前显示在日志面板里的作业
let jobTimer = null;
let pollFailures = 0;
let lastRepairAt = 0;
let resultsTimer = null;
const LOG_VIEW_MAX = 3000; // 日志面板最多保留的行数

const $ = (id) => document.getElementById(id);

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

function defaultConfig() {
  return {
    rows: 9, cols: 17,
    fixed: [{
      id: "Y1", pos: [0, 0], w: 6, h: 4,
      ports: [{ id: "OUT", dir: "S", cells: [[3,0],[3,1],[3,2],[3,3],[3,4],[3,5]] }]
    }, {
      id: "Y2", pos: [0, 11], w: 6, h: 4,
      ports: [{ id: "OUT", dir: "S", cells: [[3,0],[3,1],[3,2],[3,3],[3,4],[3,5]] }]
    }],
    movable: [],
    nets: []
  };
}

function allModules() {
  return Model.allModules(cfg);
}

function getMod(kind, index) {
  return (kind === "fixed" ? cfg.fixed : cfg.movable)[index];
}

function defaultPorts(kind, w, h) {
  if (kind === "fixed") {
    return [{ id: "OUT", dir: "S", cells: Array.from({length: w}, (_, c) => [h - 1, c]) }];
  }
  const ports = [];
  if (w >= 1) ports.push({ id: "IN", dir: "W", cells: Array.from({length: h}, (_, r) => [r, 0]) });
  if (w >= 2) ports.push({ id: "OUT", dir: "E", cells: Array.from({length: h}, (_, r) => [r, w - 1]) });
  return ports;
}

/* ---------------- problems ---------------- */
async function loadProblems() {
  const data = await api("/api/problems");
  const sel = $("problemSelect");
  const keep = currentName || sel.value;
  sel.innerHTML = "";
  data.problems.forEach(p => {
    const o = document.createElement("option");
    o.value = p.name;
    o.textContent = p.name + (p.results && p.results.length ? "  (" + p.results.length + " files)" : "");
    sel.appendChild(o);
  });
  if (keep) sel.value = keep;
}

async function loadConfig(name) {
  const data = await api("/api/config?name=" + encodeURIComponent(name));
  cfg = data;
  currentName = name;
  $("problemName").value = name;
  selected = null; selectedPort = null;
  renderAll();
}

async function selectProblem(name) {
  if (!name) return;
  await loadConfig(name);
  syncActiveJob();
  if ($("tab-result").classList.contains("active")) {
    await loadResults();
  }
  updateResultsTimer();
}

// 切题/刷新后，日志面板跟着切到该题目最近的求解作业
function syncActiveJob() {
  const job = latestSolveJob(currentName);
  if (job) {
    if (activeJobId !== job.id) setActiveJob(job.id);
  } else if (activeJobId && jobs.get(activeJobId)
             && jobs.get(activeJobId).name !== currentName) {
    activeJobId = null;
    $("jobLog").textContent = "";
  }
  updateJobUI();
}

function newConfig() {
  cfg = defaultConfig();
  currentName = "newtask";
  $("problemName").value = currentName;
  selected = null; selectedPort = null;
  renderAll();
}

async function saveConfig(asNew, notify = true) {
  const name = ($("problemName").value || currentName || "task").trim();
  if (asNew) currentName = name;
  const r = await api("/api/config", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({name, config: cfg})
  });
  currentName = r.name;
  $("problemName").value = currentName;
  await loadProblems();
  if (notify) alert("已保存 config." + currentName + ".json");
}

/* ---------------- editor ---------------- */
function renderAll() {
  $("rows").value = cfg.rows;
  $("cols").value = cfg.cols;
  renderModuleList();
  renderModuleEditor();
  renderNets();
  renderPreview();
  renderPresetPreview();
  renderLinkPreview();
}

function renderModuleList() {
  const box = $("moduleList");
  box.innerHTML = "";
  allModules().forEach(m => {
    const div = document.createElement("div");
    div.className = "mod-item " + m.kind + (selected && selected.kind === m.kind && selected.index === m.index ? " selected" : "");
    div.innerHTML = `<b>${esc(m.mod.id)}</b>
      <span class="tag">${m.kind === "fixed" ? "固定" : "可动"}</span>
      <span>${m.mod.w}×${m.mod.h}</span>
      <span class="muted">端口 ${esc((m.mod.ports || []).map(p => p.id).join("/") || "无")}</span>`;
    div.onclick = () => {
      selected = {kind: m.kind, index: m.index};
      selectedPort = (m.mod.ports && m.mod.ports[0]) ? m.mod.ports[0].id : null;
      renderModuleList(); renderModuleEditor();
    };
    box.appendChild(div);
  });
}

function renderModuleEditor() {
  const box = $("moduleEditor");
  if (!selected) { box.innerHTML = '<span class="muted">左侧点一个模块开始编辑</span>'; return; }
  const m = getMod(selected.kind, selected.index);
  box.innerHTML = `
    <div class="net-row">
      <label>ID <input id="medId" value="${esc(m.id)}" size="8"></label>
      <label>类型 <select id="medKind">
        <option value="movable" ${selected.kind === "movable" ? "selected" : ""}>可动</option>
        <option value="fixed" ${selected.kind === "fixed" ? "selected" : ""}>固定</option>
      </select></label>
      <label>宽 <input id="medW" type="number" value="${m.w}" style="width:56px"></label>
      <label>高 <input id="medH" type="number" value="${m.h}" style="width:56px"></label>
      ${selected.kind === "fixed" ? `
        <label>行 <input id="medR" type="number" value="${m.pos[0]}" style="width:56px"></label>
        <label>列 <input id="medC" type="number" value="${m.pos[1]}" style="width:56px"></label>` : `
        <label class="inline"><input type="checkbox" id="medRot" ${m.rotatable ? "checked" : ""}>可旋转</label>`}
      <button id="medDelete">删除模块</button>
    </div>
    <div class="port-list" id="portList"></div>
    <div class="addbox">
      <input id="newPortId" placeholder="端口 ID 如 IN" size="10">
      <select id="newPortDir">
        <option>N</option><option>S</option><option>W</option><option>E</option>
      </select>
      <button id="btnAddPort">添加端口</button>
      <span class="muted">选中端口后，点下方格子切换端口位置</span>
    </div>
    <div id="portGrid" class="port-grid"></div>`;

  const finish = () => {
    const id = $("medId").value.trim();
    if (id) {
      const old = m.id;
      m.id = id;
      (cfg.nets || []).forEach(n => {
        if (n.from === old) n.from = id;
        if (n.to === old) n.to = id;
      });
    }
    m.w = Math.max(1, +$("medW").value || 1);
    m.h = Math.max(1, +$("medH").value || 1);
    if (selected.kind === "fixed") {
      m.pos = [+$("medR").value || 0, +$("medC").value || 0];
    } else {
      m.rotatable = $("medRot").checked;
    }
    renderModuleList(); renderPreview(); renderPresetPreview(); renderLinkPreview();
  };
  ["medId","medW","medH","medR","medC"].forEach(id => { if ($(id)) $(id).onchange = finish; });
  if ($("medRot")) $("medRot").onchange = finish;
  $("medKind").onchange = () => changeKind(m, $("medKind").value, finish);
  $("medDelete").onclick = () => {
    getArray(selected.kind).splice(selected.index, 1);
    selected = null; renderAll();
  };
  $("btnAddPort").onclick = () => {
    const pid = $("newPortId").value.trim() || ("P" + ((m.ports || []).length + 1));
    m.ports = m.ports || [];
    m.ports.push({id: pid, dir: $("newPortDir").value, cells: []});
    selectedPort = pid;
    renderModuleEditor();
  };

  const pl = $("portList");
  (m.ports || []).forEach(p => {
    const chip = document.createElement("span");
    chip.className = "port-chip" + (p.id === selectedPort ? " active" : "");
    const info = portInfo(p.id);
    chip.textContent = `${p.id} (${p.dir}${info.flow ? "·" + info.label : ""})`;
    chip.onclick = () => { selectedPort = p.id; renderModuleEditor(); };
    pl.appendChild(chip);
  });

  const pg = $("portGrid");
  pg.style.gridTemplateColumns = `repeat(${m.w}, 46px)`;
  for (let r = 0; r < m.h; r++) {
    for (let c = 0; c < m.w; c++) {
      const cell = document.createElement("div");
      cell.className = "port-cell";
      const owners = (m.ports || []).filter(p => (p.cells || []).some(x => x[0] === r && x[1] === c));
      if (owners.length) cell.classList.add("on");
      cell.textContent = owners.map(p => p.id).join(",");
      if (owners.some(p => p.id === selectedPort)) cell.classList.add("on");
      cell.onclick = () => {
        const p = (m.ports || []).find(x => x.id === selectedPort);
        if (!p) return alert("先添加/选择一个端口");
        p.cells = p.cells || [];
        const i = p.cells.findIndex(x => x[0] === r && x[1] === c);
        if (i >= 0) p.cells.splice(i, 1); else p.cells.push([r, c]);
        renderModuleEditor();
      };
      pg.appendChild(cell);
    }
  }
  renderLinkPreview();
}

function getArray(kind) { return kind === "fixed" ? cfg.fixed : cfg.movable; }

function changeKind(m, newKind, done) {
  const src = getArray(selected.kind);
  src.splice(selected.index, 1);
  if (newKind === "fixed") {
    m.pos = m.pos || [0, 0];
    delete m.rotatable;
    cfg.fixed.push(m);
    selected = {kind: "fixed", index: cfg.fixed.length - 1};
  } else {
    m.rotatable = m.rotatable !== false;
    delete m.pos;
    cfg.movable.push(m);
    selected = {kind: "movable", index: cfg.movable.length - 1};
  }
  renderAll();
}

function renderNets() {
  const box = $("netList");
  box.innerHTML = "";
  const nets = cfg.nets || [];
  if (!nets.length) {
    box.innerHTML = '<div class="muted">还没有连接。点下面的「+ 添加连接」，'
      + '再把左边选成出口（如 SO）、右边选成入口（如 SI）。</div>';
  }
  nets.forEach((n, i) => {
    const row = document.createElement("div");
    row.className = "net-row";
    row.dataset.net = i;
    const chip = document.createElement("span");
    chip.className = "net-index";
    chip.style.background = netColor(i);
    chip.textContent = i + 1;
    row.appendChild(chip);
    row.insertAdjacentHTML("beforeend",
      moduleSelect(n.from, "nFrom" + i) + portSelect(n.from, n.from_port, "nFP" + i, "out")
      + '<span class="arrow">→</span>'
      + moduleSelect(n.to, "nTo" + i) + portSelect(n.to, n.to_port, "nTP" + i, "in"));
    const del = document.createElement("button");
    del.textContent = "删除";
    del.onclick = () => { cfg.nets.splice(i, 1); renderAll(); };
    row.appendChild(del);
    row.querySelectorAll("select").forEach(s => s.onchange = () => {
      const sels = row.querySelectorAll("select");
      n.from = sels[0].value; n.from_port = sels[1].value;
      n.to = sels[2].value;   n.to_port = sels[3].value;
      renderNets();
      renderLinkPreview();
    });
    row.onmouseenter = () => highlightNet(i);
    row.onmouseleave = () => highlightNet(null);
    box.appendChild(row);
  });
  // 连接表本身也是“预览”的一部分：改完立刻重画拓扑图
  if ($("linkPreview")) renderLinkPreview();
}

function moduleSelect(value, id) {
  let s = `<select id="${id}">`;
  allModules().forEach(m => {
    s += `<option value="${esc(m.mod.id)}" ${m.mod.id === value ? "selected" : ""}>${esc(m.mod.id)}</option>`;
  });
  return s + "</select>";
}

/* 端口下拉：按 出口/入口/其它 分组，出口端把出口排前面、入口端反过来，
   这样「OUT -> IN」不用在一堆端口里找。 */
function portSelect(modId, value, id, side) {
  const entry = allModules().find(x => x.mod.id === modId);
  const ports = (entry && entry.mod.ports) || [];
  const groups = { out: [], in: [], other: [] };
  ports.forEach(p => {
    const f = portInfo(p.id).flow;
    groups[f === "out" ? "out" : f === "in" ? "in" : "other"].push(p);
  });
  const order = side === "in" ? ["in", "out", "other"] : ["out", "in", "other"];
  const titles = { out: "出口", in: "入口", other: "其它端口" };
  if (!ports.length) return `<select id="${id}"><option value="">（无端口）</option></select>`;
  let s = `<select id="${id}" class="port-select">`;
  order.forEach(g => {
    if (!groups[g].length) return;
    s += `<optgroup label="${titles[g]}">`;
    groups[g].forEach(p => {
      s += `<option value="${esc(p.id)}" ${p.id === value ? "selected" : ""}>`
        + `${esc(p.id)} · ${esc(portInfo(p.id).label)}</option>`;
    });
    s += "</optgroup>";
  });
  return s + "</select>";
}

function renderPreview() {
  const box = $("preview");
  box.style.gridTemplateColumns = `repeat(${cfg.cols}, 30px)`;
  box.innerHTML = "";
  const cells = [];
  for (let r = 0; r < cfg.rows; r++) {
    cells.push(new Array(cfg.cols).fill(null));
  }
  (cfg.fixed || []).forEach(m => {
    for (let r = 0; r < m.h; r++) for (let c = 0; c < m.w; c++) {
      const rr = m.pos[0] + r, cc = m.pos[1] + c;
      if (cells[rr] && cells[rr][cc] !== undefined) cells[rr][cc] = m.id;
    }
  });
  cells.forEach(row => row.forEach(v => {
    const d = document.createElement("div");
    d.className = "grid-cell" + (v ? " fixed" : "");
    d.textContent = v || "";
    box.appendChild(d);
  }));
}

/* ---------------- 通用小工具 ---------------- */
/* 端口分类、同名编号、连接校验、连线几何这些“要算的”都在 model.js，
   这里只负责把它们画到页面上（也方便 node 直接测那一半）。 */
const portInfo = Model.portInfo;
const findPort = Model.findPort;
const netColor = Model.netColor;

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ---------------- 设备预设（data/设备尺寸.xlsx） ---------------- */
async function loadPresets() {
  try {
    const data = await api("/api/presets");
    presets = data.devices || [];
    presetMeta = data;
  } catch (e) {
    presets = [];
    presetMeta = { warnings: ["读取预设接口失败: " + e.message], devices: [] };
  }
  renderPresetSelect();
  renderPresetPreview();
}

function presetByName(name) {
  return presets.find(p => p.name === name) || null;
}

function renderPresetSelect() {
  const sel = $("presetSelect");
  if (!sel) return;
  const keep = sel.value;
  const kw = ($("presetFilter").value || "").trim();
  sel.innerHTML = "";
  const ph = document.createElement("option");
  ph.value = "";
  ph.textContent = presets.length ? "— 选择设备预设 —" : "（没有读到预设）";
  sel.appendChild(ph);
  presets.filter(p => !kw || p.name.includes(kw)
                       || (p.ports || []).some(x => x.id.includes(kw.toUpperCase())))
    .forEach(p => {
      const o = document.createElement("option");
      o.value = p.name;
      o.textContent = `${p.name}  ${p.w}×${p.h}  ${(p.ports || []).map(x => x.id).join("/") || "无端口"}`;
      sel.appendChild(o);
    });
  if (keep && presets.some(p => p.name === keep)) sel.value = keep;
  const src = $("presetSource");
  if (src) {
    const warns = (presetMeta && presetMeta.warnings) || [];
    src.textContent = presets.length
      ? `共 ${presets.length} 个预设 · ${(presetMeta && presetMeta.source) || "?"}`
        + (warns.length ? ` · ${warns.length} 条告警` : "")
      : "没读到设备尺寸表：" + warns.join("；");
    src.title = warns.join("\n");
  }
}

/* 预设小图：尺寸 + 每个端口格标出端口 id */
function renderPresetPreview() {
  const box = $("presetPreview");
  if (!box) return;
  const sel = $("presetSelect");
  const p = sel ? presetByName(sel.value) : null;
  if (!p) {
    box.innerHTML = '<span class="muted">选一个预设，这里会画出它的尺寸与各面端口；'
      + '添加时同名模块从 1 开始编号（如 精炼炉1、精炼炉2）。</span>';
    return;
  }
  const owners = (r, c) => (p.ports || []).filter(pt => (pt.cells || []).some(x => x[0] === r && x[1] === c));
  let html = `<div class="preset-head"><b>${esc(p.name)}</b>`
    + `<span class="tag">${p.w}×${p.h}</span>`
    + `<span class="muted">${esc(p.summary)}</span>`
    + `<span class="muted">下一个编号</span><b>${esc(Model.nextModuleId(cfg, p.name))}</b></div>`;
  html += `<div class="preset-grid" style="grid-template-columns:repeat(${p.w}, 26px)">`;
  for (let r = 0; r < p.h; r++) {
    for (let c = 0; c < p.w; c++) {
      const os = owners(r, c);
      const info = os.length ? portInfo(os[0].id) : null;
      const style = info ? `background:${info.color}22;border-color:${info.color};color:${info.color}` : "";
      html += `<div class="preset-cell" style="${style}" title="${esc(os.map(o => o.id + " " + o.label).join(", "))}">`
        + `${esc(os.map(o => o.id).join(","))}</div>`;
    }
  }
  html += "</div>";
  html += `<div class="muted">北 ${esc(p.faces.N || "-")} ｜ 南 ${esc(p.faces.S || "-")}`
    + ` ｜ 西 ${esc(p.faces.W || "-")} ｜ 东 ${esc(p.faces.E || "-")}</div>`;
  box.innerHTML = html;
}

/* 同名模块从 1 开始编号、模块 ID 必须唯一：逻辑在 model.js 里 */
function addModuleFromPreset() {
  const sel = $("presetSelect");
  const p = sel ? presetByName(sel.value) : null;
  if (!p) return alert("先在「预设设备」里选一个再添加");
  const kind = $("newModKind").value;
  const id = Model.nextModuleId(cfg, p.name);   // 相同模块名从 1 开始标记
  const m = {
    id, preset: p.name, w: p.w, h: p.h,
    ports: (p.ports || []).map(pt => ({
      id: pt.id, dir: pt.dir, cells: (pt.cells || []).map(c => [c[0], c[1]]),
    })),
  };
  if (kind === "fixed") m.pos = [+$("newModR").value || 0, +$("newModC").value || 0];
  else m.rotatable = $("newModRot").checked;
  getArray(kind).push(m);
  selected = { kind, index: getArray(kind).length - 1 };
  selectedPort = m.ports[0] && m.ports[0].id;
  renderAll();
  sel.value = p.name;                        // 连加同名设备：下一个自动变成 name2
  renderPresetPreview();
}

/* ---------------- 连接预览 ---------------- */
/* 端口锚点/刻度/连线几何/连接校验都在 model.js，这里只拼 SVG 字符串。 */
function renderLinkPreview() {
  const box = $("linkPreview");
  if (!box) return;
  const sum = $("linkSummary"), issuesBox = $("linkIssues");
  box.innerHTML = "";
  if (issuesBox) issuesBox.innerHTML = "";

  const mods = allModules();
  const nets = cfg.nets || [];
  if (!mods.length) {
    box.innerHTML = '<span class="muted">还没有模块：先加几个设备，再设置「出口 → 入口」。</span>';
    if (sum) sum.textContent = "";
    return;
  }

  const rep = Model.netReport(cfg);
  if (sum) {
    sum.textContent = `${nets.length} 条连接 · ${mods.length} 个模块`
      + (rep.unusedOut.length ? ` · ${rep.unusedOut.length} 个出口未接` : "")
      + (rep.idle.length ? ` · ${rep.idle.length} 个模块没参与连接` : "");
  }

  /* --- 布局：模块框按网格摆放，尺寸与 w×h 成比例 --- */
  const boxes = Model.layoutBoxes(mods.map(x => ({
    id: x.mod.id, kind: x.kind, mod: x.mod, w: x.mod.w, h: x.mod.h,
  })));
  const width = boxes.width, height = boxes.height;
  const boxByMod = new Map(boxes.boxes.map(b => [b.id, b]));

  const svg = [];
  svg.push(`<svg class="link-svg" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" xmlns="http://www.w3.org/2000/svg">`);
  svg.push("<defs>");
  Model.NET_COLORS.forEach((c, i) => {
    svg.push(`<marker id="netarrow${i}" viewBox="0 0 10 10" refX="9.5" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="${c}"/></marker>`);
  });
  svg.push("</defs>");

  /* --- 连线：出口 -> 入口，两端各沿端口方向伸出一小段再弯过去 --- */
  const dupKey = new Map();
  nets.forEach((n, i) => {
    const fmod = Model.moduleById(cfg, n.from), tmod = Model.moduleById(cfg, n.to);
    const fp = findPort(fmod, n.from_port, "OUT");
    const tp = findPort(tmod, n.to_port, "IN");
    const b1 = boxByMod.get(n.from), b2 = boxByMod.get(n.to);
    if (!fp || !tp || !b1 || !b2) return;
    const a = Model.portAnchor(b1, fmod, fp), z = Model.portAnchor(b2, tmod, tp);
    const k = [n.from, fp.id, n.to, tp.id].join("\u0000");
    const dup = dupKey.get(k) || 0;      // 同一条线重复出现时错开，免得完全重叠
    dupKey.set(k, dup + 1);
    const g = Model.linkGeometry(a, z, dup, { width: width, height: height });
    const color = netColor(i);
    const title = `连接 ${i + 1}：${n.from}·${fp.id}（${portInfo(fp.id).label}） → ${n.to}·${tp.id}（${portInfo(tp.id).label}）`;
    svg.push(`<g class="link-group" data-net="${i}"><title>${esc(title)}</title>`
      + `<path d="${Model.linkPath(g)}" fill="none" stroke="${color}" stroke-width="2"`
      + ` marker-end="url(#netarrow${i})"/>`
      + `<text class="link-index" x="${Model.r1(g.mid.x)}" y="${Model.r1(g.mid.y + 3)}" text-anchor="middle" font-size="10"`
      + ` fill="${color}" stroke="#fff" stroke-width="3" paint-order="stroke">${i + 1}</text></g>`);
  });

  /* --- 模块框 + 端口刻度 --- */
  boxes.boxes.forEach(b => {
    const m = b.mod;
    const fixed = b.kind === "fixed";
    svg.push(`<rect x="${Model.r1(b.x)}" y="${Model.r1(b.y)}" width="${Model.r1(b.w)}" height="${Model.r1(b.h)}" rx="6" fill="${fixed ? "#fdeaea" : "#eef2f7"}" stroke="${fixed ? "#e79b93" : "#c4ccd6"}"/>`);
    (m.ports || []).forEach(p => {
      const info = portInfo(p.id);
      (p.cells || []).forEach(c => {
        const t = Model.portTick(b, m, p, c);
        svg.push(`<line x1="${Model.r1(t.x1)}" y1="${Model.r1(t.y1)}" x2="${Model.r1(t.x2)}" y2="${Model.r1(t.y2)}" stroke="${info.color}" stroke-width="3" stroke-linecap="round"/>`);
      });
      const a = Model.portAnchor(b, m, p);
      const lx = a.x + a.dx * 11, ly = a.y + a.dy * 11;
      const ta = a.dir === "W" ? "end" : a.dir === "E" ? "start" : "middle";
      const ty = a.dir === "N" ? -3 : a.dir === "S" ? 9 : 3;
      svg.push(`<text x="${Model.r1(lx)}" y="${Model.r1(ly + ty)}" font-size="8" fill="${info.color}" text-anchor="${ta}">${esc(p.id)}</text>`);
    });
    const label = Model.shortLabel(m.id, b.w);
    svg.push(`<text x="${Model.r1(b.x + b.w / 2)}" y="${Model.r1(b.y + b.h / 2 + 4)}" font-size="10" text-anchor="middle" fill="#1d2733">${esc(label)}</text>`);
  });
  svg.push("</svg>");
  box.innerHTML = svg.join("");

  /* --- 校验提示 --- */
  if (issuesBox) {
    const items = rep.issues.slice();
    rep.idle.forEach(({ mod }) => items.push({ level: "info", net: null, text: `模块「${mod.id}」没有参与任何连接` }));
    rep.unusedOut.forEach(({ mod, port }) => items.push({ level: "info", net: null, text: `出口「${mod.id}·${port.id}」没有用到` }));
    if (!items.length) {
      issuesBox.innerHTML = nets.length
        ? '<div class="issue ok">连接检查通过：每条连接都是从出口指向入口，端口格子也都在模块内。</div>'
        : "";
      return;
    }
    const order = { error: 0, warn: 1, info: 2 };
    items.sort((x, y) => order[x.level] - order[y.level]);
    issuesBox.innerHTML = items.slice(0, 40).map(it =>
      `<div class="issue ${it.level}" ${it.net === null || it.net === undefined ? "" : `data-net="${it.net}"`}>`
      + `${it.level === "error" ? "✕" : it.level === "warn" ? "!" : "·"} ${esc(it.text)}</div>`).join("");
    issuesBox.querySelectorAll("[data-net]").forEach(el => {
      const idx = +el.dataset.net;
      el.onmouseenter = () => highlightNet(idx);
      el.onmouseleave = () => highlightNet(null);
    });
  }
}

function highlightNet(idx) {
  const on = idx !== null && idx !== undefined;
  document.querySelectorAll("#linkPreview .link-group").forEach(g => {
    g.classList.toggle("hl", on && +g.dataset.net === idx);
  });
  document.querySelectorAll("#netList .net-row").forEach(r => {
    r.classList.toggle("hl", on && +r.dataset.net === idx);
  });
}

/* ---------------- tabs ---------------- */
document.querySelectorAll("nav.tabs button").forEach(b => {
  b.onclick = () => {
    document.querySelectorAll("nav.tabs button").forEach(x => x.classList.remove("active"));
    document.querySelectorAll(".tab").forEach(x => x.classList.remove("active"));
    b.classList.add("active");
    $("tab-" + b.dataset.tab).classList.add("active");
    if (b.dataset.tab === "result") {
      loadResults();
      maybeRepairVisualizations();
    }
    updateResultsTimer();
  };
});

/* ---------------- solve ---------------- */
async function startSolve() {
  await saveConfig(false, false);
  let seed = $("solveSeed").value.trim();
  if (!seed) {
    seed = Math.floor(Math.random() * 2147483647);
    $("solveSeed").value = seed;
  }
  const running = runningSolveJob(currentName);
  if (running) {
    if (!confirm("该题目还有求解任务在运行，确定再启动一个吗？")) return;
  }
  const body = {
    name: currentName,
    mode: $("solveMode").value,
    timeout: +$("solveTimeout").value,
    workers: +$("solveWorkers").value,
    seed: +seed,
    hint: $("solveHint").checked
  };
  const r = await api("/api/solve", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body)
  });
  trackJob(r.job, {kind: "solve", name: currentName, quiet: false});
  setActiveJob(r.job);
  ensurePolling();
}

/* ---------------- 作业 / 日志流 ---------------- */
function trackJob(id, meta = {}) {
  let s = jobs.get(id);
  if (!s) {
    s = {
      id, kind: meta.kind || "", name: meta.name || "", quiet: !!meta.quiet,
      status: "running", paused: false, returncode: null,
      seq: 0, lines: [], rendered: 0, forceRender: true, finished: false
    };
    jobs.set(id, s);
  } else {
    if (meta.kind) s.kind = meta.kind;
    if (meta.name) s.name = meta.name;
    if (meta.quiet !== undefined) s.quiet = !!meta.quiet;
  }
  return s;
}

function setActiveJob(id) {
  activeJobId = id;
  const s = jobs.get(id);
  if (s) { s.forceRender = true; s.rendered = 0; }
  const pre = $("jobLog");
  pre.textContent = "";
  renderActiveLog();
  updateJobUI();
}

function runningSolveJob(name) {
  for (const s of jobs.values()) {
    if (s.kind === "solve" && !s.finished && s.status === "running"
        && (!name || s.name === name)) return s;
  }
  return null;
}

function anyRunningJob() {
  for (const s of jobs.values()) if (!s.finished) return true;
  return false;
}

// 某个题目最近一次求解作业（优先正在运行的）
function latestSolveJob(name) {
  let running = null;
  let last = null;
  for (const s of jobs.values()) {
    if (s.kind !== "solve" || s.name !== name) continue;
    last = s;
    if (!s.finished) running = s;
  }
  return running || last;
}

async function pollJobsOnce() {
  const ids = Array.from(jobs.keys());
  let failed = false;
  for (const id of ids) {
    const s = jobs.get(id);
    if (s.finished) continue;
    try {
      const snap = await api(`/api/job?id=${encodeURIComponent(id)}&since=${s.seq}`);
      if (snap.log_from !== s.seq || snap.truncated) {
        // 服务端日志被裁剪 / 客户端落后：整段替换
        s.lines = snap.lines.slice();
        s.forceRender = true;
      } else if (snap.lines.length) {
        s.lines.push(...snap.lines);
      }
      s.seq = snap.log_next;
      s.status = snap.status;
      s.paused = !!snap.paused;
      s.kind = snap.kind || s.kind;
      s.name = snap.name || s.name;
      s.quiet = !!snap.quiet;
      s.returncode = snap.returncode;
      if (snap.status !== "running") {
        s.finished = true;
        if (s.kind === "solve" && s.name === currentName) {
          await loadResults();
          maybeRepairVisualizations();
        }
        if (s.kind === "render") loadResults();
      }
    } catch (e) {
      failed = true;
    }
  }
  pollFailures = failed ? pollFailures + 1 : 0;
  renderActiveLog();
  updateJobUI();
  if (!anyRunningJob()) stopPolling();
}

function ensurePolling() {
  if (jobTimer) return;
  jobTimer = setInterval(pollJobsOnce, 700);
  pollJobsOnce();
}

function stopPolling() {
  if (jobTimer) { clearInterval(jobTimer); jobTimer = null; }
}

function renderActiveLog() {
  const pre = $("jobLog");
  const s = activeJobId ? jobs.get(activeJobId) : null;
  if (!s) { pre.textContent = ""; return; }
  // 内存里只保留最近若干行，防止长时间运行把浏览器拖死
  if (s.lines.length > LOG_VIEW_MAX * 2) {
    const drop = s.lines.length - LOG_VIEW_MAX;
    s.lines.splice(0, drop);
    s.forceRender = true;
  }
  if (s.forceRender || s.rendered > s.lines.length) {
    pre.textContent = "";
    s.rendered = 0;
    s.forceRender = false;
    if (s.lines.length > LOG_VIEW_MAX) {
      s.rendered = s.lines.length - LOG_VIEW_MAX;
      pre.textContent = `…（已省略前 ${s.rendered} 行）\n`;
    }
  }
  if (s.lines.length > s.rendered) {
    const near = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 24;
    const chunk = s.lines.slice(s.rendered).join("\n") + "\n";
    pre.appendChild(document.createTextNode(chunk));
    s.rendered = s.lines.length;
    if (near) pre.scrollTop = pre.scrollHeight;
  }
}

function updateJobUI() {
  const solveJob = runningSolveJob(currentName);
  const active = activeJobId ? jobs.get(activeJobId) : null;
  const hints = [];
  if (active) {
    hints.push(`${active.kind} · ${active.name}`);
  }
  const parts = [];
  if (solveJob) {
    parts.push(solveJob.paused ? "已暂停" : "运行中");
    parts.push(`作业 ${solveJob.id}`);
  }
  if (pollFailures > 1) parts.push(`日志连接重试中 (${pollFailures})`);
  $("jobStatus").textContent = parts.join(" · ");
  $("logHint").textContent = hints.join(" ");
  $("btnSolve").disabled = !!solveJob;
  $("btnPause").disabled = !solveJob;
  $("btnPause").textContent = (solveJob && solveJob.paused) ? "继续" : "暂停";
  $("btnStop").disabled = !solveJob;

  const renders = Array.from(jobs.values())
    .filter(s => s.kind === "render" && !s.finished);
  const bg = $("bgStatus");
  if (bg) {
    bg.textContent = renders.length
      ? `后台可视化中…（补 ${renders.length} 个任务）`
      : "";
  }
}

async function controlJob(action) {
  const solveJob = runningSolveJob(currentName) || activeSolveJobForLog();
  if (!solveJob) return;
  try {
    const r = await api("/api/" + action, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({id: solveJob.id})
    });
    if (r && r.ok === false) alert("操作失败: " + (r.message || r.error || "未知错误"));
  } catch (e) {
    alert("操作失败: " + e.message);
  }
  ensurePolling();
  await pollJobsOnce();
}

function activeSolveJobForLog() {
  const s = activeJobId ? jobs.get(activeJobId) : null;
  return s && s.kind === "solve" && !s.finished ? s : null;
}

async function startRender() {
  const r = await api("/api/render", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({name: currentName, limit: 200})
  });
  trackJob(r.job, {kind: "render", name: currentName, quiet: true});
  ensurePolling();
  updateJobUI();
}

// 有解缺少 svg/txt 时后台静默补生成（不再往日志面板里塞东西）
function maybeRepairVisualizations(force = false) {
  if (runningSolveJob(currentName)) return false;
  const missing = (resultState.solutions || []).some(s => !s.ready);
  if (!missing) return false;
  const busy = Array.from(jobs.values())
    .some(s => s.kind === "render" && !s.finished && s.name === currentName);
  if (busy) return false;
  const now = Date.now();
  if (!force && now - lastRepairAt < 5000) return false;
  lastRepairAt = now;
  startRender();
  return true;
}

/* ---------------- results ---------------- */
async function loadResults(opts = {}) {
  if (!currentName) return;
  let data;
  try {
    data = await api("/api/results?name=" + encodeURIComponent(currentName));
  } catch (e) {
    if (!opts.quiet) $("resultSummary").textContent = "读取结果失败: " + e.message;
    return;
  }
  resultState.solutions = data.solutions || [];
  resultState.bestSvg = data.best_svg || null;
  resultState.files = data.files || [];
  resultState.version = Date.now();
  resultState.page = Math.min(resultState.page, Math.max(0, Math.ceil(resultState.solutions.length / resultState.pageSize) - 1));
  const solving = runningSolveJob(currentName);
  const notReady = resultState.solutions.filter(s => !s.ready).length;
  $("resultSummary").textContent =
    `${resultState.solutions.length} 个可行解 / ${resultState.files.length} 个文件`
    + (solving ? "（求解中，可随时刷新）" : "")
    + (notReady ? `（${notReady} 个可视化生成中）` : "");
  renderResultToolbar();
  renderResults();
}

// 刷新结果：重新读盘 + 需要时后台静默补齐可视化
async function refreshResults() {
  await loadResults();
  maybeRepairVisualizations(true);
}

function updateResultsTimer() {
  const want = $("autoRefreshResults")
    && $("autoRefreshResults").checked
    && !!runningSolveJob(currentName)
    && $("tab-result").classList.contains("active");
  if (want && !resultsTimer) {
    resultsTimer = setInterval(() => { loadResults({quiet: true}); }, 1500);
  } else if (!want && resultsTimer) {
    clearInterval(resultsTimer);
    resultsTimer = null;
  }
}

function resultFileUrl(path) {
  if (!path) return null;
  return "/file?path=" + encodeURIComponent(path) + "&v=" + resultState.version;
}

function sortedSolutions() {
  const arr = resultState.solutions.slice();
  if (resultState.sort === "cost-desc") {
    arr.sort((a, b) => (b.cost ?? 0) - (a.cost ?? 0));
  } else if (resultState.sort === "index") {
    arr.sort((a, b) => a.index - b.index);
  } else {
    arr.sort((a, b) => (a.cost ?? 0) - (b.cost ?? 0));
  }
  return arr;
}

function currentResultPage() {
  const arr = sortedSolutions();
  const start = resultState.page * resultState.pageSize;
  return arr.slice(start, start + resultState.pageSize);
}

function renderResultToolbar() {
  const box = $("resultToolbar");
  if (!box) return;
  box.innerHTML = "";

  const sortLabel = document.createElement("label");
  sortLabel.textContent = "排序 ";
  const sortSel = document.createElement("select");
  [["cost-asc", "传送带格数升序"], ["cost-desc", "传送带格数降序"], ["index", "原始顺序"]].forEach(([v, t]) => {
    const o = document.createElement("option");
    o.value = v;
    o.textContent = t;
    if (v === resultState.sort) o.selected = true;
    sortSel.appendChild(o);
  });
  sortSel.onchange = () => {
    resultState.sort = sortSel.value;
    resultState.page = 0;
    renderResultToolbar();
    renderResults();
  };
  sortLabel.appendChild(sortSel);
  box.appendChild(sortLabel);

  const sizeLabel = document.createElement("label");
  sizeLabel.textContent = "每页 ";
  const sizeSel = document.createElement("select");
  [4, 8, 12].forEach(n => {
    const o = document.createElement("option");
    o.value = n;
    o.textContent = `${n} 个`;
    if (n === resultState.pageSize) o.selected = true;
    sizeSel.appendChild(o);
  });
  sizeSel.onchange = () => {
    resultState.pageSize = +sizeSel.value;
    resultState.page = 0;
    renderResultToolbar();
    renderResults();
  };
  sizeLabel.appendChild(sizeSel);
  box.appendChild(sizeLabel);

  if (resultState.bestSvg) {
    const a = document.createElement("a");
    a.className = "button";
    a.href = resultFileUrl(resultState.bestSvg);
    a.target = "_blank";
    a.textContent = "打开最优解 SVG";
    box.appendChild(a);
  }
}

function renderResults() {
  const box = $("resultCards");
  box.innerHTML = "";
  if (!resultState.solutions.length) {
    if (resultState.bestSvg) {
      box.innerHTML = `<div class="result-card"><div class="result-head"><span class="title">最优解可视化</span></div>
        <img src="${resultFileUrl(resultState.bestSvg)}" alt="最优解 SVG"></div>`;
    } else {
      box.innerHTML = '<div class="panel muted">还没有结果。先到“求解”页运行。</div>';
    }
    return;
  }

  const pageItems = currentResultPage();
  const grid = document.createElement("div");
  grid.className = "result-grid";
  pageItems.forEach(sol => {
    const card = document.createElement("div");
    card.className = "result-card";
    const head = document.createElement("div");
    head.className = "result-head";
    const title = document.createElement("span");
    title.className = "title";
    title.textContent = `可行解 ${sol.index}`;
    head.appendChild(title);
    const cost = document.createElement("span");
    cost.className = "tag";
    cost.textContent = `传送带格数 ${sol.cost ?? "-"}`;
    head.appendChild(cost);
    card.appendChild(head);

    if (sol.svg) {
      const img = document.createElement("img");
      img.src = resultFileUrl(sol.svg);
      img.alt = `可行解 ${sol.index} SVG`;
      img.loading = "lazy";
      card.appendChild(img);
    } else {
      const pending = document.createElement("div");
      pending.className = "result-pending";
      pending.textContent = "可视化生成中…（每找到一个可行解就会立刻写出 txt + svg）";
      card.appendChild(pending);
    }

    const meta = document.createElement("div");
    meta.className = "result-meta";
    if (sol.svg) {
      const a = document.createElement("a");
      a.className = "button";
      a.href = resultFileUrl(sol.svg);
      a.target = "_blank";
      a.textContent = "打开 SVG";
      meta.appendChild(a);
    }
    if (sol.txt) {
      const a = document.createElement("a");
      a.className = "button";
      a.href = resultFileUrl(sol.txt);
      a.target = "_blank";
      a.textContent = "下载 TXT";
      meta.appendChild(a);
    }
    if (meta.childNodes.length) card.appendChild(meta);
    grid.appendChild(card);
  });
  box.appendChild(grid);

  const totalPages = Math.max(1, Math.ceil(resultState.solutions.length / resultState.pageSize));
  const pager = document.createElement("div");
  pager.className = "result-pagination";
  const prev = document.createElement("button");
  prev.textContent = "上一页";
  prev.disabled = resultState.page <= 0;
  prev.onclick = () => { resultState.page -= 1; renderResults(); };
  pager.appendChild(prev);
  const info = document.createElement("span");
  info.textContent = `第 ${resultState.page + 1} / ${totalPages} 页`;
  pager.appendChild(info);
  const next = document.createElement("button");
  next.textContent = "下一页";
  next.disabled = resultState.page >= totalPages - 1;
  next.onclick = () => { resultState.page += 1; renderResults(); };
  pager.appendChild(next);
  box.appendChild(pager);
}

/*
function escapeHtml(s) {
  return s.replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
}
*/

/* ---------------- init ---------------- */
$("problemSelect").onchange = () => selectProblem($("problemSelect").value);
$("btnLoad").onclick = () => selectProblem($("problemSelect").value);
$("btnNew").onclick = newConfig;
$("btnSave").onclick = () => saveConfig(false);
$("btnSaveAs").onclick = () => saveConfig(true);
$("btnExport").onclick = () => {
  const blob = new Blob([JSON.stringify(cfg, null, 2)], {type: "application/json"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "config." + currentName + ".json";
  a.click();
};
$("fileImport").onchange = async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  cfg = JSON.parse(await f.text());
  renderAll();
};
$("btnAddModule").onclick = () => {
  const typed = $("newModId").value.trim();
  if (!typed) return alert("请填模块 ID");
  const id = Model.uniqueModuleId(cfg, typed);
  if (id !== typed) alert(`已经有一个模块叫「${typed}」了，本次添加改用「${id}」\n（模块 ID 必须唯一，重名会互相覆盖）`);
  const kind = $("newModKind").value;
  const w = Math.max(1, +$("newModW").value || 1);
  const h = Math.max(1, +$("newModH").value || 1);
  const m = {id, w, h, ports: defaultPorts(kind, w, h)};
  if (kind === "fixed") m.pos = [+$("newModR").value || 0, +$("newModC").value || 0];
  else m.rotatable = $("newModRot").checked;
  getArray(kind).push(m);
  selected = {kind, index: getArray(kind).length - 1};
  selectedPort = m.ports[0] && m.ports[0].id;
  $("newModId").value = "";
  renderAll();
};

/* 预设：选设备 -> 按预设添加（同名从 1 开始编号） */
$("presetFilter").oninput = renderPresetSelect;
$("presetSelect").onchange = renderPresetPreview;
$("btnAddPreset").onclick = addModuleFromPreset;
function syncKindUI() {
  const fixed = $("newModKind").value === "fixed";
  const box = $("posFields");
  if (box) box.classList.toggle("hidden", !fixed);
  const rot = $("newModRot");
  if (rot) rot.disabled = fixed;   // 固定模块不旋转
}
$("newModKind").onchange = syncKindUI;
syncKindUI();

$("btnAddNet").onclick = () => {
  const mods = allModules();
  if (!mods.length) return alert("先添加模块");
  const a = mods[0].mod, b = mods[Math.min(1, mods.length - 1)].mod;
  cfg.nets = cfg.nets || [];
  // 默认挑「出口 -> 入口」：出口端优先选 OUT/SO/FO/GO，入口端优先选 IN/SI/FI/GI
  const pick = (mod, flow, fallback) => {
    const ports = mod.ports || [];
    const hit = ports.find(p => portInfo(p.id).flow === flow)
      || ports.find(p => p.id === fallback);
    return hit ? hit.id : ((ports[0] || {}).id || fallback);
  };
  cfg.nets.push({
    from: a.id, from_port: pick(a, "out", "OUT"),
    to: b.id, to_port: pick(b, "in", "IN")
  });
  renderNets();
  renderLinkPreview();
};
$("btnSolve").onclick = startSolve;
// 只有精确模式支持 warm start（完整 Hint + 历史上下界）
function syncHintUI() {
  const exact = $("solveMode").value === "exact";
  $("solveHint").disabled = !exact;
}
$("solveMode").onchange = syncHintUI;
$("solveHint").onchange = syncHintUI;
syncHintUI();
$("btnPause").onclick = () => {
  const job = runningSolveJob(currentName) || activeSolveJobForLog();
  controlJob(job && job.paused ? "resume" : "pause");
};
$("btnStop").onclick = () => {
  if (confirm("停止本次求解？已经找到的可行解会保留。")) controlJob("stop");
};
$("btnReloadResults").onclick = refreshResults;
$("autoRefreshResults").onchange = updateResultsTimer;
$("rows").onchange = () => { cfg.rows = +$("rows").value; renderAll(); };
$("cols").onchange = () => { cfg.cols = +$("cols").value; renderAll(); };

// 刷新页面后接上还在跑的求解作业，日志继续接着显示
async function restoreJobs() {
  try {
    const data = await api("/api/jobs?name=" + encodeURIComponent(currentName)
                           + "&kind=solve&limit=1");
    const list = data.jobs || [];
    if (!list.length) return;
    const snap = list[0];
    trackJob(snap.id, {kind: snap.kind, name: snap.name, quiet: snap.quiet});
    const s = jobs.get(snap.id);
    s.seq = 0;
    s.lines = [];
    s.forceRender = true;
    setActiveJob(snap.id);
    ensurePolling();
  } catch (e) { /* 忽略：连不上就算了 */ }
}

(async function init() {
  await loadPresets();          // 先拿预设，第一次 renderAll 就能用上
  await loadProblems();
  const first = $("problemSelect").value;
  if (first) await loadConfig(first);
  else newConfig();
  await restoreJobs();
  updateJobUI();
})();
