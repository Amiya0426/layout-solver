#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模块布局 + 传送带可视化：输出 SVG 与改进的字符画。

同时提供“边求解边输出”的增量写盘工具 SolutionWriter：
每找到一个新的可行解，就立即追加一行 JSONL 并落盘 solN.svg / solN.txt，
写盘在后台线程完成，不阻塞求解主循环。
"""

import os
import json
import queue
import re
import threading
import unicodedata


CELL = 44
MARGIN = 40

PALETTE = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
]
MOD_FILL = "#eef3f9"
MOD_STROKE = "#33475b"
Y_FILL = "#fdeaea"


def _port_direction(module, pos, orient, pid, port_cell):
    """返回该端口在网格中的实际朝外方向（N/S/E/W）。"""
    p = module.ports.get(pid)
    if not p:
        return None
    # 模块内部 cell 相对 offset
    r0, c0 = pos
    for off in p["cells"]:
        # 计算旋转后的相对坐标，便于反查原始方向
        if orient == 0:
            pr, pc, d = off[0], off[1], p["dir"]
        else:
            rr, cc = off
            if orient == 90:
                pr, pc = cc, module.h - 1 - rr
            elif orient == 180:
                pr, pc = module.h - 1 - rr, module.w - 1 - cc
            else:
                pr, pc = module.w - 1 - cc, rr
            d = {
                90:  {"N": "E", "E": "S", "S": "W", "W": "N"},
                180: {"N": "S", "E": "W", "S": "N", "W": "E"},
                270: {"N": "W", "E": "N", "S": "E", "W": "S"},
            }[orient][p["dir"]]
        if (r0 + pr, c0 + pc) == port_cell:
            return d
    return None


def port_geometry(cfg, modules, placements, paths):
    """返回每条 net 实际使用的端口格与外部第一格。"""
    result = []
    for ni, (net, path) in enumerate(zip(cfg["nets"], paths)):
        if not path:
            result.append(None)
            continue
        src_mod = modules[net["from"]]
        dst_mod = modules[net["to"]]
        fpid = net.get("from_port", "OUT")
        tpid = net.get("to_port", "IN")
        src_pos, src_ori = placements[net["from"]]
        dst_pos, dst_ori = placements[net["to"]]

        src = None
        for pc, oc in src_mod.port_outside(src_pos, src_ori, fpid):
            if oc == path[0]:
                src = (pc, oc)
                break
        dst = None
        for pc, oc in dst_mod.port_outside(dst_pos, dst_ori, tpid):
            if oc == path[-1]:
                dst = (pc, oc)
                break
        if src is None or dst is None:
            result.append(None)
            continue
        result.append({
            "src": {"cell": src[0], "outside": src[1],
                    "dir": _port_direction(src_mod, src_pos, src_ori, fpid, src[0])},
            "dst": {"cell": dst[0], "outside": dst[1],
                    "dir": _port_direction(dst_mod, dst_pos, dst_ori, tpid, dst[0])},
        })
    return result


def path_cells(paths):
    """返回每个 cell 被哪些 net 使用。"""
    at = {}
    for i, p in enumerate(paths):
        for idx, cell in enumerate(p):
            at.setdefault(cell, []).append((i, idx, len(p)))
    return at


def is_straight(path, idx):
    if idx <= 0 or idx >= len(path) - 1:
        return None
    a, b, c = path[idx - 1], path[idx], path[idx + 1]
    if a[0] == b[0] == c[0]:
        return "H"
    if a[1] == b[1] == c[1]:
        return "V"
    return None


def save_svg(filename, cfg, modules, placements, paths, title="layout"):
    """placements: {mid: (pos, orient)}，含 fixed 与 movable。"""
    rows, cols = cfg["rows"], cfg["cols"]
    W = MARGIN * 2 + cols * CELL
    H = MARGIN * 2 + rows * CELL
    parts = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" font-family="Segoe UI, Arial, sans-serif">'
    )
    parts.append('<rect x="0" y="0" width="%d" height="%d" fill="white"/>' % (W, H))
    # 网格
    for r in range(rows + 1):
        y = MARGIN + r * CELL
        parts.append(f'<line x1="{MARGIN}" y1="{y}" x2="{MARGIN + cols*CELL}" '
                     f'y2="{y}" stroke="#ddd" stroke-width="1"/>')
    for c in range(cols + 1):
        x = MARGIN + c * CELL
        parts.append(f'<line x1="{x}" y1="{MARGIN}" x2="{x}" '
                     f'y2="{MARGIN + rows*CELL}" stroke="#ddd" stroke-width="1"/>')
    # 坐标
    for c in range(cols):
        parts.append(f'<text x="{MARGIN+c*CELL+CELL/2}" y="{MARGIN-8}" '
                     f'text-anchor="middle" font-size="11" fill="#777">{c+1}</text>')
    for r in range(rows):
        parts.append(f'<text x="{MARGIN-8}" y="{MARGIN+r*CELL+CELL/2+4}" '
                     f'text-anchor="end" font-size="11" fill="#777">{r+1}</text>')

    # 传送带先画
    for i, path in enumerate(paths):
        if not path:
            continue
        color = PALETTE[i % len(PALETTE)]
        pts = []
        for (r, c) in path:
            pts.append((MARGIN + c * CELL + CELL / 2,
                        MARGIN + r * CELL + CELL / 2))
        poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        parts.append(
            f'<polyline points="{poly}" fill="none" stroke="{color}" '
            f'stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>'
        )
        # 起点 S / 终点 E 标注
        for label, (px, py) in (("S", pts[0]), ("E", pts[-1])):
            parts.append(
                f'<circle cx="{px}" cy="{py}" r="9" fill="white" '
                f'stroke="{color}" stroke-width="1.5"/>'
            )
            parts.append(
                f'<text x="{px}" y="{py + 4}" text-anchor="middle" '
                f'font-size="11" font-weight="bold" fill="{color}">{label}</text>'
            )
        # 方向箭头：起点、每个拐弯、终点；同一线段只画一次，避免短路径重复箭头
        if len(pts) >= 2:
            import math
            segments = [(pts[0], pts[1])]
            for i in range(1, len(pts) - 1):
                d1 = (pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
                d2 = (pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
                if d1 != d2:
                    segments.append((pts[i], pts[i + 1]))
            if len(pts) > 2:
                segments.append((pts[-2], pts[-1]))

            seen_segments = set()
            for x1, y1, x2, y2 in (
                (a[0], a[1], b[0], b[1]) for a, b in segments
            ):
                key = (round(x1, 3), round(y1, 3), round(x2, 3), round(y2, 3))
                if key in seen_segments:
                    continue
                seen_segments.add(key)
                ang = math.atan2(y2 - y1, x2 - x1)
                ax1 = x2 - 10 * math.cos(ang - 0.35)
                ay1 = y2 - 10 * math.sin(ang - 0.35)
                ax2 = x2 - 10 * math.cos(ang + 0.35)
                ay2 = y2 - 10 * math.sin(ang + 0.35)
                parts.append(
                    f'<polygon points="{x2},{y2} {ax1:.1f},{ay1:.1f} {ax2:.1f},{ay2:.1f}" '
                    f'fill="{color}"/>'
                )

    # 模块
    for mid, (pos, orient) in placements.items():
        mod = modules[mid]
        r0, c0 = pos
        cells = mod.occ((r0, c0), orient)
        rs = [x[0] for x in cells]
        cs = [x[1] for x in cells]
        x = MARGIN + min(cs) * CELL
        y = MARGIN + min(rs) * CELL
        w = (max(cs) - min(cs) + 1) * CELL
        h = (max(rs) - min(rs) + 1) * CELL
        fill = Y_FILL if mid.startswith("Y") else MOD_FILL
        parts.append(
            f'<rect x="{x+2}" y="{y+2}" width="{w-4}" height="{h-4}" rx="8" '
            f'fill="{fill}" stroke="{MOD_STROKE}" stroke-width="2"/>'
        )
        parts.append(
            f'<text x="{x+w/2}" y="{y+h/2+6}" text-anchor="middle" '
            f'font-size="15" font-weight="bold" fill="#24303c">{mid}</text>'
        )

    # 端口标在模块内部格 + 连接到传送带端点
    geo_list = port_geometry(cfg, modules, placements, paths)
    for ni, geo in enumerate(geo_list):
        if not geo:
            continue
        color = PALETTE[ni % len(PALETTE)]
        for kind in ("src", "dst"):
            pc = geo[kind]["cell"]
            oc = geo[kind]["outside"]
            px = MARGIN + pc[1] * CELL + CELL / 2
            py = MARGIN + pc[0] * CELL + CELL / 2
            ox = MARGIN + oc[1] * CELL + CELL / 2
            oy = MARGIN + oc[0] * CELL + CELL / 2
            # 模块端口格 -> 传送带第一格 / 最后一格 -> 模块端口格
            parts.append(
                f'<line x1="{px}" y1="{py}" x2="{ox}" y2="{oy}" '
                f'stroke="{color}" stroke-width="2.5" stroke-dasharray="3,2"/>'
            )
            label = f"S{ni + 1}" if kind == "src" else f"E{ni + 1}"
            parts.append(
                f'<circle cx="{px}" cy="{py}" r="10" fill="white" '
                f'stroke="{color}" stroke-width="2"/>'
            )
            parts.append(
                f'<text x="{px}" y="{py+4}" text-anchor="middle" '
                f'font-size="10" font-weight="bold" fill="{color}">{label}</text>'
            )

    # 交叉点标记
    for cell, lst in path_cells(paths).items():
        if len(lst) >= 2:
            cx = MARGIN + cell[1] * CELL + CELL / 2
            cy = MARGIN + cell[0] * CELL + CELL / 2
            parts.append(
                f'<circle cx="{cx}" cy="{cy}" r="7" fill="none" '
                f'stroke="#111" stroke-width="2"/>'
            )
            parts.append(
                f'<text x="{cx}" y="{cy+4}" text-anchor="middle" '
                f'font-size="11" font-weight="bold" fill="#111">×</text>'
            )

    parts.append("</svg>")
    with open(filename, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return filename


def render_ascii(cfg, modules, placements, paths):
    """字符画：端口标在模块格内（[A/S1]、[B/E1]），带格只画方向箭头或交叉点。"""
    rows, cols = cfg["rows"], cfg["cols"]
    grid = [[None] * cols for _ in range(rows)]
    for mid, (pos, orient) in placements.items():
        mod = modules[mid]
        for r, c in mod.occ(pos, orient):
            grid[r][c] = ("M", mid)
    portmarks = {}
    for ni, geo in enumerate(port_geometry(cfg, modules, placements, paths)):
        if not geo:
            continue
        portmarks.setdefault(geo["src"]["cell"], []).append(f"S{ni + 1}")
        portmarks.setdefault(geo["dst"]["cell"], []).append(f"E{ni + 1}")

    def dch(a, b):
        if b[0] == a[0]:
            return "E" if b[1] > a[1] else "W"
        return "S" if b[0] > a[0] else "N"

    arrow = {"E": "→", "W": "←", "S": "↓", "N": "↑"}

    def direction_at(p, idx):
        """返回带在路径第 idx 格上的行进方向。"""
        n = len(p)
        if n == 1:
            return "E"
        if idx == 0:
            return dch(p[0], p[1])
        if idx == n - 1:
            return dch(p[-2], p[-1])
        return dch(p[idx], p[idx + 1])

    belt_entries = {}
    for ni, p in enumerate(paths):
        if not p:
            continue
        for idx, cell in enumerate(p):
            belt_entries.setdefault(cell, []).append((ni, idx, len(p)))

    for cell, entries in belt_entries.items():
        r, c = cell
        if grid[r][c] is not None and grid[r][c][0] == "M":
            continue
        dirs = [direction_at(paths[ni], idx) for ni, idx, _n in entries]
        has_v = any(d in ("N", "S") for d in dirs)
        has_h = any(d in ("E", "W") for d in dirs)
        if len(entries) >= 2 and has_v and has_h:
            grid[r][c] = ("B", ["╋"])
        else:
            grid[r][c] = ("B", [arrow[dirs[0]]])

    def token(v, r, c):
        if v is None:
            return "·"
        if v[0] == "M":
            marks = portmarks.get((r, c), [])
            if marks:
                return f"[{v[1]}/{'/'.join(marks)}]"
            return f"[{v[1]}]"
        chars = v[1]
        return chars[0]

    # 统一按终端显示宽度对齐，避免中文/全角字符导致列错位
    raw = []
    for r in range(rows):
        row = []
        for c in range(cols):
            row.append(token(grid[r][c], r, c))
        raw.append(row)

    def disp_width(text):
        w = 0
        for ch in text:
            if unicodedata.combining(ch):
                continue
            w += 2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1
        return w

    def pad(text, width):
        gap = width - disp_width(text)
        if gap <= 0:
            return text
        left = gap // 2
        return " " * left + text + " " * (gap - left)

    def rjust(text, width):
        gap = width - disp_width(text)
        return " " * max(gap, 0) + text

    cell_w = max((disp_width(x) for row in raw for x in row), default=1)
    cell_w = max(cell_w, 1)
    field_w = cell_w + 1
    label_w = max((disp_width(str(r + 1)) for r in range(rows)), default=1)

    header = " " * (label_w + 2) + "".join(
        pad(str(c + 1), field_w) for c in range(cols)
    )
    lines = [header]
    for r in range(rows):
        body = "".join(pad(x, field_w) for x in raw[r])
        lines.append(rjust(str(r + 1), label_w) + "  " + body)
    return "\n".join(lines)


def result_path(base, suffix):
    root, ext = os.path.splitext(base)
    return f"{root}{suffix}{ext or '.txt'}"


def problem_name(config_path):
    stem = os.path.splitext(os.path.basename(config_path))[0]
    if stem.startswith("config."):
        stem = stem[len("config."):]
    return stem


def default_output_prefix(config_path):
    """默认输出到 <项目根>/result/<题目名>/<题目名>。

    项目根 = 本文件所在目录（src/）的上一级。这样无论从哪个工作目录、
    或从 WebUI 以子进程方式调用，结果都统一落到项目的 result/ 下，
    而不是散落在当前工作目录里。
    """
    name = problem_name(config_path)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "result", name, name)


# ---------------------------------------------------------------------------
# 独立命令行：config + solutions.jsonl -> 批量 SVG / 字符画
# ---------------------------------------------------------------------------
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


class VizModule:
    def __init__(self, mid, cfg, fixed_pos=None):
        self.id = mid
        self.w = cfg["w"]
        self.h = cfg["h"]
        self.fixed_pos = fixed_pos
        self.ports = {
            p["id"]: {"cells": [tuple(x) for x in p["cells"]], "dir": p["dir"]}
            for p in cfg.get("ports", [])
        }

    def occ(self, pos, orient):
        r0, c0 = pos
        out = []
        for i in range(self.h):
            for j in range(self.w):
                if orient == 0:
                    rr, cc = i, j
                else:
                    rr, cc = ROT_CELL[orient](i, j, self.h, self.w)
                out.append((r0 + rr, c0 + cc))
        return out

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
            dr, dc = {"N": (-1, 0), "S": (1, 0), "W": (0, -1), "E": (0, 1)}[d]
            out.append(((r0 + pr, c0 + pc), (r0 + pr + dr, c0 + pc + dc)))
        return out


def build_modules(cfg):
    modules = {}
    for m in cfg.get("fixed", []):
        modules[m["id"]] = VizModule(m["id"], m, fixed_pos=tuple(m["pos"]))
    for m in cfg.get("movable", []):
        modules[m["id"]] = VizModule(m["id"], m)
    return modules


def read_solutions(path, stats=None):
    """读取 JSONL；stats 可选，用于回传 skipped（半行）计数。"""
    sols = []
    skipped = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                sols.append(json.loads(line))
            except Exception:
                # 求解中并发写入时可能读到半行，直接跳过即可
                skipped += 1
    if skipped:
        print(f"[viz] 跳过 {skipped} 行不完整的 JSONL 记录")
    if stats is not None:
        stats["skipped"] = skipped
    return sols


def rewrite_solutions_file(path, sols):
    """把（可能含半行的）JSONL 重写成只含有效记录的文件。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for s in sols:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def solution_to_layout(cfg, sol):
    """JSONL 中的 1-based state/paths 转回 0-based 布局。"""
    modules = build_modules(cfg)
    placements = {}
    for mid, m in modules.items():
        if m.fixed_pos is not None:
            placements[mid] = (m.fixed_pos, 0)
    for mid, v in sol.get("state", {}).items():
        placements[mid] = ((v["row"] - 1, v["col"] - 1), int(v.get("rotate", 0)))
    paths = [
        [(r - 1, c - 1) for r, c in p]
        for p in sol.get("paths", [])
    ]
    return modules, placements, paths


def solution_to_record(index, sol):
    """内部 0-based 解 -> JSONL 记录（1-based，与历史格式一致）。"""
    return {
        "solution_index": index,
        "cost": sol.get("cost"),
        "state": {
            k: {"row": v["pos"][0] + 1,
                "col": v["pos"][1] + 1,
                "rotate": int(v.get("orient", 0))}
            for k, v in (sol.get("state") or {}).items()
        },
        "paths": [
            [[r + 1, c + 1] for r, c in p]
            for p in (sol.get("paths") or [])
        ],
    }


def render_solution_files(cfg_data, prefix, index, sol):
    """把单个 0-based 解渲染成 <prefix>.solN.svg / <prefix>.solN.txt。"""
    record = solution_to_record(index, sol)
    modules, placements, paths = solution_to_layout(cfg_data, record)
    svg = f"{prefix}.sol{index}.svg"
    save_svg(svg, cfg_data, modules, placements, paths, title=f"solution {index}")
    txt_file = f"{prefix}.sol{index}.txt"
    with open(txt_file, "w", encoding="utf-8") as f:
        f.write(render_ascii(cfg_data, modules, placements, paths) + "\n")
    return svg, txt_file


def solution_files(prefix, index):
    return f"{prefix}.sol{index}.svg", f"{prefix}.sol{index}.txt"


def prune_solution_files(prefix, keep):
    """删除编号 >= keep 的历史 solN.svg/txt，避免新旧结果混在一起。"""
    d = os.path.dirname(os.path.abspath(prefix)) or "."
    base = os.path.basename(prefix)
    if not os.path.isdir(d):
        return 0
    pattern = re.compile(rf"^{re.escape(base)}\.sol(\d+)\.(svg|txt)$")
    removed = 0
    for fn in os.listdir(d):
        m = pattern.match(fn)
        if not m or int(m.group(1)) < keep:
            continue
        try:
            os.remove(os.path.join(d, fn))
            removed += 1
        except OSError:
            pass
    return removed


def best_solution_index(sols):
    """返回传送带格数最小的解的下标（无有效 cost 时返回 None）。"""
    best_i, best_c = None, None
    for i, s in enumerate(sols):
        c = s.get("cost")
        if not isinstance(c, (int, float)):
            continue
        if best_c is None or c < best_c:
            best_i, best_c = i, c
    return best_i


def render_best(cfg_data, prefix, sols):
    """用 JSONL 里最优的那个解重画 <prefix>.svg。"""
    i = best_solution_index(sols)
    if i is None:
        return None
    modules, placements, paths = solution_to_layout(cfg_data, sols[i])
    svg = prefix + ".svg"
    save_svg(svg, cfg_data, modules, placements, paths, title=f"best solution {i}")
    return svg, i


class SolutionWriter:
    """求解过程中增量输出可行解，**数量不限**。

    用法::

        writer = SolutionWriter(cfg, "result/toy/toy")
        writer.submit({"state": {...}, "paths": [...], "cost": 12})  # 立即返回
        writer.close()                                                # 等待落盘

    - `submit` 只做编号 + 入队，毫秒级返回，不阻塞求解；
    - 后台线程负责追加 JSONL（先写，便于网页立刻看到）和渲染 solN.svg/txt；
    - 找到多少解就写多少，编号连续；
    - 真正的清空发生在“第一个可行解落盘时”：本次没找到解时，
      上一次的结果会原样保留，不会被空跑清掉。
    """

    def __init__(self, cfg_data, prefix):
        self.cfg_data = cfg_data
        self.prefix = prefix
        self.best_svg = prefix + ".svg"
        self.count = 0
        self.costs = []
        self.errors = []
        self.solution_file = prefix + ".solutions.jsonl"
        d = os.path.dirname(os.path.abspath(prefix))
        if d:
            os.makedirs(d, exist_ok=True)
        self._fh = None          # 延迟打开：直到出现第一个解才覆盖旧结果
        self._reset_done = False
        self._queue = queue.Queue()
        self._lock = threading.Lock()
        self._closed = False
        self._thread = threading.Thread(
            target=self._run, name="solution-writer", daemon=True)
        self._thread.start()

    def submit(self, sol):
        """入队一个 0-based 解；返回分配到的编号，已关闭时返回 None。"""
        with self._lock:
            if self._closed:
                return None
            index = self.count
            self.count += 1
            self.costs.append(sol.get("cost"))
        self._queue.put((index, sol))
        return index

    def _reset_outputs(self):
        """第一次真正写出解之前，先把上一轮的产物清掉（一次求解 = 一份结果）。"""
        prune_solution_files(self.prefix, 0)
        if os.path.isfile(self.best_svg):
            try:
                os.remove(self.best_svg)
            except OSError:
                pass
        self._fh = open(self.solution_file, "w", encoding="utf-8")
        self._reset_done = True

    def _run(self):
        while True:
            item = self._queue.get()
            if item is None:
                return
            index, sol = item
            try:
                if not self._reset_done:
                    self._reset_outputs()
                record = solution_to_record(index, sol)
                self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                self._fh.flush()
                render_solution_files(self.cfg_data, self.prefix, index, sol)
            except Exception as e:  # noqa: BLE001
                self.errors.append(f"sol{index}: {e}")

    def close(self, timeout=None):
        """等待所有排队中的解写完，然后关闭文件。"""
        if self._closed:
            return
        self._closed = True
        self._queue.put(None)
        self._thread.join(timeout)
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass


def batch_render(cfg, solutions_file, output_prefix=None, preview=False,
                 limit=None, only_missing=False, prune=False):
    cfg_data = json.load(open(cfg, encoding="utf-8"))
    stats = {}
    sols = read_solutions(solutions_file, stats)
    if prune and stats.get("skipped"):
        # 上一次求解被中途停止，文件尾部可能留下半行：顺手清掉
        try:
            rewrite_solutions_file(solutions_file, sols)
            print(f"[viz] 已清理 JSONL 尾部残缺记录，共 {len(sols)} 个有效解")
        except OSError as e:
            print(f"[viz] 清理 JSONL 失败（可能正在被写入）: {e}")
    total = len(sols)
    if limit:
        sols = sols[:limit]
    prefix = output_prefix or default_output_prefix(cfg)
    os.makedirs(os.path.dirname(os.path.abspath(prefix)) or ".", exist_ok=True)
    if prune:
        prune_solution_files(prefix, total)

    made = []
    skipped = 0
    for i, sol in enumerate(sols):
        svg = f"{prefix}.sol{i}.svg"
        txt_file = f"{prefix}.sol{i}.txt"
        if only_missing and os.path.isfile(svg) and os.path.isfile(txt_file):
            skipped += 1
            continue
        modules, placements, paths = solution_to_layout(cfg_data, sol)
        save_svg(svg, cfg_data, modules, placements, paths,
                 title=f"solution {i}")
        txt = render_ascii(cfg_data, modules, placements, paths)
        with open(txt_file, "w", encoding="utf-8") as f:
            f.write(txt + "\n")
        made.append((i, sol.get("cost", "?"), svg, txt_file))
        if preview:
            print(f"\n===== 可行解 {i}  cost={sol.get('cost', '?')} =====")
            print(txt)
    if only_missing:
        print(f"[viz] 已有 {skipped} 个解的可视化文件，跳过；补生成 {len(made)} 个")
    return made


def main():
    import argparse
    ap = argparse.ArgumentParser(
        description="从 config + solutions.jsonl 批量生成 SVG/字符画")
    ap.add_argument("config", help="布局配置 JSON")
    ap.add_argument("solutions", nargs="?",
                    help="solutions.jsonl；缺省使用 config 同名前缀")
    ap.add_argument("--output", default=None,
                    help="输出前缀，例如 result；默认与 config 同名")
    ap.add_argument("--limit", type=int, default=None,
                    help="只处理前 N 个可行解")
    ap.add_argument("--preview", action="store_true",
                    help="终端同时打印每个解的字符画")
    ap.add_argument("--only-missing", action="store_true",
                    help="只补生成缺失的 solN.svg/txt，已存在的跳过")
    ap.add_argument("--prune", action="store_true",
                    help="删除编号超出 JSONL 记录数的历史 solN 文件")
    ap.add_argument("--best", action="store_true",
                    help="顺带用最优解重画 <前缀>.svg")
    args = ap.parse_args()

    if args.solutions is None:
        args.solutions = default_output_prefix(args.config) + ".solutions.jsonl"
    if not os.path.exists(args.solutions):
        print(f"找不到可行解文件: {args.solutions}")
        raise SystemExit(1)
    made = batch_render(args.config, args.solutions, args.output,
                        preview=args.preview, limit=args.limit,
                        only_missing=args.only_missing, prune=args.prune)
    print(f"\n生成 {len(made)} 个可视化：")
    for i, cost, svg, txt in made:
        print(f"  sol{i} cost={cost}: {svg} / {txt}")
    if args.best:
        cfg_data = json.load(open(args.config, encoding="utf-8"))
        prefix = args.output or default_output_prefix(args.config)
        got = render_best(cfg_data, prefix, read_solutions(args.solutions))
        if got:
            print(f"最优解可视化 -> {got[0]} (sol{got[1]})")


if __name__ == "__main__":
    main()
