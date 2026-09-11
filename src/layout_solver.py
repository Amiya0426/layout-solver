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
        self._best_unrouted = None   # 见过的最好进度：还剩几条 net 没布通

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

    def _random_layout(self):
        """随机摆一版（只管模块不重叠、不出界）。"""
        state = {}
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
                return None
        return state

    def random_state(self, tries=8):
        """随机摆位：抽 tries 版，取「有救程度」最好的那版。

        不硬性要求“端口够用 + 起终点连通”——实测 config.example.json 上
        随手摆 1 万版都过不了这个硬条件（自由空间被模块切碎），一味重试只会
        把时间烧光。改成**软打分取最好**：抽几版挑分最低的，剩下的交给 SA 的
        部分分代价去爬（布不通的 net 数本身就会惩罚端口被堵/空间被切断）。
        """
        best, best_score = None, None
        for _ in range(max(1, tries)):
            st = self._random_layout()
            if st is None:
                continue
            sc = self.placement_score(st)
            if sc == 0:
                return st
            if best_score is None or sc < best_score:
                best, best_score = st, sc
        return best

    def endpoint_need(self):
        """(模块, 端口) -> 挂了几条 net（= 需要几个互不相同的端点格）。"""
        need = {}
        for net in self.nets:
            for mid, pid in ((net["from"], net.get("from_port", "OUT")),
                             (net["to"], net.get("to_port", "IN"))):
                need[(mid, pid)] = need.get((mid, pid), 0) + 1
        return need

    def placement_score(self, state):
        """这个摆位有多「没救」：0 = 端口都够用、每条 net 起终点都连通。

        两项都是路由问题的直接征兆（真去 route() 一遍太贵，这里只做便宜的估算）：
          1. 端口外侧格少于它挂的 net 数（缺几个算几分）——端点格不够用；
          2. 起点格与终点格不落在同一块自由空间里（一条算 1 分）——根本没路。
        """
        if not self.valid(state):
            return 10 ** 6
        occ = self.occupied(state)
        free = {(r, c) for r in range(self.rows) for c in range(self.cols)
                if (r, c) not in occ}

        outs = {}
        for (mid, pid) in self.endpoint_need():
            m = self.modules.get(mid)
            if m is None:
                return 10 ** 6
            if m.fixed:
                pos, orient = m.fixed_pos, 0
            else:
                if mid not in state:
                    return 10 ** 6
                pos, orient = state[mid]["pos"], state[mid]["orient"]
            outs[(mid, pid)] = [oc for _pc, oc in m.port_outside(pos, orient, pid)
                                if oc in free]

        score = 0
        for key, k in self.endpoint_need().items():
            score += max(0, k - len(outs.get(key, [])))

        # 自由空间连通块
        comp = {}
        cid = 0
        for cell in free:
            if cell in comp:
                continue
            dq = deque([cell])
            comp[cell] = cid
            while dq:
                r, c = dq.popleft()
                for dr, dc in DIRV.values():
                    nb = (r + dr, c + dc)
                    if nb in free and nb not in comp:
                        comp[nb] = cid
                        dq.append(nb)
            cid += 1
        for net in self.nets:
            s = {comp[c] for c in outs.get((net["from"], net.get("from_port", "OUT")), [])}
            d = {comp[c] for c in outs.get((net["to"], net.get("to_port", "IN")), [])}
            if not (s & d):
                score += 1
        return score

    def _rebuild_usage(self, paths):
        """按当前 paths 重算 used_count / used_axis / 带格总数。"""
        used_count, used_axis = {}, {}
        total = 0
        for p in paths:
            if not p:
                continue
            total += len(p)
            for i, cell in enumerate(p):
                used_count[cell] = used_count.get(cell, 0) + 1
                if 0 < i < len(p) - 1:
                    a, b = p[i - 1], p[i + 1]
                    if a[0] == b[0]:
                        used_axis.setdefault(cell, "H")
                    elif a[1] == b[1]:
                        used_axis.setdefault(cell, "V")
        return used_count, used_axis, total

    def _route_one(self, ni, free, out_cells, used_count, used_axis):
        """给单条 net 找一条最短路；找不到返回 None。"""
        net = self.nets[ni]
        starts = [o for (_p, o) in out_cells.get((net["from"], net.get("from_port", "OUT")), [])
                  if o in free and used_count.get(o, 0) == 0]
        goals = [o for (_p, o) in out_cells.get((net["to"], net.get("to_port", "IN")), [])
                 if o in free and used_count.get(o, 0) == 0]
        if not starts or not goals:
            return None
        best = None
        for s in starts:
            p = bfs_cross(s, set(goals), free, used_count, used_axis)
            if p and (best is None or len(p) < len(best)):
                best = p
        return best

    def _route_order(self, order, free, out_cells):
        """按给定顺序贪心布线。返回 (带格数, paths, 未布通下标, used_count, used_axis)。"""
        used_count, used_axis = {}, {}
        paths = [None] * len(self.nets)
        total = 0
        unrouted = []
        for ni in order:
            best = self._route_one(ni, free, out_cells, used_count, used_axis)
            if best is None:
                unrouted.append(ni)
                continue
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
        return total, paths, unrouted, used_count, used_axis

    def _ripup_around(self, ni, paths, free, out_cells, radius=2):
        """拆线重布：先算一条「无视其它带」的理想路径，谁挡在路上就拆谁。

        顺序贪心一旦把某片区域堵死，后面几条就永远布不通。做法是先把这条 net
        在**空图**上的最短路找出来（只受模块和端口限制），再把它经过的格连同
        邻近一圈作为“事故现场”，把占用这些格的带一起拆掉，然后按随机顺序
        重排这一小组——让路的概率比硬挤大得多。
        """
        ideal = self._route_one(ni, free, out_cells, {}, {})
        if ideal is None:
            return None                 # 空图都走不通：这是摆位问题，得靠 SA 挪模块
        window = set(ideal)
        if radius > 0:
            for (r, c) in ideal:
                for dr in range(-radius, radius + 1):
                    for dc in range(-radius, radius + 1):
                        if abs(dr) + abs(dc) <= radius:
                            window.add((r + dr, c + dc))
        victims = [ni]
        for other, p in enumerate(paths):
            if other != ni and p and (set(p) & window):
                victims.append(other)
        if len(victims) > 16:           # 波及太多就放弃，别把整张图拆了
            return None
        trial = list(paths)
        for v in victims:
            trial[v] = None
        used_count, used_axis, total = self._rebuild_usage(trial)
        order = list(victims)
        self.rng.shuffle(order)
        for v in order:
            best = self._route_one(v, free, out_cells, used_count, used_axis)
            if best is None:
                return None
            for i, cell in enumerate(best):
                used_count[cell] = used_count.get(cell, 0) + 1
                if 0 < i < len(best) - 1:
                    a, b = best[i - 1], best[i + 1]
                    if a[0] == b[0]:
                        used_axis.setdefault(cell, "H")
                    elif a[1] == b[1]:
                        used_axis.setdefault(cell, "V")
            trial[v] = best
        return trial, total + sum(len(trial[v]) for v in victims)

    def route(self, state):
        """顺序布线 + 拆线重布，返回 (已布通的带格数, paths, 未布通的 net 下标)。

        注意第三个返回值：**允许部分布通**。原来只要有一条 net 布不通就整体
        返回失败（代价 1e9），SA 于是在“全有全无”的代价面上乱撞；现在把
        “还剩几条没布通”交给外层当代价，搜索才有梯度可爬。
        """
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
        best = None
        for _attempt in range(4):
            order = list(range(len(self.nets)))
            self.rng.shuffle(order)
            res = self._route_order(order, free, out_cells)
            if not res[2]:
                return res[0], res[1], []
            if best is None or len(res[2]) < len(best[2]):
                best = res
        total, paths, unrouted, _uc, _ua = best

        # 还有布不通的：按“理想路径”拆线重布，一轮不行就扩大事故现场再来
        for _round in range(4):
            if not unrouted:
                break
            progress = False
            for ni in list(unrouted):
                got = self._ripup_around(ni, paths, free, out_cells,
                                         radius=1 + _round // 2)
                if got is None:
                    continue
                paths, total = got
                unrouted = [n for n in unrouted if not paths[n]]
                progress = True
            if not progress:
                break
        return total, paths, unrouted

    def evaluate(self, state):
        if state is None or not self.valid(state):
            return 1e9
        res = self.route(state)
        # 没布通的 net 用大罚分（但**有梯度**：少一条就少 1000），
        # 这样 SA 至少能朝“布通的条数更多”的方向爬；全布通才是真代价。
        if res[2]:
            if self._best_unrouted is None or len(res[2]) < self._best_unrouted:
                self._best_unrouted = len(res[2])
            return 1e6 + 1000.0 * len(res[2]) + res[0]
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
        if rng < 0.20:
            # 跳一步：把这块搬到一个随机合法位置。只挪一格/转 90 度走不出局部
            # 最优时（例如整块区域被堵死），靠它换个盆地。
            for _ in range(60):
                o2 = self.rng.choice([0, 90, 180, 270]) if m.rotatable else 0
                w2, h2 = m.dims(o2)
                if w2 > self.cols or h2 > self.rows:
                    continue
                ns[mid] = {"pos": (self.rng.randrange(self.rows - h2 + 1),
                                   self.rng.randrange(self.cols - w2 + 1)),
                           "orient": o2}
                if self.valid(ns):
                    return ns
            ns[mid] = {"pos": (pos[0], pos[1]), "orient": orient}
            return ns
        if rng < 0.75:
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
            now = time.time() - t0
            if state is None:
                # 摆位要么放不下、要么端口接不出去/起终点不连通，换一个再试
                time.sleep(0.005)
                if now - last_report >= 2.0:
                    print(f"[heuristic] {now:6.1f}s 还在找「端口接得出去 + "
                          f"起终点连通」的摆位…", flush=True)
                    last_report = now
                continue
            attempts += 1
            cur = self.evaluate(state)
            if cur < best_cost and self._last_ok is not None \
                    and self._last_ok[0] is state:
                emit(state, cur, self._last_ok[2])
            now = time.time() - t0
            if now - last_report >= 2.0:
                best_txt = f"{best_cost:.0f}" if best_cost < 1e9 else "-"
                near = ("" if self._best_unrouted is None
                        else f", 最接近时还差 {self._best_unrouted} 条 net 没布通")
                print(
                    f"[heuristic] {now:6.1f}s 已尝试 {attempts} 个随机布局, "
                    f"当前最优传送带格数 {best_txt}{near}",
                    flush=True,
                )
                last_report = now
            T = 1.5
            stuck = 0
            last_beat = time.time()
            while time.time() - t0 < timeout:
                cand = self.neighbor(state)
                cc = self.evaluate(cand)
                if cc >= 1e6:           # 还有 net 没布通（1e6 起是罚分档）
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
                    near = ("" if self._best_unrouted is None
                            else f", 最接近时还差 {self._best_unrouted} 条 net")
                    print(
                        f"[heuristic] {time.time()-t0:6.1f}s 仍在搜索… "
                        f"当前最优传送带格数 {best_txt}{near}, "
                        f"本轮回火次数 {stuck}",
                        flush=True,
                    )
                if stuck > 4000:
                    break
        if best is None:
            near = ("" if self._best_unrouted is None
                    else f"；最接近的一次还差 {self._best_unrouted} 条 net 没布通")
            print(
                f"[heuristic] 搜索结束，未找到可行布局 (耗时 {time.time()-t0:.1f}s){near}",
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
        total, paths, unrouted = solver.route(state)
        if not unrouted and all(paths) and total not in writer.costs:
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
    total, paths, unrouted = solver.route(state)
    if unrouted and solver._last_ok is not None:
        total, paths = solver._last_ok[1], solver._last_ok[2]
    show(cfg, state, total, paths, args.output)


if __name__ == "__main__":
    main()
