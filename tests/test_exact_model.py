#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""精确求解器自检：解必须满足「原始语义」，建模进度日志必须齐全。

用法（在项目根目录执行）::

    python tests/test_exact_model.py

需要 OR-Tools；没装就打印一条提示并以 0 退出（与其他测试保持一致：不因为
缺可选依赖而变红）。

为什么要它：`layout_exact.py` 的模型是「按格聚合」写出来的（禁带/共格/端点三处
等价改写），这些写法一旦写错，CP-SAT 依然会返回 OPTIMAL——只是答案是错的。
本脚本不看模型内部，只拿**原始语义**去核对它输出的解：

    1. 模块不出界、互不重叠；
    2. 每条 net 的路径四邻接连通、不自交、不穿过任何模块；
    3. 路径首尾正好是该模块端口的外侧格；
    4. 两条带共格时必须「一横一纵」直通，交叉格不能是端点；
    5. cost == 各 net 占用格数之和。

顺带把「建模阶段进度日志」也钉住：这些日志是排查“建模卡住”的唯一线索。
"""
import io
import contextlib
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

try:
    import layout_exact
except ImportError:                      # pragma: no cover
    print("跳过：需要 OR-Tools（pip install -r requirements.txt）")
    sys.exit(0)


class Report:
    def __init__(self):
        self.failures = []

    def check(self, name, cond, detail=""):
        print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if detail else ""))
        if not cond:
            self.failures.append(name)


D_VEC = {"N": (-1, 0), "S": (1, 0), "W": (0, -1), "E": (0, 1)}
D_ROT = {90: {"N": "E", "E": "S", "S": "W", "W": "N"},
         180: {"N": "S", "E": "W", "S": "N", "W": "E"},
         270: {"N": "W", "E": "N", "S": "E", "W": "S"}}


def _rot(ori, r, c, h, w):
    if ori == 0:
        return r, c
    if ori == 90:
        return c, h - 1 - r
    if ori == 180:
        return h - 1 - r, w - 1 - c
    return w - 1 - c, r


def validate(cfg, res, tag=""):
    """按原始语义核对一个解，返回问题列表（空 = 没问题）。"""
    bad = []
    rows, cols = cfg["rows"], cfg["cols"]
    nets = cfg["nets"]
    mods = {}
    for m in cfg.get("fixed", []) + cfg.get("movable", []):
        mods[m["id"]] = m

    place = {}
    for m in cfg.get("fixed", []):
        place[m["id"]] = (tuple(m["pos"]), 0)
    for mid, v in res["state"].items():
        place[mid] = (tuple(v["pos"]), v["orient"])
    for mid in mods:
        if mid not in place:
            bad.append(f"{tag} 模块 {mid} 没有位姿")

    # 1) 出界 / 重叠
    covered = {}
    for mid, (pos, ori) in place.items():
        m = mods[mid]
        w, h = (m["h"], m["w"]) if ori in (90, 270) else (m["w"], m["h"])
        r0, c0 = pos
        if r0 < 0 or c0 < 0 or r0 + h > rows or c0 + w > cols:
            bad.append(f"{tag} 模块 {mid} 出界 pos={pos} ori={ori}")
            continue
        for r in range(r0, r0 + h):
            for c in range(c0, c0 + w):
                if (r, c) in covered:
                    bad.append(f"{tag} 模块 {mid} 与 {covered[(r, c)]} 重叠 @{(r, c)}")
                covered[(r, c)] = mid

    def port_outside(mid, pid):
        m = mods[mid]
        ports = {p["id"]: p for p in m.get("ports", [])}
        if pid not in ports or mid not in place:
            return []
        p = ports[pid]
        pos, ori = place[mid]
        out = []
        for (rr, cc) in p["cells"]:
            if ori == 0:
                pr, pc, d = rr, cc, p["dir"]
            else:
                pr, pc = _rot(ori, rr, cc, m["h"], m["w"])
                d = D_ROT[ori][p["dir"]]
            dr, dc = D_VEC[d]
            out.append(((pos[0] + pr, pos[1] + pc),
                        (pos[0] + pr + dr, pos[1] + pc + dc)))
        return out

    # 2) 路径合法性
    paths = res["paths"]
    if len(paths) != len(nets):
        return bad + [f"{tag} 路径条数 {len(paths)} != net 数 {len(nets)}"]
    used = {}
    for ni, (net, path) in enumerate(zip(nets, paths)):
        if not path:
            bad.append(f"{tag} net {ni} 路径为空")
            continue
        for u, v in zip(path, path[1:]):
            if abs(u[0] - v[0]) + abs(u[1] - v[1]) != 1:
                bad.append(f"{tag} net {ni} 路径不连通 {u}->{v}")
        for cell in path:
            if cell in covered:
                bad.append(f"{tag} net {ni} 穿过模块 {covered[cell]} @{cell}")
        if len(set(path)) != len(path):
            bad.append(f"{tag} net {ni} 路径自交: {path}")
        s_out = [oc for _pc, oc in port_outside(net["from"], net.get("from_port", "OUT"))]
        d_out = [oc for _pc, oc in port_outside(net["to"], net.get("to_port", "IN"))]
        if path[0] not in s_out:
            bad.append(f"{tag} net {ni} 起点 {path[0]} 不是 {net['from']} 端口外侧 {s_out}")
        if path[-1] not in d_out:
            bad.append(f"{tag} net {ni} 终点 {path[-1]} 不是 {net['to']} 端口外侧 {d_out}")
        for cell in path:
            used.setdefault(cell, []).append(ni)

    # 3) 共格：只能一横一纵直通，且交叉格不能是端点
    for cell, owners in used.items():
        if len(owners) > 2:
            bad.append(f"{tag} {cell} 上挤了 {len(owners)} 条带 {owners}")
            continue
        if len(owners) < 2:
            continue

        def kind(ni):
            p = paths[ni]
            k = p.index(cell)
            if k == 0 or k == len(p) - 1:
                return "end"
            prv, nxt = p[k - 1], p[k + 1]
            if prv[0] == nxt[0] == cell[0] and abs(prv[1] - nxt[1]) == 2:
                return "h"
            if prv[1] == nxt[1] == cell[1] and abs(prv[0] - nxt[0]) == 2:
                return "v"
            return "turn"

        a, b = owners
        ka, kb = kind(a), kind(b)
        if {ka, kb} != {"h", "v"}:
            bad.append(f"{tag} {cell} 上 {a}/{b} 不是一横一纵直通（{ka}/{kb}）")

    # 4) cost 语义
    cost = sum(len(set(p)) for p in paths)
    if res.get("cost") != cost:
        bad.append(f"{tag} cost={res.get('cost')} 与路径占用格之和 {cost} 不符")
    return bad


BUILD_STEPS = [
    "读取配置:",
    "枚举候选位置",
    "候选位置:",
    "place 变量",
    "可用格点",
    "占用聚合变量",
    "端点格变量",
    "弧变量",
    "直通变量",
    "流量平衡约束",
    "同格共带/交叉约束",
    "模块禁带约束",
    "目标函数",
    "合法下界",
    "模型规模",
    "模型构建完成",
]


def run_case(rep, name, time_limit=30.0, expect_cost=None):
    path = os.path.join(ROOT, "configs", f"config.{name}.json")
    cfg = json.load(open(path, encoding="utf-8"))
    buf = io.StringIO()
    t0 = time.perf_counter()
    with contextlib.redirect_stdout(buf):
        res = layout_exact.solve_exact(cfg, time_limit=time_limit, workers=4)
    wall = time.perf_counter() - t0
    log = buf.getvalue()

    missing = [s for s in BUILD_STEPS if s not in log]
    rep.check(f"{name}: 建模进度日志齐全", not missing, f"缺 {missing}")
    order = [log.find(s) for s in BUILD_STEPS]
    rep.check(f"{name}: 进度日志按建模顺序出现", order == sorted(order))
    rep.check(f"{name}: 日志带耗时", bool(re.search(r"\(\d+\.\d+s, \+\d+\.\d+s\)", log)))
    rep.check(f"{name}: 报出模型规模", bool(re.search(r"模型规模 变量 \d+ 个 / 约束 \d+ 条", log)))

    rep.check(f"{name}: 找到解", res is not None, f"用时 {wall:.1f}s")
    if res is None:
        return None
    problems = validate(cfg, res, tag=name)
    rep.check(f"{name}: 解满足原始语义（不重叠/连通/不穿模块/共格直通/cost 一致）",
              not problems, "; ".join(problems[:4]))
    rep.check(f"{name}: 已证下界不低于「每条 net 至少 1 格」",
              res.get("lb") is not None and res["lb"] >= len(cfg["nets"]),
              f"lb={res.get('lb')} nets={len(cfg['nets'])}")
    rep.check(f"{name}: status=OPTIMAL 且 cost==已证下界", res["status"] == "OPTIMAL"
              and res["cost"] == res.get("lb"),
              f"status={res['status']} cost={res['cost']} lb={res.get('lb')}")
    if expect_cost is not None:
        rep.check(f"{name}: 最优值 == {expect_cost}", res["cost"] == expect_cost,
                  f"实际 {res['cost']}")
    print(f"       （{name}: cost={res['cost']} lb={res.get('lb')} 用时 {wall:.1f}s）")
    return res


def main():
    rep = Report()
    run_case(rep, "toy", time_limit=40.0, expect_cost=7)
    run_case(rep, "cross", time_limit=20.0, expect_cost=12)
    print()
    if rep.failures:
        print(f"失败 {len(rep.failures)} 项: {rep.failures}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
