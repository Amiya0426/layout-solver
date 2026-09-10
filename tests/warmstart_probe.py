#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证改版后的 warm start：

  1. cold  ：不加 --hint，产出 solutions + bounds.json
  2. warm  ：加 --hint，完整 Hint + obj<=C + obj>=L
  3. 指纹  ：同配置认、改配置不认
  4. lb>best：历史下界自相矛盾时忽略并告警
  5. 只有上界：删掉 bounds.json 后 --hint 仍工作

CP-SAT 的 verbose 日志被 dup2 到 tests/ws/<phase>.log。
"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
os.chdir(ROOT)

import layout_exact  # noqa: E402
import layout_viz  # noqa: E402

OUT = os.path.join(HERE, "ws")
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(OUT, exist_ok=True)
PREFIX = os.path.join(OUT, "toy")
CFG = json.load(open("configs/config.toy.json", encoding="utf-8"))
BOUNDS = PREFIX + ".bounds.json"

REPORT = []
try:  # 控制台可能是 GBK，报告统一走 UTF-8 文件
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


def say(msg, **kw):
    REPORT.append(msg)
    print(msg, flush=True)
    with open(os.path.join(OUT, "report.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(REPORT) + "\n")


def phase(tag, use_hint, cfg=None, time_limit=15, workers=4):
    cfg = cfg or CFG
    log = open(os.path.join(OUT, f"{tag}.log"), "w", encoding="utf-8",
               buffering=1)
    saved = os.dup(1)
    os.dup2(log.fileno(), 1)
    writer = layout_viz.SolutionWriter(cfg, PREFIX)
    try:
        res = layout_exact.solve_exact(
            cfg, time_limit=time_limit, workers=workers, verbose=True,
            on_solution=writer.submit,
            hint_file=PREFIX, use_hint=use_hint)
    finally:
        sys.stdout.flush()
        os.dup2(saved, 1)
        os.close(saved)
        log.close()
    writer.close()
    out = None if res is None else (res["cost"], res.get("lb"))
    say(f"[{tag}] use_hint={use_hint} -> cost/lb={out} 输出解数={writer.count}",
          flush=True)
    return res


def show(tag):
    path = os.path.join(OUT, f"{tag}.log")
    keys = ("exact]", "hint", "Hint", "#Bound", "#1 ", "complete_hint",
            "status:", "obj<=", "obj>=")
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.rstrip("\n")
            if any(k in s for k in keys):
                say(f"    {tag}| {s}")


phase("cold", False)
show("cold")
say("bounds.json = " + json.dumps(json.load(open(BOUNDS, encoding="utf-8")),
                                  ensure_ascii=False))

phase("warm", True)
show("warm")

say("同配置 load_prev_bound -> "
    + str(layout_exact.load_prev_bound(PREFIX, CFG) is not None))
bad = json.loads(json.dumps(CFG))
bad["rows"] = bad["rows"] + 1
say("改配置 load_prev_bound -> "
    + str(layout_exact.load_prev_bound(PREFIX, bad)))

rec = json.load(open(BOUNDS, encoding="utf-8"))
rec["lb"] = 999
with open(BOUNDS, "w", encoding="utf-8") as f:
    json.dump(rec, f, ensure_ascii=False)
phase("lb_gt_best", True)
show("lb_gt_best")

# 4b) solutions.jsonl 被“更差的一轮”覆盖后，bounds.json 里的 best 仍能兜住上界
sol_path = PREFIX + ".solutions.jsonl"
sols = [json.loads(x) for x in open(sol_path, encoding="utf-8") if x.strip()]
for s in sols:
    s["cost"] = 9
with open(sol_path, "w", encoding="utf-8") as f:
    for s in sols:
        f.write(json.dumps(s, ensure_ascii=False) + "\n")
phase("stale_jsonl", True)
show("stale_jsonl")

os.remove(BOUNDS)
phase("ub_only", True)
show("ub_only")
