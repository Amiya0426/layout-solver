---
name: layout-solver-maintainer
description: 在「模块布点 + 传送带布线」求解器仓库里改代码、调参或排障时使用：环境与运行方式、命令速查与验收标准、必须守住的不变量、性能基线与实测数字、排障决策树、常见坑，以及深潜文档索引。
---

# SKILL：维护与排障这份求解器

面向**改代码 / 排障的人（或助手）**。只想跑题目的话看 [`README.md`](README.md)；
想理解模型原理看 `docs/`（见文末索引）。

## 0. 什么时候用这份 skill

- 要改 `src/layout_exact.py`（CP-SAT 模型）或 `src/layout_solver.py`（启发式）；
- 题目跑不动 / 跑不出解，需要判断卡在哪一环；
- 要评估一个改动值不值得（先看第 4 节基线与第 7 节检查清单）。

## 1. 环境

- Python 用 **miniconda base**：`C:\ProgramData\miniconda3\python.exe`（3.12.2），
  OR-Tools **9.15.6755**。系统 PATH 上的 `python` 可能是别的解释器，动手前先确认：

  ```powershell
  C:\ProgramData\miniconda3\python.exe -c "import sys, ortools; print(sys.executable, ortools.__version__)"
  ```

- 中文乱码：`$env:PYTHONIOENCODING="utf-8"`。
- 只有精确求解器需要 OR-Tools；启发式 / 可视化 / WebUI / 预设只用标准库。
- WebUI 用 `sys.executable` 起子进程，**用它启动 WebUI 的那个解释器就是跑求解器的解释器**。
- `config.gudi.json` 的精确模型约占 1GB 内存、`--workers` 吃满核；一次只跑一个重活。

文件地图（README 里不再列这些细节，改代码前对着看）：

| 文件 | 干什么 |
| --- | --- |
| `src/layout_exact.py` | 精确最优求解器（CP-SAT）：建模 + 两阶段 + 上下界与 warm start |
| `src/layout_solver.py` | 启发式求解器（模拟退火 + 逐条布线），零第三方依赖 |
| `src/layout_viz.py` | SVG / 字符画渲染 + `SolutionWriter`（后台线程边求解边落盘）+ `default_output_prefix()` |
| `src/proc_ctl.py` | 跨平台 暂停/恢复 子进程（WebUI 的暂停按钮） |
| `src/device_presets.py` | 设备尺寸表 xlsx -> 模块预设（只用标准库解析 xlsx） |
| `src/webui_server.py` + `ui/` | 图形界面服务端与前端（`ui/model.js` 是不碰 DOM 的纯逻辑，可单测） |
| `tests/` | `test_smoke` / `test_exact_model` / `test_presets` / `test_webui_*` + 三个探针（warmstart / stop_bound / webui_presets） |
| `docs/` | 深度文档：`exact-model` / `warm-start` / `cp-sat-log` / `heuristic` / `config-format` / `webui` / `outputs` |

## 2. 命令速查（含验收）

| 目的 | 命令 | 通过标准 |
| --- | --- | --- |
| 精确求解（含两阶段） | `python src/layout_exact.py configs/config.toy.json --time-limit 40 --workers 4` | toy 得 `OPTIMAL cost=7` |
| 强制交叉用例 | 同上换 `config.cross.json` | `OPTIMAL cost=12` |
| 关两阶段排查 | 加 `--no-relax-phase` | 结果不变（可能更慢） |
| 调 CP-SAT 参数 | 加 `--param NAME=VALUE`（可重复） | 日志回显参数；名字写错会干净报错 |
| 关/开 presolve | `--presolve off/on/auto` | 日志有「presolve=…」说明行 |
| warm start | 先跑一轮，再加 `--hint` | 日志有「完整 Hint … 共 hint N 个变量」 |
| 启发式 | `python src/layout_solver.py configs/config.toy.json --timeout 10` | 12s 内拿到 cost=7 |
| 自检（无依赖，约 10s） | `python tests/test_smoke.py` | 全部通过 |
| 模型语义 + 日志契约 | `python tests/test_exact_model.py` | 全部通过（见第 7 节） |
| warm start 回归 | `python tests/warmstart_probe.py` | `tests/ws/report.txt` 里 5 个 phase 都是 `cost/lb=(7, 7)` |
| 停止时下界落盘 | `python tests/stop_bound_probe.py` | 全部通过 |
| 预设 / 前端逻辑 | `python tests/test_presets.py`、`tests/test_webui_frontend.py` | 全部通过（后者需要 node） |

跑实验时**一定加 `--output <临时目录>/<名字>`**，别污染 `result/`。

## 3. 必须守住的不变量

1. **一次求解 = 一份结果**：第一次真正写盘前清掉旧产物；本轮没找到解则保留上一次。
2. **边求解边落盘**：每找到一个可行解立刻写 `solN.svg/txt` + 追加 `.solutions.jsonl`；
   写盘在后台线程，不能阻塞搜索。
3. **下界只能来自证明**，且只认指纹（config 内容 + 建模代码 sha256）一致的历史记录；
   指纹对不上必须忽略（宁可冷启动，也不能静默剪掉最优解）。
4. **松弛解不是解**：两阶段里阶段1 的解可能违反「共格十字直通」，**不得**写成 `solN`、
   **不得**当目标上界；只有「违规 0 处」的合法解才允许 `obj <= C`。
5. **Hint 必须完整**（每个非固定变量都给值），否则 CP-SAT 会另起 `hint search` 子求解器，
   “路径怎么连通”这部分信息就白丢了。
6. **三处按格聚合要保住整数解等价**：禁带 + 非重叠（`occ`）、共格十字（`T`/`H`/`V`/`E`）、
   端点格（`end`）。改这几处一定跑 `tests/test_exact_model.py`。
7. **`[exact] bounds-key {...}` 这行日志格式不能动**：WebUI 的「停止」直接 terminate
   子进程，服务端只能靠这行里的指纹替它把已证下界补写进 `bounds.json`。
8. **默认不启用 warm start**：不带 `--hint` 时行为与历史一致（不读任何历史文件）。

## 4. 性能基线与实测数字（改动后的对照标尺）

| 对象 | 数字 |
| --- | --- |
| `config.gudi.json`（27×30 / 10 固定 + 33 可动 / 47 连接）建模 | **28.7s**、465,128 变量、1,043,673 约束（完整模型约 1,083,453 条）、**0.99GB** |
| 同上，改动前（按格聚合之前） | 2,077,708 变量 / ≈6,290 万约束 → 建模阶段就建不完 |
| 合法下界 | `obj >= 47`（= 连接条数）**建模即生效**；不加时 example 要 224s 才爬到同样的界 |
| presolve 策略阈值 | 约束 ≥ 20 万条时 `auto` 关掉 presolve；gudi 关掉后 10s 进搜索，开着 5 分钟还没进 |
| `config.example.json`（10×17 / 7 可动 / 9 连接）单阶段 | 300s / 10 workers **零解**（下界爬到 9 用了 224s） |
| 同上，两阶段 | 240s：阶段1 84s 给出「cost=18、违规 2 处」骨架 + `lb_relax=9`；阶段2 81s 出第一个合法解，共 22 个解、最好 **cost 24** |
| 启发式 `config.example.json` | 90~120s 仍无解，但能爬到「最接近只差 1 条 net」 |
| 已知最优（回归锚点） | toy **7**、cross **12** |

## 5. 排障决策树

1. **建模阶段没动静** → 看 `[exact]` 分步日志停在哪一步（每行带累计/本步耗时）；
   对照基线：gudi 全流程 26~29s。停住说明那一步的规模爆炸了（看它报的变量/约束数）。
2. **模型建好了但长时间没有任何输出** → 大概率在 presolve 静默期：
   `--verbose` 看有没有 `Starting search at …`；大模型上 `--presolve auto` 会自动跳过
   presolve，想强制用 `--presolve on`。
3. **长时间找不到可行解**（status=UNKNOWN）：
   - 先看两阶段日志：阶段1 有没有骨架？违规几处？
   - 有上一轮的解就用 `--hint`（完整 Hint + `obj<=C` 直接给 incumbent）；
   - 没有历史解就先跑启发式（写的是同一份 `.solutions.jsonl`），或换 `--seed` 多试；
   - 大题目给足时间，并且**确认 `--time-limit` 没被 presolve 吃掉**。
4. **下界爬得慢** → 先确认「合法下界 obj >= N」那行有没有出现（N 应 ≥ 连接条数）；
   再看 `bounds.json` 里有没有可复用的 `lb` / `lb_relax`（`--hint` 才会读）。
5. **怀疑解不合法** → 跑 `tests/test_exact_model.py`：里面有一份**独立**的语义校验器
   （不重叠 / 连通 / 不穿模块 / 共格必须一横一竖直通 / cost = 占用格之和）。
6. **WebUI 停止后下界丢了** → 检查子进程日志里 `[exact] bounds-key …` 是否还在。
7. **启发式毫无进展** → 看心跳里的「最接近时还差 N 条 net」：N 在下降说明有梯度；
   一直 N=全部说明摆位太差（`placement_score` 高），换 seed / 加时间。

## 6. 常见坑（都踩过）

1. **改了 `layout_exact.py` 一行，历史下界就失效**（指纹变了）→ 下一轮是冷启动，
   日志会写「忽略历史下界：建模代码已改动」，这是有意为之。
2. **别在 pybind11 类型上 monkeypatch**：`CpModel.Minimize` / `CpSolver.Solve` 这类
   实例查找绕过类属性，`setattr` 看起来成功但根本不会生效（吃过一次亏：
   整个实验变成空转）。要做参数/模型实验，**把源码复制到临时目录改副本**。
3. **硬条件 vs 软打分**：给启发式的 `random_state` 加「端口可达 + 起终点连通」这类
   硬过滤，会让它在 example 上 12780 版全被拒、直接饿死。必须做成**软打分取最好**，
   可行性交给代价函数去爬。
4. **CP-SAT 的日志走 C++ stdout**：`contextlib.redirect_stdout` 抓不到 `#Bound`
   这类行；要分析就得把子进程输出 tee 到文件再 grep。
5. **松弛模型里保留「每格 ≤2 条带」**（`T` 的域仍是 0..2）——把容量也放开会让骨架
   质量明显变差（一堆叠带），repair 更难。
6. **阶段1 不要提前收工**：找到合法解就停会丢掉松弛下界，toy 会从 5.7s 掉到 9.2s。
7. **`tests/test_smoke.py` 的启发式 `--timeout` 要留余量**：首个可行解出现时间在
   3.2s 上下浮动，写死 3s 会时红时绿。
8. **产物目录**：实验请 `--output` 到临时目录；`result/` 里是用户的真实结果。

## 7. 改完代码的检查清单

```powershell
$env:PYTHONIOENCODING="utf-8"
C:\ProgramData\miniconda3\python.exe tests\test_exact_model.py   # 语义 + 建模日志 + 两阶段日志
C:\ProgramData\miniconda3\python.exe tests\test_smoke.py
C:\ProgramData\miniconda3\python.exe tests\test_presets.py
C:\ProgramData\miniconda3\python.exe tests\stop_bound_probe.py
C:\ProgramData\miniconda3\python.exe tests\warmstart_probe.py    # 5 个 phase 应为 cost/lb=(7,7)
```

- 动过建模：**toy=7 / cross=12 不能变**；`config.gudi.json` 的建模时间与规模别退化
  （基线见第 4 节）；建模分步日志要齐全且按顺序出现（`test_exact_model.py` 会钉住）。
- 动过求解流程：warm start 的 5 个 phase 必须仍是 `cost/lb=(7, 7)`；UNKNOWN 分支
  仍要把已证下界写进 `bounds.json`。
- 动过启发式：toy 在 12s 内仍应拿到 7（seed 1 与 7 都试一下）。

## 8. 深入阅读

- [`docs/exact-model.md`](docs/exact-model.md)：模型怎么建（按格聚合三处等价改写）、
  合法下界、两阶段、presolve 策略
- [`docs/warm-start.md`](docs/warm-start.md)：完整 Hint、上下界复用、`bounds.json` 字段
- [`docs/cp-sat-log.md`](docs/cp-sat-log.md)：`next:[lb, ub]` / `bool_core` / presolve 静默期
- [`docs/heuristic.md`](docs/heuristic.md)：启发式的打分、拆线重布、为什么大题目难
- [`docs/webui_frontend_report.md`](docs/webui_frontend_report.md)：WebUI 前后端实现报告
- [`README.md`](README.md)：面向使用者的用法与配置格式
