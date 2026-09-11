# 产物与可视化

求解器每找到一个可行解就立刻落盘；这里写清楚产物长什么样、目录规则、以及怎么批量重画。

> 本文由 `README.md` 拆分而来，内容原样搬移。

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

# 输出

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
