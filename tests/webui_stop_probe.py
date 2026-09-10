#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端：真的起 WebUI 服务端 -> 真的求解 -> 真的点「停止」-> 检查 bounds.json。

用 config.example.json 的一份副本（题目名 stopdemo），所以不会碰到
result/example 下的既有结果；跑完自动清理。
整个用例约 40~70 秒（要等 CP-SAT 把下界从 0 抬起来）。
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
PORT = 8793
BASE = f"http://127.0.0.1:{PORT}"
NAME = "stopdemo"
CFG = os.path.join(ROOT, "configs", f"config.{NAME}.json")
SRC_CFG = os.path.join(ROOT, "configs", "config.example.json")
RESULT_DIR = os.path.join(ROOT, "result", NAME)
BOUNDS = os.path.join(RESULT_DIR, f"{NAME}.bounds.json")

FAIL = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        FAIL.append(name)


def post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    try:
        os.remove(CFG)
    except OSError:
        pass
    shutil.rmtree(RESULT_DIR, ignore_errors=True)
    shutil.copy(SRC_CFG, CFG)
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

        r = post("/api/solve", {"name": NAME, "mode": "exact",
                                "timeout": 600, "workers": 8, "hint": False})
        jid = r.get("job")
        check("求解作业已启动", bool(jid), str(r))

        lb = 0
        deadline = time.time() + 150
        while time.time() < deadline:
            snap = get(f"/api/job?id={jid}&since=0")
            for line in snap.get("lines", []):
                if "bounds-key" in line:
                    check("服务端收到 bounds-key", True)
                if "next:[" in line:
                    try:
                        v = int(line.split("next:[", 1)[1].split(",")[0].strip())
                    except ValueError:
                        continue
                    lb = max(lb, v)
            if lb > 0:
                break
            if snap.get("status") != "running":
                break
            time.sleep(1.0)
        check("求解过程中证明了正的下界", lb > 0, f"lb={lb}")
        check("停止前还没有 bounds.json（子进程来不及写）",
              not os.path.isfile(BOUNDS))

        st = post("/api/stop", {"id": jid})
        check("停止请求被接受", st.get("ok") is True, str(st))
        for _ in range(100):
            if get(f"/api/job?id={jid}").get("status") != "running":
                break
            time.sleep(0.1)

        check("停止后写出了 bounds.json", os.path.isfile(BOUNDS))
        rec = json.load(open(BOUNDS, encoding="utf-8")) if os.path.isfile(BOUNDS) else {}
        check("lb 与日志里的下界一致", rec.get("lb") == lb,
              f"file={rec.get('lb')} log={lb}")
        check("status=stopped", rec.get("status") == "stopped",
              str(rec.get("status")))
        check("指纹已写入", bool(rec.get("config_sha256"))
              and bool(rec.get("code_sha256")))
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except Exception:  # noqa: BLE001
            srv.kill()
        for p in (CFG,):
            try:
                os.remove(p)
            except OSError:
                pass
        shutil.rmtree(RESULT_DIR, ignore_errors=True)

    print()
    if FAIL:
        print(f"失败 {len(FAIL)} 项: {FAIL}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
