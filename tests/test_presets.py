#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模块预设（设备尺寸表）测试：解析、端口换算、接口，以及“预设拼出来的配置真的能求解”。

用法（在项目根目录执行）::

    python tests/test_presets.py

不需要 OR-Tools（只用启发式求解器验证一遍主链路），也不需要浏览器。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

import device_presets as dp  # noqa: E402


class Report:
    def __init__(self):
        self.failures = []

    def check(self, name, cond, detail=""):
        print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if detail else ""))
        if not cond:
            self.failures.append(name)
        return cond


def by_name(devices, name):
    for d in devices:
        if d["name"] == name:
            return d
    return None


def ports_of(dev, pid):
    return [p for p in dev["ports"] if p["id"] == pid]


def main():
    rep = Report()
    payload = dp.load_presets()

    # ---- 1) 表格本身 ----
    rep.check("从 xlsx 读到预设", payload["source_kind"] == "xlsx",
              f"source={payload['source']} kind={payload['source_kind']}")
    rep.check("没有任何解析告警", not payload["warnings"], str(payload["warnings"]))
    devices = payload["devices"]
    rep.check("设备数量与表格一致（46）", len(devices) == 46, f"实际 {len(devices)}")

    # ---- 2) 标记含义 ----
    rep.check("标记对照表齐全",
              set(dp.MARKER_LABELS) == {"si", "so", "fi", "fo", "gi", "go", "n"},
              str(sorted(dp.MARKER_LABELS)))
    rep.check("si/so 是固体进出、fi/fo 液体、gi/go 气体",
              (dp.MARKER_LABELS["si"], dp.MARKER_LABELS["so"],
               dp.MARKER_LABELS["fi"], dp.MARKER_LABELS["fo"],
               dp.MARKER_LABELS["gi"], dp.MARKER_LABELS["go"]) ==
              ("固体入口", "固体出口", "液体入口", "液体出口", "气体入口", "气体出口"))
    rep.check("标记串分词正确",
              dp.tokenize_face("nsinsin") == ["n", "si", "n", "si", "n"]
              and dp.tokenize_face("sisi") == ["si", "si"]
              and dp.tokenize_face("nn") == ["n", "n"])
    bad = ""
    try:
        dp.tokenize_face("sx")
    except ValueError as e:
        bad = str(e)
    rep.check("无法识别的标记会报错", bool(bad), bad)

    # ---- 3) 具体设备 ----
    jl = by_name(devices, "精炼炉")
    rep.check("精炼炉 3×3", jl and (jl["w"], jl["h"]) == (3, 3))
    si, so = ports_of(jl, "SI"), ports_of(jl, "SO")
    rep.check("精炼炉 北面 sisisi -> SI 朝北 3 格",
              len(si) == 1 and si[0]["dir"] == "N"
              and si[0]["cells"] == [[0, 0], [0, 1], [0, 2]], json.dumps(si, ensure_ascii=False))
    rep.check("精炼炉 南面 sososo -> SO 朝南 3 格",
              len(so) == 1 and so[0]["dir"] == "S"
              and so[0]["cells"] == [[2, 0], [2, 1], [2, 2]], json.dumps(so, ensure_ascii=False))

    gz = by_name(devices, "储液罐")
    rep.check("储液罐 FI 朝西 (1,0) / FO 朝东 (1,2)",
              ports_of(gz, "FI") and ports_of(gz, "FI")[0]["cells"] == [[1, 0]]
              and ports_of(gz, "FI")[0]["dir"] == "W"
              and ports_of(gz, "FO")[0]["cells"] == [[1, 2]]
              and ports_of(gz, "FO")[0]["dir"] == "E")

    hx = by_name(devices, "协议核心")
    rep.check("协议核心 9×9 四个面各自成端口（同标记跨面要拆开）",
              hx and (hx["w"], hx["h"]) == (9, 9)
              and sorted(p["id"] for p in hx["ports"]) == ["SI-N", "SI-S", "SO-E", "SO-W"],
              str(sorted(p["id"] for p in hx["ports"])))

    zq = by_name(devices, "液气转化机（气体产出）")
    rep.check("液气转化机的 fi 同时出现在北面和西面 -> FI-N / FI-W",
              zq and sorted(p["id"] for p in zq["ports"]) == ["FI-N", "FI-W", "GO"],
              str(sorted(p["id"] for p in zq["ports"])))

    empty = by_name(devices, "仓库存取线源桩")
    rep.check("全 n 的设备没有端口", empty is not None and empty["ports"] == [])

    # ---- 4) 全部设备的结构自检 ----
    bad_face, bad_dims, no_label = [], [], []
    for d in devices:
        for p in d["ports"]:
            if not p.get("label"):
                no_label.append((d["name"], p["id"]))
            for r, c in p["cells"]:
                on_face = ((p["dir"] == "N" and r == 0) or (p["dir"] == "S" and r == d["h"] - 1)
                           or (p["dir"] == "W" and c == 0) or (p["dir"] == "E" and c == d["w"] - 1))
                inside = 0 <= r < d["h"] and 0 <= c < d["w"]
                if not (on_face and inside):
                    bad_face.append((d["name"], p["id"], r, c, p["dir"]))
            if not p["cells"]:
                bad_dims.append((d["name"], p["id"]))
    rep.check("每个端口格子都压在它自己的那一面上", not bad_face, str(bad_face[:3]))
    rep.check("没有空端口", not bad_dims, str(bad_dims[:3]))
    rep.check("每个端口都有中文含义", not no_label, str(no_label[:3]))

    # ---- 5) 面长度写错时要报错，而不是悄悄错位 ----
    err = ""
    try:
        dp.build_ports(3, 3, {"N": "sisisi", "S": "soso", "W": "nnn", "E": "nnn"})
    except ValueError as e:
        err = str(e)
    rep.check("南面长度与宽度不符时报错", "南面" in err, err)

    # ---- 6) 西/东面的读法可以整体翻转 ----
    flipped = dp.build_ports(3, 5, {"N": "nnn", "S": "nnn", "W": "nfinfin", "E": "nnnnn"},
                             vertical="bottom-up")
    rep.check("bottom-up 时西面格子上下翻转",
              flipped[0]["cells"] == [[3, 0], [1, 0]], json.dumps(flipped, ensure_ascii=False))

    # ---- 7) 用预设拼一份配置，交给启发式求解器真跑一遍 ----
    def preset_module(name, mid, rotatable=True, pos=None):
        d = by_name(devices, name)
        m = {"id": mid, "preset": d["name"], "w": d["w"], "h": d["h"],
             "ports": [{"id": p["id"], "dir": p["dir"], "cells": p["cells"]} for p in d["ports"]]}
        if pos is not None:
            m["pos"] = list(pos)
        else:
            m["rotatable"] = rotatable
        return m

    cfg = {
        "rows": 8, "cols": 16,
        "fixed": [preset_module("仓库存货口", "存货口1", pos=[0, 0])],
        "movable": [preset_module("精炼炉", "精炼炉1"), preset_module("储液罐", "储液罐1")],
        "nets": [],
    }
    rep.check("固定模块保留了 pos、可动模块保留了 rotatable",
              "pos" in cfg["fixed"][0] and cfg["movable"][0].get("rotatable") is True)
    out_dir = tempfile.mkdtemp(prefix="layout_presets_")
    try:
        cfg_path = os.path.join(out_dir, "config.presetdemo.json")
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False)
        r = subprocess.run(
            [sys.executable, os.path.join(SRC, "layout_solver.py"), cfg_path,
             "--timeout", "8", "--output", os.path.join(out_dir, "presetdemo")],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
        rep.check("无连接的预设配置能被求解器接受（退出码 0）", r.returncode == 0,
                  (r.stdout or "")[-300:] + (r.stderr or "")[-200:])

        # 再来一份带连接的：研磨机(SO 朝南) -> 精炼炉(SI 朝北)
        cfg2 = {
            "rows": 9, "cols": 18,
            "fixed": [],
            "movable": [preset_module("研磨机", "研磨机1"), preset_module("精炼炉", "精炼炉1"),
                        preset_module("储液罐", "储液罐1")],
            "nets": [{"from": "研磨机1", "from_port": "SO",
                      "to": "精炼炉1", "to_port": "SI"}],
        }
        cfg2_path = os.path.join(out_dir, "config.presetnet.json")
        with open(cfg2_path, "w", encoding="utf-8") as f:
            json.dump(cfg2, f, ensure_ascii=False)
        r2 = subprocess.run(
            [sys.executable, os.path.join(SRC, "layout_solver.py"), cfg2_path,
             "--timeout", "10", "--output", os.path.join(out_dir, "presetnet")],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
        jl_path = os.path.join(out_dir, "presetnet.solutions.jsonl")
        sols = []
        if os.path.isfile(jl_path):
            sols = [json.loads(x) for x in open(jl_path, encoding="utf-8") if x.strip()]
        rep.check("预设端口能真的连通（SO 朝南 -> SI 朝北）", r2.returncode == 0 and len(sols) >= 1,
                  f"rc={r2.returncode} 解数={len(sols)} " + (r2.stdout or "")[-240:])
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)

    # ---- 8) 缓存文件与表格内容一致 ----
    rep.check("data/device_presets.json 缓存存在且设备数一致",
              os.path.isfile(dp.cache_path())
              and len(json.load(open(dp.cache_path(), encoding="utf-8"))["devices"]) == len(devices))
    rep.check("--dump 可以重新生成缓存", bool(dp.dump_cache()))

    # ---- 9) 表格丢失时退回缓存 ----
    fallback = dp.load_presets(xlsx=os.path.join(ROOT, "data", "不存在的表.xlsx"))
    rep.check("表格缺失时退回缓存并给出告警",
              fallback["source_kind"] == "cache" and len(fallback["devices"]) == len(devices)
              and any("设备尺寸表不存在" in w for w in fallback["warnings"]),
              str(fallback["warnings"]))

    print()
    if rep.failures:
        print(f"失败 {len(rep.failures)} 项: {rep.failures}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
