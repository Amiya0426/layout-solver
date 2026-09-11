# 通用“模块布点 + 传送带布线”求解器

通用「模块布点 + 传送带布线」求解器：把每个可动模块放在哪、转多少度、每条传送带怎么走，一次性算出来。
两种后端——启发式（模拟退火 + A*，零依赖）和精确最优（CP-SAT，可证最优）；外加一个只用标准库的图形界面。

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
├── docs/        深度文档 + 布局参考图、原始布局文本、归档代码
│   ├── exact-model.md        精确求解器：按格聚合、合法下界、两阶段、presolve
│   ├── warm-start.md         warm start：完整 Hint、上下界复用、bounds.json
│   ├── cp-sat-log.md         怎么读 CP-SAT 的 --verbose 日志
│   └── heuristic.md          启发式的打分与搜索
├── requirements.txt
├── SKILL.md     维护/排障手册（环境、命令速查、不变量、性能基线、决策树）
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

# 5) 精确求解器自检（需要 OR-Tools；独立校验解的语义 + 建模/两阶段日志）
python tests/test_exact_model.py

# 6) 模块预设（读 data/设备尺寸.xlsx，并用预设拼出的配置真跑一遍启发式求解）
python tests/test_presets.py

# 7) 前端逻辑（需要 node；没装就跳过）
python tests/test_webui_frontend.py

# 8) 端到端前端探针（需要能用的无头 Chrome/Edge，否则自动跳过）
python tests/webui_presets_probe.py
```

Windows 下如果中文乱码，可先设置 `$env:PYTHONIOENCODING="utf-8"`。

> 提示：`configs/config.example.json` 状态空间很大，跑几分钟也未必到
> `OPTIMAL`，不适合快速试跑；小规模验证请用 `config.toy.json` /
> `config.cross.json`（已知最优分别是 **7** 和 **12**）。

各类求解器的详细用法见下文「启发式求解」「精确最优求解（CP-SAT）」；
其余回归脚本（`warmstart_probe.py` / `stop_bound_probe.py`）与改动检查清单
见 [`SKILL.md`](SKILL.md)。

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
- `SKILL.md`：维护/排障手册（环境、命令速查、必须守住的不变量、性能基线、决策树）
- `docs/exact-model.md` / `docs/warm-start.md` / `docs/cp-sat-log.md` /
  `docs/heuristic.md`：深度文档，见下面的「文档地图」
- `docs/archive_layout_exact_optimized.py`：早期实验性增强版求解器，
  **未被任何入口调用**，仅作归档，不再维护

## 文档地图

| 文件 | 写给谁 / 放什么 |
| --- | --- |
| `README.md`（本文） | 使用者：怎么装、怎么跑、配置怎么写、输出在哪 |
| `SKILL.md` | 维护者/助手：环境、命令速查、必须守住的不变量、性能基线、排障决策树 |
| `docs/exact-model.md` | 精确求解器原理：按格聚合、合法下界、两阶段、presolve 策略 |
| `docs/warm-start.md` | warm start 机制、上下界复用、`bounds.json` 字段语义 |
| `docs/cp-sat-log.md` | 怎么读 CP-SAT 的 `--verbose` 日志 |
| `docs/heuristic.md` | 启发式的打分函数、拆线重布、为什么大题目难 |
| `docs/webui_frontend_report.md` | WebUI 前端实现报告（历史文档） |

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

打分方式、为什么大题目难、以及「最接近时还差几条 net」这类日志怎么看，见 [`docs/heuristic.md`](docs/heuristic.md)。

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
- `--presolve auto|on|off`：CP-SAT 的 presolve 策略。`auto`（默认）在**约束很多**
  （≥ 20 万条）时自动关掉 presolve——大模型上它可能先吃掉好几分钟才轮到搜索
  （实测 `config.gudi.json` 108 万条约束：开着 presolve 跑 5 分钟还没进搜索，
  关掉后 10s 就进搜索）。小题目（example 3 万条、toy 6 千条）保持开启；
- `--param NAME=VALUE`：透传任意 CP-SAT 参数，可重复，例如
  `--param cp_model_presolve=false --param random_seed=7 --param max_lp_solve_seconds=10`；
- `--verbose`：打印 CP-SAT 搜索过程。

搜索结果**不设数量上限**：找到多少个可行解就立刻写多少份
（`solN.txt` / `solN.svg` / `.solutions.jsonl`），编号 `0..N-1` 连续。

一句话现状：精确求解器默认**两阶段**跑（阶段1 先解不含「共格十字直通」细则的
松弛模型拿骨架和合法下界，阶段2 补回细则求最优），并在建模时补一条合法下界
`obj >= 连接条数`。大题目（`config.gudi.json`，27×30 / 33 可动模块 / 47 连接）
建模约 **29s、约 1GB**，之后 presolve 会自动让位给搜索。

想深入了解时看这几份：

| 想知道 | 看 |
| --- | --- |
| 模型是怎么建的、为什么这么建（按格聚合、合法下界、两阶段、presolve 策略） | [`docs/exact-model.md`](docs/exact-model.md) |
| `--hint` 的完整 Hint / 上下界 / `<题目>.bounds.json` 字段与复用规则 | [`docs/warm-start.md`](docs/warm-start.md) |
| `--verbose` 日志里的 `next:[lb, ub]`、`bool_core`、presolve 静默期怎么读 | [`docs/cp-sat-log.md`](docs/cp-sat-log.md) |
| 启发式怎么打分、为什么大题目难 | [`docs/heuristic.md`](docs/heuristic.md) |
| 想在这个仓库里改代码 / 排障（环境、不变量、性能基线、决策树） | [`SKILL.md`](SKILL.md) |

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

## 约束与假设

- 传送带不能穿过任何模块或固定设施。
- 一格最多走两条带；两条带共格时必须是「一横一纵」的十字直通——不能转弯，
  交叉格也不能是任何一条带的端点（模型里的「共格细则」，见
  [`docs/exact-model.md`](docs/exact-model.md)）。
- 模块之间至少要有一个空格，传送带才有地方放；如果两个相连模块被算法摆到紧贴位置，该 net 会判为不可行，从而被淘汰。
- 目标默认是最小化总传送带格数；启发式版可自行在 `Solver.route` 中加入拐弯、交叉等惩罚项。

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
