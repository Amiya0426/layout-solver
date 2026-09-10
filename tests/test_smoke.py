#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""冒烟测试：不需要 OR-Tools，直接跑通「启发式求解 -> 可视化落盘」主链路。

用法（在项目根目录执行）::

    python tests/test_smoke.py

为什么需要它：这个项目没有别的自动化测试，而“边求解边落盘 / 一次求解 = 一份
结果 / --only-missing 补图”这几条不变量很容易在重构时被悄悄破坏（历史上有过）。
本脚本用一个 3 秒的小例子把这几个不变量钉住。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
CONFIG = os.path.join(ROOT, "configs", "config.toy.json")
sys.path.insert(0, SRC)


class Report:
    def __init__(self):
        self.failures = []

    def check(self, name, cond, detail=""):
        print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if detail else ""))
        if not cond:
            self.failures.append(name)


def run(cmd):
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def main():
    rep = Report()
    out_dir = tempfile.mkdtemp(prefix="layout_smoke_")
    prefix = os.path.join(out_dir, "toy")
    try:
        # 1) 启发式求解：必须产出 JSONL + SVH/字符画
        r = run([sys.executable, os.path.join(SRC, "layout_solver.py"),
                 CONFIG, "--timeout", "3", "--output", prefix])
        rep.check("启发式求解退出码为 0", r.returncode == 0,
                  f"rc={r.returncode}")
        jl = prefix + ".solutions.jsonl"
        rep.check("生成了 solutions.jsonl", os.path.isfile(jl))

        sols = [json.loads(x) for x in open(jl, encoding="utf-8") if x.strip()]
        rep.check("至少找到 1 个可行解", len(sols) >= 1, f"实际 {len(sols)}")

        # 2) 每条记录都必须带 solution_index，且编号与文件名一一对应
        ok_idx = all("solution_index" in s for s in sols)
        rep.check("每条记录都带 solution_index", ok_idx)

        missing = []
        for i, s in enumerate(sols):
            n = s.get("solution_index", i)
            for ext in (".svg", ".txt"):
                f = f"{prefix}.sol{n}{ext}"
                if not os.path.isfile(f):
                    missing.append(f)
        rep.check("每个解都有 solN.svg / solN.txt", not missing,
                  f"缺 {len(missing)} 个")

        # 3) "一次求解 = 一份结果"：删掉一个可视化后 --only-missing 只补缺的
        victim = f"{prefix}.sol0.svg"
        if os.path.isfile(victim):
            os.remove(victim)
        r = run([sys.executable, os.path.join(SRC, "layout_viz.py"),
                 CONFIG, "--only-missing", "--output", prefix])
        rep.check("--only-missing 补图退出码为 0", r.returncode == 0)
        rep.check("缺失的 sol0.svg 被补回", os.path.isfile(victim))

        # 4) --prune 应清掉超出 JSONL 记录数的历史文件
        stale = f"{prefix}.sol999.txt"
        with open(stale, "w", encoding="utf-8") as f:
            f.write("stale\n")
        run([sys.executable, os.path.join(SRC, "layout_viz.py"),
             CONFIG, "--prune", "--output", prefix])
        rep.check("--prune 清掉了编号超出的历史文件", not os.path.isfile(stale))

        # 5) 目录划分约定：结果必须落在项目根的 result/ 下
        r = run([sys.executable, "-c",
                 "import sys; sys.path.insert(0, r'%s'); "
                 "import layout_viz; "
                 "print(layout_viz.default_output_prefix(r'%s'))"
                 % (SRC, CONFIG)])
        got = r.stdout.strip()
        expect = os.path.join(ROOT, "result", "toy", "toy")
        rep.check("default_output_prefix 指向项目 result/", got == expect,
                  f"得到 {got}")
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)

    print()
    if rep.failures:
        print(f"失败 {len(rep.failures)} 项: {rep.failures}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
