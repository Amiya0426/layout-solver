# 通用“模块布点 + 传送带布线”求解器

通用「模块布点 + 传送带布线」求解器：给定画布、模块和连接表，一次算出**每个模块放哪、转多少度**和**每条传送带怎么走**。
启发式求解器零依赖、求「尽量好」；精确求解器（CP-SAT）能**证明全局最优**；另外带一个只用标准库的图形界面。

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
# 启发式（不需要 OR-Tools）
python src/layout_solver.py configs/config.toy.json --timeout 10

# 精确最优（需要 OR-Tools）
python src/layout_exact.py configs/config.toy.json --time-limit 60

# 图形界面
python src/webui_server.py --port 8765     # 打开 http://127.0.0.1:8765/

# 自检
python tests/test_smoke.py                 # 不需要 OR-Tools，约 10 秒
python tests/test_exact_model.py           # 需要 OR-Tools：校验解的语义 + 建模日志
```

Windows 下中文乱码先设 `$env:PYTHONIOENCODING="utf-8"`。

> 试跑请用 `config.toy.json`（已知最优 **7**）或 `config.cross.json`（**12**）；
> `config.example.json` 状态空间很大，跑几分钟也未必到 `OPTIMAL`。

## 求解目标与约束

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
还有两条约定：

- 模块之间至少要留一个空格，传送带才有地方放；两个相连模块被摆到紧贴位置时，
  该 net 判为不可行；
- 目标可以按需加惩罚项（拐弯、交叉等）：启发式版改 `Solver.route` 即可。

## 求解器怎么用

### 启发式（模拟退火 + A*，零依赖）

```bash
python src/layout_solver.py configs/config.toy.json --timeout 10
```

- `--timeout 秒`、`--seed N`（换个种子就是换一种运气，值得多跑几个）、`--output 前缀`；
- 打分 = `1000 × 没布通的 net 数 + 已布格的格数`，日志会报「最接近时还差 N 条 net」；
- 只保证“尽量好”；要证明最优用下面的精确版。细节见 [`docs/heuristic.md`](docs/heuristic.md)。

### 精确最优（CP-SAT，可证最优）

```bash
python src/layout_exact.py configs/config.toy.json --time-limit 60
```

| 参数 | 作用 |
| --- | --- |
| `--time-limit 秒` / `--workers N` | 求解时间上限 / 并行线程数 |
| `--hint` | warm start：拿上次的解做完整 Hint，并施加历史上下界（默认关） |
| `--presolve auto\|on\|off` | 大模型（≥20 万条约束）自动跳过 presolve，把时间留给搜索 |
| `--param NAME=VALUE` | 透传任意 CP-SAT 参数（可重复） |
| `--no-relax-phase` | 关掉两阶段求解的阶段1（排查用） |
| `--verbose` | 打印 CP-SAT 搜索过程 |

默认两阶段：阶段1 先解**不含共格细则**的松弛模型拿骨架与合法下界，阶段2 补回
细则求最优；建模时还会补一条合法下界 `obj >= 连接条数`。大题目
（`config.gudi.json`：27×30 / 33 可动模块 / 47 连接）建模约 **29s、约 1GB**。

- 模型怎么建、为什么这么建 → [`docs/exact-model.md`](docs/exact-model.md)
- `--hint` 与 `<题目>.bounds.json` → [`docs/warm-start.md`](docs/warm-start.md)
- `--verbose` 日志怎么读 → [`docs/cp-sat-log.md`](docs/cp-sat-log.md)

## 配置 JSON 怎么写

题目就是一份 JSON：画布 + 固定模块 + 可动模块 + 连接表。

```json
{
  "rows": 9, "cols": 17,
  "fixed":   [{"id": "Y1", "pos": [0, 0], "w": 6, "h": 4,
               "ports": [{"id": "OUT", "cells": [[3, 0], [3, 1]], "dir": "S"}]}],
  "movable": [{"id": "J1", "w": 3, "h": 3, "rotatable": true,
               "ports": [{"id": "IN",  "cells": [[0, 0]], "dir": "W"},
                         {"id": "OUT", "cells": [[0, 2]], "dir": "E"}]}],
  "nets":    [{"from": "Y1", "from_port": "OUT", "to": "J1", "to_port": "IN"}]
}
```

- `pos` 是左上角的行、列（0 起）；`cells` 是端口相对模块左上角的格；`dir` 是
  传送带离开端口的绝对方向 `N/S/E/W`；
- 一个端口可以有多格，多条 net 用同一个端口时算法会自动挑不同的格；
- 完整字段说明（含“多组端口”“端口格可以不接”）见
  [`docs/config-format.md`](docs/config-format.md)；对着图形界面改更省事，见
  [`docs/webui.md`](docs/webui.md)。

## 输出与产物

求解过程中**每找到一个可行解就立刻写一份**，编号 `0..N-1` 连续、数量不设上限：

```text
result/<题目>/<题目>.solN.svg / .solN.txt   第 N 个可行解的彩色图与字符画
result/<题目>/<题目>.solutions.jsonl        按发现顺序追加所有可行解
result/<题目>/<题目>.svg                    搜索结束后再画一遍最优解
```

批量重画 / 补图（WebUI 的「刷新结果」走的就是这条路）：

```bash
python src/layout_viz.py configs/config.example.json --only-missing --prune --best
```

字符画图例、目录规则与全部参数见 [`docs/outputs.md`](docs/outputs.md)。

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

## 目录结构

```text
├── src/       求解器（layout_exact.py / layout_solver.py）、可视化、WebUI 服务端
├── ui/        WebUI 前端（原生 HTML/CSS/JS，无构建步骤）
├── configs/   题目配置 config.<题目名>.json
├── data/      设备尺寸.xlsx（模块预设来源）+ 生成缓存
├── result/    求解产物（solN.svg / solN.txt / solutions.jsonl）
├── tests/     自检与探针脚本
├── docs/      深度文档（模型原理、warm start、日志、配置格式、WebUI、产物）
└── SKILL.md   维护/排障手册
```

`result/`、`configs/` 的位置由 `src/layout_viz.py:default_output_prefix()` 统一
推导，所以从任何工作目录运行、或由 WebUI 以子进程调用，产物都落在项目的
`result/` 下。

## 文档地图

| 想看什么 | 看哪份 |
| --- | --- |
| 题目 JSON 的完整字段说明 | [`docs/config-format.md`](docs/config-format.md) |
| 图形界面（编辑、预设、连接预览、暂停/停止） | [`docs/webui.md`](docs/webui.md) |
| 产物的全部参数与字符画图例 | [`docs/outputs.md`](docs/outputs.md) |
| 精确求解器原理（按格聚合、合法下界、两阶段、presolve） | [`docs/exact-model.md`](docs/exact-model.md) |
| warm start、上下界复用、`bounds.json` | [`docs/warm-start.md`](docs/warm-start.md) |
| `--verbose` 日志怎么读 | [`docs/cp-sat-log.md`](docs/cp-sat-log.md) |
| 启发式的打分与搜索 | [`docs/heuristic.md`](docs/heuristic.md) |
| 改代码 / 排障（环境、不变量、性能基线、决策树） | [`SKILL.md`](SKILL.md) |
