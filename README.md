# 通用“模块布点 + 传送带布线”求解器

## 目录结构

```text
.
├── src/         求解器与可视化源码
│   ├── layout_exact.py       精确最优求解器（CP-SAT）
│   ├── layout_solver.py      启发式求解器（模拟退火 + A*，无需第三方依赖）
│   ├── layout_viz.py         SVG / 字符画可视化 + 增量输出
│   ├── proc_ctl.py           跨平台 暂停/恢复 子进程
│   └── webui_server.py       图形化 WebUI 服务端（只用标准库）
├── ui/          WebUI 前端（原生 HTML/CSS/JS，无构建步骤）
├── configs/     题目配置 config.<题目名>.json
├── result/      求解产物（每个新解一份 solN.svg / solN.txt + solutions.jsonl）
├── tests/       冒烟测试
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

# 4) 自检（不需要 OR-Tools，约 5 秒）
python tests/test_smoke.py
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
- `src/webui_server.py` + `ui/`：图形化 WebUI
- `configs/config.example.json`：17×9 / Y1Y2 / J1P1… 拓扑示例
- `configs/config.toy.json`：可运行的小示例
- `configs/config.cross.json`：强制垂直交叉的最小示例
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
- 端口编辑：选择端口后，在模块格子上点击即可设置 3 进/3 出位置与方向；
- 连接表：下拉框选择「哪个模块的哪个端口 -> 哪个模块的哪个端口」；
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
  SVG/字符画，服务器会自动起一个**后台静默补图**任务补齐。

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
- `--max-solutions N`：最多保存多少个可行解（默认 200，0 = 不限）。
  按**发现顺序**记满 N 个即停止上报，不淘汰、不重排，`solN` 编号始终连续；
- `--hint`：启用 warm start，把上次的最好解作为 CP-SAT 起点（**默认关闭**）；
- `--hint-strict`：配合 `--hint`，只搜索**严格更优**的解（见下）；
- `--verbose`：打印 CP-SAT 搜索过程。

### Warm start（可选）：把上次的最好解当起点

WebUI「求解」页有 **Warm start（用上次最好解做起点）** 勾选框，默认不勾；
命令行对应 `--hint`。

同一个题目往往会反复求解，从头开始意味着要把走过的搜索重走一遍。勾上之后，
`layout_exact.py` 会读取该题目上次留下的
`result/<题目>/<题目>.solutions.jsonl`，取出其中**传送带格数最小**的那个解，
用 `model.AddHint()` 当作 CP-SAT 的 hint：

- 模块位姿按 `(位置, 旋转)` 精确匹配；对不上时退化为只匹配位置；
- 每条 net 的起点/终点按端口外侧格匹配，路径格用 `use = 1` 提示；
- 同时把目标**上界**压到上次的 cost，让求解器直接去找"不比上次差"的解
  （安全：该上界对应一个已知可行解，不会剪掉最优解）。

Hint 只是**搜索起点，不是硬约束**——CP-SAT 不保证沿用它（日志里会打印
`The solution hint is incomplete: ...`，这是正常的），也不会因此失去最优性
保证。日志会写明是否用上了 Hint：

```text
[exact] 模型构建完成: 63 个可用格点, 开始 CP-SAT 搜索 (time_limit=60.0s, workers=8)，已用上次最好解作为 Hint (cost=7, 匹配模块 3 个)
```

实测（`config.toy.json`，3 个可旋转模块，8 线程，15s 预算）：

| 运行 | Hint | 首次达到最优 cost=7 | 本次上报解数 |
| --- | --- | --- | --- |
| 首次（无历史） | 无 | 4.84s | 3 |
| 再次求解 | 匹配 3 个模块 | **3.14s（第 1 个解就是最优）** | 1 |
| 对照 | 无 | 5.55s | 4 |

带上 Hint 后，搜索一上来就落在最优解上，省掉了先摸几个劣解的过程。

### 先搞清「上下界」：`next:[lb, ub]` 是什么

跑 `--verbose` 会看到 CP-SAT 的搜索进度，例如：

```text
#Bound  0.11s best:inf   next:[4,128]    initial_domain
#2      0.12s best:40    next:[4,39]
#7      0.16s best:14    next:[5,13]
#Bound  0.16s best:14    next:[7,13]
#Bound  0.16s best:14    next:[10,13]
#Bound  0.18s best:14    next:[11,13]
#Bound  0.18s best:14    next:[12,13]
#8      0.18s best:12    next:[]
```

这是**分支定界**的两个数，回答"当前搜索正卡在哪个区间"：

- **下界 `lb`**（`next` 左值）：**已证明**解的格数不可能低于它。
  由上方的 LP 松弛 + 约束传播**算出来**，随搜索逐步上升（上面 4→5→7→10→11→12）。
- **上界 `ub`**（`next` 右值）：接下来要找的解的格数上限，恒等于 `best - 1`。
- 两者的含义就是"**下一步在 [lb, ub] 里找解**"。
- 当 `best` 掉到 `lb` 时（上面 `best:12` 配 `lb:12`），**最优性得证**，直接 `#Done`。

两个容易搞错的点：

1. **`best` 和 `next` 的上界是联动的**：`ub` 永远等于 `best - 1`。
   所以 `best:19` 不可能配 `next:[14,18]`——那两行必然来自不同时刻。
2. **下界不是可以设定的参数，而是问题的计算结果。**
   没有任何 API 能"把下界设成 13"：13 是否成立取决于 CP-SAT 自己的证明。
   而且知道"不可能低于 13"也**帮不上找更优解**的忙——真正起作用的是
   **上界**和**一个现成的好解**，这正是 warm start 在做的事。

我们能加的约束只有**上界**：

| 我们加什么 | 效果 |
| --- | --- |
| `obj <= C`（`--hint`） | 窗口变成 `[lb, C]`：不比上次差 |
| `obj <= C-1`（`--hint-strict`） | 窗口变成 `[lb, C-1]`：只找严格更优 |
| 下界 | **不动**，始终由 CP-SAT 自己推理 |

### 只找严格更优解（`--hint-strict`）

目标是**最小化**传送带格数，**越小越好**。已知上次有一个 cost = C 的解，那么

```text
严格更优  <=>  obj <= C - 1
严格更差  <=>  obj >= C + 1
```

所以「只找严格更优解」的正确做法是把**上界**再压一格：

```text
obj <= C - 1        # C = 上次最好解的格数
```

这样"和上次一样好"的解会被整片剪掉，只回报真正的改进解。

**方向很容易搞反**：压上界（`<= C-1`）才是找更优解；如果错写成抬下界
（`obj >= C+1`），那是在找**更差**的解，而且会与上界 `obj <= C` 同时成立，
模型直接 `INFEASIBLE`，整轮搜索报废。

另外，`AddHint` 只是建议、不是保证：CP-SAT 会打印
`The solution hint is incomplete: ...`，说明它并没有真正采用那个赋值当解。
因此**不能**因为"hint 变量都填上了"就断定那个 cost 一定可达——这也是
不去动下界的另一个原因。

代价与结论：

- 如果上次的解**已经是全局最优**，模型会返回 `INFEASIBLE`——这不是错误，
  而是一个结论：**上次那个解就是最优**，程序会明确提示，且上一次的结果
  文件原样保留；
- 返回 `UNKNOWN` 表示在 `--time-limit` 内**没能证明完**——它**不代表**上次
  一定最优，只是时间不够，加大时间上限再试即可；
- 如果历史解偏陈旧（cost 比当前模型的真实最优还小），上界会偏紧，
  需要更长时间才能证明不可行（可能先返回 `UNKNOWN`）；此时不要
  `--hint-strict`，直接 `--hint` 或干脆不带 `--hint` 重新搜索即可。

实测（`config.toy.json`）：

| 场景 | 命令 | 结果 |
| --- | --- | --- |
| 上次 cost=7（已最优），时间充足 | `--hint --hint-strict` | `INFEASIBLE` ⇒ 免费得到"7 已最优"的证明 |
| 同上但时间只给 8s | `--hint --hint-strict` | `UNKNOWN`（没证完，加大时限即可） |
| 人为构造上次 cost=20（非最优） | `--hint --hint-strict` | 正常搜到并证明最优 cost=7 |
| 人为构造陈旧 hint cost=5 | `--hint`（旧写法会矛盾） | 正常搜到最优 cost=7 |

> 关于收益：hint 的价值随问题规模放大。小问题（`config.toy.json`）CP-SAT
> 本身几秒就搜到最优，加不加 hint 差别不大；大问题里"找到第一个可行解"
> 本身就很慢，此时 hint 直接把一个已知可行解塞进搜索起点、并把窗口收到
> `[lb, C]`，省掉的正是这段最耗时的过程。

问题越大、可动模块越多，这个差距越明显。

另外提醒：`config.example.json`（17 列 9 条 net）的状态空间很大，
即使跑几分钟也只到 `FEASIBLE`，不适合拿来快速试跑；
小规模验证请用 `config.toy.json` / `config.cross.json`。

精确版搜索过程中得到的**每一个可行解**都会当场保存下来，不用等搜索结束。
数量上限由 `--max-solutions` 控制（默认 200，0 = 不限），按发现顺序记录，
编号 `0..N-1` 连续。

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
