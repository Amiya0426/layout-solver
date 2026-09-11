#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跑前端测试（两段 node 脚本），并校验它们生成的 SVG 真的能当 XML 解析。

用法（在项目根目录执行）::

    python tests/test_webui_frontend.py

    tests/test_webui_model.js     ui/model.js 的纯逻辑：端口分类、同名编号、连接校验、连线几何
    tests/test_webui_app_load.js  用极小 DOM 桩把 ui/app.js 真跑一遍：按预设添加 -> 连接 -> 画预览

为什么要有它：这台机器上的 Chrome/Edge 起不了无头实例（命令会被已有会话接管），
浏览器探针（tests/webui_presets_probe.py）只能跳过，而 app.js 里那几百行渲染代码
又必须验证。于是把不碰 DOM 的逻辑抽到 ui/model.js，再用桩件执行 app.js，
最后用 ElementTree 把生成的 SVG 解析一遍（标记写错时浏览器只会白屏，不会报错）。

没装 node 就跳过并返回 0：这个仓库本来只用 Python 标准库，node 只是跑前端逻辑的手段，
不该变成硬依赖。
"""

import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SVG_RE = re.compile(r"---SVG-BEGIN---\n(.*?)\n---SVG-END---", re.S)
SVG_NS = "{http://www.w3.org/2000/svg}"

FAIL = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        FAIL.append(name)


def run_script(node, name):
    r = subprocess.run([node, os.path.join(HERE, name)], cwd=ROOT, text=True,
                       encoding="utf-8", errors="replace", capture_output=True)
    sys.stdout.write(r.stdout)
    if r.stderr:
        sys.stdout.write(r.stderr)
    return r


def check_svg(stdout):
    """把 app.js 生成的连接预览 SVG 当 XML 解析，确认标记是完整的。"""
    m = SVG_RE.search(stdout)
    if not m:
        check("JS 侧吐出了连接预览 SVG", False, "没找到 ---SVG-BEGIN--- 块")
        return
    svg = m.group(1)
    check("JS 侧吐出了连接预览 SVG", len(svg) > 500, f"{len(svg)} 字节")
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as e:
        check("SVG 能被 XML 解析（标记完整）", False, str(e))
        return
    check("SVG 能被 XML 解析（标记完整）", True)

    def count(tag):
        return len(list(root.iter(SVG_NS + tag)))
    groups = [g for g in root.iter(SVG_NS + "g")
              if (g.get("class") or "") == "link-group"]
    link_paths = [p.get("d") for g in groups for p in g.iter(SVG_NS + "path")]
    check("根节点是 <svg> 且带 viewBox", root.tag == SVG_NS + "svg" and root.get("viewBox"),
          f"{root.tag} {root.get('viewBox')}")
    check("画了 4 个模块框", count("rect") == 4, str(count("rect")))
    check("画了 6 条连线", len(groups) == 6, str(len(groups)))
    check("每条连线一个 path", len(link_paths) == 6, str(len(link_paths)))
    check("每条连线末端都有箭头标记", count("marker") == 12, str(count("marker")))
    check("连线上的编号文字齐全", count("text") >= 6, str(count("text")))
    lines = list(root.iter(SVG_NS + "line"))
    check("端口刻度都画出来了（20 格）", len(lines) == 20, str(len(lines)))
    check("每条连接都有 tooltip", count("title") == 6, str(count("title")))

    bad = []
    for el in lines + list(root.iter(SVG_NS + "rect")):
        for attr in ("x1", "y1", "x2", "y2", "x", "y", "width", "height"):
            v = el.get(attr)
            if v is None:
                continue
            try:
                f = float(v)
            except ValueError:
                bad.append((el.tag, attr, v))
                continue
            if f != f or abs(f) > 1e6:      # NaN / 无穷大
                bad.append((el.tag, attr, v))
    check("坐标都是正常数字（没有 NaN/越界）", not bad, str(bad[:3]))

    # 连线路径：M x y C c1x c1y, c2x c2y, px py
    num = r"(-?[\d.]+)"
    sep = r"[ ,]+"
    path_re = re.compile(r"^M" + sep + num + sep + num + r" C"
                         + sep + num + sep + num + r"," + sep + num + sep + num
                         + r"," + sep + num + sep + num + r"$")
    vb = (root.get("viewBox") or "0 0 0 0").split()
    W, H = float(vb[2]), float(vb[3])
    bad_path = [d for d in link_paths if not (d and path_re.match(d))]
    check("每条连线都是三次贝塞尔，且格式规整", not bad_path, str(bad_path[:2]))
    escaped = []
    for d in link_paths:
        m = path_re.match(d or "")
        if not m:
            continue
        nums = [float(x) for x in m.groups()]
        # 控制点被夹住 = 整条曲线（贝塞尔凸包）都在画布里，不会被裁掉
        for i in range(0, 8, 2):
            if not (-1 <= nums[i] <= W + 1 and -1 <= nums[i + 1] <= H + 1):
                escaped.append(d)
                break
    check("所有连线都在画布内（不会被裁掉）", not escaped, str(escaped[:1]))


def main():
    node = shutil.which("node")
    if not node:
        print("SKIP  没找到 node，跳过前端逻辑测试（ui/model.js、ui/app.js）")
        return 0
    for name in ("test_webui_model.js", "test_webui_app_load.js"):
        print(f"===== {name} =====")
        r = run_script(node, name)
        if r.returncode != 0:
            print(f"{name} 失败（退出码 {r.returncode}）")
            return r.returncode
        if name == "test_webui_app_load.js":
            print("===== 校验生成的 SVG =====")
            check_svg(r.stdout)
        print()

    if FAIL:
        print(f"失败 {len(FAIL)} 项: {FAIL}")
        return 1
    print("前端测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
