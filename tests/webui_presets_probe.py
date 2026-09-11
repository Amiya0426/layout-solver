#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端前端探针：真的用无头浏览器打开 WebUI，验证三件事

1. 「模块预设」下拉真的读到了设备尺寸表（46 个设备）；
2. 按预设添加模块时，相同模块名从 1 开始编号（精炼炉1、精炼炉2…）；
3. 设置好「出口 -> 入口」后，连接预览真的画出了连线（SVG path + 箭头 + 编号）。

用法（在项目根目录执行）::

    python tests/webui_presets_probe.py

没有找到 Chrome/Edge 时自动跳过（返回 0），不会让 CI 变红。
原因：这个仓库本来只用 Python 标准库，浏览器只是本机验证手段，不该成为硬依赖。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
UI = os.path.join(ROOT, "ui")
PORT = 8794
BASE = f"http://127.0.0.1:{PORT}"
PROBE_NAME = "__presets_probe.html"
PROBE = os.path.join(UI, PROBE_NAME)

BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]

PROBE_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>probe</title></head>
<body>
<pre id="out">pending</pre>
<iframe id="frame" src="/" style="width:1500px;height:1000px;border:0"></iframe>
<script>
const out = [];
function rec(k, v) {
  out.push(k + "=" + v);
  document.getElementById("out").textContent = out.join("\\n");
  document.title = out.join(" | ");
}
document.getElementById("frame").onload = () => {
  const w = document.getElementById("frame").contentWindow;
  const d = document.getElementById("frame").contentDocument;
  let tries = 0;
  (function tick() {
    tries += 1;
    try {
      const sel = d.getElementById("presetSelect");
      if (!sel || sel.options.length < 2) {
        if (tries > 200) { rec("error", "预设下拉一直是空的"); return; }
        return setTimeout(tick, 100);
      }
      rec("presetCount", sel.options.length - 1);
      rec("presetSource", (d.getElementById("presetSource").textContent || "").trim());
      rec("firstPreset", sel.options[1].value);

      // 预设详情：选「精炼炉」应画出 3x3 的端口小图
      sel.value = "精炼炉";
      sel.onchange();
      rec("presetSummary", (d.querySelector("#presetPreview .preset-head") || {}).textContent || "");
      rec("presetCells", d.querySelectorAll("#presetPreview .preset-cell").length);
      rec("presetNextId", (d.querySelectorAll("#presetPreview .preset-head b")[1] || {}).textContent || "");

      // 要求 2：同名模块从 1 开始编号
      w.addModuleFromPreset();
      w.addModuleFromPreset();
      w.addModuleFromPreset();
      sel.value = "储液罐";
      sel.onchange();
      w.addModuleFromPreset();
      const ids = Array.from(d.querySelectorAll("#moduleList .mod-item b")).map(e => e.textContent);
      rec("moduleIds", ids.join(","));
      rec("numbered1", ids.includes("精炼炉1") && ids.includes("精炼炉2") && ids.includes("精炼炉3"));
      rec("numbered2", ids.includes("储液罐1"));

      // 要求 3：连接（OUT -> IN）后的链接预览
      d.getElementById("btnAddNet").click();     // 精炼炉1.SO -> 精炼炉1.SI（可自行改）
      d.getElementById("btnAddNet").click();
      const rows = d.querySelectorAll("#netList .net-row");
      rec("netRows", rows.length);
      // 把第一条连接改成 精炼炉1.SO -> 储液罐1.FI
      const sels = rows[0].querySelectorAll("select");
      sels[0].value = "精炼炉1"; sels[0].onchange();
      const sels2 = d.querySelectorAll("#netList .net-row")[0].querySelectorAll("select");
      sels2[1].value = "SO"; sels2[1].onchange();
      const sels3 = d.querySelectorAll("#netList .net-row")[0].querySelectorAll("select");
      sels3[2].value = "储液罐1"; sels3[2].onchange();
      const sels4 = d.querySelectorAll("#netList .net-row")[0].querySelectorAll("select");
      sels4[3].value = "FI"; sels4[3].onchange();

      rec("linkSummary", (d.getElementById("linkSummary").textContent || "").trim());
      rec("linkGroups", d.querySelectorAll("#linkPreview .link-group").length);
      rec("linkPaths", d.querySelectorAll("#linkPreview .link-group path").length);
      rec("linkHasArrow", d.querySelectorAll("#linkPreview marker").length > 0);
      rec("linkIndexText", Array.from(d.querySelectorAll("#linkPreview text.link-index")).map(t => t.textContent).join(","));
      rec("linkTitles", Array.from(d.querySelectorAll("#linkPreview .link-group title")).map(t => t.textContent).join(" || "));
      const first = d.querySelector("#linkPreview .link-group");
      rec("firstPathD", first ? first.querySelector("path").getAttribute("d").slice(0, 60) : "");
      // 出口端下拉应该把出口排在前面（optgroup）
      rec("fromGroups", Array.from(sels4[1].querySelectorAll("optgroup")).map(g => g.label).join(","));
      rec("toGroups", Array.from(sels4[3].querySelectorAll("optgroup")).map(g => g.label).join(","));
      // 高亮：悬停连接表应该点亮对应的曲线
      w.highlightNet(0);
      rec("hlGroups", d.querySelectorAll("#linkPreview .link-group.hl").length);
      w.highlightNet(null);
      rec("hlCleared", d.querySelectorAll("#linkPreview .link-group.hl").length);
      rec("issuesText", (d.getElementById("linkIssues").textContent || "").trim().slice(0, 90));
      rec("done", 1);
    } catch (e) {
      rec("error", String((e && e.message) || e));
    }
  })();
};
</script>
</body></html>
"""

FAIL = []
SKIPPED = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        FAIL.append(name)


def skip(name, why):
    print(f"SKIP  {name}  {why}")
    SKIPPED.append(name)


def find_browser():
    for p in BROWSERS:
        if os.path.isfile(p):
            return p
    return None


def browser_works(browser):
    """无头浏览器真的能吐 DOM 吗？

    本机实测过一种情况：Chrome/Edge 已经开着，命令行一启动就被转发给已有会话，
    进程退出码 0 但 stdout 是空的。这种“装了但用不了”的必须当成跳过，
    否则测试会在不同机器上随机变红。
    """
    try:
        dom = dump_dom(browser, "data:text/html,<title>probe-ok</title>", budget=3000)
    except Exception:  # noqa: BLE001
        return False
    return "probe-ok" in dom


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return r.read().decode("utf-8", "replace")


def dump_dom(browser, url, budget=20000):
    tmp = tempfile.mkdtemp(prefix="probe_profile_")
    try:
        cmd = [browser, "--headless=new", "--disable-gpu", "--no-first-run",
               "--no-default-browser-check", "--disable-extensions",
               "--user-data-dir=" + tmp,
               f"--virtual-time-budget={budget}", "--dump-dom", url]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=180)
        return r.stdout or ""
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def parse_probe(dom):
    m = re.search(r'<pre id="out">(.*?)</pre>', dom, re.S)
    if not m:
        return {}
    text = m.group(1)
    for ent, ch in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"')):
        text = text.replace(ent, ch)
    vals = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip()
    return vals


def main():
    browser = find_browser()
    if not browser:
        skip("前端探针", "本机没有 Chrome/Edge")
        return 0
    if not browser_works(browser):
        skip("前端探针", "无头浏览器起不来（例如已被现有会话接管），"
                        "前端逻辑请用 python tests/test_webui_frontend.py 验证")
        return 0

    srv = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "src", "webui_server.py"),
         "--port", str(PORT)], cwd=ROOT,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                get("/api/problems")
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.1)
        else:
            print("服务端没起来")
            return 1

        with open(PROBE, "w", encoding="utf-8") as f:
            f.write(PROBE_HTML)
        dom = dump_dom(browser, f"{BASE}/static/{PROBE_NAME}")
        vals = parse_probe(dom)
        if not vals:
            check("探针页面返回了结果", False, dom[-500:])
            return 1

        print("探针原始输出：")
        for k, v in vals.items():
            print(f"    {k} = {v}")
        print()

        check("探针跑到底（没有 JS 异常）", vals.get("done") == "1",
              vals.get("error", ""))
        check("预设下拉读到了 46 个设备", vals.get("presetCount") == "46",
              vals.get("presetCount", ""))
        check("预设来源显示的是设备尺寸表", "设备尺寸.xlsx" in vals.get("presetSource", ""),
              vals.get("presetSource", ""))
        check("选中预设后画出了 3×3=9 个格子", vals.get("presetCells") == "9",
              vals.get("presetCells", ""))
        check("预设小图提示下一个编号是 精炼炉1", vals.get("presetNextId") == "精炼炉1",
              vals.get("presetNextId", ""))
        check("同名模块从 1 开始编号（连续三次 -> 1/2/3）",
              vals.get("numbered1") == "true", vals.get("moduleIds", ""))
        check("换个名字重新从 1 开始（储液罐1）", vals.get("numbered2") == "true",
              vals.get("moduleIds", ""))
        check("连接表有 2 行", vals.get("netRows") == "2", vals.get("netRows", ""))
        check("连接预览画出了 2 条连线", vals.get("linkGroups") == "2", vals.get("linkGroups", ""))
        check("连线是带箭头的 path", vals.get("linkPaths") == "2" and vals.get("linkHasArrow") == "true")
        check("连线上有编号 1,2", vals.get("linkIndexText") == "1,2", vals.get("linkIndexText", ""))
        check("连线 tooltip 写清了 出口 -> 入口",
              "SO" in vals.get("linkTitles", "") and "FI" in vals.get("linkTitles", ""),
              vals.get("linkTitles", ""))
        check("连线路径不是空的", "M " in vals.get("firstPathD", ""), vals.get("firstPathD", ""))
        check("出口端下拉把出口排在最前", vals.get("fromGroups", "").startswith("出口"),
              vals.get("fromGroups", ""))
        check("入口端下拉把入口排在最前", vals.get("toGroups", "").startswith("入口"),
              vals.get("toGroups", ""))
        check("悬停高亮点亮了对应曲线", vals.get("hlGroups") == "1", vals.get("hlGroups", ""))
        check("取消高亮后恢复", vals.get("hlCleared") == "0", vals.get("hlCleared", ""))
        check("连接摘要显示条数与模块数",
              "2 条连接" in vals.get("linkSummary", ""), vals.get("linkSummary", ""))
    finally:
        try:
            os.remove(PROBE)
        except OSError:
            pass
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except Exception:  # noqa: BLE001
            srv.kill()

    print()
    if FAIL:
        print(f"失败 {len(FAIL)} 项: {FAIL}")
        return 1
    print("全部通过" + (f"（跳过 {len(SKIPPED)} 项）" if SKIPPED else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
