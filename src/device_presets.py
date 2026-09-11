#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设备尺寸表 -> 模块预设（只用标准库，连 openpyxl 都不需要）。

为什么要有这个文件
------------------
在 WebUI 里手填一个设备的 ``w/h/ports`` 很容易错：端口在第几行第几列、朝哪个
方向，都要一格一格点出来。而《设备尺寸.xlsx》本来就把这些信息写清楚了：

    设备名称 | 宽度 | 高度 | 北面 | 南面 | 西面 | 东面

每个面是一串标记，一个标记占一格：

    si 固体入口    so 固体出口
    fi 液体入口    fo 液体出口
    gi 气体入口    go 气体出口
    n  普通面（没有端口）

读法约定（与俯视图上“从左到右、从上到下”的读法一致）：

    北面 = 第 0 行，   标记从左到右 = 列 0..w-1，端口朝 N
    南面 = 第 h-1 行， 标记从左到右 = 列 0..w-1，端口朝 S
    西面 = 第 0 列，   标记从上到下 = 行 0..h-1，端口朝 W
    东面 = 第 w-1 列， 标记从上到下 = 行 0..h-1，端口朝 E

同一个面上标记相同的格子会合成一个端口（例如北面 ``nsinsin`` -> 端口 ``SI``，
cells ``[[0,1],[0,3]]``，dir ``N``）：求解器会在这些格子里挑一个不冲突的用。
若同一个标记同时出现在**两个面**上（例如 ``固气转化机（固体产出）`` 的 ``gi``
既在北面又在西面），两个面的方向不同、不能合成一个端口，于是拆成 ``GI-N`` /
``GI-W``。

xlsx 就是一个 zip + 一堆 XML，标准库完全够用：本模块不依赖 openpyxl，和项目
其它部分一样“什么都不用装”。

命令行::

    python src/device_presets.py --list          # 人类可读的表格
    python src/device_presets.py --json          # 打印 JSON（喂给前端/其它脚本）
    python src/device_presets.py --dump          # 写入 data/device_presets.json 缓存
    python src/device_presets.py --xlsx 别的表.xlsx
"""

import argparse
import json
import os
import sys
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter

SOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SOURCE_DIR)
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

XLSX_NAME = "设备尺寸.xlsx"        # 表格的默认文件名
CACHE_NAME = "device_presets.json"  # 表格缺失时用的缓存

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"

#: 标记 -> 中文含义（和表格第二张工作表「标记对照」一致）
MARKER_LABELS = {
    "si": "固体入口",
    "so": "固体出口",
    "fi": "液体入口",
    "fo": "液体出口",
    "gi": "气体入口",
    "go": "气体出口",
    "n": "普通面",
}

#: 入口 / 出口：前端用它把连接表按「OUT -> IN」分组显示
MARKER_FLOW = {
    "si": "in", "fi": "in", "gi": "in",
    "so": "out", "fo": "out", "go": "out",
}

FACE_DIRS = ("N", "S", "W", "E")
FACE_LABELS = {"N": "北", "S": "南", "W": "西", "E": "东"}

#: 西面/东面的标记串按“从上到下”读。若哪天发现表格其实是反着的，
#: 把这里改成 "bottom-up" 即可（只影响 W/E 两个面的格位）。
VERTICAL_ORDER = "top-down"

#: 表头别名：用户改个列名也不至于认不出来
HEADER_ALIASES = {
    "名称": "name", "设备名称": "name", "设备": "name", "名字": "name",
    "宽度": "w", "宽": "w",
    "高度": "h", "高": "h",
    "北面": "N", "北": "N",
    "南面": "S", "南": "S",
    "西面": "W", "西": "W",
    "东面": "E", "东": "E",
}


# --------------------------------------------------------------------------
# 最小 xlsx 读取（zip + XML）
# --------------------------------------------------------------------------
def _col_index(ref):
    """A1 -> 0、B1 -> 1、AA1 -> 26（只取字母部分）。"""
    n = 0
    for ch in ref:
        if ch.isalpha():
            n = n * 26 + (ord(ch.upper()) - ord("A") + 1)
        else:
            break
    return max(0, n - 1)


def _shared_strings(z):
    try:
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    out = []
    for si in root.iter(NS + "si"):
        out.append("".join(t.text or "" for t in si.iter(NS + "t")))
    return out


def _cell_text(c, shared):
    t = c.get("t")
    if t == "s":                       # 共享字符串
        v = c.find(NS + "v")
        if v is None or v.text is None:
            return ""
        try:
            return shared[int(v.text)]
        except (ValueError, IndexError):
            return ""
    if t == "inlineStr":               # 内联字符串
        return "".join(x.text or "" for x in c.iter(NS + "t"))
    v = c.find(NS + "v")               # 数字/普通值
    return (v.text or "") if v is not None else ""


def _sheet_targets(z):
    """返回 [(工作表名, zip 内路径)]，顺序与表格里一致。"""
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = {}
    try:
        rr = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        for rel in rr.iter(PKG_REL_NS + "Relationship"):
            rels[rel.get("Id")] = rel.get("Target")
    except KeyError:
        pass
    out = []
    for sh in wb.iter(NS + "sheet"):
        rid = sh.get(REL_NS + "id")
        target = rels.get(rid, "")
        if target.startswith("/"):
            path = target.lstrip("/")
        elif target:
            path = "xl/" + target
        else:
            path = ""
        if path and not path.startswith("xl/"):
            path = "xl/" + path
        out.append((sh.get("name") or "", path))
    return out


def read_sheet_rows(path, sheet=0):
    """读出 xlsx 的第 ``sheet`` 张工作表，返回二维字符串列表。"""
    with zipfile.ZipFile(path) as z:
        targets = _sheet_targets(z)
        if not targets:
            raise ValueError("xlsx 里没有工作表")
        if isinstance(sheet, int):
            if sheet >= len(targets):
                raise ValueError(f"xlsx 里只有 {len(targets)} 张工作表")
            target = targets[sheet][1]
        else:
            target = dict((n, t) for n, t in targets)[sheet]
        root = ET.fromstring(z.read(target))
        shared = _shared_strings(z)
        rows = []
        for row in root.iter(NS + "row"):
            try:
                idx = int(row.get("r") or (len(rows) + 1))
            except ValueError:
                idx = len(rows) + 1
            while len(rows) < idx:
                rows.append([])
            cells = rows[idx - 1]
            for c in row.findall(NS + "c"):
                col = _col_index(c.get("r") or "")
                while len(cells) <= col:
                    cells.append("")
                cells[col] = _cell_text(c, shared).strip()
    return rows


# --------------------------------------------------------------------------
# 面标记 -> 端口
# --------------------------------------------------------------------------
def tokenize_face(s):
    """``"nsinsin"`` -> ``["n","si","n","si","n"]``；遇到不认识的标记就报错。"""
    out = []
    i = 0
    text = (s or "").strip()
    while i < len(text):
        two = text[i:i + 2].lower()
        if two in MARKER_LABELS and two != "n":
            out.append(two)
            i += 2
            continue
        one = text[i].lower()
        if one == "n":
            out.append("n")
            i += 1
            continue
        raise ValueError(f"无法识别的面标记 {text[i:i + 2]!r}（只允许 si/so/fi/fo/gi/go/n）")
    return out


def build_ports(w, h, faces, vertical=VERTICAL_ORDER):
    """把四个面的标记串转成端口的 ``cells`` + ``dir``。

    返回 ``[{"id","mark","label","flow","dir","cells"}, ...]``，顺序固定为
    北/南/西/东、面内按格子顺序 —— 结果可复现，方便 diff 与测试。
    """
    buckets = {}   # (mark, face) -> [cell, ...]
    for face in FACE_DIRS:
        expected = w if face in ("N", "S") else h
        raw = (faces.get(face) or "").strip()
        tokens = tokenize_face(raw) if raw else ["n"] * expected
        if len(tokens) != expected:
            raise ValueError(
                f"{FACE_LABELS[face]}面有 {len(tokens)} 个标记，"
                f"但该面只有 {expected} 格（{FACE_LABELS[face]}面长度应等于"
                f"{'宽度' if face in ('N', 'S') else '高度'} {expected}）")
        flip = (face in ("W", "E") and vertical == "bottom-up")
        for i, mark in enumerate(tokens):
            if mark == "n":
                continue
            k = h - 1 - i if flip else i
            if face == "N":
                cell = (0, i)
            elif face == "S":
                cell = (h - 1, i)
            elif face == "W":
                cell = (k, 0)
            else:
                cell = (k, w - 1)
            buckets.setdefault((mark, face), []).append(cell)

    seen = Counter(mark for (mark, _face) in buckets)
    ports = []
    for (mark, face), cells in buckets.items():
        # 同一标记只在一个面上 -> 直接用 SI/SO；出现在多个面 -> 带上方向后缀
        pid = mark.upper() if seen[mark] == 1 else f"{mark.upper()}-{face}"
        ports.append({
            "id": pid,
            "mark": mark,
            "label": MARKER_LABELS[mark],
            "flow": MARKER_FLOW[mark],
            "dir": face,
            "cells": [list(c) for c in cells],
        })
    return ports


def summarize(ports):
    """``SI×3(北) · SO×3(南)`` 这样的一行摘要。"""
    if not ports:
        return "无端口"
    return " · ".join(
        f"{p['id']}×{len(p['cells'])}（{FACE_LABELS.get(p['dir'], p['dir'])}）"
        for p in ports)


def parse_rows(rows):
    """解析工作表内容，返回 ``(devices, warnings)``。"""
    devices, warnings = [], []
    header, hrow = None, -1
    for i, row in enumerate(rows):
        keys = [HEADER_ALIASES.get((x or "").strip(), None) for x in row]
        if "name" in keys and "N" in keys:
            header, hrow = keys, i
            break
    if header is None:
        raise ValueError("找不到表头：需要「设备名称 / 宽度 / 高度 / 北面 / 南面 / 西面 / 东面」"
                         "（北面一列不能少，否则认不出这张表）")

    for row in rows[hrow + 1:]:
        rec = {}
        for key, val in zip(header, row):
            if key and val and key not in rec:
                rec[key] = val
        name = (rec.get("name") or "").strip()
        if not name:
            continue
        try:
            w = int(float(rec.get("w") or 0))
            h = int(float(rec.get("h") or 0))
        except ValueError:
            warnings.append(f"{name}: 宽度/高度不是数字，已跳过")
            continue
        if w <= 0 or h <= 0:
            warnings.append(f"{name}: 宽度/高度必须为正整数（得到 {w}×{h}），已跳过")
            continue
        faces = {f: rec.get(f, "") for f in FACE_DIRS}
        try:
            ports = build_ports(w, h, faces)
        except ValueError as e:
            warnings.append(f"{name}: {e}，已跳过")
            continue
        devices.append({
            "name": name,
            "w": w,
            "h": h,
            "faces": faces,
            "ports": ports,
            "summary": summarize(ports),
        })
    return devices, warnings


# --------------------------------------------------------------------------
# 载入 / 缓存
# --------------------------------------------------------------------------
def find_xlsx():
    """找设备尺寸表：先看默认名，再看 data/ 下任意 .xlsx（名字带“设备/尺寸”优先）。"""
    env = os.environ.get("DEVICE_XLSX")
    if env and os.path.isfile(env):
        return env
    default = os.path.join(DATA_DIR, XLSX_NAME)
    if os.path.isfile(default):
        return default
    if not os.path.isdir(DATA_DIR):
        return None
    cands = [f for f in sorted(os.listdir(DATA_DIR))
             if f.lower().endswith((".xlsx", ".xlsm")) and not f.startswith("~$")]
    if not cands:
        return None
    cands.sort(key=lambda f: (0 if ("设备" in f or "尺寸" in f) else 1, f))
    return os.path.join(DATA_DIR, cands[0])


def cache_path():
    return os.path.join(DATA_DIR, CACHE_NAME)


def load_presets(xlsx=None):
    """返回给前端/接口用的预设包。

    ``{"source": ..., "source_kind": "xlsx"|"cache"|None, "devices": [...],
       "warnings": [...], "markers": {...}}``
    xlsx 是权威来源；表格丢失时退回 ``data/device_presets.json`` 缓存。
    """
    path = xlsx or find_xlsx()
    payload = {
        "source": None,
        "source_kind": None,
        "devices": [],
        "warnings": [],
        "markers": {k: {"label": v, "flow": MARKER_FLOW.get(k)} for k, v in MARKER_LABELS.items()},
        "vertical_order": VERTICAL_ORDER,
    }
    if path and os.path.isfile(path):
        try:
            rows = read_sheet_rows(path, 0)
            devices, warnings = parse_rows(rows)
            payload.update({
                "source": os.path.relpath(path, PROJECT_ROOT).replace("\\", "/"),
                "source_kind": "xlsx",
                "devices": devices,
                "warnings": warnings,
            })
            return payload
        except Exception as e:  # noqa: BLE001  表格坏了就退回缓存，别把 WebUI 拖垮
            payload["warnings"].append(f"读取设备尺寸表失败（{e}），改用缓存")
    elif path:
        payload["warnings"].append(f"设备尺寸表不存在：{path}，改用缓存")
    else:
        payload["warnings"].append(
            f"没找到设备尺寸表（期望 {os.path.join('data', XLSX_NAME)}），改用缓存")

    cp = cache_path()
    if os.path.isfile(cp):
        try:
            with open(cp, "r", encoding="utf-8") as f:
                cached = json.load(f)
            payload.update({
                "source": os.path.relpath(cp, PROJECT_ROOT).replace("\\", "/"),
                "source_kind": "cache",
                "devices": cached.get("devices", []),
                "warnings": payload["warnings"] + list(cached.get("warnings", [])),
            })
            return payload
        except Exception as e:  # noqa: BLE001
            payload["warnings"].append(f"读取缓存失败: {e}")
    return payload


def dump_cache(xlsx=None):
    payload = load_presets(xlsx)
    if not payload["devices"]:
        raise SystemExit("没有解析出任何设备，未写缓存：" + "; ".join(payload["warnings"]))
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(cache_path(), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return cache_path()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _print_list(payload):
    print(f"来源: {payload['source'] or '(无)'}  "
          f"设备 {len(payload['devices'])} 个  纵面读法: {payload['vertical_order']}")
    if payload["warnings"]:
        print("告警:")
        for w in payload["warnings"]:
            print("  -", w)
    print()
    for d in payload["devices"]:
        print(f"{d['name']}  {d['w']}×{d['h']}")
        print(f"    北 {d['faces']['N'] or '-'} | 南 {d['faces']['S'] or '-'} | "
              f"西 {d['faces']['W'] or '-'} | 东 {d['faces']['E'] or '-'}")
        for p in d["ports"]:
            cells = " ".join(f"({r},{c})" for r, c in p["cells"])
            print(f"    {p['id']:<6} {p['label']}  朝{p['dir']}  {cells}")
    print()


def main():
    ap = argparse.ArgumentParser(description="设备尺寸表 -> 模块预设")
    ap.add_argument("--xlsx", default=None, help="指定 xlsx 路径（默认 data/设备尺寸.xlsx）")
    ap.add_argument("--json", action="store_true", help="打印 JSON")
    ap.add_argument("--list", action="store_true", help="打印可读表格")
    ap.add_argument("--dump", action="store_true", help=f"写入 data/{CACHE_NAME}")
    args = ap.parse_args()

    payload = load_presets(args.xlsx)
    if args.dump:
        print("已写入", dump_cache(args.xlsx))
        payload = load_presets(args.xlsx)
    if args.json:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        print()
    if args.list or not args.json:
        _print_list(payload)
    return 0 if payload["devices"] else 1


if __name__ == "__main__":
    sys.exit(main())
