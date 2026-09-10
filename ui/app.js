let cfg = null;
let currentName = "example";
let selected = null;       // {kind, index}
let selectedPort = null;   // port id
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
  const out = [];
  (cfg.fixed || []).forEach((m, i) => out.push({ kind: "fixed", index: i, mod: m }));
  (cfg.movable || []).forEach((m, i) => out.push({ kind: "movable", index: i, mod: m }));
  return out;
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
}

function renderModuleList() {
  const box = $("moduleList");
  box.innerHTML = "";
  allModules().forEach(m => {
    const div = document.createElement("div");
    div.className = "mod-item " + m.kind + (selected && selected.kind === m.kind && selected.index === m.index ? " selected" : "");
    div.innerHTML = `<b>${m.mod.id}</b>
      <span class="tag">${m.kind === "fixed" ? "固定" : "可动"}</span>
      <span>${m.mod.w}×${m.mod.h}</span>
      <span class="muted">端口 ${(m.mod.ports || []).map(p => p.id).join("/") || "无"}</span>`;
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
      <label>ID <input id="medId" value="${m.id}" size="8"></label>
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
    renderModuleList(); renderPreview();
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
    chip.textContent = `${p.id} (${p.dir})`;
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
  (cfg.nets || []).forEach((n, i) => {
    const row = document.createElement("div");
    row.className = "net-row";
    row.innerHTML = moduleSelect(n.from, "nFrom" + i) +
      portSelect(n.from, n.from_port, "nFP" + i) + " → " +
      moduleSelect(n.to, "nTo" + i) + portSelect(n.to, n.to_port, "nTP" + i) +
      `<button data-del="${i}">删除</button>`;
    box.appendChild(row);
    row.querySelectorAll("select").forEach(s => s.onchange = () => {
      n.from = row.querySelector("select").value;
      const sels = row.querySelectorAll("select");
      n.from = sels[0].value; n.from_port = sels[1].value;
      n.to = sels[2].value; n.to_port = sels[3].value;
      renderNets();
    });
    row.querySelector("button").onclick = () => { cfg.nets.splice(i, 1); renderNets(); };
  });
}

function moduleSelect(value, id) {
  let s = `<select id="${id}">`;
  allModules().forEach(m => {
    s += `<option value="${m.mod.id}" ${m.mod.id === value ? "selected" : ""}>${m.mod.id}</option>`;
  });
  return s + "</select>";
}

function portSelect(modId, value, id) {
  const m = allModules().find(x => x.mod.id === modId);
  let s = `<select id="${id}">`;
  ((m && m.mod.ports) || []).forEach(p => {
    s += `<option value="${p.id}" ${p.id === value ? "selected" : ""}>${p.id}</option>`;
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
    max_solutions: +$("solveMaxSolutions").value || 0,
    hint: $("solveHint").checked,
    hint_strict: $("solveHintStrict").checked
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
  const id = $("newModId").value.trim();
  if (!id) return alert("请填模块 ID");
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
$("btnAddNet").onclick = () => {
  const mods = allModules();
  if (!mods.length) return alert("先添加模块");
  const a = mods[0].mod, b = mods[Math.min(1, mods.length - 1)].mod;
  cfg.nets = cfg.nets || [];
  cfg.nets.push({
    from: a.id, from_port: (a.ports[0] || {}).id || "OUT",
    to: b.id, to_port: (b.ports[0] || {}).id || "IN"
  });
  renderNets();
};
$("btnSolve").onclick = startSolve;
// 精确模式才有 warm start；「只找严格更优解」依赖 warm start
function syncHintUI() {
  const exact = $("solveMode").value === "exact";
  $("solveHint").disabled = !exact;
  $("solveHintStrict").disabled = !exact || !$("solveHint").checked;
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
  await loadProblems();
  const first = $("problemSelect").value;
  if (first) await loadConfig(first);
  else newConfig();
  await restoreJobs();
  updateJobUI();
})();
