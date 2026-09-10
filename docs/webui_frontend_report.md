# WebUI 前后端技术报告（`webui/` + `webui_server.py`）

> 阅读范围（全文逐行读取）：
> - `webui/index.html`（实际 **119** 行，非任务描述中的 109 行）
> - `webui/app.js`（实际 **879** 行，非 815 行）
> - `webui/style.css`（104 行）
> - `webui_server.py`（实际 **531** 行，非 473 行）
> - 追加：`proc_ctl.py`（179 行，暂停/恢复的实现方，用于第 6 节）
>
> 行号均以当前磁盘文件为准。文中所有 DOM id、函数名、URL 均为实际读到的内容，未作推测。

---

## 1. 页面结构（`index.html`）

整体是一个单页三标签布局，`<header>` 顶栏 + `<nav class="tabs">` + `<main>` 中三个 `<section class="tab">`（仅 `.active` 可见，CSS `webui/style.css:23-24`）。

### 1.1 顶栏（题目管理，`index.html:9-22`）

| DOM id | 元素 | 作用 |
|---|---|---|
| `#problemSelect` | `<select>` (12) | 题目下拉框，选项由 `loadProblems()` 填成 `名字 (N files)`（`app.js:66-71`） |
| `#problemName` | `<input>` (13) | 当前题目名，保存/另存为时的目标名（`app.js:116`） |
| `#btnLoad` | `<button>` (14) | 载入下拉框选中的题目 → `selectProblem()`（`app.js:798`） |
| `#btnNew` | `<button>` (15) | 新建空题目 → `newConfig()`（`app.js:799`） |
| `#btnSave` | `<button>` (16) | 保存到当前名（`app.js:800`） |
| `#btnSaveAs` | `<button>` (17) | 以输入框名字另存（`app.js:801`） |
| `#fileImport` | `<input type=file accept=".json" hidden>` (18) | 本地 JSON 导入，`FileReader` 取代 `cfg`（`app.js:809-814`） |
| `#btnExport` | `<button>` (19) | 把 `cfg` 序列化成 Blob 下载为 `config.<name>.json`（`app.js:802-808`） |
| `#btnRefresh` | `<a class="button" onclick="loadProblems();return false;">` (20) | 刷新题目列表。依赖 `app.js` 是 **经典脚本**（非 module，`index.html:117`），函数在全局作用域 |
| `#fileImport` 外层 `label.file-btn` | (18) | 触发隐藏 file input 的可点击样式壳 |

### 1.2 标签导航（`index.html:24-28`）

三个 `<button data-tab="edit|solve|result">`，无 id，靠 `document.querySelectorAll("nav.tabs button")` 绑定（`app.js:334-346`），点击后给 `#tab-<data-tab>` 加 `.active`。

### 1.3 题目编辑页 `#tab-edit`（`index.html:31-74`）

| DOM id | 行 | 作用 |
|---|---|---|
| `#rows` | 34 | 画布行数，`onchange` 写回 `cfg.rows` 并 `renderAll()`（`app.js:851`） |
| `#cols` | 35 | 画布列数，`onchange` 写回 `cfg.cols`（`app.js:852`） |
| `#moduleList` | 41 | 模块列表容器，`.mod-item`，点击选中模块（`app.js:138-155`） |
| `#newModId` | 43 | 新模块 ID 输入 |
| `#newModKind` | 44 | 新模块类型：`movable` / `fixed` |
| `#newModW` / `#newModH` | 48 / 49 | 新模块宽、高（默认 3×3） |
| `#newModR` / `#newModC` | 50 / 51 | 固定模块的行、列锚点 |
| `#newModRot` | 52 | 可动模块"可旋转"复选框（默认勾选） |
| `#btnAddModule` | 53 | 添加模块 → `app.js:815-829` |
| `#moduleEditor` | 59 | 右侧"选中模块 / 端口编辑"面板容器，整块由 `renderModuleEditor()` 用 `innerHTML` 重建（`app.js:157-251`） |
| `#netList` | 65 | 连接表容器，每行由 `renderNets()` 生成（`app.js:272-292`） |
| `#btnAddNet` | 66 | 添加连接（默认 mods[0] → mods[1]）（`app.js:830-840`） |
| `#preview` | 71 | "固定模块预览"网格，`renderPreview()` 只画固定模块（`app.js:311-331`） |

`#moduleEditor` 内部**运行时动态产生**的 id（`app.js:161-185`，不在 `index.html` 里）：
`#medId`、`#medKind`、`#medW`、`#medH`、`#medR`（仅固定）、`#medC`（仅固定）、`#medRot`（仅可动）、`#medDelete`、`#portList`、`#newPortId`、`#newPortDir`、`#btnAddPort`、`#portGrid`。

### 1.4 求解页 `#tab-solve`（`index.html:76-102`）

| DOM id | 行 | 作用 |
|---|---|---|
| `#solveMode` | 80 | `exact`（CP-SAT 精确最优）/ `heuristic`（启发式） |
| `#solveTimeout` | 85 | 时间上限秒（默认 120） |
| `#solveWorkers` | 86 | 线程数（默认 8，仅 exact 分支使用） |
| `#solveSeed` | 87 | 随机种子；留空时前端用 `Math.random()*2147483647` 生成并回填（`app.js:351-355`）。**仅 heuristic 分支使用** |
| `#solveMaxSolutions` | 88 | 最多保存解（默认 200） |
| `#btnSolve` | 89 | 开始求解 |
| `#btnPause` | 90 | 初始 `disabled`，文案在"暂停/继续"间切换 |
| `#btnStop` | 91 | 初始 `disabled` |
| `#jobStatus` | 92 | 文本状态："运行中/已暂停 · 作业 <id>"，失败重试计数（`app.js:516-521`） |
| `#logHint` | 99 | 当前日志面板所显示作业的 `kind · name`（`app.js:522`） |
| `#jobLog` | 100 | `<pre class="log">` 日志面板，增量追加 |

另外 `index.html:95-96` 是静态说明文字，明确写了"暂停会把求解进程挂起（不占 CPU），但挂起时间仍计入时间上限"——与后端 `webui_server.py:433-434` 的日志文案一致。

### 1.5 结果页 `#tab-result`（`index.html:104-114`）

| DOM id | 行 | 作用 |
|---|---|---|
| `#btnReloadResults` | 107 | "刷新结果" → `refreshResults()`（`app.js:849`） |
| `#autoRefreshResults` | 108 | 默认 `checked`，控制 1.5s 自动刷新（`app.js:850` → `updateResultsTimer`） |
| `#bgStatus` | 109 | 后台补图状态："后台可视化中…（补 N 个任务）"（`app.js:528-535`） |
| `#resultSummary` | 110 | "N 个可行解 / M 个文件（求解中，可随时刷新）（K 个可视化生成中）"（`app.js:601-604`） |
| `#resultToolbar` | 112 | 排序（cost-asc/cost-desc/index）、每页 4/8/12、"打开最优解 SVG" 链接（`app.js:651-702`） |
| `#resultCards` | 113 | 结果卡片网格 `.result-grid` + 分页器（`app.js:704-788`） |

### 1.6 样式要点（`style.css`）

`.tab{display:none}` / `.tab.active{display:block}`（23-24）实现标签切换；`.grid-cell`（53-57）用于预览、`.port-cell`（59-64）用于端口网格（46×34px）；`.result-grid` 自适应 `minmax(440px,1fr)`（78-81）；`button:disabled{opacity:.45}`（100）。

---

## 2. 前端状态模型（`app.js` 全局变量）

| 变量 | 行 | 含义 |
|---|---|---|
| `cfg` | 1 | 当前 config 对象 `{rows, cols, fixed:[], movable:[], nets:[]}`，是**唯一的可变真源**，所有编辑直接原地改它；`null` 表示尚未载入 |
| `currentName` | 2 | 当前题目名，初值 `"example"`；保存成功后取服务端回显的 `r.name`（`app.js:122`） |
| `selected` | 3 | `{kind:"fixed"\|"movable", index}` 或 `null`，模块列表/编辑器的选中项。注意 index 是**数组下标**，删除/改类型后会重排（`app.js:210-211, 255-268`） |
| `selectedPort` | 4 | 当前选中的端口 id 字符串或 `null`；决定 `#portGrid` 点击改哪个端口 |
| `resultState` | 5 | `{solutions:[], page:0, pageSize:4, sort:"cost-asc", version:0}`；运行时还会挂上 `bestSvg`、`files`（`app.js:594-596`）。`version` 只用作 `/file` 的 cache-buster（`app.js:630`） |
| `jobs` | 8 | `Map<jobId, stream>`，每个作业一份独立日志缓冲，互不覆盖 |
| `activeJobId` | 9 | 当前显示在 `#jobLog` 里的作业 id |
| `jobTimer` | 10 | 作业轮询 `setInterval` 句柄（700ms） |
| `pollFailures` | 11 | 连续轮询失败计数，>1 时在 `#jobStatus` 显示"日志连接重试中 (N)" |
| `lastRepairAt` | 12 | 上次触发后台补图的时间戳，用于 5s 限流（`app.js:578`） |
| `resultsTimer` | 13 | 结果页 1.5s 自动刷新 `setInterval` 句柄 |
| `LOG_VIEW_MAX` | 14 | 3000，日志面板最多渲染的行数 |
| `$` | 16 | `document.getElementById` 简写 |
| `api()` | 18-22 | `fetch` + `!r.ok` 时 `throw new Error(await r.text())` + `r.json()` |

`jobs` 中每个 stream 的结构（`app.js:381-385`）：

```
{ id, kind, name, quiet, status, paused, returncode,
  seq,          // 服务端绝对日志游标（= 上次响应的 log_next）
  lines: [],    // 已拉到的日志行
  rendered,     // 已经写进 DOM 的行数
  forceRender,  // 需要整段重绘
  finished }    // 已收到非 running 状态
```

**持久化**：全部状态只在内存里，没有 `localStorage`/`sessionStorage`。刷新页面后唯一的状态恢复通道是 `restoreJobs()`（`app.js:855-870`），且只恢复 `kind=solve` 的最近一个作业。

---

## 3. HTTP 接口契约

前端所有请求都过 `api()`（`app.js:18-22`）。后端是一个 `BaseHTTPRequestHandler` 子类，用 `if path == ...` 链式分发：`do_GET`（`webui_server.py:282-348`）、`do_POST`（`webui_server.py:351-413`），控制类请求统一进 `handle_control`（415-472）。

| # | 方法 | 路径 | 前端调用点 | 查询参数 / 请求体 | 响应 JSON | 后端实现 |
|---|---|---|---|---|---|---|
| 1 | GET | `/api/problems` | `loadProblems()` `app.js:62`；`#btnRefresh` 内联 `loadProblems()` | 无 | `{"problems":[{"name","file","results":[文件名...]}]}` | `do_GET` **300-301** → `list_problems()` **76-86** |
| 2 | GET | `/api/config?name=<name>` | `loadConfig()` `app.js:76` | `name` | **裸 config 对象**（无包装，直接就是 `cfg`）；文件不存在时 404 `{"error":"not found"}` | `do_GET` **302-307** → `config_path()` **59-60**、`read_json()` **68-73** |
| 3 | POST | `/api/config` | `saveConfig()` `app.js:118-121` | body `{"name","config":{...}}` | `{"ok":true,"name","file"}`；非法名 → 400 `{"error":...}`；`config` 非 dict → 400 | `do_POST` **358-369**（`json.dump(..., indent=2, ensure_ascii=False)`，367-368） |
| 4 | POST | `/api/solve` | `startSolve()` `app.js:368-371` | body `{"name","mode","timeout","workers","seed","max_solutions"}` | `{"job":"<12位hex>"}`；400 非法名；404 `{"error":"config not found"}` | `do_POST` **371-394** → `start_job()` **159-231** |
| 5 | POST | `/api/render` | `startRender()` `app.js:560-563`（body 里发 `limit:200`，**后端忽略**） | body `{"name","limit"}` | `{"job":"<id>"}`；400/404 同上 | `do_POST` **396-405** → `start_repair()` **234-238** |
| 6 | POST | `/api/pause` | `controlJob("pause")` ← `#btnPause` `app.js:842-845` | body `{"id":jobId}` | `{"ok":true,"paused":true,"paused,"message"}`；非运行中 → **409** `{"error":"当前没有正在运行的进程"}`；已暂停 → 200 `{"ok":true,"paused":true,"message":"已经是暂停状态"}` | `do_POST` **407-411** → `handle_control()` **423-437** → `proc_ctl.suspend()` `proc_ctl.py:133-149` |
| 7 | POST | `/api/resume` | `controlJob("resume")` `app.js:844` | body `{"id"}` | `{"ok","paused","message"}`；409 同上；未暂停 → 200 `{"ok":true,"paused":false,...}` | `handle_control()` **439-452** → `proc_ctl.resume()` `proc_ctl.py:152-168` |
| 8 | POST | `/api/stop` | `controlJob("stop")` ← `#btnStop` `app.js:846-848` | body `{"id"}` | `{"ok":true,"stopped":true}`；已结束 → `{"ok":true,"stopped":true,"message":"进程已结束"}`；terminate 抛错 → `{"ok":false,"message"}` | `handle_control()` **454-472** |
| 9 | GET | `/api/job?id=<id>&since=<seq>` | `pollJobsOnce()` `app.js:437` | `id`（必需）、`since`（可选，缺省/非法则 `None`＝全量） | `{"id","kind","name","status","quiet","paused","returncode","started","ended","pid","cmd","log_from","log_next","truncated","lines":[...]}` | `do_GET` **308-320** → `job_snapshot()` **116-156** |
| 10 | GET | `/api/jobs?name=&kind=solve&limit=1` | `restoreJobs()` `app.js:857-858` | `name`、`kind`、`limit`（默认 5） | `{"jobs":[snapshot(tail=40), ...]}`，按 `started` 倒序 | `do_GET` **321-331**（`tail=40` 由 331 行传入） |
| 11 | GET | `/api/results?name=<name>` | `loadResults()` `app.js:589` | `name` | `{"name","solutions":[{"index","cost","state","paths","svg","txt","ready"}],"files":[文件名],"best_svg"}` | `do_GET` **332-334** → `collect_results()` **475-513** |
| 12 | GET | `/file?path=<相对路径>` | `resultFileUrl()` `app.js:628-631`，用于 `<img src>` / 下载链接（`app.js:697,710,737,753,761`） | `path`（相对 `ROOT`）；前端额外发 `&v=<version>` 做缓存击穿，**后端忽略** | 文件字节流，`Content-Type` 由 `mimetypes.guess_type` 决定；越界 → 403 `forbidden`；不存在 → 404 `not found` | `do_GET` **294-299**（`inside_root` 校验 297-298）→ `send_file()` **264-275** |
| 13 | GET | `/static/style.css`、`/static/app.js` | `index.html:6,117` | 路径后缀即 `WEBUI_DIR` 下的相对路径 | 文件字节流；越界 → 403 | `do_GET` **288-293** |
| 14 | GET | `/`、`/index.html` | 浏览器地址栏 | — | `webui/index.html` | `do_GET` **286-287** |
| 15 | GET | `/api/files?name=` | **前端未调用（死接口）** | `name` | `{"files":[{"name","size","path"}]}` | `do_GET` **335-347** |

补充事实：

- `/api/solve` 的两种命令构造：`mode=="exact"` → `layout_exact.py <config> --time-limit <t> --workers <w> --max-solutions <n> --verbose`（**383-387**）；否则（任意非 `exact` 字符串）→ `layout_solver.py <config> --timeout <t> --seed <s> --max-solutions <n>`（**389-392**）。注意 `seed` 只在 heuristic 分支出现，`workers` 只在 exact 分支出现。
- `/api/solve`、`/api/render` 返回的 job id 是 `uuid.uuid4().hex[:12]`（`webui_server.py:160`）。
- `truncated` 与 `log_from`/`log_next` 构成增量日志契约：`log_from` 是本次 `lines[0]` 对应的**绝对行号**，`log_next` 是同一条流的下一个绝对行号（`job_snapshot` **139-155**）。前端只用 `log_from`/`truncated` 判断是否需要整段替换、用 `log_next` 推进游标，**不使用** `log_from` 直接拼接。
- `collect_results` 的 `svg`/`txt` 字段是**相对 `ROOT` 的 POSIX 路径**（`f"result/{name}/{svg}"`，**499-500**），正好能喂给 `/file?path=`；`ready = has_svg and has_txt`（**501**）。

---

## 4. 网格编辑器交互

### 4.1 模块列表与选中（`app.js:138-155`）

`allModules()`（39-44）把 `cfg.fixed` 和 `cfg.movable` 摊平成 `{kind,index,mod}`。点击 `.mod-item` 设置 `selected`，并把 `selectedPort` 重置为该模块的第一个端口 id：

```js
selected = {kind: m.kind, index: m.index};
selectedPort = (m.mod.ports && m.mod.ports[0]) ? m.mod.ports[0].id : null;   // 150
```

### 4.2 端口位置编辑：点格子 toggle（`app.js:230-250`）

`#portGrid` 用 CSS Grid 渲染，**列宽固定 46px**，行数 = `m.h`、列数 = `m.w`：

```js
pg.style.gridTemplateColumns = `repeat(${m.w}, 46px)`;    // 231
```

每个格子的渲染逻辑（236-239）：
- `owners` = 所有 `cells` 里包含 `[r,c]` 的端口；
- 只要有 owner 就加 `.on`；
- `cell.textContent = owners.map(p => p.id).join(",")` —— 所以格子是**多端口重叠的并集视图**；
- 239 行又对 `owners.some(p => p.id === selectedPort)` 加一次 `.on`，与 237 行效果重复（冗余代码）。

点击行为（240-247）：在**当前 `selectedPort`** 的 `cells` 里 toggle `[r,c]`；找不到选中端口时 `alert("先添加/选择一个端口")`。也就是说：格子的高亮是"所有端口"的并集，但点击只改"选中端口"一个，不能通过点击把格子从别的端口摘掉。

### 4.3 方向的选定（`app.js:177-184`）

端口方向**只在创建时**由 `#newPortDir` 决定（选项 N/S/W/E，默认第一项 `N`）：

```js
m.ports.push({id: pid, dir: $("newPortDir").value, cells: []});   // 216
```

该值写入 config 的 `ports[].dir` 后就**没有任何 UI 能再修改**；端口 chip 只读显示 `${p.id} (${p.dir})`（225）。同样地，**没有删除端口**的按钮，只能整块删除模块。

### 4.4 模块增 / 删 / 改写回 config

- **增**（`app.js:815-829`，`#btnAddModule`）：`{id, w, h, ports: defaultPorts(kind,w,h)}`；固定模块补 `pos=[R,C]`，可动模块补 `rotatable`。`w/h` 都用 `Math.max(1, +value || 1)` 兜底。默认端口由 `defaultPorts()`（**50-58**）生成：固定 = 底边整行 `OUT/dir=S`（`cells=[[h-1,c] for c in 0..w-1]`）；可动 = `w>=1` 时左边整列 `IN/dir=W`，`w>=2` 时右边整列 `OUT/dir=E`。
- **改**：编辑器所有输入框绑 `onchange = finish`（`app.js:206-207`），`finish()`（**187-205**）直接原地改对象 `m`：
  - `m.id = 新值`，并且**同步改写 `cfg.nets` 里引用旧 id 的 `from`/`to`**（192-195）；
  - `m.w/h = Math.max(1, +v || 1)`；
  - 固定模块写 `m.pos = [row, col]`；可动模块写 `m.rotatable`；
  - 最后 `renderModuleList(); renderPreview();`。
- **改类型**：`#medKind.onchange → changeKind(m, newKind, finish)`（208），`changeKind()`（**255-270**）先把 `m` 从旧数组 `splice` 出去（用 `selected.kind/selected.index`），再 `push` 到新数组，并 `delete` 掉不适用的字段（固定 → `delete m.rotatable` 并补 `m.pos`；可动 → `delete m.pos` 并 `m.rotatable = m.rotatable !== false`），随后更新 `selected` 下标并 `renderAll()`。端口数组原样保留。
- **删**（`app.js:209-212`，`#medDelete`）：`getArray(selected.kind).splice(selected.index, 1)`，然后 `selected = null; renderAll();`。**不清理 `cfg.nets` 中对被删模块的引用**。

### 4.5 连接表（`app.js:272-309`）

`renderNets()` 为每条 net 生成 `[模块下拉] [端口下拉] → [模块下拉] [端口下拉] [删除]`；任一 `select` 变化时**重新按 4 个 select 的位置整体回读**（283-289）：

```js
n.from = sels[0].value; n.from_port = sels[1].value;
n.to   = sels[2].value; n.to_port   = sels[3].value;
renderNets();
```

`moduleSelect()`（294-300）的选项来自 `allModules()`，取 `mod.id` 作 value；`portSelect()`（302-309）按 modId 找模块、用 `mod.ports` 生成选项。因此改模块 id 后下拉框会跟着变（因为 `finish()` 已同步改写 nets）。

### 4.6 固定模块预览（`app.js:311-331`）

按 `cfg.rows × cfg.cols` 建立 `cells` 二维数组，把每个固定模块的 `m.h×m.w` 矩形写为 `m.id`：

```js
const rr = m.pos[0] + r, cc = m.pos[1] + c;
if (cells[rr] && cells[rr][cc] !== undefined) cells[rr][cc] = m.id;   // 321-322
```

越界的部分被静默丢弃；重叠部分后写覆盖先写；负坐标因为 `cells[-1]` 为 `undefined` 也被跳过。

---

## 5. 求解页轮询与日志、按钮状态机、结果页刷新

### 5.1 轮询间隔与生命周期

- `ensurePolling()`（**470-474**）：若 `jobTimer` 为空则 `setInterval(pollJobsOnce, 700)`，**并立刻调用一次** `pollJobsOnce()`。所以作业日志的轮询间隔是 **700ms**。
- `stopPolling()`（**476-478**）清掉定时器；触发点是 `pollJobsOnce()` 末尾：`if (!anyRunningJob()) stopPolling();`（**467**），`anyRunningJob()`（413-416）只要 `jobs` 里还有 `!finished` 的条目就为真。
- `pollJobsOnce()`（**430-468**）顺序 `await` 遍历 `jobs` 里所有未完成作业，每个发一次 `GET /api/job?id=<id>&since=<s.seq>`：
  - `snap.log_from !== s.seq || snap.truncated` → `s.lines = snap.lines.slice(); s.forceRender = true;`（**438-441**）即**整段替换**；
  - 否则 `s.lines.push(...snap.lines)`（443）即**增量追加**；
  - `s.seq = snap.log_next`（**445**）——游标是服务端给的**绝对**行号，不是本地数组下标，这才是跨裁剪不丢行的关键；
  - 同步 `status/paused/kind/name/quiet/returncode`；
  - `status !== "running"` → `s.finished = true`；若 `kind==="solve" && name===currentName` 则 `await loadResults()` 并 `maybeRepairVisualizations()`（**452-459**）；若 `kind==="render"` 则 `loadResults()`；
  - 任一请求抛错 → `failed = true`，`pollFailures++`（464）；成功则清零。

### 5.2 增量日志游标的服务端语义（`webui_server.py:105-156`）

- `job["log"]` 是行数组，`log_base` = 已被裁掉的行数，`log_next` = 累计追加过的总行数；`job_log()`（**105-113**）在 `JOBS_LOCK` 下 append 并裁剪到 `LOG_MAX_LINES = 4000`（`extra = len(log) - 4000; del log[:extra]`，**110-113**）。
- `job_snapshot(job, since)`（**116-156**）：
  - `since is None` → 全量，`start = base`；
  - `since < base`（客户端落后到已被裁掉的位置）→ **整段重发**，`start = base`，`truncated = True`（**125-129**）；
  - 否则 `offset = min(max(since - base, 0), len(lines))`，`payload = lines[offset:]`，`start = base + offset`（**131-132**）。

### 5.3 前端日志渲染（`app.js:480-506`）

- 客户端缓冲上限 `LOG_VIEW_MAX = 3000`：当 `s.lines.length > 6000` 时一次性 `splice` 掉头部到只剩 3000 行，并 `forceRender = true`（**485-489**）。
- `renderActiveLog()` 是**增量 DOM 追加**：`forceRender` 或 `rendered > lines.length` 时清空重画（此时若超过 3000 行会先写一行 `…（已省略前 N 行）`，**494-497**）；否则只 append 从 `rendered` 起的新行（**499-505**），并在**用户已经在底部附近**（`scrollTop + clientHeight >= scrollHeight - 24`）时才自动滚到底（**500,504**），避免打断向上翻日志的用户。

### 5.4 暂停 / 继续 / 停止的状态机

- `updateJobUI()`（**508-536**）：
  - `solveJob = runningSolveJob(currentName)`（405-411，遍历本地 `jobs` 找 `kind==="solve" && !finished && status==="running" && name===currentName`）；
  - `#btnSolve.disabled = !!solveJob`（523）；
  - `#btnPause.disabled = !solveJob`，文案 `solveJob.paused ? "继续" : "暂停"`（524-525）；
  - `#btnStop.disabled = !solveJob`（526）；
  - `#jobStatus` 拼 `"运行中"/"已暂停" + 作业 id + 可选重试计数`（516-521）；
  - `#bgStatus` 统计未完成的 `render` 作业数（528-535）。
- `#btnPause.onclick`（**842-845**）：`controlJob(job && job.paused ? "resume" : "pause")`——**按钮本身就是暂停/继续的双态开关**，没有独立的继续按钮。
- `#btnStop.onclick`（846-848）：先 `confirm("停止本次求解？已经找到的可行解会保留。")`。
- `controlJob(action)`（**538-552**）：POST `/api/<action>`，body `{id}`；HTTP 非 2xx 时 `api()` 抛错 → `alert("操作失败: " + e.message)`（消息是服务端返回的**原始 JSON 文本**）；HTTP 200 但 `ok === false` 时 `alert("操作失败: " + (r.message || r.error))`（546）；最后 `ensurePolling(); await pollJobsOnce();` 立即强制刷新一次状态，不等 700ms。
- **接回正在运行的作业**有两处：
  1. 页面加载：`restoreJobs()`（**855-870**）→ `GET /api/jobs?name=<currentName>&kind=solve&limit=1`，取第一条，`trackJob()` 后用 `s.seq = 0; s.lines = []` 复位，再 `setActiveJob()` + `ensurePolling()`；随后第一次轮询会以 `since=0` 拉回服务端缓冲里的全部（≤4000 行）日志。
  2. 切换题目：`selectProblem()`（84-92）→ `syncActiveJob()`（**95-105**）从**本地** `jobs` 里找 `latestSolveJob(currentName)`（418-428，优先未完成的），找到就 `setActiveJob`，否则在"当前活动作业属于别的题目"时清空 `#jobLog`。

### 5.5 结果页"刷新结果"

- `#btnReloadResults` → `refreshResults()`（**610-613**）：先 `await loadResults()`（重新 `GET /api/results`），再 `maybeRepairVisualizations(true)`——`force=true` 绕开 5s 限流（578）。
- `loadResults(opts)`（**585-607**）：成功后写 `resultState.solutions/bestSvg/files`、`version = Date.now()`，把 `page` 夹到合法范围（598），拼 `#resultSummary` 文案，再 `renderResultToolbar()` + `renderResults()`。失败时只在非 quiet 模式下把错误写进 `#resultSummary`（591），不抛异常。
- **自动刷新**：`updateResultsTimer()`（**615-626**）判定 `want = #autoRefreshResults.checked && !!runningSolveJob(currentName) && #tab-result 处于 active`，为真则挂 1500ms 的 `setInterval(() => loadResults({quiet:true}), 1500)`，为假则清除。调用点只有三处：标签点击（344）、`selectProblem`（91）、复选框 `onchange`（850）。
- `#file` 图片 URL 带 `&v=<resultState.version>` 做缓存击穿（630）。
- 结果渲染支持排序（cost-asc/cost-desc/index，633-643）与分页（pageSize ∈ {4,8,12}），无 SVG 的解显示 `.result-pending` 占位（742-745）。
- 后台补图：`maybeRepairVisualizations()`（**570-582**）在"该题目没有运行中的求解 && 存在 `!ready` 的解 && 没有同名 render 作业在跑 && （force 或距上次 >5s）"时 `startRender()` → POST `/api/render`。

---

## 6. 后端架构（`webui_server.py`，531 行）

### 6.1 全局与类结构

- 只有一个 handler 类：`class Handler(BaseHTTPRequestHandler)`（**241**），`server_version = "LayoutWebUI/1.0"`（242），`log_message()` 被重写为空以关闭访问日志（**244-245**）。
- 服务器：`ThreadingHTTPServer((host, port), Handler)`（**521**），默认 `127.0.0.1:8765`（**518-519**）。每个请求一个线程 → 多线程共享全局状态。
- 全局状态（**36-45**）：`ROOT`、`WEBUI_DIR=ROOT/webui`、`RESULT_DIR=ROOT/result`、`NAME_RE`、`JOBS = {}`、`JOBS_LOCK`、`LOG_MAX_LINES=4000`、`LOG_TRIM_STEP=1000`（**未被使用**）、`JOB_KEEP=40`。
- 辅助方法：`send_json()`（248-254，`ensure_ascii=False`，带 `Content-Length`）、`send_text()`（256-262）、`send_file()`（264-275，`mimetypes.guess_type`）、`read_body()`（277-279，按 `Content-Length` 读并 `json.loads`）。
- 工具函数：`safe_name()`（**48-56**，剥掉 `config.` 前缀和 `.json` 后缀，再用 `NAME_RE = ^[A-Za-z0-9_.\-\u4e00-\u9fff]+$` 校验，非法抛 `ValueError`）、`config_path()`（59-60）、`inside_root()`（**63-65**，`os.path.realpath` 后判断等 ROOT 或以其 + 分隔符开头）、`read_json()`（68-73，异常吞掉返回 default）、`list_problems()`（76-86）、`count_solutions()`（**89-102，全文件无调用者，死代码**）。

### 6.2 路由分发

`do_GET`（282-348）与 `do_POST`（351-413）都是**线性的 `if path == "...": return ...` 链**，没有路由表、没有装饰器、没有统一异常包装。`do_GET` 未匹配返回 `send_text("not found", 404)`（348）；`do_POST` 同理（413）。POST 开头先 `read_body()`，失败返回 400 `{"error":"bad json: ..."}`（353-356）。

### 6.3 求解子进程的启动与管理

`start_job(kind, name, cmd, quiet=False)`（**159-231**）：

1. 生成 `job_id = uuid.uuid4().hex[:12]`，构造 job 字典（**161-178**），含 `status:"running"`、`log:[]`、`log_base:0`、`log_next:0`、`paused:False`、`stopped:False`、`pid:None`、`proc:None` 等。
2. 在 `JOBS_LOCK` 下登记，并按 `JOB_KEEP=40` 淘汰**已完成**的旧作业（**179-187**：`finished = [j for j in JOBS.values() if j["status"] != "running"]`，按 `started` 排序后 `pop` 掉多余的）。
3. 起一个 **daemon 线程** `worker()`（**189-230**）：
   - 复制环境变量并强制 `PYTHONIOENCODING=utf-8`、`PYTHONUNBUFFERED=1`（**190-192**）；
   - `subprocess.Popen(cmd, cwd=ROOT, stdout=PIPE, stderr=STDOUT, text=True, encoding="utf-8", errors="replace", env=env)`（**195-198**）——stderr 合并进 stdout，按行读取；
   - Popen 返回后在锁内写 `job["proc"]` / `job["pid"]`（**199-201**）；
   - `for line in proc.stdout: job_log(job, line.rstrip("\n"))`（202-203）→ `proc.wait()` → `code = proc.returncode`；
   - `finally`（**208-218**）锁内清 `proc`、写 `returncode`，并按 `stopped → "stopped"` / `code==0 → "done"` / 否则 `"error"` 决定终态，写 `ended`；
   - 若 `kind == "solve" and (job["stopped"] or code not in (0, None))`，调用 `start_repair(name)` 补图，并把"已启动后台补图（作业 X）"写回**求解作业**的日志（**221-228**）。

### 6.4 暂停 / 恢复 / 停止（`handle_control`，415-472）

- 先在锁内快照：`proc`、`running = job["status"]=="running" and proc is not None`、`pid`、`paused`（**417-421**）。
- `pause`（423-437）：非运行中 → 409；已暂停 → 200 幂等返回；否则 `proc_ctl.suspend(pid)`，成功则置 `job["paused"]=True` 并写日志"已暂停求解：进程挂起，不再占用 CPU（注意：挂起期间仍计入 --time-limit/--timeout）"，失败则把原因写进日志并返回 `{"ok":false,...}`。
- `resume`（439-452）：对称调用 `proc_ctl.resume(pid)`，成功置 `paused=False` 并写"已继续求解"。
- `stop`（454-472）：**若进程已暂停，先 `proc_ctl.resume(pid)` 再 `proc.terminate()`**（**461-467**），注释说明"避免挂起状态干扰"；置 `job["stopped"]=True`。若 `proc is None`（见第 7 节竞态）只是记日志并返回成功。
- `proc_ctl`（`proc_ctl.py`）：`suspend`（133-149）/`resume`（152-168）在 Windows 上优先用 ntdll 的 `NtSuspendProcess`/`NtResumeProcess`（**81-92**，通过 `OpenProcess(PROCESS_SUSPEND_RESUME=0x0800)`），失败退回 kernel32 逐线程 `SuspendThread`/`ResumeThread`（**112-130**），POSIX 上用 `SIGSTOP`/`SIGCONT`（146, 165）。所有函数返回 `(ok, message)`，从不抛异常。

### 6.5 后台补图线程

`start_repair(name)`（**234-238**）：

```python
cmd = [sys.executable, os.path.join(ROOT, "layout_viz.py"),
       config_path(name), "--only-missing", "--prune", "--best"]
return start_job("render", name, cmd, quiet=True)
```

即复用同一套 `start_job` 机制，把 `layout_viz.py` 当成一个 `kind="render"`、`quiet=True` 的作业跑；它的 stdout 只进自己的 job 日志，前端不会 `setActiveJob` 到它，所以**不占用求解日志面板**（`/api/render` 的处理函数注释 397 行也这么写）。触发点有三处：求解停止/异常退出后的自动补图（222-228）、`POST /api/render`（396-405，由前端 `maybeRepairVisualizations` 调用）、以及无。

### 6.6 静态文件服务与路径安全

| 路径 | 实现 | 校验 |
|---|---|---|
| `/`、`/index.html` | **286-287** → `send_file(WEBUI_DIR/index.html)` | 无 |
| `/static/<rel>` | **288-293**：`rel = unquote(path[11:])`，`f = os.path.join(WEBUI_DIR, rel)` | `inside_root(f)`（291），不通过则 403 |
| `/file?path=<rel>` | **294-299**：`f = os.path.join(ROOT, unquote(path))` | `inside_root(f)`（297），不通过则 403 |

`inside_root()`（63-65）用 `realpath` 归一化后要求 `real == ROOT or real.startswith(ROOT + os.sep)`，因此能防住 `../` 逃出项目根和绝对路径，但**不限制在 `WEBUI_DIR` 内**（见第 7 节）。所有响应都是"一次性读进内存再写"（`send_file` 269-275），没有分块、没有 Range、没有 `ETag`/`Cache-Control`。

---

## 7. 前后端契约中值得注意的问题

### 7.1 竞态与作业生命周期

1. **`/api/solve` 返回 job id 与 `Popen` 之间的窗口（真 bug）**。`start_job()` 先把 job 以 `status="running"`、`proc=None` 放进 `JOBS` 并**立即返回**（**179-181, 231**），`Popen` 在 worker 线程里才执行（195-201）。若前端在这几十毫秒内（`startSolve` 里 `trackJob` 后马上 `pollJobsOnce`，或用户手快点了停止）发来控制请求：
   - `pause` 看到 `running = status=="running" and proc is not None` 为 `False` → 409 `{"error":"当前没有正在运行的进程"}`（**424-425**）；
   - `stop` 的早退条件 `if not running and job["status"] != "running"`（**455**）为 `False`（status 就是 `"running"`），于是**只置 `stopped=True` 并记一条日志，`proc is None` 所以根本不 terminate**（**465**）。随后 worker 照常启动子进程并跑完；`finally` 里因为 `job["stopped"]` 为真把终态标成 `"stopped"`（**212-213**），前端由此认为"已停止"，而求解进程其实还在跑并继续写结果文件。
2. **已完成作业被 `JOB_KEEP` 淘汰后，前端游标永久卡死**。`start_job` 的淘汰逻辑（**182-187**）会 `JOBS.pop()` 掉旧作业；前端 `pollJobsOnce` 对 404 只做 `pollFailures++`（**460-462**），**不会**把该 job 标记 `finished` → `anyRunningJob()` 永远为真 → 700ms 轮询永不停，`#jobStatus` 一直显示"日志连接重试中 (N)"，面板也不再更新。这是缺少"作业不存在＝终结"的处理分支。
3. **多标签页/多浏览器上下文的状态不同步**。`updateJobUI` 的暂停/停止/求解按钮全部依赖**本地** `jobs` Map（`runningSolveJob`，405-411）。切换题目时 `selectProblem`（84-92）只调 `syncActiveJob()`（本地查找），**不会**查询 `/api/jobs`。因此在另一个标签页启动的作业，或页面刷新后切到另一个题目时，UI 会显示"开始求解"可用，用户在只看到 `confirm("该题目还有求解任务在运行…")`（358，同样基于本地状态，因此不会弹出）的情况下即可启动重复求解，暂停/停止按钮也是灰的。
4. **`restoreJobs()`（855-870）不过滤状态**：`/api/jobs` 返回所有状态的作业（后端只按 name/kind 过滤，**326-328**），所以刷新页面时即使没有作业在跑，也会把**最近一次已结束的**求解作业挂成 `activeJobId` 并显示其最后 40 行日志（`tail=40`），同时 `ensurePolling()` 起 700ms 轮询；第一次拉取后才发现已结束并 `loadResults()`。功能上可接受，但 `/api/jobs` 里 `tail=40` 拉回的 `lines` 前端完全没用（864-866 显式 `s.lines = []`），是白做的序列化。
5. **`resultsTimer` 在作业结束后不会自动停**。`updateResultsTimer()` 只在切标签、切题目、勾选框变化时被调用（**344, 91, 850**），`pollJobsOnce` 结束作业时不调用它。于是作业跑完、`runningSolveJob` 已为 null 后，1.5s 的结果轮询会继续跑到用户下一次切标签或取消勾选；`#tab-result` 非激活时 `loadResults` 仍在发请求并重排 DOM。
6. **轮询是串行的**：`pollJobsOnce` 用 `for ... await api(...)`（**433-437**），N 个作业就是 N 次串行往返；单个请求卡住会拖住其余作业的日志刷新，也没有超时（`fetch` 无 `AbortController`）。

### 7.2 错误处理缺口

7. **`do_GET` 没有 try/except，`safe_name` 的 `ValueError` 会直接炸掉本次请求**。`/api/config?name=`（空名，**303-304** → `config_path` → `safe_name` 54-55）、`/api/results?name=<含空格或 % 的名字>`（**333**）、`/api/files`（**336**）都会抛未捕获异常，`BaseHTTPRequestHandler` 不会返回任何响应就断连，前端只看到 `fetch` 层面的失败（`api()` 抛 `TypeError: Failed to fetch`）。同理 `/api/jobs?limit=abc` 的 `int()`（**324**）也会抛。
8. **`/api/solve` 的数值解析同样无保护**：`float(body.get("timeout", 120))`、`int(body.get("workers", 8))`、`int(body.get("seed", 7))`（**380-391**）。前端 `+$("solveTimeout").value` 在输入框被清空时是 `NaN`，`JSON.stringify` 会把它变成 `null`，服务端 `float(None)` → `TypeError` → 未捕获异常、无响应。`mode` 也没有白名单，任何非 `"exact"` 的字符串都静默走 heuristic 分支（**382**）。
9. **`api()`（18-22）的错误消息是原始响应体**：`throw new Error(await r.text())`，于是 `alert("操作失败: " + e.message)` 会弹出 `{"error":"config not found"}` 这类 JSON 原文；成功响应如果不是 JSON 也会在 `r.json()` 处炸。
10. **`loadProblems()`（61-73）与 `loadConfig()`（75-82）都没有 try/catch**，`init()`（872-879）里 `await loadProblems()` 裸等待 → 服务端不可用时是未捕获的 promise rejection，整页停在"未初始化"状态（下拉框空、编辑器不渲染），只有控制台报错。
11. **`/api/render` 忽略前端发送的 `limit: 200`**（`app.js:562` vs `webui_server.py:396-405`），`start_repair` 也没有数量参数——契约里存在一个前端以为生效、实际无效的字段。

### 7.3 安全边界

12. **`inside_root` 的作用域过宽**：`/static/` 只校验"在 `ROOT` 以内"（**291**），而不是"在 `WEBUI_DIR` 以内"，所以 `GET /static/../config.<name>.json`、`/static/../webui_server.py` 等**项目根下任意文件**都能读；`/file?path=` 同理（**297**），且它不做 `NAME_RE` 校验、不限扩展名，等于开放了"读取 `ROOT` 下任意文件"。`unquote` 在 `join` 之前调用（289, 295），`..` 未被过滤，靠 `realpath` 兜底。
13. **无任何认证、无 CSRF 防护、无 Origin/Referer 校验，`--host` 可改成 `0.0.0.0`**（**516-520**）。一旦监听非回环地址，`POST /api/config`（写文件，路径由 `safe_name` 约束在 `ROOT` 内）、`POST /api/solve`（以 `sys.executable` 执行 `layout_exact.py`/`layout_solver.py`，并把用户提交的 config 交给它）、`/file?path=`（读任意项目文件）组合起来是完整的读写面。即使在默认回环下，浏览器里的任意页面也能向 `http://127.0.0.1:8765/api/config` 发简单请求（POST 不触发预检也可发送，`read_body` 不检查 `Content-Type`，**277-279**），只是跨域下读不到响应。
14. **前端 XSS 面**：模块 id、端口 id、net 端点全部经 `innerHTML` 拼接，未转义——`app.js:144-147`（模块列表）、`163-185`（编辑器表单）、`278-281`（连接表）、`297`、`306`。文件里本来有 `escapeHtml()` 但**被注释掉了**（**790-794**）。配合"导入任意本地 JSON"（`fileImport`，809-814）和"服务端把 config 原样回显"（`/api/config`），一个 id 为 `<img src=x onerror=...>` 的 config 就能在页面里执行脚本；再叠加 12/13 的文件写接口，构成"导入配置 → 页面内执行"的注入链。
15. **`send_file` 无路径规范化后的二次校验回退**：`os.path.isfile` 在 TOCTOU 下可能被符号链接绕过（Windows 上较少见），且没有 `X-Content-Type-Options` 等响应头。

### 7.4 性能与可维护性

16. **`collect_results()` 每次请求都全量重读并解析整个 JSONL**（**479-487**），并把每条解的 `state` 与 `paths` 一并序列化（**494-502**）。结果页在有求解作业时每 **1.5s** 调一次（`app.js:621`），N 条解就是 O(N) 的解析 + 大 JSON；没有任何分页、缓存或 `mtime` 短路。`open(jl, encoding="utf-8")` 还没用 `with`（**481**），文件对象要等 GC，长跑会积累句柄。
17. **游标契约的一个隐含依赖**：`collect_results` 用 JSONL 的**行序号** `i` 去拼 `{name}.sol{i}.svg`（**489-491**），即假设 `layout_viz.py` 的落盘编号与 JSONL 行号严格一一对应；一旦有一行 JSON 解析失败被 `except: pass` 跳过（**486-487**），后续所有解的 `index` 与文件名就会错位（`ready` 也可能全假），而前端只会显示"可视化生成中…"。
18. **日志裁剪常量不一致，且有一个未使用常量**：服务端 `LOG_MAX_LINES=4000` 裁剪，但 `LOG_TRIM_STEP=1000`（**44**）**全文件无引用**（`job_log` 直接 `del log[:extra]`，先注释"超过上限时一次性丢弃的行数"已与实现不符）；前端渲染上限 `LOG_VIEW_MAX=3000`（`app.js:14`）。三处数字各自为政。
19. **其他死代码/死接口**：`count_solutions()`（**89-102**）无调用者；`/api/files`（**335-347**）前端不调用；`app.js:790-794` 的 `escapeHtml` 被注释；`app.js:239` 的 `.on` 判断与 237 行重复。
20. **前端有多个"改一半"的入口，容易产生不一致的 config**：
    - `changeKind(m, newKind, done)` 签名收 `done` 但**函数体从不使用**（**255-270**），而调用处传的是 `finish`（**208**）→ 在编辑器里先改 ID/宽高、再直接改"类型"，这些未 commit 的输入会被丢弃（因为 `renderAll()` 重建了表单）。
    - `finish()`（187-205）改小 `w/h` 时**不会清理超界的端口 `cells`**，残留坐标仍会写进 config，只是不渲染。
    - `#medDelete`（209-212）删模块**不清理 `cfg.nets`**，会保存出悬空引用。
    - `btnAddNet`（830-840）在只有一个模块时生成自环 `a → a`（`mods[Math.min(1, mods.length-1)]`），没有自环/重复校验。
    - `fileImport`（809-814）导入后不重置 `currentName`/`selected`，也不做结构校验，紧接着"保存"就会用当前题目名覆盖服务端文件。
    - `saveConfig(asNew)`（115-126）：`asNew` 时 `currentName = name`，如果用户没改名字，"另存为"和"保存"行为完全一致（静默覆盖）。
    - `renderPreview`（319-324）对越界/重叠的固定模块静默丢弃或覆盖，不提示，问题要到求解时才暴露。
21. **`Ctrl+C` 不清理子进程**：`main()`（**516-527**）只捕获 `KeyboardInterrupt` 后退出，`start_job` 起的求解进程不会被 `terminate()`，会继续孤儿运行并写 `result/`。
22. **`handle_control` 的 stop 对"`proc.wait()` 已返回但 `finally` 尚未执行"的瞬间同样无法区分**（`running` 的定义依赖 `proc is not None`，**419**），此时 stop 会走到"置 stopped 但不 terminate"的路径并返回成功。

---

### 附：一句话总览

架构是"标准库单文件 HTTP 服务 + 经典脚本无框架前端"：后端用 `ThreadingHTTPServer` + 一个 `JOBS` 字典管理子进程、以 `(log_base, log_next)` 绝对游标提供增量日志、用 `proc_ctl` 做跨平台挂起/恢复、用后台 `render` 作业补齐可视化；前端用一个可变 `cfg` 真源 + `Map<jobId, stream>` 的本地作业镜像 + 700ms 作业轮询 / 1.5s 结果轮询驱动三个标签页。设计上最值得称赞的是日志游标的绝对编号与"客户端落后就整段重发"的契约；最需要修的是 `Popen` 与 job 注册之间的停止竞态（7.1-1）、作业被淘汰后前端轮询永不停（7.1-2）、`do_GET`/`/api/solve` 缺失的异常兜底（7.2-7/8），以及 `/static/` 与 `/file` 过宽的路径白名单（7.3-12）。
