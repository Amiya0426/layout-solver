#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用“模块布点 + 传送带布线”求解器

用法:
    python layout_solver.py config.json [--timeout 30] [--seed 7]

配置 JSON 字段:
    rows, cols            画布大小
    fixed                 固定模块数组
      id, pos [r,c], w, h, ports
    movable               可动模块数组
      id, w, h, rotatable, ports
    nets                  连接表
      {from, from_port, to, to_port}

port 定义:
    {"id": "OUT", "cells": [[r,c],...], "dir": "S"}
  cells 是相对模块左上角的格位；dir 表示传送带离开该端口的绝对方向
  （N/S/E/W）。旋转模块时，cells 与 dir 一起旋转。

传送带占用网格格位：
    - 不能穿过 Y/模块；
    - 两条传送带不能共用同一格；
    - 求解目标是所有 net 可连通且总带长尽量短。
"""

import argparse
import json
import math
import os
import random
import sys
import time
from collections import deque

import layout_viz

DIRV = {"N": (-1, 0), "S": (1, 0), "W": (0, -1), "E": (0, 1)}
OPPOSITE = {"N": "S", "S": "N", "W": "E", "E": "W"}
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


class Module:
    def __init__(self, mid, cfg, pos=None, fixed=False):
        self.id = mid
        self.w = cfg["w"]
        self.h = cfg["h"]
        self.fixed = fixed
        self.fixed_pos = tuple(pos) if fixed else None
        self.rotatable = bool(cfg.get("rotatable", False))
        self.ports = {}
        for p in cfg.get("ports", []):
            self.ports[p["id"]] = {
                "cells": [tuple(c) for c in p["cells"]],
                "dir": p["dir"],
            }

    def dims(self, orient):
        if orient in (90, 270):
            return self.h, self.w
        return self.w, self.h

    def occ(self, pos, orient):
        r0, c0 = pos
        if orient in (90, 270):
            cells = []
            for i in range(self.h):
                for j in range(self.w):
                    rr, cc = ROT_CELL[orient](i, j, self.h, self.w)
                    cells.append((r0 + rr, c0 + cc))
            return cells
        return [(r0 + i, c0 + j) for i in range(self.h) for j in range(self.w)]

    def port_outside(self, pos, orient, port_id):
        """返回 (模块端口, 外侧第一个带格) 列表。"""
        p = self.ports.get(port_id)
        if not p:
            return []
        r0, c0 = pos
        out = []
        for (rr, cc) in p["cells"]:
            if orient == 0:
                pr, pc = rr, cc
                d = p["dir"]
            else:
                pr, pc = ROT_CELL[orient](rr, cc, self.h, self.w)
                d = ROT_DIR[orient][p["dir"]]
            dr, dc = DIRV[d]
            out.append(((r0 + pr, c0 + pc), (r0 + pr + dr, c0 + pc + dc)))
        return out


def _axis(d):
    return "H" if d in ("E", "W") else "V"


def bfs_cross(start, goals, free, used_count, used_axis):
    """最短路径；允许与已有传送带垂直交叉，但交叉格必须两边都直通。"""
    if start not in free or used_count.get(start, 0) > 0:
        return None
    goals = {g for g in goals if g in free and used_count.get(g, 0) == 0}
    if not goals:
        return None
    if start in goals:
        return [start]

    start_state = (start, None)
    parent = {start_state: None}
    dq = deque([start_state])

    while dq:
        cur, in_dir = dq.popleft()
        if cur in goals:
            # 重建路径
            st = (cur, in_dir)
            path = []
            while st is not None:
                path.append(st[0])
                st = parent[st]
            return path[::-1]

        if used_count.get(cur, 0) >= 2:
            continue

        if used_count.get(cur, 0) == 1:
            # 当前格已被一条带占用：只能垂直交叉且本带必须直通
            old_axis = used_axis.get(cur)
            if old_axis is None or in_dir is None:
                continue
            new_axis = _axis(in_dir)
            if old_axis == new_axis:
                continue
            out_dir = in_dir
            dr, dc = DIRV[out_dir]
            nb = (cur[0] + dr, cur[1] + dc)
            if nb in free and used_count.get(nb, 0) < 2:
                st = (nb, out_dir)
                if st not in parent:
                    parent[st] = (cur, in_dir)
                    dq.append(st)
            continue

        # 空闲格：允许转弯，也可以直行进入已有带（作为交叉点）
        for d, (dr, dc) in DIRV.items():
            nb = (cur[0] + dr, cur[1] + dc)
            if nb not in free or used_count.get(nb, 0) >= 2:
                continue
            if used_count.get(nb, 0) == 1:
                old_axis = used_axis.get(nb)
                if old_axis is None or old_axis == _axis(d):
                    continue
            st = (nb, d)
            if st not in parent:
                parent[st] = (cur, in_dir)
                dq.append(st)
    return None


class Solver:
    def __init__(self, cfg, seed=7):
        self.seed = seed
        self.rows = cfg["rows"]
        self.cols = cfg["cols"]
        self.nets = cfg["nets"]
        self.fixed = {}
        self.movable = {}
        for m in cfg.get("fixed", []):
            mod = Module(m["id"], m, pos=m["pos"], fixed=True)
            self.fixed[mod.id] = mod
        for m in cfg.get("movable", []):
            mod = Module(m["id"], m)
            self.movable[mod.id] = mod
        self.modules = {**self.fixed, **self.movable}
        self.rng = random.Random(seed)
        self._last_ok = None    # 最近一次成功布线: (state, 带格数, paths)

    def occupied(self, state):
        occ = set()
        for mid, m in self.modules.items():
            if m.fixed:
                pos, orient = m.fixed_pos, 0
            elif mid not in state:
                continue
            else:
                pos, orient = state[mid]["pos"], state[mid]["orient"]
            occ.update(m.occ(pos, orient))
        return occ

    def valid(self, state):
        occ = set()
        for mid, m in self.modules.items():
            if m.fixed:
                pos, orient = m.fixed_pos, 0
            elif mid not in state:
                continue
            else:
                pos, orient = state[mid]["pos"], state[mid]["orient"]
            for cell in m.occ(pos, orient):
                if not (0 <= cell[0] < self.rows and 0 <= cell[1] < self.cols):
                    return False
                if cell in occ:
                    return False
                occ.add(cell)
        return True

    def random_state(self):
        for _ in range(5000):
            state = {}
            ok = True
            for mid, m in self.movable.items():
                placed = False
                for _ in range(300):
                    orient = self.rng.choice([0, 90, 180, 270]) if m.rotatable else 0
                    w, h = m.dims(orient)
                    if w > self.cols or h > self.rows:
                        continue
                    r = self.rng.randrange(self.rows - h + 1)
                    c = self.rng.randrange(self.cols - w + 1)
                    state[mid] = {"pos": (r, c), "orient": orient}
                    if self.valid(state):
                        placed = True
                        break
                    del state[mid]
                if not placed:
                    ok = False
                    break
            if ok:
                return state
        return None

    def route(self, state):
        """顺序布线，返回 (带格总数, path per net, 失败信息)。"""
        occ = self.occupied(state)
        free = {(r, c) for r in range(self.rows)
                for c in range(self.cols) if (r, c) not in occ}

        out_cells = {}
        for mid, m in self.modules.items():
            if m.fixed:
                pos, orient = m.fixed_pos, 0
            else:
                pos, orient = state[mid]["pos"], state[mid]["orient"]
            for pid in m.ports:
                out_cells[(mid, pid)] = m.port_outside(pos, orient, pid)

        # 尝试多个 net 顺序，提高“先布几条、再交叉补线”的可行性
        for _attempt in range(4):
            used_count = {}
            used_axis = {}
            paths = [None] * len(self.nets)
            total = 0
            order = list(range(len(self.nets)))
            self.rng.shuffle(order)
            ok = True

            for ni in order:
                net = self.nets[ni]
                fid = net["from"]
                fpid = net.get("from_port", "OUT")
                tid = net["to"]
                tpid = net.get("to_port", "IN")
                starts = [o for (_p, o) in out_cells.get((fid, fpid), [])
                          if o in free and used_count.get(o, 0) == 0]
                goals = [o for (_p, o) in out_cells.get((tid, tpid), [])
                         if o in free and used_count.get(o, 0) == 0]
                if not starts or not goals:
                    ok = False
                    break
                best = None
                for s in starts:
                    p = bfs_cross(s, set(goals), free, used_count, used_axis)
                    if p and (best is None or len(p) < len(best)):
                        best = p
                if best is None:
                    ok = False
                    break

                # 更新占用：标记哪些格可以被将来垂直交叉
                for i, cell in enumerate(best):
                    used_count[cell] = used_count.get(cell, 0) + 1
                    if 0 < i < len(best) - 1:
                        a, b = best[i - 1], best[i + 1]
                        if a[0] == b[0]:
                            used_axis.setdefault(cell, "H")
                        elif a[1] == b[1]:
                            used_axis.setdefault(cell, "V")
                paths[ni] = best
                total += len(best)
            if ok:
                return total, paths, None
        return None, [None] * len(self.nets), None

    def evaluate(self, state):
        if state is None or not self.valid(state):
            return 1e9
        res = self.route(state)
        if res[0] is None:
            return 1e9 + self.rng.random()
        self._last_ok = (state, res[0], res[1])
        return res[0]

    def neighbor(self, state):
        ns = {k: dict(v) for k, v in state.items()}
        if not self.movable:
            return ns
        mid = self.rng.choice(list(self.movable))
        m = self.movable[mid]
        pos = list(ns[mid]["pos"])
        orient = ns[mid]["orient"]
        w, h = m.dims(orient)
        rng = self.rng.random()
        if rng < 0.55:
            ns[mid]["pos"] = (
                max(0, min(self.rows - h, pos[0] + self.rng.choice((-1, 0, 0, 1)))),
                max(0, min(self.cols - w, pos[1] + self.rng.choice((-1, 0, 0, 1)))),
            )
        elif m.rotatable:
            ns[mid]["orient"] = (orient + self.rng.choice((90, 270))) % 360
        return ns

    def solve(self, timeout=20, on_solution=None):
        t0 = time.time()
        print(
            f"[heuristic] 开始搜索: {self.rows}x{self.cols} 画布, "
            f"固定模块 {len(self.fixed)} 个, 可动模块 {len(self.movable)} 个, "
            f"连接 {len(self.nets)} 条, 超时 {timeout:.1f}s, seed={self.seed}",
            flush=True,
        )
        best = None
        best_cost = 1e9
        emitted = 0
        self._last_ok = None

        def emit(state, cost, paths):
            """发现（更优的）可行布局：立即输出，然后继续搜索。"""
            nonlocal best, best_cost, emitted
            best = {k: dict(v) for k, v in state.items()}
            best_cost = cost
            emitted += 1
            index = None
            if on_solution is not None:
                try:
                    index = on_solution({"state": best, "paths": paths,
                                         "cost": cost})
                except Exception as e:  # noqa: BLE001
                    print(f"[heuristic] 输出可行解失败: {e}", flush=True)
            tail = (f" -> 已输出 sol{index}.svg / sol{index}.txt"
                    if index is not None else "")
            print(
                f"[heuristic] {time.time()-t0:6.1f}s 发现可行布局 #{emitted}, "
                f"传送带格数 {cost}, 位置 "
                + str({k: (v["pos"][0] + 1, v["pos"][1] + 1, v["orient"])
                       for k, v in best.items()}) + tail,
                flush=True,
            )

        if not self.movable:
            state = {}
            cur = self.evaluate(state)
            if cur < 1e8:
                print("[heuristic] 无可动模块，直接评估固定布局", flush=True)
                return state, cur
            return None, best_cost
        attempts = 0
        last_report = 0.0
        while time.time() - t0 < timeout:
            state = self.random_state()
            if state is None:
                continue
            attempts += 1
            cur = self.evaluate(state)
            if cur < best_cost and self._last_ok is not None \
                    and self._last_ok[0] is state:
                emit(state, cur, self._last_ok[2])
            now = time.time() - t0
            if now - last_report >= 2.0:
                best_txt = f"{best_cost:.0f}" if best_cost < 1e9 else "-"
                print(
                    f"[heuristic] {now:6.1f}s 已尝试 {attempts} 个随机布局, "
                    f"当前最优传送带格数 {best_txt}",
                    flush=True,
                )
                last_report = now
            T = 1.5
            stuck = 0
            last_beat = time.time()
            while time.time() - t0 < timeout:
                cand = self.neighbor(state)
                cc = self.evaluate(cand)
                if cc >= 1e9:
                    stuck += 1
                else:
                    stuck = 0
                if cc <= cur or self.rng.random() < math.exp(-(cc - cur) / max(T, 0.05)):
                    state, cur = cand, cc
                    if cur < best_cost and self._last_ok is not None \
                            and self._last_ok[0] is cand:
                        emit(state, cur, self._last_ok[2])
                T = max(0.02, T * 0.9998)
                # 心跳：长时间没有改进也让日志（和网页）知道还在跑
                if time.time() - last_beat >= 5.0:
                    last_beat = time.time()
                    best_txt = f"{best_cost:.0f}" if best_cost < 1e9 else "-"
                    print(
                        f"[heuristic] {time.time()-t0:6.1f}s 仍在搜索… "
                        f"当前最优传送带格数 {best_txt}, 本轮回火次数 {stuck}",
                        flush=True,
                    )
                if stuck > 4000:
                    break
        if best is None:
            print(
                f"[heuristic] 搜索结束，未找到可行布局 (耗时 {time.time()-t0:.1f}s)",
                flush=True,
            )
        else:
            print(
                f"[heuristic] 搜索结束，共输出 {emitted} 个可行布局，"
                f"最优传送带格数 {best_cost:.0f} (耗时 {time.time()-t0:.1f}s)",
                flush=True,
            )
        return best, best_cost

    def render(self, state, paths):
        grid = [[None] * self.cols for _ in range(self.rows)]
        for mid, m in self.modules.items():
            if m.fixed:
                pos, orient = m.fixed_pos, 0
            else:
                pos, orient = state[mid]["pos"], state[mid]["orient"]
            for r, c in m.occ(pos, orient):
                grid[r][c] = mid
        for k, p in enumerate(paths):
            if p:
                for r, c in p:
                    if grid[r][c] is None:
                        grid[r][c] = f"L{k + 1}"
        return grid


def show(cfg, state, best_cost, paths, output=None):
    s = Solver(cfg)
    print("\n=== 结果 ===")
    for mid, m in s.movable.items():
        v = state.get(mid)
        if v:
            print(f"{mid}: 行 {v['pos'][0]+1}, 列 {v['pos'][1]+1}, "
                  f"旋转 {v['orient']}°")
    print("传送带总格数:", best_cost)
    for i, net in enumerate(cfg["nets"]):
        p = paths[i] or []
        print(f"  {net['from']}->{net['to']}: " +
              ", ".join(f"({r+1},{c+1})" for r, c in p))
    placements = {}
    for mid, m in s.fixed.items():
        placements[mid] = (m.fixed_pos, 0)
    for mid, v in state.items():
        placements[mid] = (tuple(v["pos"]), v["orient"])
    placements = {mid: (tuple(p), o) for mid, (p, o) in placements.items()}
    if output:
        base = os.path.splitext(output)[0]
    else:
        base = layout_viz.default_output_prefix(cfg["_config_path"])
    os.makedirs(os.path.dirname(os.path.abspath(base)) or ".", exist_ok=True)
    svg = base + ".svg"
    layout_viz.save_svg(svg, cfg, s.modules, placements, paths)
    print("SVG 可视化 ->", svg)
    print("\n字符画预览：")
    print(layout_viz.render_ascii(cfg, s.modules, placements, paths))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--timeout", type=float, default=25)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--output", default=None,
                    help="SVG 文件名前缀")
    args = ap.parse_args()
    cfg = json.load(open(args.config, encoding="utf-8"))
    cfg["_config_path"] = args.config

    base = args.output or layout_viz.default_output_prefix(args.config)
    os.makedirs(os.path.dirname(os.path.abspath(base)) or ".", exist_ok=True)
    writer = layout_viz.SolutionWriter(cfg, base)
    print(f"[heuristic] 增量输出目录: {os.path.dirname(os.path.abspath(base))}"
          f"（找到可行解即写盘，数量不限）",
          flush=True)

    solver = Solver(cfg, seed=args.seed)
    state, cost = solver.solve(timeout=args.timeout, on_solution=writer.submit)
    if state is not None and cost < 1e8:
        total, paths, _ = solver.route(state)
        if total is not None and all(paths) and total not in writer.costs:
            writer.submit({"state": state, "paths": paths, "cost": total})
    writer.close()
    print(f"共输出 {writer.count} 个可行解 -> {writer.solution_file}", flush=True)
    if writer.count == 0:
        print("[heuristic] 本次没有找到可行解，上一次的结果保持不变",
              flush=True)
    for err in writer.errors:
        print(f"[heuristic] 输出告警: {err}", flush=True)

    if state is None or cost >= 1e8:
        print("没有找到可行布局；请增大 timeout 或检查端口/连接配置。")
        sys.exit(1)
    total, paths, _ = solver.route(state)
    if total is None and solver._last_ok is not None:
        total, paths = solver._last_ok[1], solver._last_ok[2]
    show(cfg, state, total, paths, args.output)


if __name__ == "__main__":
    main()
