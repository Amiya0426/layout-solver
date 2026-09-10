#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
精确求解器：模块布点 + 旋转 + 传送带布线（CP-SAT / OR-Tools）

用法:
    python layout_exact.py config.json [--time-limit 120] [--workers 8]

默认不启用 warm start。加上 `--hint` 后，会对同一个题目做“接着上一轮跑”：

    1. 把上一次留下的最好解作为**完整** CP-SAT Hint（每个变量都有提示，
       搜索开始前就能直接落一个 incumbent）；
    2. 用历史最好解压目标**上界**：`obj <= 上次最好解`（有现成可行解作见证）；
    3. 用历史**已证明下界**抬目标**下界**：`obj >= 上次证明的界`
       （该界是上一轮真正证明出来的，等价于把“0..L-1 无解”这个结论带到本轮）。

下界保存在 `<输出前缀>.bounds.json`，带 config/代码指纹；指纹对不上会自动
忽略（下界只对它被证明的那一份模型成立，绝不能跨配置复用）。
界文件不存在、或带 `--hint` 以外的方式运行时，行为与从前完全一致。

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
import hashlib
import json
import os
import sys
import time

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
    """CP-SAT 每报出一个可行解，就立刻交给 on_solution 输出。

    on_solution 只做“入队”，真正的渲染/写盘在后台线程里完成，
    因此输出不会拖慢、也不会改变搜索过程。

    **不设数量上限**：按发现顺序全部上报，编号始终是连续的 0..N-1；
    同一个 cost 只报一次（重复的解没有意义）。
    """

    def __init__(self, builder, on_solution=None):
        super().__init__()
        self.builder = builder
        self.count = 0
        self.seen_costs = set()
        self.on_solution = on_solution

    def OnSolutionCallback(self):
        try:
            sol = self.builder(self)
        except Exception:
            return
        c = sol["cost"]
        if c in self.seen_costs:
            return
        self.seen_costs.add(c)
        self.count += 1
        index = None
        if self.on_solution is not None:
            try:
                index = self.on_solution(sol)
            except Exception as e:  # noqa: BLE001
                print(f"[exact] 输出可行解失败: {e}", flush=True)
        tail = f" -> 已输出 sol{index}.svg / sol{index}.txt" if index is not None else ""
        print(
            f"[exact] 发现可行解 #{self.count}: "
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


def load_prev_best(solutions_file):
    """读取上次求解留下的 JSONL，返回传送带格数最小的那个记录（warm start 用）。

    参数可以是结果前缀（如 result/toy/toy）或直接的 .solutions.jsonl 路径，
    两种写法都接受；找不到文件 / 没有带 cost 的记录时返回 None——此时不施加
    任何 Hint，行为与从前完全一致。
    """
    if not solutions_file:
        return None
    if solutions_file.endswith(".jsonl"):
        candidates = [solutions_file]
    else:
        candidates = [solutions_file + ".solutions.jsonl", solutions_file]
    path = next((p for p in candidates if os.path.isfile(p)), None)
    if path is None:
        return None
    best = None
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:  # 半行/损坏行，忽略
                    continue
                c = rec.get("cost")
                if not isinstance(c, (int, float)):
                    continue
                if best is None or c < best.get("cost", float("inf")):
                    best = rec
    except OSError:
        return None
    return best


def _state_prefix(path):
    """把 .solutions.jsonl 路径还原成输出前缀（bounds.json 与它并列存放）。"""
    suffix = ".solutions.jsonl"
    if path and path.endswith(suffix):
        return path[: -len(suffix)]
    return path


def bound_state_path(prefix):
    return (prefix or "") + ".bounds.json"


def _file_sha256(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _cfg_fingerprint(cfg):
    """配置的内容指纹：只跟语义内容有关，与 JSON 排版无关。"""
    payload = json.dumps(cfg, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _code_fingerprint():
    """建模/施加目标界的那份代码的指纹（本文件）。"""
    return _file_sha256(os.path.abspath(__file__))


def _ortools_version():
    try:
        from importlib.metadata import version
        return version("ortools")
    except Exception:  # noqa: BLE001  仅用于记录，失败不影响求解
        return None


def load_prev_bound(prefix, cfg, quiet=False):
    """读取上次存下的**已证明**目标下界；只有 config/代码指纹都对上才认。

    下界是“上一轮证明出来的结论”，而这份结论只对它当时那一份模型成立：
    配置改了、建模代码改了，它就不再适用。所以这里做指纹校验，任何不一致
    都直接丢弃（宁可不复用，也不能把最优解剪掉——那种错误是静默的）。
    """
    path = bound_state_path(prefix)
    if not prefix or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict):
        return None
    lb = rec.get("lb")
    if not isinstance(lb, int) or isinstance(lb, bool) or lb <= 0:
        return None
    if rec.get("config_sha256") != _cfg_fingerprint(cfg):
        if not quiet:
            print("[exact] 忽略历史下界：配置已改动（指纹不一致）", flush=True)
        return None
    if rec.get("code_sha256") != _code_fingerprint():
        if not quiet:
            print("[exact] 忽略历史下界：建模代码已改动（指纹不一致）", flush=True)
        return None
    return rec


def save_prev_bound(prefix, cfg, lb, best, status, solve_sec, applied=None):
    """把**已证明**的目标下界落盘，供下次同配置求解复用。

    - 只增不减：新界比旧界小的话保留旧界（旧界同样有效，没必要退回去）；
    - 只有 lb > 0 才写；INFEASIBLE 的那一轮不写（那种“界”没有意义）。
    """
    if not prefix or lb is None:
        return None
    prev = load_prev_bound(prefix, cfg, quiet=True)   # 只认指纹一致的旧记录
    lb = int(lb)
    if lb <= 0:
        return None
    applied = list(applied or [])
    if prev is not None:
        prev_lb = prev.get("lb")
        if isinstance(prev_lb, int) and prev_lb >= lb:
            # 界来自旧记录，那么“当时加了什么约束”也以旧记录为准
            lb = prev_lb
            if prev.get("applied"):
                applied = list(prev["applied"])
        # 上界（best）也必须兜住：这一轮没找到解 / 找到的更差时，
        # 不能把旧记录里那个更好的 upper bound 抹掉。
        prev_best = prev.get("best")
        if isinstance(prev_best, int) and not isinstance(prev_best, bool):
            best = prev_best if best is None else min(int(best), prev_best)
    rec = {
        "lb": lb,
        "best": None if best is None else int(best),
        "status": status,
        "solve_sec": round(float(solve_sec), 3),
        "config_sha256": _cfg_fingerprint(cfg),
        "code_sha256": _code_fingerprint(),
        "ortools": _ortools_version(),
        "applied": applied,
        "when": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    path = bound_state_path(prefix)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as e:  # noqa: BLE001  落盘失败不影响本次求解结论
        print(f"[exact] 下界落盘失败: {e}", flush=True)
        return None
    return rec


def solve_exact(cfg, time_limit=120, workers=8, verbose=False,
                on_solution=None, hint_file=None, use_hint=False):
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
        cand_index[m.id] = {tuple(x["pos"]): i for i, x in enumerate(cs)}

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
    comp_vars = {}   # (ni, cell) -> {"hE"|"hW"|"vN"|"vS": BoolVar or None}
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
            comp_vars[(ni, (r, c))] = {"hE": hE, "hW": hW,
                                       "vN": vN, "vS": vS}
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

    # 同格最多两条带；若两条带共格，必须一条横直通 + 一条竖直通，
    # 且交叉格不能是任何一条带的端点，也不能有转弯。
    for cell in all_cells:
        model.Add(sum(use[(ni, cell)] for ni in range(len(nets))) <= 2)

    share_vars = {}   # (cell, a, b) -> “a、b 两条带共格”的 BoolVar
    for cell in all_cells:
        for a in range(len(nets)):
            for b in range(a + 1, len(nets)):
                both = model.NewBoolVar(f"share_{cell_idx[cell]}_{a}_{b}")
                share_vars[(cell, a, b)] = both
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

    # ---------- Warm start：历史最好解（完整 Hint）+ 目标上下界 ----------
    # 三样东西互相独立、依据不同：
    #   Hint    一个已知可行解，只是搜索起点（不是硬约束）；
    #   obj<=C  上界，依据是那份现成可行解——写错会 INFEASIBLE，会“吵”；
    #   obj>=L  下界，依据是上一轮**证明**出来的界——写错会静默剪掉最优解，
    #           所以只认指纹一致的历史记录（见 load_prev_bound）。
    prefix = _state_prefix(hint_file)
    if prefix:
        # 把界文件的 key 打到日志里：WebUI 服务端在“手动停止 / 进程被强杀”时
        # 拿不到子进程的返回值，只能靠这一行把 config/代码指纹带出来，
        # 才能替它把已经证明的下界落盘。
        print("[exact] bounds-key " + json.dumps({
            "prefix": os.path.abspath(prefix),
            "config_sha256": _cfg_fingerprint(cfg),
            "code_sha256": _code_fingerprint(),
        }, ensure_ascii=False), flush=True)
    prev_best = load_prev_best(hint_file) if (use_hint and hint_file) else None
    prev_bound = load_prev_bound(prefix, cfg) if (use_hint and hint_file) else None

    notes = []
    hint_cost = None
    if prev_best is not None and isinstance(prev_best.get("cost"), (int, float)):
        hint_cost = int(prev_best["cost"])

    # 上界取两者里**更紧**的那个：JSONL 里最好解的 cost、bounds.json 记的 best。
    # 两个数都对应“某个真实找到过的解”，所以上界写错只会 INFEASIBLE（会吵，
    # 不会静默出错）。有它兜底，即使 solutions.jsonl 被后来更差的一轮覆盖，
    # 之前那个好上界也不会丢。
    ub = hint_cost
    if prev_bound is not None and isinstance(prev_bound.get("best"), int):
        b = int(prev_bound["best"])
        ub = b if ub is None else min(ub, b)
    if ub is not None:
        model.Add(obj <= ub)
        notes.append(f"obj<={ub}")
    if ub is not None and hint_cost is not None and ub < hint_cost:
        print(f"[exact] 上界 {ub} 比 solutions.jsonl 里的最好解 cost={hint_cost} "
              f"更紧：Hint 会被 CP-SAT 拿去 repair（保住结构去找更优解）",
              flush=True)

    lb = None
    if prev_bound is not None:
        lb = int(prev_bound["lb"])
        if ub is not None and lb > ub:
            print(f"[exact] 忽略历史下界 {lb}：大于本次上界 {ub}，"
                  f"历史数据自相矛盾", flush=True)
            lb = None
        else:
            model.Add(obj >= lb)
            notes.append(f"obj>={lb}")
            if ub is not None and lb == ub:
                notes.append("上下界重合=已证该值最优")

    def apply_full_hint():
        """把历史最好解翻译成**每个变量**的取值，做成完整 Hint。

        全给上之后，CP-SAT 要么直接把它当成 incumbent（presolve 阶段、
        搜索都不用开始），要么发现它不可行而去 repair；只给一部分变量的话
        它得另起一个 “hint search” 子求解器补全，路径怎么连通这部分信息
        就白丢了（历史上就是这个问题）。
        返回 (统计 dict, None) 或 (None, 放弃原因)。
        """
        state = prev_best.get("state") or {}
        prev_paths = prev_best.get("paths") or []
        if len(prev_paths) < len(nets):
            return None, f"历史解只有 {len(prev_paths)} 条路径，当前有 {len(nets)} 条"

        # 1) 模块位姿：必须 (位置, 旋转) 精确对上，对不上就整个放弃
        exact_idx = {}
        for m in movable_mods:
            for i, cand in enumerate(cands[m.id]):
                exact_idx[(m.id, tuple(cand["pos"]), cand["orient"])] = i
        picks = {}
        for m in movable_mods:
            v = state.get(m.id)
            if not isinstance(v, dict):
                return None, f"历史解里没有模块 {m.id} 的位姿"
            pos = (int(v["row"]) - 1, int(v["col"]) - 1)
            orient = int(v.get("rotate", 0)) % 360
            i = exact_idx.get((m.id, pos, orient))
            if i is None:
                return None, (f"模块 {m.id} 的位姿 pos={pos} rotate={orient} "
                              f"在当前候选里不存在（配置改过？）")
            picks[m.id] = i

        # 2) 路径转 0-based 并校验首尾相接
        paths = []
        for ni in range(len(nets)):
            pts = prev_paths[ni]
            if not pts:
                return None, f"net {ni} 的历史路径为空"
            cells = [(int(r) - 1, int(c) - 1) for r, c in pts]
            for u, v in zip(cells, cells[1:]):
                if v not in neighbors.get(u, []):
                    return None, f"net {ni} 的历史路径不连通: {u} -> {v}"
            paths.append(cells)
        pos_in_path = [{cell: i for i, cell in enumerate(p)} for p in paths]
        on_path = [set(p) for p in paths]

        # 3) 端点：必须能在当前候选里精确命中（模块也用上面的精确位姿）
        src_hit, dst_hit = [], []
        for ni in range(len(nets)):
            fm = modules[nets[ni]["from"]]
            tm = modules[nets[ni]["to"]]
            ks = [k for k, (ci, oc) in enumerate(src_cands[ni])
                  if oc == paths[ni][0] and (fm.fixed or ci == picks.get(fm.id))]
            kd = [k for k, (ci, oc) in enumerate(dst_cands[ni])
                  if oc == paths[ni][-1] and (tm.fixed or ci == picks.get(tm.id))]
            if not ks or not kd:
                return None, (f"net {ni} 的端点 {paths[ni][0]} -> "
                              f"{paths[ni][-1]} 在候选里对不上")
            src_hit.append(ks[0])
            dst_hit.append(kd[0])

        n = 0
        # 4) 模块候选：选中的 1，其余 0
        for m in movable_mods + fixed_mods:
            for i in range(len(cands[m.id])):
                model.AddHint(pvar[(m.id, i)],
                              1 if (m.fixed or picks.get(m.id) == i) else 0)
                n += 1
        # 5) 端点候选：命中的 1，其余 0
        for ni in range(len(nets)):
            for k in range(len(p_src[ni])):
                model.AddHint(p_src[ni][k], 1 if k == src_hit[ni] else 0)
                n += 1
            for k in range(len(p_dst[ni])):
                model.AddHint(p_dst[ni][k], 1 if k == dst_hit[ni] else 0)
                n += 1
        # 6) use：路径格 1，其余 0
        for ni in range(len(nets)):
            for cell in all_cells:
                model.AddHint(use[(ni, cell)],
                              1 if cell in on_path[ni] else 0)
                n += 1
        # 7) arc：路径上相邻格对 1，其余 0
        arc_on = set()
        for ni in range(len(nets)):
            for u, v in zip(paths[ni], paths[ni][1:]):
                arc_on.add((ni, (u, v)))
        for key in arc:
            model.AddHint(arc[key], 1 if key in arc_on else 0)
            n += 1
        # 8) hcomp/vcomp：由“从哪进、往哪出”唯一确定
        for (ni, cell), cv in comp_vars.items():
            r, c = cell
            idx = pos_in_path[ni].get(cell)
            if idx is None:
                want = {k: False for k in cv}
            else:
                p = paths[ni]
                prv = p[idx - 1] if idx > 0 else None
                nxt = p[idx + 1] if idx < len(p) - 1 else None
                want = {
                    "hE": prv == (r, c - 1) and nxt == (r, c + 1),
                    "hW": prv == (r, c + 1) and nxt == (r, c - 1),
                    "vN": prv == (r - 1, c) and nxt == (r + 1, c),
                    "vS": prv == (r + 1, c) and nxt == (r - 1, c),
                }
            for name, var in cv.items():
                if var is None:
                    continue
                model.AddHint(var, 1 if want.get(name) else 0)
                n += 1
        # 9) share：两条带都占该格才为 1
        for (cell, a, b), var in share_vars.items():
            model.AddHint(var, 1 if (cell in on_path[a] and cell in on_path[b])
                          else 0)
            n += 1
        return {
            "cost": hint_cost,
            "modules": len(picks),
            "nets": len(nets),
            "cells": sum(len(p) for p in paths),
            "hints": n,
        }, None

    hint_stat = None
    if prev_best is not None:
        hint_stat, why = apply_full_hint()
        if hint_stat is None:
            print(f"[exact] 未施加 Hint：{why}（上下界约束仍照常生效）",
                  flush=True)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_workers = workers
    solver.parameters.log_search_progress = verbose

    hint_note = ""
    if notes:
        hint_note = "，warm start：" + " + ".join(notes)
    if hint_stat is not None:
        hint_note += (f"；完整 Hint cost={hint_stat['cost']}, "
                      f"模块 {hint_stat['modules']} 个, "
                      f"路径 {hint_stat['nets']} 条/{hint_stat['cells']} 格, "
                      f"共 hint {hint_stat['hints']} 个变量")
    print(
        f"[exact] 模型构建完成: {len(all_cells)} 个可用格点, "
        f"开始 CP-SAT 搜索 (time_limit={time_limit:.1f}s, workers={workers})"
        + hint_note,
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

    saver = SolutionSaver(make_solution, on_solution=on_solution)
    status = solver.Solve(model, saver)
    status_name = solver.StatusName(status)
    wall = solver.WallTime()

    if status == cp_model.UNKNOWN:
        # 时间上限到了、还没找到可行解：解没有，但**已证明的下界**是真成果，
        # 必须存下来（硬题目通常就是这个结局，下一轮 --hint 能接着用）。
        proven_lb = int(round(solver.BestObjectiveBound()))
        saved = save_prev_bound(prefix, cfg, proven_lb, None, status_name, wall,
                                applied=notes)
        print(f"[exact] 时间上限内没找到可行解（status=UNKNOWN）；"
              f"已证明下界 lb={proven_lb}"
              + (f"，已存入 {bound_state_path(prefix)}" if saved else ""),
              flush=True)
        print("[exact] 上一次的结果保持不变", flush=True)
        return None

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        print("[exact] 模型不可行，status =", status_name, flush=True)
        if notes:
            print(f"[exact] 本次施加了 warm start 约束（{' + '.join(notes)}）。"
                  f"历史最好解本身是一条可行解、历史下界上一轮已被证明，"
                  f"两者同时成立时模型不该无解——INFEASIBLE 通常说明 history "
                  f"与当前配置不一致（hint/bounds 文件陈旧或来自别的题目）。"
                  f"删掉这两个文件、或去掉 --hint 重跑即可。",
                  flush=True)
        print("[exact] 上一次的结果保持不变", flush=True)
        return None
    best = make_solution(solver)
    best["status"] = status_name
    best["solve_sec"] = wall
    best["num_solutions"] = saver.count
    best["fixed"] = {m.id: {"pos": m.fixed_pos, "orient": 0} for m in fixed_mods}
    best["modules"] = modules
    best["cfg"] = cfg

    # 把这一轮**证明出来**的下界落盘（下一轮 --hint 会把它当 obj>=L 用）。
    proven_lb = int(round(solver.BestObjectiveBound()))
    best["lb"] = proven_lb
    best["bound_file"] = save_prev_bound(prefix, cfg, proven_lb, best["cost"],
                                        status_name, wall, applied=notes)
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--time-limit", type=float, default=120)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--output", default=None,
                    help="结果 SVG/字符画文件名前缀（缺省用 config 文件名）")
    ap.add_argument("--hint", action="store_true",
                    help="warm start：用上次的最好解做完整 Hint，并同时施加"
                         "历史上下界（obj<=上次最好解、obj>=上次已证下界）"
                         "（默认关闭）")
    args = ap.parse_args()
    cfg = json.load(open(args.config, encoding="utf-8"))

    base = args.output or layout_viz.default_output_prefix(args.config)
    os.makedirs(os.path.dirname(os.path.abspath(base)) or ".", exist_ok=True)
    # 边求解边输出：每找到一个可行解立刻写 JSONL + solN.svg / solN.txt
    writer = layout_viz.SolutionWriter(cfg, base)
    print(f"[exact] 增量输出目录: {os.path.dirname(os.path.abspath(base))}"
          f"（找到可行解即写盘，数量不限）", flush=True)

    res = solve_exact(cfg, time_limit=args.time_limit, workers=args.workers,
                      verbose=args.verbose, on_solution=writer.submit,
                      hint_file=base, use_hint=args.hint)
    if res is not None and res["cost"] not in writer.costs:
        # 兜底：回调没来得及输出最终最优解时补一份
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
          f"最优传送带格数: {res['cost']}, 已证明下界: {res.get('lb')}")
    if res.get("lb") is not None and res["lb"] == res["cost"]:
        print("[exact] 上界=下界：已证明这就是全局最优（间隙 0）", flush=True)
    elif res.get("lb") is not None:
        print(f"[exact] 尚未证完最优：当前间隙 {res['cost'] - res['lb']}"
              f"（{res['lb']}..{res['cost']}）", flush=True)
    bf = res.get("bound_file")
    if bf:
        print(f"[exact] 已把证明出的下界 lb={bf['lb']} 存入 "
              f"{bound_state_path(base)}（下次 --hint 会用它抬下界）",
              flush=True)

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
