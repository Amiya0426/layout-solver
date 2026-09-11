# 怎么读 CP-SAT 的日志

`--verbose` 会把 CP-SAT 的搜索进度打出来。本文只讲一件事：那些 `next:[lb, ub]` / `bool_core` / `best:` 到底在说什么，以及 presolve 期间为什么没有输出。

> 本文由 `README.md` 拆分而来，内容是原样搬移的工程说明；操作向的速查见 [`../SKILL.md`](../SKILL.md)。

## 先搞清「上下界」：`next:[lb, ub]` 是什么

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

## presolve 期间为什么安静

模型建好之后、开始报可行解之前，CP-SAT 会先做一轮 presolve；大模型上这一步
可能几十秒到几分钟**一行解的信息都没有**（`--verbose` 下能看到 presolve 的内部
计时行）。求解器会明确提示这一点，并且按模型规模自动决定要不要跳过 presolve
（见 [`exact-model.md`](exact-model.md) 的「presolve 策略」）：

```text
[exact] 模型构建完成: …；开始求解 (time_limit=300.0s, workers=8)
[exact] 提示：CP-SAT 会先做一轮 presolve，模型大时这一步可能几十秒没有任何输出，之后才会开始报可行解
```
