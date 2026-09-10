#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
精确求解器：模块布点 + 旋转 + 传送带布线（CP-SAT / OR-Tools）

用法:
    python layout_exact.py config.json [--time-limit 120] [--workers 8]

输入 JSON 格式与 layout_solver.py 完全一致：
    fixed / movable / nets / rows / cols

求解内容（全局最优）：
    1. 每个可动模块的唯一位置和旋转角；
    2. 每条 net 的传送带格位；
    3. 目标 = 最小化所有传送带占用的网格格总数。

约束：
    - 模块不出界、不重叠、不压固定模块；
    - 传送带不穿过模块；
    - 不同传送带不共格；
    - 相连模块之间必须至少有一个带格（紧贴无法布线）。
"""

import argparse
import json
import os
import sys
import time
from collections import deque

try:
    from ortools.sat.python import cp_model
except ImportError:
    print("需要 OR-Tools：pip install ortools")
    sys.exit(1)

import layout_viz

DIRV = {"N": (-1, 0), "S": (1, 0), "W": (0, -1), "E": (0, 1)}
ROT_CELL = {
    90:  lambda r, c, h, w: (c, h - 1 - r),
    180: lambda r, c, h, w: (h - 1 - r, w - 1 - c),
    270: lambda r, c, h, w: (w - 1 - c, r),
}
ROT_DIR = {
    90:  {"N": "E", "E": "S", "S": "W", "W": "N"},
    180: {"N": "S", "E": "W", "S": "N", "W": "E"},
    270: {"N": "W", "E": "N", "S": "E", "W": "S"},
}


class SolutionSaver(cp_model.CpSolverSolutionCallback):
    """CP-SAT 每报出一个（更优的）可行解，就立刻交给 on_solution 输出。

    on_solution 只做“入队”，真正的渲染/写盘在后台线程里完成，
    因此输出不会拖慢、也不会改变搜索过程。
    """

    def __init__(self, builder, max_solutions=200, on_solution=None):
        super().__init__()
        self.builder = builder
        self.solutions = []
        self.max_solutions = max_solutions
        self.seen_costs = set()
        self.on_solution = on_solution

    def OnSolutionCallback(self):
        # max_solutions = 0 表示不限，不封顶
        if self.max_solutions and len(self.solutions) >= self.max_solutions:
            return
        try:
            sol = self.builder(self)
        except Exception:
            return
        c = sol["cost"]
        if c in self.seen_costs:
            return
        self.seen_costs.add(c)
        self.solutions.append(sol)
        index = None
        if self.on_solution is not None:
            try:
                index = self.on_solution(sol)
            except Exception as e:  # noqa: BLE001
                print(f"[exact] 输出可行解失败: {e}", flush=True)
        tail = f" -> 已输出 sol{index}.svg / sol{index}.txt" if index is not None else ""
        print(
            f"[exact] 发现可行解 #{len(self.solutions)}: "
            f"传送带格数 {c}, 用时 {self.WallTime():.2f}s{tail}",
            flush=True,
        )


class Module:
    def __init__(self, mid, cfg, fixed_pos=None):
        self.id = mid
        self.w = cfg["w"]
        self.h = cfg["h"]
        self.fixed = fixed_pos is not None
        self.fixed_pos = fixed_pos
        self.rotatable = bool(cfg.get("rotatable", False))
        self.ports = {}
        for p in cfg.get("ports", []):
            self.ports[p["id"]] = {
                "cells": [tuple(c) for c in p["cells"]],
                "dir": p["dir"],
            }

    def occ(self, pos, orient):
        r0, c0 = pos
        cells = []
        for i in range(self.h):
            for j in range(self.w):
                if orient == 0:
                    rr, cc = i, j
                else:
                    rr, cc = ROT_CELL[orient](i, j, self.h, self.w)
                cells.append((r0 + rr, c0 + cc))
        return cells

    def port_outside(self, pos, orient, pid):
        p = self.ports.get(pid)
        if not p:
            return []
        r0, c0 = pos
        out = []
        for rr, cc in p["cells"]:
            if orient == 0:
                pr, pc, d = rr, cc, p["dir"]
            else:
                pr, pc = ROT_CELL[orient](rr, cc, self.h, self.w)
                d = ROT_DIR[orient][p["dir"]]
            dr, dc = DIRV[d]
            out.append(((r0 + pr, c0 + pc), (r0 + pr + dr, c0 + pc + dc)))
        return out


def solve_exact(cfg, time_limit=120, workers=8, verbose=False,
                on_solution=None, max_solutions=200):
    rows, cols = cfg["rows"], cfg["cols"]
    fixed_mods = []
    movable_mods = []
    for m in cfg.get("fixed", []):
        fixed_mods.append(Module(m["id"], m, fixed_pos=tuple(m["pos"])))
    for m in cfg.get("movable", []):
        movable_mods.append(Module(m["id"], m))
    modules = {m.id: m for m in fixed_mods + movable_mods}
    nets = cfg["nets"]
    print(
        f"[exact] 读取配置: {rows}x{cols} 画布, 固定模块 {len(fixed_mods)} 个, "
        f"可动模块 {len(movable_mods)} 个, 连接 {len(nets)} 条",
        flush=True,
    )

    fixed_cells = set()
    for m in fixed_mods:
        fixed_cells.update(m.occ(m.fixed_pos, 0))
    fixed_occ = {}
    for m in fixed_mods:
        fixed_occ[m.id] = m.occ(m.fixed_pos, 0)

    # ---------- 候选位置 ----------
    cands = {}          # mid -> [{"pos","orient","cells","port_out":{pid:[...]}}]
    # 候选索引：按 (位置, 旋转) 建索引。
    # 原版只按位置索引，旋转模块时同一位置可能有多个候选。
    cand_index = {}
    for m in fixed_mods:
        cands[m.id] = [{
            "pos": m.fixed_pos,
            "orient": 0,
            "cells": m.occ(m.fixed_pos, 0),
            "port_out": {pid: m.port_outside(m.fixed_pos, 0, pid) for pid in m.ports},
        }]
    for m in movable_mods:
        cs = []
        orients = [0, 90, 180, 270] if m.rotatable else [0]
        for o in orients:
            # occ 结果天然给出旋转后的包围盒范围
            cells = m.occ((0, 0), o)
            max_r = rows - (max(c[0] for c in cells) + 1)
            max_c = cols - (max(c[1] for c in cells) + 1)
            if max_r < 0 or max_c < 0:
                continue
            for r in range(max_r + 1):
                for c in range(max_c + 1):
                    occ = [(r + rr, c + cc) for rr, cc in cells]
                    if fixed_cells & set(occ):
                        continue
                    po = {
                        pid: m.port_outside((r, c), o, pid)
                        for pid in m.ports
                    }
                    cs.append({"pos": (r, c), "orient": o, "cells": occ, "port_out": po})
        cands[m.id] = cs
        cand_index[m.id] = {(tuple(x["pos"]), x["orient"]): i
                           for i, x in enumerate(cs)}

    # 如果某个模块没有候选，直接不可行
    for m in movable_mods:
        if not cands[m.id]:
            print(f"[exact] 模块 {m.id} 无任何合法候选位置", flush=True)
            return None
    print(
        "[exact] 候选位置: "
        + ", ".join(f"{m.id}={len(cands[m.id])}" for m in movable_mods),
        flush=True,
    )
    print("[exact] 正在构建 CP-SAT 模型...", flush=True)

    model = cp_model.CpModel()

    # ---------- place 变量 ----------
    pvar = {}   # (mid, cand_i) -> BoolVar
    for m in fixed_mods + movable_mods:
        for i in range(len(cands[m.id])):
            pvar[(m.id, i)] = model.NewBoolVar(f"place_{m.id}_{i}")
    for m in movable_mods:
        model.AddExactlyOne(pvar[(m.id, i)] for i in range(len(cands[m.id])))

    # 固定模块默认选中（不加 exactly one 也行，但约束路径需要 place 索引）
    for m in fixed_mods:
        model.Add(pvar[(m.id, 0)] == 1)

    # ---------- 非重叠 ----------
    # 每个格位被可动模块占据的次数 <= 1
    occ_by_cell = {}
    for m in movable_mods:
        for i, c in enumerate(cands[m.id]):
            for cell in c["cells"]:
                occ_by_cell.setdefault(cell, []).append((m.id, i))
    for cell, lst in occ_by_cell.items():
        model.Add(sum(pvar[key] for key in lst) <= 1)

    # ---------- 网格格点与固定禁区 ----------
    all_cells = [(r, c) for r in range(rows) for c in range(cols) if (r, c) not in fixed_cells]
    cell_idx = {v: i for i, v in enumerate(all_cells)}

    # 端点候选
    # net_source[n] = [(mid, port_idx 外部格, 需要的候选 place 键 or None, cand 可选范围)]
    src_cands = []
    dst_cands = []
    for net in nets:
        fm = modules[net["from"]]
        tm = modules[net["to"]]
        fpid = net.get("from_port", "OUT")
        tpid = net.get("to_port", "IN")
        # 收集该模块所有候选下端口外侧可用的格
        s = []
        d = []
        for mid in (net["from"], net["to"]):
            is_fixed = modules[mid].fixed
            for ci, cand in enumerate(cands[mid]):
                for pid in (fpid if mid == net["from"] else tpid,):
                    if mid == net["from"]:
                        for (_pc, oc) in cand["port_out"].get(pid, []):
                            if oc in cell_idx:
                                s.append((ci, oc))
                    else:
                        for (_pc, oc) in cand["port_out"].get(pid, []):
                            if oc in cell_idx:
                                d.append((ci, oc))
        src_cands.append(s)
        dst_cands.append(d)

    # 若某 net 没有可用端口，直接不可行
    for ni, (s, d) in enumerate(zip(src_cands, dst_cands)):
        if not s or not d:
            print(f"[exact] net {ni} 无可用端口格", flush=True)
            return None

    # ---------- 静态最短路下界 ----------
    # 这里故意只把“固定模块”当障碍，把可动模块当成空白。
    # 因而这是原问题的松弛：不会错误剪掉真正的最优解。
    # 相比单纯 Manhattan 距离，BFS 还能正确处理固定模块造成的墙/通道。
    def bfs_distances(start):
        dist = {start: 0}
        q = deque([start])
        while q:
            cur = q.popleft()
            for nb in neighbors_relaxed[cur]:
                if nb not in dist:
                    dist[nb] = dist[cur] + 1
                    q.append(nb)
        return dist

    neighbors_relaxed = {}
    for r, c in all_cells:
        ns = []
        for dr, dc in DIRV.values():
            nb = (r + dr, c + dc)
            if nb in cell_idx:
                ns.append(nb)
        neighbors_relaxed[(r, c)] = ns

    # 每个端点候选给出一个安全的 net 长度下界。
    # 例如选定 source 后，至少要走到某个合法 destination；
    # endpoint 相同的特殊情况仍至少占 1 个 conveyor cell。
    src_lb = []
    dst_lb = []
    for ni, (s, d) in enumerate(zip(src_cands, dst_cands)):
        dst_cells = {oc for _ci, oc in d}
        src_cells = {oc for _ci, oc in s}

        # 缓存同一端点格的 BFS，避免同格重复计算。
        bfs_cache = {}
        for oc in src_cells:
            bfs_cache[oc] = bfs_distances(oc)

        s_lbs = []
        for _ci, oc in s:
            ds = [bfs_cache[oc].get(dc) for dc in dst_cells
                  if dc in bfs_cache[oc]]
            # 无法通过固定障碍到达任何目标格，则这个 source 候选
            # 在任何 movable module 摆法下都不可能形成连接，可直接禁用。
            if not ds:
                s_lbs.append(None)
            else:
                s_lbs.append(max(1, min(ds)))

        # destination 侧同理。
        for oc in dst_cells:
            if oc not in bfs_cache:
                bfs_cache[oc] = bfs_distances(oc)
        d_lbs = []
        for _ci, oc in d:
            ds = [bfs_cache[oc].get(sc) for sc in src_cells
                  if sc in bfs_cache[oc]]
            d_lbs.append(max(1, min(ds)) if ds else None)

        src_lb.append(s_lbs)
        dst_lb.append(d_lbs)

    # ---------- 端点变量 ----------
    p_src = []   # [net][k] Bool
    p_dst = []
    for ni in range(len(nets)):
        p_src.append([model.NewBoolVar(f"src_{ni}_{k}") for k in range(len(src_cands[ni]))])
        p_dst.append([model.NewBoolVar(f"dst_{ni}_{k}") for k in range(len(dst_cands[ni]))])
        model.AddExactlyOne(p_src[ni])
        model.AddExactlyOne(p_dst[ni])
        srcm = modules[nets[ni]["from"]]
        dstm = modules[nets[ni]["to"]]
        for k, (ci, _oc) in enumerate(src_cands[ni]):
            if not srcm.fixed:
                model.Add(p_src[ni][k] <= pvar[(srcm.id, ci)])
        for k, (ci, _oc) in enumerate(dst_cands[ni]):
            if not dstm.fixed:
                model.Add(p_dst[ni][k] <= pvar[(dstm.id, ci)])

        # 静态最短路不可达的端点候选直接禁用；可达候选给 net cost
        # 加一个安全下界。这样 CP-SAT 在优化过程中能更早抬高 lower bound。
        for k, lb in enumerate(src_lb[ni]):
            if lb is None:
                model.Add(p_src[ni][k] == 0)
        for k, lb in enumerate(dst_lb[ni]):
            if lb is None:
                model.Add(p_dst[ni][k] == 0)

    # ---------- 弧与 use 变量 ----------
    neighbors = {}
    for (r, c) in all_cells:
        ns = []
        for dr, dc in DIRV.values():
            nb = (r + dr, c + dc)
            if nb in cell_idx:
                ns.append(nb)
        neighbors[(r, c)] = ns

    arc = {}   # (net, (u,v))
    use = {}   # (net, cell)
    for ni in range(len(nets)):
        for cell in all_cells:
            use[(ni, cell)] = model.NewBoolVar(f"use_{ni}_{cell_idx[cell]}")
        for cell in all_cells:
            for nb in neighbors[cell]:
                arc[(ni, (cell, nb))] = model.NewBoolVar(
                    f"arc_{ni}_{cell_idx[cell]}_{cell_idx[nb]}")

    # 每个 net 在每个格点是否为“笔直横向/纵向穿过”
    # hE/hW: 横向由西向东 / 由东向西；vN/vS: 纵向由北向南 / 由南向北
    hcomp = {}
    vcomp = {}
    for ni in range(len(nets)):
        for (r, c) in all_cells:
            wb = (r, c - 1) if c - 1 >= 0 and (r, c - 1) in cell_idx else None
            eb = (r, c + 1) if c + 1 < cols and (r, c + 1) in cell_idx else None
            nb_ = (r - 1, c) if r - 1 >= 0 and (r - 1, c) in cell_idx else None
            sb = (r + 1, c) if r + 1 < rows and (r + 1, c) in cell_idx else None

            def make(name, ia, oa):
                if ia is None or oa is None:
                    return None
                b = model.NewBoolVar(
                    f"{name}_{ni}_{cell_idx[(r, c)]}")
                # b = arc(in) AND arc(out)
                model.Add(b <= arc[(ni, (ia, (r, c)))])
                model.Add(b <= arc[(ni, ((r, c), oa))])
                model.Add(b >= arc[(ni, (ia, (r, c)))] +
                          arc[(ni, ((r, c), oa))] - 1)
                return b

            hE = make("hE", wb, eb)
            hW = make("hW", eb, wb)
            vN = make("vN", nb_, sb)
            vS = make("vS", sb, nb_)
            comps = [b for b in (hE, hW, vN, vS) if b is not None]
            # 一个简单路径在同一格最多只能有一种“直通”方向
            if comps:
                model.Add(sum(comps) <= 1)
            hcomp[(ni, (r, c))] = sum(b for b in (hE, hW) if b is not None)
            vcomp[(ni, (r, c))] = sum(b for b in (vN, vS) if b is not None)

    # 端点 cell 对 use 的贡献
    src_at_cell = {ni: {} for ni in range(len(nets))}
    dst_at_cell = {ni: {} for ni in range(len(nets))}
    for ni in range(len(nets)):
        for cell in all_cells:
            src_at_cell[ni][cell] = []
            dst_at_cell[ni][cell] = []
    for ni in range(len(nets)):
        for k, (_ci, oc) in enumerate(src_cands[ni]):
            src_at_cell[ni][oc].append(k)
        for k, (_ci, oc) in enumerate(dst_cands[ni]):
            dst_at_cell[ni][oc].append(k)

    # 每个 net 在每个格点的流量平衡
    for ni in range(len(nets)):
        for cell in all_cells:
            outs = [arc[(ni, (cell, nb))] for nb in neighbors[cell]]
            ins = [arc[(ni, (nb, cell))] for nb in neighbors[cell]]
            so = sum(p_src[ni][k] for k in src_at_cell[ni][cell])
            do = sum(p_dst[ni][k] for k in dst_at_cell[ni][cell])
            model.Add(sum(outs) - sum(ins) == so - do)
            # 简单路径：每个点至多一进一出
            model.Add(sum(outs) <= 1)
            model.Add(sum(ins) <= 1)

            # 禁止 u<->v 的 2-cycle。原模型允许这种冗余回路；它不会帮助
            # 最小化 use，但会给 CP-SAT 增加大量无意义的可行分支。
            for nb in neighbors[cell]:
                if cell < nb:
                    model.Add(arc[(ni, (cell, nb))] +
                              arc[(ni, (nb, cell))] <= 1)

            expr = sum(outs) + sum(ins) + so + do
            model.Add(use[(ni, cell)] <= expr)
            model.Add(expr >= use[(ni, cell)])
            # 若端点被选中，该格必须属于该 net
            model.Add(use[(ni, cell)] >= so)
            model.Add(use[(ni, cell)] >= do)
            for a in outs:
                model.Add(use[(ni, cell)] >= a)
            for a in ins:
                model.Add(use[(ni, cell)] >= a)

    # 每条 net 的占用格数下界。
    # 注意：只对“选中的端点”施加下界，不对所有候选统一加，避免错误剪枝。
    for ni in range(len(nets)):
        net_cost = sum(use[(ni, cell)] for cell in all_cells)
        for k, lb in enumerate(src_lb[ni]):
            if lb is not None:
                model.Add(net_cost >= lb * p_src[ni][k])
        for k, lb in enumerate(dst_lb[ni]):
            if lb is not None:
                model.Add(net_cost >= lb * p_dst[ni][k])

    # 同格最多两条带；若两条带共格，必须一条横直通 + 一条竖直通，
    # 且交叉格不能是任何一条带的端点，也不能有转弯。
    for cell in all_cells:
        model.Add(sum(use[(ni, cell)] for ni in range(len(nets))) <= 2)

    for cell in all_cells:
        for a in range(len(nets)):
            for b in range(a + 1, len(nets)):
                both = model.NewBoolVar(f"share_{cell_idx[cell]}_{a}_{b}")
                model.Add(both >= use[(a, cell)] + use[(b, cell)] - 1)
                model.Add(both <= use[(a, cell)])
                model.Add(both <= use[(b, cell)])
                # a、b 在该格必须笔直（一个横向或一个纵向）
                model.Add(hcomp[(a, cell)] + vcomp[(a, cell)] == 1).OnlyEnforceIf(both)
                model.Add(hcomp[(b, cell)] + vcomp[(b, cell)] == 1).OnlyEnforceIf(both)
                # 必须恰好一条横、一条纵
                model.Add(hcomp[(a, cell)] + hcomp[(b, cell)] == 1).OnlyEnforceIf(both)
                model.Add(vcomp[(a, cell)] + vcomp[(b, cell)] == 1).OnlyEnforceIf(both)
                # 交叉格不是端点
                so_a = sum(p_src[a][k] for k in src_at_cell[a][cell])
                do_a = sum(p_dst[a][k] for k in dst_at_cell[a][cell])
                so_b = sum(p_src[b][k] for k in src_at_cell[b][cell])
                do_b = sum(p_dst[b][k] for k in dst_at_cell[b][cell])
                model.Add(so_a + do_a == 0).OnlyEnforceIf(both)
                model.Add(so_b + do_b == 0).OnlyEnforceIf(both)

    # 模块占用格不能被带穿过
    for m in movable_mods + fixed_mods:
        for i, cand in enumerate(cands[m.id]):
            for cell in set(cand["cells"]):
                if cell not in cell_idx:
                    continue
                for ni in range(len(nets)):
                    model.Add(pvar[(m.id, i)] + use[(ni, cell)] <= 1)

    # 目标：最小化传送带占用格总数
    obj = sum(use[(ni, cell)] for ni in range(len(nets)) for cell in all_cells)
    model.Minimize(obj)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_workers = workers
    solver.parameters.log_search_progress = verbose

    # 搜索参数：保持 CP-SAT 默认的完整求解能力，同时打开更积极的
    # presolve / probing / symmetry。不会牺牲“exact”性质，只影响搜索速度。
    solver.parameters.cp_model_presolve = True
    solver.parameters.cp_model_probing_level = 2
    solver.parameters.symmetry_level = 2
    solver.parameters.use_lns = True
    solver.parameters.randomize_search = True
    total_lb = 0
    for ni in range(len(nets)):
        # 端点下界的全局安全下界：该 net 至少需要其所有合法 source/dest
        # 候选中可能达到的最小值。
        lbs = [x for x in src_lb[ni] + dst_lb[ni] if x is not None]
        if not lbs:
            print(f"[exact] net {ni} 在固定障碍下无可达端点组合", flush=True)
            return None
        total_lb += min(lbs)

    print(
        f"[exact] 模型构建完成: {len(all_cells)} 个可用格点, "
        f"静态最短路总下界 >= {total_lb}, "
        f"开始 CP-SAT 搜索 (time_limit={time_limit:.1f}s, workers={workers})",
        flush=True,
    )

    def make_solution(slv):
        state = {}
        for m in movable_mods:
            for i in range(len(cands[m.id])):
                if slv.Value(pvar[(m.id, i)]) == 1:
                    state[m.id] = {"pos": cands[m.id][i]["pos"],
                                   "orient": cands[m.id][i]["orient"]}
                    break
        paths = []
        for ni, _net in enumerate(nets):
            src_oc = dst_oc = None
            for k, (_ci, oc) in enumerate(src_cands[ni]):
                if slv.Value(p_src[ni][k]) == 1:
                    src_oc = oc
                    break
            for k, (_ci, oc) in enumerate(dst_cands[ni]):
                if slv.Value(p_dst[ni][k]) == 1:
                    dst_oc = oc
                    break
            if src_oc == dst_oc:
                path = [src_oc]
            else:
                cur = src_oc
                path = [cur]
                for _ in range(len(all_cells) + 1):
                    nxt = None
                    for nb in neighbors.get(cur, []):
                        if slv.Value(arc[(ni, (cur, nb))]) == 1:
                            nxt = nb
                            break
                    if nxt is None:
                        break
                    path.append(nxt)
                    cur = nxt
                    if cur == dst_oc:
                        break
            paths.append(path)
        return {
            "state": state,
            "paths": paths,
            "cost": int(slv.ObjectiveValue()),
        }

    saver = SolutionSaver(make_solution, max_solutions=max_solutions,
                          on_solution=on_solution)
    status = solver.Solve(model, saver)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        print("[exact] 无可行解，status =", solver.StatusName(status), flush=True)
        return None
    best = make_solution(solver)
    best["status"] = solver.StatusName(status)
    best["solve_sec"] = solver.WallTime()
    best["all_solutions"] = saver.solutions
    best["fixed"] = {m.id: {"pos": m.fixed_pos, "orient": 0} for m in fixed_mods}
    best["modules"] = modules
    best["cfg"] = cfg
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--time-limit", type=float, default=120)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--output", default=None,
                    help="结果 SVG/字符画文件名前缀（缺省用 config 文件名）")
    ap.add_argument("--max-solutions", type=int, default=200,
                    help="最多输出多少个可行解（0 = 不限）")
    args = ap.parse_args()
    cfg = json.load(open(args.config, encoding="utf-8"))

    base = args.output or layout_viz.default_output_prefix(args.config)
    os.makedirs(os.path.dirname(os.path.abspath(base)) or ".", exist_ok=True)
    # 边求解边输出：每找到一个可行解立刻写 JSONL + solN.svg / solN.txt
    writer = layout_viz.SolutionWriter(cfg, base,
                                       max_solutions=args.max_solutions)
    print(f"[exact] 增量输出目录: {os.path.dirname(os.path.abspath(base))}"
          f"（找到可行解即写盘，最多 {args.max_solutions or '不限'} 个）",
          flush=True)

    res = solve_exact(cfg, time_limit=args.time_limit, workers=args.workers,
                      verbose=args.verbose, on_solution=writer.submit,
                      max_solutions=args.max_solutions)
    if res is not None and res["cost"] not in writer.costs:
        # 兜底：回调因上限/异常没来得及输出最终最优解时补一份
        writer.submit(res)
    writer.close()
    print(f"共记录 {writer.count} 个搜索过程中的可行解 -> "
          f"{writer.solution_file}", flush=True)
    if writer.count == 0:
        print("[exact] 本次没有找到可行解，上一次的结果保持不变",
              flush=True)
    for err in writer.errors:
        print(f"[exact] 输出告警: {err}", flush=True)

    if not res:
        return
    print(f"\nstatus: {res['status']}, 用时 {res['solve_sec']:.2f}s, "
          f"最优传送带格数: {res['cost']}")

    print("模块位置：")
    for mid, v in res["state"].items():
        print(f"  {mid}: 行 {v['pos'][0] + 1}, 列 {v['pos'][1] + 1}, "
              f"旋转 {v['orient']}°")
    print("传送带路径：")
    for i, net in enumerate(cfg["nets"]):
        cells = [(r + 1, c + 1) for r, c in res["paths"][i]]
        print(f"  {net['from']}->{net['to']}: {cells}")

    placements = {}
    for mid, v in res["fixed"].items():
        placements[mid] = (tuple(v["pos"]), 0)
    for mid, v in res["state"].items():
        placements[mid] = (tuple(v["pos"]), v["orient"])
    svg = base + ".svg"
    layout_viz.save_svg(svg, cfg, res["modules"], placements, res["paths"])
    print("SVG 可视化 ->", svg)
    print("\n字符画预览（模块/带格/交叉点）：")
    print(layout_viz.render_ascii(cfg, res["modules"], placements, res["paths"]))


if __name__ == "__main__":
    main()
