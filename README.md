# 通用“模块布点 + 传送带布线”求解器

## 目录结构

```text
.
├── src/         求解器与可视化源码
│   ├── layout_exact.py       精确最优求解器（CP-SAT）
│   ├── layout_solver.py      启发式求解器（模拟退火 + A*，无需第三方依赖）
│   ├── layout_viz.py         SVG / 字符画可视化 + 增量输出
│   ├── proc_ctl.py           跨平台 暂停/恢复 子进程
│   ├── device_presets.py     设备尺寸表(xlsx) -> 模块预设，只用标准库
│   └── webui_server.py       图形化 WebUI 服务端（只用标准库）
├── ui/          WebUI 前端（原生 HTML/CSS/JS，无构建步骤）
│   ├── index.html / style.css
│   ├── model.js              纯逻辑：端口分类、同名编号、连接校验、连线几何
│   └── app.js                DOM 渲染与交互
├── data/        设备尺寸.xlsx（模块预设的来源）+ device_presets.json（生成缓存）
├── configs/     题目配置 config.<题目名>.json
├── result/      求解产物（每个新解一份 solN.svg / solN.txt + solutions.jsonl）
├── tests/       冒烟测试、预设测试、前端逻辑测试、端到端探针
├── docs/        布局参考图、原始布局文本、分析报告、归档代码
├── requirements.txt
└── README.md
```

`result/` 与 `configs/` 的实际位置由 `src/layout_viz.py:default_output_prefix()`
统一推导（= 本文件上一级），因此**从任何工作目录运行、或由 WebUI 以子进程
调用，结果都会落到项目的 `result/` 下**。

## 安装

只有**精确最优求解器**需要第三方依赖（OR-Tools）；启发式求解器、可视化、
WebUI 全部只用 Python 标准库，不装任何东西也能跑。

```bash
python -m pip install -r requirements.txt
```

源码直接运行，无需 `pip install -e .` 或任何构建步骤——用 `python src/xxx.py`
即可（各脚本通过同目录导入互相引用）。

国内网络建议加镜像：

```bash
python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

> OR-Tools 自带 CP-SAT 本地二进制，体积较大（约 100–300 MB）。
> 只想要启发式版的话可以跳过这步。

## 快速开始

```bash
# 1) 启发式求解（不需要 OR-Tools）
python src/layout_solver.py configs/config.toy.json --timeout 10

# 2) 精确最优求解（需要 OR-Tools，见「安装」）
python src/layout_exact.py configs/config.toy.json --time-limit 60

# 3) 图形界面
python src/webui_server.py --port 8765     # 打开 http://127.0.0.1:8765/

# 4) 自检（不需要 OR-Tools，约 10 秒）
python tests/test_smoke.py

# 5) 模块预设（读 data/设备尺寸.xlsx，并用预设拼出的配置真跑一遍启发式求解）
python tests/test_presets.py

# 6) 前端逻辑（需要 node；没装就跳过）
python tests/test_webui_frontend.py

# 7) 端到端前端探针（需要能用的无头 Chrome/Edge，否则自动跳过）
python tests/webui_presets_probe.py
```

Windows 下如果中文乱码，可先设置 `$env:PYTHONIOENCODING="utf-8"`。

> 提示：`configs/config.example.json` 状态空间很大，跑几分钟也未必到
> `OPTIMAL`，不适合快速试跑；小规模验证请用 `config.toy.json` /
> `config.cross.json`。

各类求解器的详细用法见下文「启发式求解」「精确最优求解（CP-SAT）」。

## 模块一览

- `src/layout_solver.py`：启发式求解器主程序
- `src/layout_exact.py`：精确最优求解器（CP-SAT），支持可选 warm start
- `src/layout_viz.py`：SVG / 字符画可视化，负责“边求解边落盘”
- `src/device_presets.py`：设备尺寸表 -> 模块预设（xlsx 用标准库解析）
- `src/webui_server.py` + `ui/`：图形化 WebUI（`ui/model.js` 是不碰 DOM 的纯逻辑）
- `data/设备尺寸.xlsx`：模块预设的来源表格（`device_presets.json` 是生成缓存）
- `configs/config.example.json`：17×9 / Y1Y2 / J1P1… 拓扑示例
- `configs/config.toy.json`：可运行的小示例
- `configs/config.cross.json`：强制垂直交叉的最小示例
- `tests/test_presets.py` / `tests/test_webui_frontend.py`：预设与前端逻辑测试
- `docs/archive_layout_exact_optimized.py`：早期实验性增强版求解器，
  **未被任何入口调用**，仅作归档，不再维护

## WebUI（图形化编辑 + 一键求解）

启动本地服务：

```bash
python src/webui_server.py --port 8765
```

浏览器打开：

```text
http://127.0.0.1:8765/
```

WebUI 提供：

- 题目列表：自动读取 `configs/` 下所有 `config.<name>.json`；
- 模块编辑：添加、删除固定/可动模块，设置尺寸、位置、是否可旋转；
- **模块预设**：从 `data/设备尺寸.xlsx` 读设备，一键添加，尺寸与各面端口按表格自动落位，
  同名模块从 1 开始编号（精炼炉1、精炼炉2…）；
- 端口编辑：选择端口后，在模块格子上点击即可设置 3 进/3 出位置与方向；
- 连接表：下拉框选择「哪个模块的哪个端口 -> 哪个模块的哪个端口」；
- **连接预览**：连接表一改就立刻画出「出口 → 入口」的拓扑图，并检查接错没有；
- 固定模块预览：网格图上直接看到 Y 等固定模块的位置；
- 一键求解：精确 / 启发式两种模式，实时日志；
- 边求解边输出：每找到一个新的可行解，就立刻写出 `solN.txt` 与 `solN.svg`，
  写入在后台线程完成，不阻塞、也不改变求解过程；
- 暂停 / 继续 / 停止：求解进行中可随时挂起（不占 CPU）、恢复或提前结束，
  已经找到的解都会保留；
- 结果页：按可行解逐个展示 SVG 与字符画；「刷新结果」在求解过程中也可以点，
  能看到解一个个冒出来，缺失的可视化会在后台静默补齐。

新题目再也不需要手写 JSON，直接在 WebUI 里点选保存即可。

### 实时输出与日志

- 求解进程每发现一个（更优的）可行解，就立刻落盘
  `result/<题目>/<题目>.solN.txt`、`.solN.svg`，并往 `.solutions.jsonl`
  追加一行；写盘在后台线程完成，不阻塞求解，也不影响搜索结果。
- 日志按“行号”增量拉取：刷新结果、后台补图、切换题目都不会清空或覆盖它；
  浏览器刷新后会自动接回还在运行的求解作业。
- **一次求解 = 一份结果**。新的一轮只有在真正找到第一个可行解时才覆盖该题目的
  历史结果；如果这一轮什么都没找到（或一开始就被停掉），上一次的结果原样保留。

### 暂停 / 继续 / 停止

「求解」页有「暂停 / 继续 / 停止」三个按钮：

- 暂停：把求解进程整体挂起（Windows 用 `NtSuspendProcess`，类 Unix 用 `SIGSTOP`），
  CPU 立刻降到 0，点「继续」原样接着跑；
- 因为 CP-SAT 与启发式都按**墙钟时间**计时，挂起期间仍然消耗
  `--time-limit` / `--timeout` 的预算，适合临时让出机器；
- 停止：结束当前求解，已经找到的可行解会保留；JSONL 里若还有解缺少
  SVG/字符画，服务器会自动起一个**后台静默补图**任务补齐。精确求解还会把
  日志里**已经证明出来的下界**补写进 `bounds.json`（见「下界持久化」），
  所以“跑到一半停掉”的那部分证明成果不会白费，下次 `--hint` 可以直接接着用。

## 模块预设：直接读《设备尺寸.xlsx》

`data/设备尺寸.xlsx` 就是模块的“字典”。表格长这样：

| 设备名称 | 宽度 | 高度 | 北面 | 南面 | 西面 | 东面 |
| --- | --- | --- | --- | --- | --- | --- |
| 精炼炉 | 3 | 3 | sisisi | sososo | nnn | nnn |
| 储液罐 | 3 | 3 | nnn | nnn | nfin | nfon |

每个面是一串标记，**一个标记占一格**：

| 标记 | 含义 | 标记 | 含义 |
| --- | --- | --- | --- |
| `si` | 固体入口 | `so` | 固体出口 |
| `fi` | 液体入口 | `fo` | 液体出口 |
| `gi` | 气体入口 | `go` | 气体出口 |
| `n` | 普通面（没有端口） | | |

读法与俯视图上“从左到右、从上到下”一致：

- **北面** = 第 0 行，标记从左到右 = 列 `0..w-1`，端口朝 `N`；
- **南面** = 第 `h-1` 行，标记从左到右 = 列 `0..w-1`，端口朝 `S`；
- **西面** = 第 0 列，标记从上到下 = 行 `0..h-1`，端口朝 `W`；
- **东面** = 第 `w-1` 列，标记从上到下 = 行 `0..h-1`，端口朝 `E`。

> 如果哪天发现表格其实是“从下到上”记的，把 `src/device_presets.py` 里的
> `VERTICAL_ORDER` 改成 `"bottom-up"` 即可 —— 只影响西/东两个面。

同一个面上标记相同的格子会合成**一个端口**，求解器会在这些格子里挑一个不冲突的用：

```text
精炼炉  北 sisisi -> 端口 SI  朝 N  cells [[0,0],[0,1],[0,2]]
        南 sososo -> 端口 SO  朝 S  cells [[2,0],[2,1],[2,2]]
```

若同一个标记同时出现在**两个面**上（例如 `固气转化机（固体产出）` 的 `gi` 北面、西面都有），
两个面方向不同、不能合成一个端口，于是拆成 `GI-N` / `GI-W`。

### 在 WebUI 里用

1. 「题目编辑」→「模块」→「预设设备」里筛选/选择一个设备（也可以按端口号筛，比如输入 `FI`）；
2. 下面会画出它的 `w×h`、各面端口，以及**下一个编号**；
3. 点「按预设添加」：模块 ID = `设备名 + 序号`，**同名从 1 开始**——
   连点三次「精炼炉」得到 `精炼炉1`、`精炼炉2`、`精炼炉3`；换「储液罐」又从 `储液罐1` 开始；
   中间删掉一个再加，会补上缺的那个号；
4. 模块 ID 必须唯一（求解器拿 id 当字典键，重名会互相覆盖），
   所以手动添加重名时会自动改号并提示；
5. 端口位置如果和游戏里对不上，选中模块后照样能一个格子一个格子点着改。

用预设添加的模块会在配置里额外记一个 `"preset": "精炼炉"` 字段，
求解器不认这个字段（只读 `w/h/rotatable/ports`），删掉也不影响。

### 命令行 / 接口

```bash
python src/device_presets.py --list    # 打印解析结果（设备、端口、格位）
python src/device_presets.py --json    # 打印 JSON
python src/device_presets.py --dump    # 重新生成 data/device_presets.json 缓存
python src/device_presets.py --xlsx 别的表.xlsx
```

- 服务端 `GET /api/presets` 每次都重新读表，**改完 xlsx 刷新页面即生效**；
- `data/device_presets.json` 只是**缓存**：表格被删/改名/读坏时前端不至于没预设可用，
  这时界面上会显示告警，说明当前用的是缓存；
- 换成自己的表：表头认「设备名称 / 宽度 / 高度 / 北面 / 南面 / 西面 / 东面」
  （`宽/高/北/南/西/东` 也认）；名字为空、宽高不是正数、某个面的标记数与宽高对不上的行
  会被**跳过**，并在 `--list` 与界面上报出来，而不是悄悄错位。

## 连接预览（OUT → IN）

「题目编辑」页最下方的**连接预览**把连接表画成一张拓扑图，用来在求解之前确认“接对没有”：

- 每个模块一个框，大小与 `w×h` 成比例；框边上的彩色刻度就是表格里的端口格，
  颜色按 固体/液体/气体 区分（入口深、出口浅），鼠标悬停可以看到端口含义；
- 每条连接是一条带**箭头**和**编号**的曲线，从**出口**画到**入口**；曲线两端沿端口朝向往外
  伸一小段再弯过去，所以“从哪一面进、哪一面出”一眼就能看出来；
- 悬停连接表某一行（或预览下方的提示）会高亮对应的那条曲线；
- 连线颜色与连接表左侧的编号圆点一致；
- 下方列出检查结果：
  - `✕` 模块/端口不存在、端口一个格子都没有；
  - `!` 出口端用了入口端口（或反过来）、首尾是同一个模块、完全重复的连接、端口格子越界；
  - `·` 没参与任何连接的模块、没用到的出口；
  - 全都没问题时显示「连接检查通过」。

连接表里的两个下拉也按「出口 / 入口 / 其它端口」分了组：出口那一侧把出口排最前面，
入口那一侧反过来，不用在一堆端口里找。老配置里的 `IN`/`OUT` 一样会被认出来。

## 启发式求解

```bash
python src/layout_solver.py configs/config.toy.json --timeout 10
python src/layout_solver.py configs/config.example.json --timeout 60
python src/layout_solver.py configs/config.cross.json --timeout 5
```

Windows 下如果中文乱码，可先设置：

```powershell
$env:PYTHONIOENCODING="utf-8"
python src/layout_solver.py configs/config.toy.json
```

## 精确最优求解（CP-SAT）

启发式版用模拟退火 + A*，只能保证“尽量好”。

如果你要**证明最优**，使用 `layout_exact.py`，它把同一份 JSON 编译成
OR-Tools CP-SAT 整数规划，模块位置、旋转、传送带路径一次性全局求解：

```bash
python src/layout_exact.py configs/config.toy.json --time-limit 60
```

可选项：

- `--time-limit 秒`：求解时间上限；
- `--workers N`：并行线程数；
- `--hint`：启用 warm start：上次的最好解作为**完整** Hint，并同时施加历史
  上下界（`obj <= 上次最好解`、`obj >= 上次已证明的下界`）（**默认关闭**）；
- `--no-relax-phase`：关掉两阶段求解的阶段1（不再先解松弛模型），直接在完整
  模型上搜——排查用；
- `--verbose`：打印 CP-SAT 搜索过程。

搜索结果**不设数量上限**：找到多少个可行解就立刻写多少份
（`solN.txt` / `solN.svg` / `.solutions.jsonl`），编号 `0..N-1` 连续。

### 建模阶段的进度日志

大题目（`config.gudi.json`：27x30 画布 / 33 个可动模块 / 47 条连接）光是**建模**
就要几十秒，而这段时间以前一行日志都没有，看起来和卡死没有区别。现在建模被拆成
有名字的若干步，每行都带 `(累计, +本步)` 两个耗时：

```text
[exact] 读取配置: 27x30 画布, 固定模块 10 个, 可动模块 33 个, 连接 47 条
[exact] 候选位置: 精炼炉1=2688, …, 协议储存箱1=2688 (1.8s, +1.8s)
[exact] place 变量 83890 个（每个可动模块恰好选中一个候选位姿） (2.6s, +0.7s)
[exact] 可用格点 780 个（固定模块占掉的 30 格永久禁带） (2.6s, +0.0s)
[exact] 占用聚合变量 780 个（同格模块互斥已并入其中） (5.3s, +2.8s)
[exact] 端点格变量 65530 个（原逐 (候选, 外侧格) 写法要 836490 个） (9.4s, +4.1s)
[exact] 弧变量 141376 个, use 变量 36660 个 (11.2s, +1.8s)
[exact] 直通变量 136112 个 (17.1s, +5.9s)
[exact] 流量平衡约束 36660 组 (17.1s, +0.0s)
[exact] 同格共带/交叉约束（按格聚合，替代逐对 share 变量） (22.4s, +5.3s)
[exact] 模块禁带约束 36660 条 (23.8s, +1.4s)
[exact] 目标函数：最小化传送带占用格总数 (25.9s, +2.1s)
[exact] 模型规模 变量 465129 个 / 约束 1083452 条 (26.1s, +0.2s)
[exact] 模型构建完成: 780 个可用格点, 变量 465129 个 / 约束 1083452 条, 建模总耗时 26.1s，进程内存 1.01GB；开始 CP-SAT 搜索 (time_limit=120.0s, workers=8)
[exact] 提示：CP-SAT 会先做一轮 presolve，模型大时这一步可能几十秒没有任何输出，之后才会开始报可行解
```

`+本步` 让你一眼看出慢在哪一步、有没有在推进；`模型规模` 与`进程内存`是判断
“还能不能再加时间 / 要不要换机器”的依据。最后两行也说明白了：建模结束之后
还有一段**没有解输出的 presolve 期**，那段时间安静是正常的。

### 建模规模：为什么原来会在大题目上卡死

同一份题目（27x30 / 33 可动模块 / 47 net）改成“按格聚合”前的规模：

| 部分 | 旧写法 | 现写法 |
| --- | ---: | ---: |
| place（模块候选位姿） | 83,890 | 83,890 |
| 端点变量（哪一格是带的起点/终点） | 836,490 | 65,530 |
| use（每 net x 每格是否被占） | 36,660 | 36,660 |
| arc（每 net x 每格 x 每方向） | 141,376 | 141,376 |
| 直通变量 hE/hW/vN/vS | 136,112 | 136,112 |
| share（每格每一对 net 共格） | 843,180 | 0（换成每格一个整数 `T`） |
| 占用聚合 `occ` | — | 780 |
| **变量合计** | **2,077,708** | **465,129** |
| 禁带约束（模块候选格 x net） | 55,466,016 | 36,660 |
| 共格约束（每对 x 8 条） | 6,745,440 | ~40,000 |
| **约束合计** | **≈6,290 万** | **1,083,452** |

三处等价改写（都只改“怎么写”，不改“约束的是什么”）：

1. **禁带 + 非重叠**：`occ[cell] = 覆盖该格的所有候选 place 之和`（限成 0/1），
   于是“同格最多一个模块”和“模块占的格不能走带”（每格 `occ + use <= 1`）都由
   这一条等式推出来，不必再写 `模块候选 x 格 x net` 那一层。
2. **共格/交叉**：记 `T` = 该格被几条带占用（`<=2`），`H`/`V` = 该格横向/纵向直通
   的条数，`E` = 该格的端点数。`T=2` 时用 `use_i + T <= 2 + 直通_i`、`H+V >= 2T-2`
   配合 `H<=1, V<=1`、`E + 2T <= 4` 就能表达“两条带共格必须一横一纵直通、交叉格
   不能是端点”，和原来逐对 `(a,b)` 写 8 条 `OnlyEnforceIf` 在整数解上完全等价。
3. **端点**：不再给每个 `(候选, 外侧格)` 建变量，而是按**格**建
   `end[cell] <= Σ(能落到该格的候选 place)` 且 `Σ end = 1`；模块候选恰好选一个，
   于是“选中的候选必须能覆盖那个端点格”与原来的精确命中等价，顺带剪掉了
   “端口接不出去”的候选。

结果：`config.gudi.json` 从“建模阶段就吃光内存/时间”变成 **约 26 秒建完、
约 1GB 内存**（8 线程、`--verbose`），日志里每一步都看得见。

### 合法冗余下界：`obj >= 连接条数`

下界不是只能靠求解器慢慢爬。有两条是原始语义的**直接推论**，写进模型既不会切掉
任何可行解，又能让求解器一开始就拿到一个像样的界：

1. 每条 net 恰好一个起点格，而 `use >= 起点变量`，所以它至少占 1 格
   ⇒ `obj >= 连接条数`；
2. 路径四邻接连通，从起点格走到终点格至少要「曼哈顿距离」步
   ⇒ 单条 net 的占用格数 `>= 曼哈顿距离 + 1`（起点/终点各自只在候选可能落到的
   那组格里选，取这组格之间的最小距离即可，模块挡路只会更长）。

实测（`config.example.json`，9 条 net）：

```text
[exact] 合法下界 obj >= 9（每条 net 至少 1 格 = 9，端点曼哈顿距离再加 0）
#Bound   8.22s best:inf   next:[9,714]    initial_domain     ← 一开始就是 9
```

不加这条时，同一个模型要靠 `bool_core` 一步步爬，**224s 才到 9**。
`config.cross.json` 更直观：两条 net 的端点相距很远，曼哈顿部分直接贡献 10，
于是 `obj >= 12` —— 恰好就是它的最优值（日志里 `合法下界 obj >= 12`）。

### 两阶段求解：先解松弛模型拿骨架，再补细则求最优

精确模型里最难满足的不是“不重叠”“不走模块”，而是**共格细则**：两条带同格
必须是「一横一纵的十字直通」，交叉格不能是端点、也不能转弯。硬模型上求解器
连第一个可行解都很难找到（实测 `config.example.json`：300s 零解）。

所以默认走两阶段（同一份模型，先不加那层细则，阶段2 再补回来）：

```text
[exact] 同格共带/交叉约束：两阶段求解——阶段1 先不加（松弛），阶段2 再补
[exact] 阶段1（松弛：先不加共格细则）开始，预算 105.0s
[exact] 阶段1 松弛解 cost=53，违反共格细则 16 处（挑的是违规最少的骨架，一共报过 29 个解）——不能当结果，只作 Hint
[exact] 阶段1 结束: status=FEASIBLE, 用时 105.3s, 松弛下界 lb_relax=9（对完整模型同样成立）
[exact] 阶段1 骨架已作为阶段2 的 Hint（12650 个变量，违规处交给 CP-SAT repair）
[exact] 共格十字直通细则 +122 格已补进模型（阶段2 用完整模型）
[exact] 阶段2 开始（完整模型）: … 阶段2 预算 194.7s
[exact] 发现可行解 #1: 传送带格数 53, 用时 39.21s -> 已输出 sol0.svg / sol0.txt
…
status: FEASIBLE, 用时 300.53s, 最优传送带格数: 28, 已证明下界: 9
```

几条关键约定（都写在代码注释里了）：

- 阶段1 的解**可能不合法**（它只满足松弛后的约束），所以既不能输出成 `solN`，
  也**不能当目标上界**（它的 cost 可能低于真正的最优值）。它只有两个用途：
  当阶段2 的 Hint 让 CP-SAT 去 repair，以及提供**合法下界** `lb_relax`。
- 挑哪个松弛解给阶段2？按**违规格数优先、再按 cost**：最便宜的解往往在多格
  叠了带（违规多），拿去 repair 更难；违规最少的骨架才是好 Hint。
- 阶段1 找到**0 违规**的解就是**合法解**，此时才允许当上界（`obj<=C`），
  阶段2 若没找到更好的就直接采用它。
- `lb_relax` 单独存进 `bounds.json` 的 `lb_relax` 字段（来源和 `lb` 不同，方便
  排查），下一轮 `--hint` 会取两者更紧的那个用。超时且阶段2 没找到解时，下界
  直接用 `lb_relax` 兜底。
- 已经带历史解 Hint 的一轮会**跳过阶段1**（`阶段1 跳过：本轮已经有历史解作 Hint`）；
  `--no-relax-phase` 可以整体关掉两阶段，用于排查。

### Warm start（可选）：接着上一轮跑

WebUI「求解」页有 **Warm start** 勾选框，默认不勾；命令行对应 `--hint`。

同一个题目往往会反复求解。勾上之后，`layout_exact.py` 会读该题目上次留下的
`result/<题目>/<题目>.solutions.jsonl` 与 `result/<题目>/<题目>.bounds.json`，
一次做三件事：

| 做什么 | 依据 | 做错了会怎样 |
| --- | --- | --- |
| **完整 Hint**：把上次的解翻译成**每个变量**的取值（模块位姿、每格占用、端点格、`use`、`arc`、直通方向） | 一个已知可行解 | 只是搜索起点，没被采纳也无妨 |
| **压上界**：`obj <= min(solutions.jsonl 最好解, bounds.json 的 best)` | 一份现成可行解 | `INFEASIBLE`/`UNKNOWN`，会报错、会“吵” |
| **抬下界**：`obj >= 上次已证明的下界` | 上一轮**证明**出来的结论 | 会**静默**剪掉最优解，最危险 |

三样里只有**下界**需要额外保护：它是对“上一轮那份模型”成立的定理，配置或建模
代码一变就不再适用。所以下界单独存 `result/<题目>/<题目>.bounds.json`，里面带
**config 内容指纹 + 建模代码指纹**，任何一项对不上就自动忽略（日志会写
`忽略历史下界：配置已改动（指纹不一致）`）。删掉这个文件即可彻底从零开始。

Hint 为什么必须是**完整**的：CP-SAT 只在“每个非固定变量都有提示”时才会在
presolve 阶段直接把它当成 incumbent，搜索都不用开始：

```text
[exact] 模型构建完成: 63 个可用格点, 变量 2645 个 / 约束 6663 条, 建模总耗时 0.3s，进程内存 0.10GB；开始 CP-SAT 搜索 (time_limit=15.0s, workers=4)，warm start：obj<=7 + obj>=7 + 上下界重合=上一轮已证该值最优；完整 Hint cost=7, 模块 3 个, 路径 4 条/7 格, 共 hint 2644 个变量
The solution hint is complete and is feasible. Its objective value is 7.
#Bound   2.72s best:inf   next:[7,7]      initial_domain
#1       2.72s best:7     next:[]         complete_hint
```

日志里那个 `共 hint N 个变量` 就是覆盖率，`2644/2644` 即完整。旧实现只提示
模块位姿、端点和路径格，CP-SAT 只能另起一个 `hint search` 子求解器去补全：

```text
The solution hint is incomplete: 18 out of 4424 non fixed variables hinted.
```

补全要重新决定“每条带怎么连通”，这部分信息就白丢了。现在 `arc` / `hcomp` /
`vcomp` / 每格占用 `occ` / `use=0` / `pvar=0` / 未命中的端点格全部都会提示。
万一历史解已经不适配当前模型，完整 Hint 也不会让整轮报废：CP-SAT 会打印
`The solution hint is complete, but it is infeasible! we will try to repair it.`
并尝试修补；实在修不出来就退回普通搜索。

实测（`config.toy.json`，3 个可旋转模块，4 线程，15s 预算）：

| 运行 | 施加的约束 | 拿到最优解 cost=7 | 证明最优 |
| --- | --- | --- | --- |
| 冷启动（无历史） | 无 | 4.72s（此前还报了 13、11 两个劣解） | 10.97s |
| warm start | 完整 Hint + `obj<=7` + `obj>=7` | **2.72s（presolve 阶段，只 1 个解）** | **2.72s（立刻得证）** |
| warm start，但删掉 bounds.json | 完整 Hint + `obj<=7` | 2.74s | 9.28s 时还在爬下界 |

第二、三行说明了上下界各自的作用：上界+Hint 负责“很快拿到好解”，下界负责
“不用重新证一遍已经证过的部分”。

### 先搞清「上下界」：`next:[lb, ub]` 是什么

跑 `--verbose` 会看到 CP-SAT 的搜索进度。`#Bound` 行打印的就是
**当前目标函数的上下界**（源码里的表头是 `Objective bounds`），格式为
`next:[lb,ub]`，含义是"**接下来要在 `[lb,ub]` 这个区间里找解**"：

```text
#Bound   2.70s best:inf   next:[0,252]    initial_domain   ← 一开始 lb=0
#3       4.28s best:8     next:[0,7]                      ← 找到 cost=8
#Bound   4.44s best:8     next:[1,7]      bool_core (num_cores=1 …)  ← lb 升到 1
#4       4.52s best:7     next:[1,6]                      ← 找到 cost=7
#Bound   4.80s best:7     next:[4,6]      bool_core (num_cores=4 …)  ← lb 升到 4
#Done    9.82s
```

- **`lb`（`next` 左值）**：**已证明**解的格数不可能低于它，随搜索逐步上升
  （上面 0 → 1 → 4）。它由约束传播 / LP 松弛 / core 推理**算出来**，任何 API
  都设不了（详见下一节）。
- **`ub`（`next` 右值）**：本次要找的解的格数**上限**——因为已找到的解
  再重复找到没有意义，所以只要严格更优的，即 `< best`。
- **`best`**：当前**已找到**的最好解的格数，是另一个独立的量。
  `best:19` 配 `next:[14,18]` 完全可能，含义是：
  **已证明 0–13 不可能，当前最好解是 19，接下来在 14–18 里找**。
- 当 `lb` 追上 `best`（如 `best:7` 且 `lb` 也到 7），**最优性得证**，直接 `#Done`。
- 右边那列是**这个界是谁给的**，随版本变化：老版本是 `default_lp` / `max_lp`，
  新版本（9.13+）常见 `bool_core`。`bool_core (num_cores=…)` 里的
  **`num_cores` 不是 CPU 核数**，而是 core 推理攒下的 core 个数——布尔目标下
  它往往和 `lb` 同步增长（日志里 `num_cores=10` 配 `next:[10,18]`），
  `( … )` 里是它的内部统计，`fixed=` / `clauses=` 是底层 SAT 的计数器。

下界**不能凭空设定**：`model.Add(obj >= L)` 这一行谁都能写，但 L 是否成立取决于
**有没有证明**。上界之所以能随手写，是因为 `obj <= C` 背后有一份现成的可行解
作见证；下界没有见证，只有上一轮跑出来的结论。所以 `--hint` 里的下界只认
`bounds.json` 里那份**带指纹校验的、上一轮真正证明出来的**界；跨配置抄一个数字
（或把日志里 `next:[L, …]` 的 L 搬过来）会让最优解被静默剪掉，而 CP-SAT 依然
返回 `OPTIMAL`——这是本项目里唯一“错了不会吵”的改动，因此不做成可手填的参数。

| 我们加什么 | 依据 | 效果 |
| --- | --- | --- |
| `obj <= C`（`--hint`） | 一份现成可行解 | 不比上次差 |
| `obj >= L`（`--hint`，L 来自 bounds.json） | 上一轮已证明的下界 | `0..L-1` 不必重新证 |
| 手填一个下界 | **无** | 写错 = 静默的错误答案，不要这么干 |

### 下界持久化：`<题目>.bounds.json`

一轮求解正常收工（`OPTIMAL` / `FEASIBLE` / `UNKNOWN`）之后，本轮**证明出来**的
目标下界会写进 `result/<题目>/<题目>.bounds.json`：

```json
{
  "lb": 14,
  "best": 19,
  "status": "UNKNOWN",
  "solve_sec": 835.58,
  "config_sha256": "d75771d7…",
  "code_sha256": "dff286b4…",
  "ortools": "9.15.6755",
  "applied": ["obj<=19"],
  "when": "2026-09-10 22:10:58"
}
```

下一轮带 `--hint` 时会加 `obj >= 14`，于是日志里第一个 `#Bound` 就直接是
`next:[14, …]`，省掉“重新把界从初始值抬到 14”这段（本例里是 835s）。注意
**省不掉 14→19 那段**：core 推理学到的结构不会随数字一起搬过来。

几条约定：

- **只增不减**：新界比旧界小时保留旧界（旧界同样有效，不必退回去）；
- `INFEASIBLE` 的那一轮**不写**文件（把目标挤没了的模型，其“下界”没有意义）；
- **手动停止也写**：WebUI 的「停止」是直接终止求解进程，子进程来不及收尾，
  这时由**服务端**从它已经打印出来的日志里取最后那个
  `next:[lb, …]`，补写 bounds.json（日志里会看到
  `[server] 已把日志中证明的下界 lb=14 存入 example.bounds.json`）；
- 终端里 Ctrl-C 直接杀进程，**不保证**能留下下界（CP-SAT 求解中途不把控制权
  交回 Python），要保存成果请用 WebUI 的「停止」按钮；
- 指纹不一致（配置改了 / `layout_exact.py` 改了，哪怕只改注释）自动忽略，
  日志会说明原因；也就是说改完代码后的第一轮会退化成冷启动，这是有意为之；
- 想看“完全不复用历史”的表现：删掉 `.bounds.json`，或干脆不加 `--hint`；
- `applied` 记录的是**证明这个界时模型上还加了什么约束**，仅供排查用；
- 里面的 `best` 也会被当**上界**复用：即使 `solutions.jsonl` 被后来更差的一轮
  覆盖（“一次求解 = 一份结果”的副作用），之前那个好上界也不会丢，日志会提示
  `上界 N 比 solutions.jsonl 里的最好解 cost=M 更紧`。

### 收益与边界

- warm start 让“再次求解”从一个现成 incumbent 出发，并把窗口收到 `[L, C]`
  （L = 历史已证下界，C = 历史最好解）。小题目（`config.toy.json`）CP-SAT 本身
  几秒就搜到最优，加不加差别不大；大题目里“找到第一个可行解”本身就很慢，这时
  收益最明显——实测 `config.toy.json` 是 10.97s → 2.72s。
- 但它**加速不了“证明最优”**：如果 `best` 长时间不动、只有 `lb` 在爬（说明卡在
  下界证明），Hint 帮不上忙。能动的只有 (a) 更多时间；(b) 复用历史下界，
  把上一轮证到的 L 带过来；(c) 模型本身，例如给每条 net 加“路径格数 ≥ 源/宿格
  曼哈顿距离 + 1”这类**合法**的加强下界条件约束。
- 如果历史解陈旧（cost 比当前模型的真实最优还小），`obj <= C` 会偏紧，模型直接
  `INFEASIBLE`；程序会提示“历史数据与当前配置不一致”，删掉历史文件或去掉
  `--hint` 重跑即可。

另外提醒：`config.example.json`（17 列 9 条 net）的状态空间很大，
即使跑几分钟也只到 `FEASIBLE`，不适合拿来快速试跑；
小规模验证请用 `config.toy.json` / `config.cross.json`。

精确版搜索过程中得到的**每一个可行解**都会当场保存下来，不用等搜索结束。
数量**不设上限**，按发现顺序记录，编号 `0..N-1` 连续；同一个传送带格数
只报一次（重复的解没有意义）。

```bash
python src/layout_exact.py configs/config.example.json --time-limit 600 --workers 16
```

边搜索边生成（每找到一个解就刷新一次）：

- `result/example/example.solN.txt` / `.solN.svg`：第 N 个可行解的字符画与彩色 SVG；
- `result/example/example.solutions.jsonl`：按搜索顺序追加所有可行解；
- `result/example/example.svg`：搜索结束后再画一遍最优解；
- 终端字符画：`[A/S1]` / `[B/E1]` 显示模块端口，路径格只保留
  `→ ← ↑ ↓` 方向箭头，`╋` 表示交叉。

启发式版 `layout_solver.py` 现在也是同样的输出方式：它会一直搜索到
`--timeout` 用完，每找到更好的布局就立刻输出一份，可以中途暂停/停止。

默认输出目录规则：`result/<题目文件名>/<题目文件名>.*`，例如：

- `config.cross.json` -> `result/cross/cross.solutions.jsonl`、`result/cross/cross.svg`
- `config.toy.json`   -> `result/toy/toy.solutions.jsonl`、`result/toy/toy.svg`
- `config.example.json` -> `result/example/example.solutions.jsonl`、`result/example/example.svg`

可自定义输出前缀：

```bash
python src/layout_exact.py configs/config.example.json --time-limit 600 --output my_dir/my_problem
```

会生成 `my_dir/my_problem.solutions.jsonl` 和 `my_dir/my_problem.svg`。

## 从 solutions.jsonl 批量生成可视化

已经跑完求解、只想批量看所有可行解时，直接渲染 JSONL：

```bash
python src/layout_viz.py configs/config.example.json
```

缺省自动读取 `result/example/example.solutions.jsonl`。
生成的每个解：

- `result/example/example.sol0.svg`
- `result/example/example.sol0.txt`
- `result/example/example.sol1.svg`
- `result/example/example.sol1.txt`
- ...

常用参数：

```bash
# 终端同时打印字符画
python src/layout_viz.py configs/config.example.json --preview

# 只生成前 3 个
python src/layout_viz.py configs/config.example.json --limit 3

# 指定输出前缀
python src/layout_viz.py configs/config.example.json --output result/example/example

# 只补缺失的 solN.svg/txt（WebUI 的“刷新结果”走的就是这条路）
python src/layout_viz.py configs/config.example.json --only-missing --prune --best
```

- `--only-missing`：已经有 svg+txt 的解直接跳过，只补缺的；
- `--prune`：删掉编号超出 JSONL 记录数的历史 `solN` 文件，顺带清掉被中途
  停止时留下的半行 JSONL；
- `--best`：用 JSONL 里最优的那个解再画一次 `<前缀>.svg`。

目标函数默认为：

```text
min 所有 net 使用的传送带格数
```

约束：模块不重叠、不压固定模块、传送带不穿模块、相连模块之间
必须留带位；同格最多两条带，且两条带只能按“一横一竖直通”交叉。

启发式版和精确版都支持**严格垂直交叉**：

- 同一个格最多两条传送带；
- 两条带必须一横一竖笔直穿过交叉格；
- 交叉格不能是任一条带的起点/终点；
- 交叉格内不允许任一条带转弯。

可用 `config.cross.json` 验证交叉规则：

```bash
python src/layout_exact.py configs/config.cross.json --time-limit 20
python src/layout_solver.py configs/config.cross.json --timeout 5
```

求解器返回 `status: OPTIMAL` 时，结果就是**数学上的全局最优**；
若时间不足会返回 `FEASIBLE`，即可行但不是已证明最优。

同一个 `config.*.json` 可以同时喂给启发式版和精确版，两者共用一套输入格式。

## 配置 JSON 格式

### 画布

```json
{
  "rows": 9,
  "cols": 17,
  "fixed": [ ... ],
  "movable": [ ... ],
  "nets": [ ... ]
}
```

### 固定模块（Y、墙、固定设施等）

```json
{
  "id": "Y1",
  "pos": [0, 0],
  "w": 6,
  "h": 4,
  "ports": [
    {
      "id": "OUT",
      "cells": [[3,0],[3,1],[3,2],[3,3],[3,4],[3,5]],
      "dir": "S"
    }
  ]
}
```

- `pos`：左上角行、列，0 起始。
- `cells`：端口相对模块左上角的格子。
- `dir`：传送带离开这个端口的绝对方向，`N/S/E/W`。

Y 底部一行全部是出口，就声明 6 个底部格，`dir: "S"`。

### 可动模块

```json
{
  "id": "J1",
  "w": 3,
  "h": 3,
  "rotatable": true,
  "ports": [
    {"id": "IN",  "cells": [[0,0],[1,0],[2,0]], "dir": "W"},
    {"id": "OUT", "cells": [[0,2],[1,2],[2,2]], "dir": "E"}
  ]
}
```

如果你的 J1 实际是：

```text
[入口] [J1] [出口]
[入口] [J1] [出口]
[入口] [J1] [出口]
```

那它的端口就是：

- `IN`：左列 3 格，方向 W
- `OUT`：右列 3 格，方向 E

端口可以声明多组，例如某模块有两组输入/输出，只要各组 id 不同即可。

### 连接表

```json
{
  "from": "J1",
  "from_port": "OUT",
  "to": "S1",
  "to_port": "IN"
}
```

一条 net 表示“J1 的出口端接到 S1 的入口端”。

同一个端口组有 3 个格时，如果有多条 net 使用它，算法会自动选择不同的格位，避免冲突；不使用的端口格可以留空，对应你说的“可以接可以不接”。

## 输出

程序输出：

1. 每个可动模块最终位置和旋转角；
2. 每条 net 经过的传送带格位；
3. SVG 彩色图：模块为色块；`S1/E1` 端口徽章画在模块内部对应格，
   虚线把端口与传送带端点连起来，折线带方向箭头；
4. 终端字符画：
   - 模块端口直接标在模块内部对应格：`[A/S1]` 表示 A 的第 1 条输出口，
     `[B/E1]` 表示 B 的第 1 条输入口；
   - 路径格只保留方向箭头 `→ ← ↑ ↓`；
   - 垂直交叉点显示为 `╋`；
   - 所有列按终端实际显示宽度对齐，格式与 SVG 的箭头/交叉表达保持一致。

## 约束与假设

- 传送带不能穿过任何模块或固定设施。
- 两条不同传送带不能共用同一个网格格。
- 模块之间至少要有一个空格，传送带才有地方放；如果两个相连模块被算法摆到紧贴位置，该 net 会判为不可行，从而被淘汰。
- 目标默认是最小化总传送带格数；可自行在 `Solver.route` 中加入拐弯、交叉等惩罚项。

## 换成你自己的题目

1. 把 `movable` 中每个模块的 `ports` 改成你模块真实的“3 进/3 出”位置和方向；
2. 修改 Y 的端口与位置；
3. 修改 `nets` 为你的连接表；
4. 运行：

```bash
python src/layout_solver.py configs/my_config.json --timeout 60
```

如果搜不到解，优先检查：

- 模块端口是否声明正确；
- 画布是否过小、是否有足够的空格做传送带；
- 把 `--timeout` 调大，或更换 `--seed` 多次尝试。
