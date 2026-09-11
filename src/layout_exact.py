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
    - 一格最多走两条带；两条带共格时必须是“一横一纵”的十字直通，
      且交叉格不能是任何一条带的端点、也不能有转弯；
    - 相连模块之间必须至少有一个带格（紧贴无法布线）。

建模规模（大题目上最容易卡住的地方）：
    禁带/共格/端点三处都按**格**聚合，而不是按「模块候选 x 格 x net」或
    「每格 x 每对 net」展开。以 27x30 / 33 可动模块 / 47 连接的
    configs/config.gudi.json 为例，模型从 208 万变量 / 6290 万约束降到
    47 万变量 / 108 万约束，建模耗时约 26s、内存约 1GB。
    建模分步进度（每步的累计/本步耗时、规模、内存）都会打到日志里，
    求解前的 CP-SAT presolve 静默期也有明确提示。
"""

import argparse
import hashlib
import json
import os
import re
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


class BuildLog:
    """建模阶段的进度日志：每条都带「累计」和「距上一步」的耗时。

    为什么非要有它：大题目（27x30 / 33 可动模块 / 47 连接）光是建模就要
    几十秒甚至几分钟，以前这段时间一行日志都没有，看起来和卡死没区别。
    把建模拆成有名字的若干步之后，日志停在哪一步、每步各花了多久一目了然。

    计时基准在每次 solve_exact 开头重置（BuildLog() 构造那一刻），
    所以「累计」= 本次求解已经过去的时间，与 CP-SAT 的 WallTime 无关。
    """

    def __init__(self):
        self.t0 = time.perf_counter()
        self.last = self.t0

    def __call__(self, msg):
        now = time.perf_counter()
        print(f"[exact] {msg} ({now - self.t0:.1f}s, +{now - self.last:.1f}s)",
              flush=True)
        self.last = now
        return now - self.t0


def _rss_mb():
    """当前进程常驻内存（MB）；取不到就返回 None——只是给人看的诊断信息。"""
    try:                                    # Linux / macOS
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux 单位是 KB，macOS 是字节
        return rss / (1024.0 * 1024.0) if sys.platform == "darwin" else rss / 1024.0
    except Exception:                       # noqa: BLE001
        pass
    try:                                    # Windows：kernel32.K32GetProcessMemoryInfo
        import ctypes
        from ctypes import wintypes

        class _PMC(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.GetCurrentProcess.argtypes = []
        k32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
        k32.K32GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(_PMC), wintypes.DWORD]
        pmc = _PMC()
        pmc.cb = ctypes.sizeof(pmc)
        if k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(),
                                       ctypes.byref(pmc), pmc.cb):
            return pmc.WorkingSetSize / (1024.0 * 1024.0)
    except Exception:                       # noqa: BLE001
        pass
    return None


def _model_size_text(model):
    """从 CpModel.ModelStats() 里抠出「变量 N 个 / 约束 M 条」，只用于日志。"""
    try:
        stats = model.ModelStats()
    except Exception:                       # noqa: BLE001
        return ""
    n_vars = None
    n_cons = 0
    for line in stats.splitlines():
        line = line.strip()
        m = re.match(r"^#Variables:\s*([\d'\u2019]+)", line)
        if m:
            n_vars = int(re.sub(r"\D", "", m.group(1)))
            continue
        m = re.match(r"^#k\w+:\s*([\d'\u2019]+)", line)
        if m:
            n_cons += int(re.sub(r"\D", "", m.group(1)))
    if n_vars is None:
        return ""
    return f"变量 {n_vars} 个 / 约束 {n_cons} 条"


def _mem_text():
    mb = _rss_mb()
    return f"，进程内存 {mb / 1024.0:.2f}GB" if mb else ""


def legal_length_bounds(nets, src_end, dst_end):
    """逐条 net 的**合法**路径长度下界（占用格数），返回 [下界...]。

    两条依据都是原始语义的直接推论：

    1. 每条 net 恰好一个起点格（`Σ_cell 起点变量 == 1`），而 `use >= 起点变量`，
       所以它至少占 1 格；
    2. 路径四邻接连通，从起点格走到终点格至少要 `曼哈顿距离` 步，
       所以占用格数 >= 曼哈顿距离 + 1（有模块挡路只会更长）。
       起点/终点都只在「候选可能落到的那组格」里选，取这组格的最小距离即可。

    写进模型不会切掉任何可行解，只是把求解器要自己爬很久的界直接告诉它
    （见 solve_exact 里的「合法冗余下界」）。空 nets 时返回 []。
    """
    out = []
    for ni in range(len(nets)):
        s_cells = list(src_end[ni]) if ni < len(src_end) else []
        d_cells = list(dst_end[ni]) if ni < len(dst_end) else []
        if not s_cells or not d_cells:
            out.append(1)
            continue
        dmin = min(abs(a[0] - b[0]) + abs(a[1] - b[1])
                   for a in s_cells for b in d_cells)
        out.append(dmin + 1)
    return out


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


class RelaxRecorder(cp_model.CpSolverSolutionCallback):
    """两阶段求解的**阶段1**（松弛模型）用：只记下最有用的那个松弛解，不写盘。

    松弛模型丢掉了「共格只能一横一纵十字直通」这层细则，所以它的解可能不合法：
    既不能当结果输出，也不能当目标上界（它的 cost 可能低于真正的最优值）。
    它只有两个用途：

      1. 给阶段2 当 Hint ——CP-SAT 会拿它去 repair，比从零搜索强得多；
      2. 提供**合法下界** ——松弛问题的最优值一定 <= 原问题最优值。

    挑哪个解留给阶段2？**按违规格数优先、再按 cost**：最便宜的那个解往往在
    很多格上叠了带（违规多），拿去 repair 反而更难；违规最少（最好是 0）的
    骨架才是好 Hint。违规数由 violations 回调算（传 None 就退化成只按 cost）。

    找到合法解也不提前收工：阶段1 的预算本来就只有总时间的 35%，继续跑能把
    松弛问题的界证得更紧（小题目上往往能直接证到最优值），阶段2 拿这个界
    几乎立刻就能收尾——实测 toy 因此从 9.2s 回到 5.7s。
    """

    def __init__(self, builder, violations=None):
        super().__init__()
        self.builder = builder
        self.violations = violations
        self.best = None
        self.best_viol = None       # int：违规格数
        self.best_vac = []          # 违规明细（日志用）
        self.count = 0

    def OnSolutionCallback(self):
        try:
            sol = self.builder(self)
        except Exception:  # noqa: BLE001  取不出解就跳过，不影响搜索
            return
        self.count += 1
        vac = self.violations(self) if self.violations is not None else []
        v = len(vac)
        if self.best is None or (v, sol["cost"]) < (self.best_viol, self.best["cost"]):
            self.best = sol
            self.best_viol = v
            self.best_vac = vac


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


def save_prev_bound(prefix, cfg, lb, best, status, solve_sec, applied=None,
                    lb_relax=None):
    """把**已证明**的目标下界落盘，供下次同配置求解复用。

    - 只增不减：新界比旧界小的话保留旧界（旧界同样有效，没必要退回去）；
    - 只有 lb > 0 才写；INFEASIBLE 的那一轮不写（那种“界”没有意义）。
    - `lb_relax` 是两阶段里**阶段1 松弛模型**证出的下界，单独存一个字段：
      它对完整模型同样成立（松弛问题的最优值 <= 原问题最优值），但来源不同，
      分开记便于排查“这个界到底是谁给的”。
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
        # 松弛下界同样只增不减
        prev_relax = prev.get("lb_relax")
        if isinstance(prev_relax, int) and not isinstance(prev_relax, bool):
            lb_relax = (prev_relax if lb_relax is None
                        else max(int(lb_relax), prev_relax))
    rec = {
        "lb": lb,
        "lb_relax": None if lb_relax is None else int(lb_relax),
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
                on_solution=None, hint_file=None, use_hint=False,
                relax_phase=True):
    blog = BuildLog()   # 建模进度日志：每条都带累计/本步耗时
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

    # ---------- 候选位置 ----------
    blog("枚举候选位置（可动模块 x 旋转角 x 不压固定块的落点）")
    cands = {}          # mid -> [{"pos","orient","cells","port_out":{pid:[...]}}]
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

    # 如果某个模块没有候选，直接不可行
    for m in movable_mods:
        if not cands[m.id]:
            print(f"[exact] 模块 {m.id} 无任何合法候选位置", flush=True)
            return None
    blog("候选位置: "
         + ", ".join(f"{m.id}={len(cands[m.id])}" for m in movable_mods))

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
    blog(f"place 变量 {len(pvar)} 个（每个可动模块恰好选中一个候选位姿）")

    # ---------- 网格格点与固定禁区 ----------
    all_cells = [(r, c) for r in range(rows) for c in range(cols) if (r, c) not in fixed_cells]
    cell_idx = {v: i for i, v in enumerate(all_cells)}
    blog(f"可用格点 {len(all_cells)} 个（固定模块占掉的 {len(fixed_cells)} 格永久禁带）")

    # ---------- 非重叠 & 模块禁带（按格聚合） ----------
    # 原写法是「每个模块候选 x 它的每一格 x 每一条 net」都写一条
    # `place + use <= 1`：27x30 / 47 net 的题目会写出 5500 万条约束，
    # 光是把它们塞进模型就要十几分钟加几十 GB，建模阶段直接卡死。
    #
    # 这里按**格**聚合：
    #   occ[cell] = 覆盖该格的所有候选 place 之和
    # 它的取值范围被限制成 0/1（BoolVar），因此
    #   * 「同格最多一个可动模块」= 原来的非重叠约束，
    #   * 「模块占的格不能走带」= 每格 nets 条 occ + use <= 1。
    # 两者都从这一条等式推出来，语义与原来逐条写法完全一致。
    block_by = {}   # cell -> [place key...]（只含可动模块；固定格子不在 all_cells 里）
    for m in movable_mods:
        for i, cand in enumerate(cands[m.id]):
            for cell in set(cand["cells"]):
                block_by.setdefault(cell, []).append(pvar[(m.id, i)])
    occ_var = {}
    for cell, keys in block_by.items():
        b = model.NewBoolVar(f"occ_{cell_idx[cell]}")
        model.Add(b == sum(keys))
        occ_var[cell] = b
    blog(f"占用聚合变量 {len(occ_var)} 个（同格模块互斥已并入其中）")

    # ---------- 端点候选（模块端口外侧第一格） ----------
    # 原写法给每个 (候选, 外侧格) 都建一个 Bool，再加一条 place 链接约束：
    # 3x3 模块一个端口面就有 3 格，47 条 net 的题目会生成 84 万变量 + 84 万约束。
    #
    # 改成按**格**建变量（同样精确）：
    #   end[cell] <= 「该格能当这个端口外侧格」的候选 place 之和
    #   sum(end) == 1        （整条 net 恰好一个起点格、一个终点格）
    # 模块候选恰好选一个 + end 恰好一个 => 被选中的那个候选必须覆盖那个 end 格，
    # 也就是原来的「(候选, 外侧格) 精确命中」。顺带还剪掉了「端口根本接不出去」
    # 的候选（原来只有靠 ExactlyOne 才隐含地排除掉它们）。
    blog("枚举端点候选（各模块端口外侧第一格）")

    def endpoint_cover(mid, pid):
        """该模块端口在当前候选集合下可能落到的外侧格 -> [候选序号]。"""
        cover = {}
        for ci, cand in enumerate(cands[mid]):
            for _pc, oc in cand["port_out"].get(pid, []):
                if oc in cell_idx:
                    cover.setdefault(oc, []).append(ci)
        return cover

    src_cover = []   # [net] -> {cell: [候选序号]}（Hint 校验与端点回读用）
    dst_cover = []
    src_end = []     # [net] -> {cell: BoolVar}
    dst_end = []
    pinned = set()   # (模块, 端口)：同一个端口只加一条「候选可用」约束
    for ni, net in enumerate(nets):
        fm = modules[net["from"]]
        tm = modules[net["to"]]
        fpid = net.get("from_port", "OUT")
        tpid = net.get("to_port", "IN")
        s = endpoint_cover(net["from"], fpid)
        d = endpoint_cover(net["to"], tpid)
        src_cover.append(s)
        dst_cover.append(d)

        # 若某 net 没有可用端口，直接不可行
        if not s or not d:
            print(f"[exact] net {ni} 无可用端口格", flush=True)
            return None

        # 端点模块必须落在一个「端口能接出去」的候选上
        for m, pid, cover in ((fm, fpid, s), (tm, tpid, d)):
            if m.fixed or (m.id, pid) in pinned:
                continue
            pinned.add((m.id, pid))
            usable = sorted({ci for cis in cover.values() for ci in cis})
            model.Add(sum(pvar[(m.id, ci)] for ci in usable) == 1)

        svar = {cell: model.NewBoolVar(f"src_{ni}_{cell_idx[cell]}") for cell in s}
        dvar = {cell: model.NewBoolVar(f"dst_{ni}_{cell_idx[cell]}") for cell in d}
        if not fm.fixed:
            for cell, v in svar.items():
                model.Add(v <= sum(pvar[(fm.id, ci)] for ci in s[cell]))
        if not tm.fixed:
            for cell, v in dvar.items():
                model.Add(v <= sum(pvar[(tm.id, ci)] for ci in d[cell]))
        model.AddExactlyOne(list(svar.values()))
        model.AddExactlyOne(list(dvar.values()))
        src_end.append(svar)
        dst_end.append(dvar)
    naive_end = 0
    for net in nets:
        for mid, pid in ((net["from"], net.get("from_port", "OUT")),
                         (net["to"], net.get("to_port", "IN"))):
            for cand in cands[mid]:
                naive_end += sum(1 for _pc, oc in cand["port_out"].get(pid, [])
                                 if oc in cell_idx)
    blog(f"端点格变量 {sum(len(v) for v in src_end + dst_end)} 个"
         f"（原逐 (候选, 外侧格) 写法要 {naive_end} 个）")

    # ---------- 弧与 use 变量 ----------
    blog("建邻接与弧/use 变量")
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
    blog(f"弧变量 {len(arc)} 个, use 变量 {len(use)} 个")

    # 每个 net 在每个格点是否为“笔直横向/纵向穿过”
    # hE/hW: 横向由西向东 / 由东向西；vN/vS: 纵向由北向南 / 由南向北
    blog("建直通变量（每格每种直通方向 = 一进一出两条弧的 AND）")
    hcomp = {}
    vcomp = {}
    comp_vars = {}   # (ni, cell) -> {"hE"|"hW"|"vN"|"vS": BoolVar or None}
    n_comp = 0
    for ni in range(len(nets)):
        for (r, c) in all_cells:
            wb = (r, c - 1) if c - 1 >= 0 and (r, c - 1) in cell_idx else None
            eb = (r, c + 1) if c + 1 < cols and (r, c + 1) in cell_idx else None
            nb_ = (r - 1, c) if r - 1 >= 0 and (r - 1, c) in cell_idx else None
            sb = (r + 1, c) if r + 1 < rows and (r + 1, c) in cell_idx else None

            def make(name, ia, oa):
                nonlocal n_comp
                if ia is None or oa is None:
                    return None
                b = model.NewBoolVar(
                    f"{name}_{ni}_{cell_idx[(r, c)]}")
                # b = arc(in) AND arc(out)
                model.Add(b <= arc[(ni, (ia, (r, c)))])
                model.Add(b <= arc[(ni, ((r, c), oa))])
                model.Add(b >= arc[(ni, (ia, (r, c)))] +
                          arc[(ni, ((r, c), oa))] - 1)
                n_comp += 1
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
    blog(f"直通变量 {n_comp} 个")

    # 每个 net 在每个格点的流量平衡 + use 的定义
    blog(f"流量平衡约束 {len(nets) * len(all_cells)} 组")
    for ni in range(len(nets)):
        for cell in all_cells:
            outs = [arc[(ni, (cell, nb))] for nb in neighbors[cell]]
            ins = [arc[(ni, (nb, cell))] for nb in neighbors[cell]]
            # 端点是「格」级的 BoolVar；该格当不了端点时就是常数 0
            so = src_end[ni].get(cell, 0)
            do = dst_end[ni].get(cell, 0)
            u = use[(ni, cell)]
            model.Add(sum(outs) - sum(ins) == so - do)
            # 简单路径：每个点至多一进一出
            model.Add(sum(outs) <= 1)
            model.Add(sum(ins) <= 1)
            # use = 该格被本 net 占用（有进、有出、或本身是端点）
            for t in (so, do):
                if not isinstance(t, int):      # 常数 0 不用写约束
                    model.Add(u >= t)
            for a in outs:
                model.Add(u >= a)
            for a in ins:
                model.Add(u >= a)
            # 原来这里 `expr >= use` 与上一行完全重复，删掉
            model.Add(u <= sum(outs) + sum(ins) + so + do)

    # 同格最多两条带；若两条带共格，必须一条横直通 + 一条竖直通，
    # 且交叉格不能是任何一条带的端点，也不能有转弯。
    #
    # 原写法对每格每一对 (a,b) 都建一个 share 变量 + 8 条 OnlyEnforceIf 约束：
    # 780 格 x C(47,2)=1081 对 => 84 万变量 + 674 万条约束，47 条 net 的题目
    # 光这一块就把建模拖死。这里换成按格聚合的等价写法：
    #   T[cell] = 该格被几条带占用（<=2，等价于原来的 sum(use) <= 2）
    #   H/V     = 该格有几条带横向/纵向直通（每个 net 至多一种直通方向）
    #   E       = 该格上有几个端点
    # T==2（两条带共格）时下面几条合力给出与逐对写法完全相同的要求：
    #   use_i + T <= 2 + straight_i  => 两条带都必须笔直（不能转弯）
    #   H + V >= 2T - 2 连同 H<=1、V<=1 => 恰好一条横、一条纵
    #   E + 2T <= 4                  => 交叉格不能是端点
    # T<=1 时这几条都是恒真式，所以不会误伤单条带的情形。
    #
    # 这层细则**故意先不加**（见 add_share_constraints）：它正是最难满足的一层，
    # 先解不含它的松弛模型能很快拿到骨架和下界（阶段1），补回来再求最优（阶段2）。
    if relax_phase and nets:
        blog("同格共带/交叉约束：两阶段求解——阶段1 先不加（松弛），阶段2 再补")
    else:
        blog("同格共带/交叉约束：本次直接全加（不走两阶段）")
    tvar = {}   # cell -> IntVar 该格的带数（松弛模型里也用它做 Hint / 违规检查）
    for cell in all_cells:
        T = model.NewIntVar(0, 2, f"t_{cell_idx[cell]}")
        model.Add(T == sum(use[(ni, cell)] for ni in range(len(nets))))
        tvar[cell] = T

    def add_share_constraints():
        """把「共格只能一横一纵十字直通、交叉格不能是端点」补进模型（阶段2）。

        这一层是原始语义的一部分，补回来之后模型与从前完全一致；去掉它的版本是
        原问题的**松弛**（可行解更多），所以阶段1 得到的解可能违反细则——
        既不能当结果输出，也不能拿来当目标上界，只能当 Hint 和**合法下界**
        （松弛问题的最优值 <= 原问题最优值）。返回补了多少格。
        """
        for cell in all_cells:
            T = tvar[cell]
            hs = [hcomp[(ni, cell)] for ni in range(len(nets))]
            vs = [vcomp[(ni, cell)] for ni in range(len(nets))]
            es = [src_end[ni].get(cell, 0) for ni in range(len(nets))]
            es += [dst_end[ni].get(cell, 0) for ni in range(len(nets))]
            model.Add(sum(hs) <= 1)
            model.Add(sum(vs) <= 1)
            model.Add(sum(hs) + sum(vs) >= 2 * T - 2)
            model.Add(sum(es) + 2 * T <= 4)
            for ni in range(len(nets)):
                model.Add(use[(ni, cell)] + T
                          <= 2 + hcomp[(ni, cell)] + vcomp[(ni, cell)])
        return len(all_cells)

    # 模块占用格不能被带穿过
    # （原来这里是 模块候选格数 x net 数 条约束，现在是 格数 x net 数）
    blog(f"模块禁带约束 {len(occ_var) * len(nets)} 条")
    for cell, b in occ_var.items():
        for ni in range(len(nets)):
            model.Add(b + use[(ni, cell)] <= 1)

    # 目标：最小化传送带占用格总数
    obj = sum(use[(ni, cell)] for ni in range(len(nets)) for cell in all_cells)
    model.Minimize(obj)
    blog("目标函数：最小化传送带占用格总数")

    # ---------- 合法冗余下界 ----------
    # obj = Σ_net Σ_cell use，而每条 net 恰好有一个起点格、且 use >= 起点变量，
    # 所以 obj >= net 条数；更进一步，路径格数 >= 起点格到终点格的曼哈顿距离 + 1。
    # 两条都是**模型的推论**（写出来不会切掉任何可行解），但求解器自己往往要几百秒
    # 才能爬到同样的值：实测 config.example.json（9 条 net）不加时 224s 才爬到 9，
    # 加上之后一开始就是 next:[9, …]。
    lb_per_net = legal_length_bounds(nets, src_end, dst_end)
    total_lb = sum(lb_per_net)
    for ni, lbn in enumerate(lb_per_net):
        if lbn > 1:
            model.Add(sum(use[(ni, cell)] for cell in all_cells) >= lbn)
    if total_lb > 0:
        model.Add(obj >= total_lb)
        blog(f"合法下界 obj >= {total_lb}"
             f"（每条 net 至少 1 格 = {len(nets)}，"
             f"端点曼哈顿距离再加 {total_lb - len(nets)}）")

    # 规模统计（ModelStats 要遍历整个模型，大模型上本身也要一两秒，单独记一行）
    size = _model_size_text(model)
    if size:
        blog(f"模型规模 {size}")

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
        # 上一轮两阶段里阶段1 证出的松弛下界：来源不同，但对完整模型同样成立，
        # 单独记在 lb_relax 字段（见 save_prev_bound），这里取两者更紧的那个用。
        prev_relax = prev_bound.get("lb_relax")
        if isinstance(prev_relax, int) and not isinstance(prev_relax, bool):
            if prev_relax > lb:
                print(f"[exact] 历史松弛下界 lb_relax={prev_relax} 比 lb={lb} 更紧："
                      f"这次用它抬下界", flush=True)
                lb = prev_relax
        if ub is not None and lb > ub:
            print(f"[exact] 忽略历史下界 {lb}：大于本次上界 {ub}，"
                  f"历史数据自相矛盾", flush=True)
            lb = None
        else:
            model.Add(obj >= lb)
            notes.append(f"obj>={lb}")
            if ub is not None and lb == ub:
                notes.append("上下界重合=已证该值最优")

    def exact_pick(mid, pos, orient):
        """(位置, 旋转) -> 候选序号；不在当前候选集合里返回 None。"""
        want = (tuple(pos), int(orient) % 360)
        for i, cand in enumerate(cands[mid]):
            if (tuple(cand["pos"]), cand["orient"]) == want:
                return i
        return None

    def share_violations(slv):
        """数出这个解违反「共格只能一横一纵十字直通」的格，返回 [(格, T, H, V, E)]。

        空列表 = 这个解在共格细则上是合法的（其余约束本来就在模型里）。
        两阶段求解用它判断阶段1 的松弛解能不能直接当结果。
        """
        def val(x):
            return 0 if isinstance(x, int) else int(slv.Value(x))

        bad = []
        for cell in all_cells:
            T = sum(int(slv.Value(use[(ni, cell)])) for ni in range(len(nets)))
            if T <= 1:
                continue
            H = sum(val(hcomp[(ni, cell)]) for ni in range(len(nets)))
            V = sum(val(vcomp[(ni, cell)]) for ni in range(len(nets)))
            E = sum(val(src_end[ni].get(cell, 0)) + val(dst_end[ni].get(cell, 0))
                    for ni in range(len(nets)))
            if T == 2 and H == 1 and V == 1 and E == 0:
                continue
            bad.append((cell, T, H, V, E))
        return bad

    def add_hints(picks, paths):
        """把一份解（模块候选序号 + 每条 net 的路径格）翻译成**每个变量**的 Hint。

        全给上之后，CP-SAT 要么直接把它当成 incumbent（presolve 阶段、搜索都不用
        开始），要么发现它不可行而去 repair；只给一部分变量的话它得另起一个
        “hint search” 子求解器补全，路径怎么连通这部分信息就白丢了。
        返回提示了多少个变量。
        """
        n = 0
        for m in movable_mods + fixed_mods:
            for i in range(len(cands[m.id])):
                model.AddHint(pvar[(m.id, i)],
                              1 if (m.fixed or picks.get(m.id) == i) else 0)
                n += 1
        used_cells = set()
        for m in movable_mods:
            i = picks.get(m.id)
            if i is not None:
                used_cells.update(cands[m.id][i]["cells"])
        for cell, b in occ_var.items():
            model.AddHint(b, 1 if cell in used_cells else 0)
            n += 1
        on_path = [set(p) for p in paths]
        for ni in range(len(nets)):
            for cell, v in src_end[ni].items():
                model.AddHint(v, 1 if cell == paths[ni][0] else 0)
                n += 1
            for cell, v in dst_end[ni].items():
                model.AddHint(v, 1 if cell == paths[ni][-1] else 0)
                n += 1
        for ni in range(len(nets)):
            for cell in all_cells:
                model.AddHint(use[(ni, cell)],
                              1 if cell in on_path[ni] else 0)
                n += 1
        for cell in all_cells:
            model.AddHint(tvar[cell],
                          sum(1 for ni in range(len(nets)) if cell in on_path[ni]))
            n += 1
        arc_on = set()
        for ni in range(len(nets)):
            for u, v in zip(paths[ni], paths[ni][1:]):
                arc_on.add((ni, (u, v)))
        for key in arc:
            model.AddHint(arc[key], 1 if key in arc_on else 0)
            n += 1
        pos_in_path = [{cell: i for i, cell in enumerate(p)} for p in paths]
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
        # （原来第 9 步还要给 84 万个 share 变量逐个 AddHint，
        #   聚合写法下这些变量已经不存在了。）
        return n

    def apply_full_hint():
        """把历史最好解翻译成完整 Hint。返回 (统计 dict, None) 或 (None, 放弃原因)。"""
        state = prev_best.get("state") or {}
        prev_paths = prev_best.get("paths") or []
        if len(prev_paths) < len(nets):
            return None, f"历史解只有 {len(prev_paths)} 条路径，当前有 {len(nets)} 条"

        # 1) 模块位姿：必须 (位置, 旋转) 精确对上，对不上就整个放弃
        picks = {}
        for m in movable_mods:
            v = state.get(m.id)
            if not isinstance(v, dict):
                return None, f"历史解里没有模块 {m.id} 的位姿"
            pos = (int(v["row"]) - 1, int(v["col"]) - 1)
            orient = int(v.get("rotate", 0)) % 360
            i = exact_pick(m.id, pos, orient)
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

        # 3) 端点：必须能在当前候选里精确命中（模块也用上面的精确位姿）
        for ni in range(len(nets)):
            fm = modules[nets[ni]["from"]]
            tm = modules[nets[ni]["to"]]
            want_s = paths[ni][0]
            want_d = paths[ni][-1]
            ok_s = (want_s in src_cover[ni]
                    and (fm.fixed or picks.get(fm.id) in src_cover[ni][want_s]))
            ok_d = (want_d in dst_cover[ni]
                    and (tm.fixed or picks.get(tm.id) in dst_cover[ni][want_d]))
            if not ok_s or not ok_d:
                return None, (f"net {ni} 的端点 {want_s} -> "
                              f"{want_d} 在候选里对不上")

        n = add_hints(picks, paths)
        return {
            "cost": hint_cost,
            "modules": len(picks),
            "nets": len(nets),
            "cells": sum(len(p) for p in paths),
            "hints": n,
        }, None

    hint_stat = None
    if prev_best is not None:
        t_hint = time.perf_counter()
        blog("施加完整 Hint（历史最好解的每个变量取值）")
        hint_stat, why = apply_full_hint()
        if hint_stat is None:
            print(f"[exact] 未施加 Hint：{why}（上下界约束仍照常生效）",
                  flush=True)
        else:
            blog(f"Hint 施加完成: {hint_stat['hints']} 个变量, "
                 f"共耗时 {time.perf_counter() - t_hint:.1f}s")

    print(
        f"[exact] 模型构建完成: {len(all_cells)} 个可用格点, "
        f"{size or '规模未知'}, 建模总耗时 {time.perf_counter() - blog.t0:.1f}s"
        f"{_mem_text()}；开始求解 (time_limit={time_limit:.1f}s, "
        f"workers={workers})"
        + ("；两阶段：阶段1 先解松弛模型（不含共格细则），阶段2 补回来求最优"
           if relax_phase and nets and hint_stat is None else ""),
        flush=True,
    )
    print("[exact] 提示：CP-SAT 会先做一轮 presolve，模型大时这一步可能几十秒"
          "没有任何输出，之后才会开始报可行解", flush=True)

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
            for cell, v in src_end[ni].items():
                if slv.Value(v) == 1:
                    src_oc = cell
                    break
            for cell, v in dst_end[ni].items():
                if slv.Value(v) == 1:
                    dst_oc = cell
                    break
            if src_oc is None or dst_oc is None:
                # 理论上不会发生（每条 net 恰好一个起点/终点）；真出现就
                # 别硬凑路径，交给上层按空路径处理。
                paths.append([])
                continue
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

    # ---------- 阶段1：先解「不含共格十字直通细则」的松弛模型 ----------
    # 为什么：那层细则正是最难满足的部分（共格必须一横一纵直通、交叉格不能是端点、
    # 不能转弯），硬模型上求解器连一个可行解都很难找到（实测 example：300s 零解）。
    # 拿掉它以后模型好解得多，能很快给出：
    #   * 一个骨架解（模块摆位 + 大致路径）——当阶段2 的 Hint，让 CP-SAT 去 repair；
    #   * 一个**合法下界** lb_relax（松弛问题最优值 <= 原问题最优值）。
    # 松弛解可能违反细则，所以：绝不能输出成 solN，也绝不能当目标上界。
    relax_lb = None
    relax_stat = None
    relax_sol = None
    relax_legal = False
    relax_wall = 0.0
    relax_hint_n = 0

    if relax_phase and nets and hint_stat is None:
        t1 = max(3.0, min(0.35 * time_limit, 120.0))
        blog(f"阶段1（松弛：先不加共格细则）开始，预算 {t1:.1f}s")
        s1 = cp_model.CpSolver()
        s1.parameters.max_time_in_seconds = t1
        s1.parameters.num_workers = workers
        s1.parameters.log_search_progress = verbose
        rec = RelaxRecorder(make_solution, violations=share_violations)
        st1 = s1.Solve(model, rec)
        relax_stat = s1.StatusName(st1)
        relax_wall = s1.WallTime()
        relax_lb = int(round(s1.BestObjectiveBound()))
        if rec.best is not None:
            relax_sol = rec.best
            relax_legal = (rec.best_viol == 0)
            if relax_legal:
                blog(f"阶段1 拿到**合法解** cost={relax_sol['cost']}"
                     f"（松弛模型上就满足了共格细则，可以直接当上界用）")
            else:
                first = rec.best_vac[0]
                blog(f"阶段1 松弛解 cost={relax_sol['cost']}，"
                     f"违反共格细则 {rec.best_viol} 处"
                     f"（例如 格{first[0]} T={first[1]} H={first[2]} "
                     f"V={first[3]} E={first[4]}；挑的是违规最少的骨架，"
                     f"一共报过 {rec.count} 个解）——不能当结果，只作 Hint")
        blog(f"阶段1 结束: status={relax_stat}, 用时 {relax_wall:.1f}s, "
             f"松弛下界 lb_relax={relax_lb}（对完整模型同样成立）")

        # 骨架当 Hint；只有「验证过合法」的解才允许当目标上界
        if relax_sol is not None and all(relax_sol["paths"]):
            picks1 = {}
            for m in movable_mods:
                v = relax_sol["state"].get(m.id)
                i = None if v is None else exact_pick(m.id, v["pos"], v["orient"])
                if i is None:
                    picks1 = None
                    break
                picks1[m.id] = i
            if picks1 is not None:
                relax_hint_n = add_hints(picks1, relax_sol["paths"])
                blog(f"阶段1 骨架已作为阶段2 的 Hint（{relax_hint_n} 个变量，"
                     f"违规处交给 CP-SAT repair）")
            if relax_legal:
                model.Add(obj <= relax_sol["cost"])
                notes.append(f"obj<={relax_sol['cost']}(阶段1合法解)")
        if relax_legal:
            # 阶段1 就拿到了合法解：它是本轮的真成果，但**先不写盘**——
            # 写完阶段2 很可能再报一个同样的 cost，会多出一份一模一样的 solN。
            # 统一由上层收尾时按 cost 去重补写（main 里的 writer.submit 兜底）。
            print(f"[exact] 阶段1 已得到合法解 cost={relax_sol['cost']}，"
                  f"阶段2 若没有更好的就采用它", flush=True)
        if relax_lb > 0 and (lb is None or relax_lb > lb) \
                and (ub is None or relax_lb <= ub):
            model.Add(obj >= relax_lb)
            notes.append(f"obj>={relax_lb}(阶段1松弛下界)")
            blog(f"松弛下界 obj >= {relax_lb} 已加进阶段2"
                 + (f"（比历史下界 {lb} 更紧）" if lb else ""))
    elif relax_phase and nets:
        blog("阶段1 跳过：本轮已经有历史解作 Hint，直接进阶段2")

    # ---------- 阶段2：把共格细则补回来，在完整模型上求最优 ----------
    n_share = add_share_constraints()
    blog(f"共格十字直通细则 +{n_share} 格已补进模型（阶段2 用完整模型）")

    t_left = max(1.0, time_limit - relax_wall)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = t_left
    solver.parameters.num_workers = workers
    solver.parameters.log_search_progress = verbose

    hint_note = ""
    if notes:
        hint_note = "，warm start：" + " + ".join(notes)
    if hint_stat is not None:
        hint_note += (f"；完整 Hint cost={hint_stat['cost']}, "
                      f"模块 {hint_stat['modules']} 个, "
                      f"路径 {hint_stat['nets']} 条/{hint_stat['cells']} 格, "
                      f"共 hint {hint_stat['hints']} 个变量（来自历史解）")
    elif relax_hint_n:
        hint_note += (f"；完整 Hint 来自阶段1 骨架（{relax_hint_n} 个变量）"
                      + (f"，含合法解上界 obj<={relax_sol['cost']}"
                         if relax_legal else "，含违规处待 repair"))
    print(
        f"[exact] 阶段2 开始（完整模型）: {len(all_cells)} 个可用格点, "
        f"建模总耗时 {time.perf_counter() - blog.t0:.1f}s{_mem_text()}, "
        f"阶段1 用时 {relax_wall:.1f}s, 阶段2 预算 {t_left:.1f}s "
        f"(time_limit={time_limit:.1f}s, workers={workers})"
        + hint_note,
        flush=True,
    )

    saver = SolutionSaver(make_solution, on_solution=on_solution)
    status = solver.Solve(model, saver)
    status_name = solver.StatusName(status)
    wall = relax_wall + solver.WallTime()

    def finish(sol, n_solutions, proven_lb, tag=""):
        """把一份解补全成结果 dict，并把这一轮证出的界落盘。"""
        sol["status"] = status_name
        sol["solve_sec"] = wall
        sol["num_solutions"] = n_solutions
        sol["fixed"] = {m.id: {"pos": m.fixed_pos, "orient": 0}
                        for m in fixed_mods}
        sol["modules"] = modules
        sol["cfg"] = cfg
        sol["lb"] = proven_lb
        sol["lb_relax"] = relax_lb
        sol["bound_file"] = save_prev_bound(prefix, cfg, proven_lb, sol["cost"],
                                           status_name, wall, applied=notes,
                                           lb_relax=relax_lb)
        if tag:
            print(f"[exact] {tag}", flush=True)
        return sol

    hard_lb = int(round(solver.BestObjectiveBound())) \
        if status in (cp_model.UNKNOWN, cp_model.OPTIMAL, cp_model.FEASIBLE) else 0
    # 松弛下界对完整模型同样成立，超时/没找到解时直接用它兜底
    proven_lb = max(hard_lb, relax_lb or 0)

    if status == cp_model.UNKNOWN:
        # 时间上限到了、阶段2 没找到可行解。解没有，但**已证明的下界**是真成果，
        # 必须存下来（硬题目通常就是这个结局，下一轮 --hint 能接着用）。
        if relax_legal and relax_sol is not None:
            # 阶段1 已经给出**合法解**：它是本轮的真成果（上面已经写过一份 solN）
            return finish(relax_sol, saver.count + 1, proven_lb,
                          tag=f"阶段2 超时前没找到更好的解；本轮结果是阶段1 的"
                              f"合法解 cost={relax_sol['cost']}，"
                              f"下界 lb={proven_lb}"
                              f"（其中松弛下界 {relax_lb}）")
        saved = save_prev_bound(prefix, cfg, proven_lb, None, status_name, wall,
                                applied=notes, lb_relax=relax_lb)
        print(f"[exact] 时间上限内没找到可行解（status=UNKNOWN）；"
              f"已证明下界 lb={proven_lb}"
              + (f"（含阶段1 松弛下界 {relax_lb}）" if relax_lb else "")
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
    if relax_legal and relax_sol is not None \
            and relax_sol["cost"] < int(round(solver.ObjectiveValue())):
        # 阶段2 找到的解反而更差（少见，但可能：阶段1 已经给过合法解）
        return finish(relax_sol, saver.count + 1, proven_lb,
                      tag=f"阶段1 的合法解 cost={relax_sol['cost']} 比阶段2 更好，"
                          f"采用阶段1 的结果")
    best = make_solution(solver)
    return finish(best, saver.count, proven_lb)


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
    ap.add_argument("--no-relax-phase", action="store_true",
                    help="关掉两阶段求解的阶段1（不再先解松弛模型拿骨架/下界），"
                         "直接在完整模型上搜——排查用，一般不需要")
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
                      hint_file=base, use_hint=args.hint,
                      relax_phase=not args.no_relax_phase)
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
    if res.get("lb_relax") is not None:
        print(f"[exact] 其中阶段1 松弛下界 lb_relax={res['lb_relax']}"
              f"（松弛模型证出的，对完整模型同样成立；单独记在 bounds.json 的"
              f" lb_relax 字段里）", flush=True)
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
