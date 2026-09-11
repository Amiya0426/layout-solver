# Warm start 与上下界复用

同一个题目往往要反复求解。`--hint` 让这一轮**接着上一轮跑**：完整 Hint + 上下界，并把本轮证出的界落盘给下一轮用。本文写清楚三者的依据、风险与 `bounds.json` 的字段语义。

> 本文由 `README.md` 拆分而来，内容是原样搬移的工程说明；操作向的速查见 [`../SKILL.md`](../SKILL.md)。

## Warm start（可选）：接着上一轮跑

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

## 下界持久化：`<题目>.bounds.json`

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

## 收益与边界

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
