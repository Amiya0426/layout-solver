#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证「手动停止也能保存下界」。

走的是 WebUI 的真实代码路径：webui_server.start_job() -> worker 线程读日志
-> watch_bound_line() 收集下界 -> job_stop() 终止进程 -> persist_job_bound() 落盘。

子进程用假的 CP-SAT 日志（与真实格式逐字一致，含用户那份 bool_core 行），
所以这个测试是确定性的、秒级完成。
"""
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
os.chdir(ROOT)

import webui_server as ws  # noqa: E402

OUT = os.path.join(HERE, "ws")
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(OUT, exist_ok=True)
PREFIX = os.path.join(OUT, "stopdemo")
BOUNDS = PREFIX + ".bounds.json"

FAIL = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        FAIL.append(name)


REAL_LOG = r'''
#Model 323.56s var:20658/21151 constraints:32742/32972
#Bound 385.16s best:19    next:[10,18]    bool_core (num_cores=10 [size:280 mw:1 amo:42 lit:127 d:10] a=181 d=10 fixed=507/22653 clauses=76'504)
#Model 389.79s var:20643/21151 constraints:32731/32972
#Bound 399.91s best:19    next:[11,18]    bool_core (num_cores=11 [size:149 mw:1 amo:21 lit:54 d:11] a=33 d=11 fixed=509/23295 clauses=78'943)
#Bound 446.48s best:19    next:[12,18]    bool_core (num_cores=12 [size:2 mw:1 d:12] a=32 d:12 fixed=509/23832 clauses=84'392)
#Bound 607.35s best:19    next:[13,18]    bool_core (num_cores=13 [size:18 mw:1 amo:3 lit:6 d:13] a=15 d=13 fixed=514/24162 clauses=86'619)
#Bound 835.58s best:19    next:[14,18]    bool_core (num_cores=14 [size:15 mw:1 amo:4 lit:8 d:14] a=1 d=14 fixed=538/24461 clauses=99'911)
'''


def main():
    # ---- 1) 纯解析：喂用户那份真实日志 ----
    job = {"bound_lb": 0, "bound_key": None, "log": [], "log_next": 0,
           "log_base": 0}
    for line in REAL_LOG.strip().splitlines():
        ws.watch_bound_line(job, line)
    check("从真实 bool_core 日志里解析出下界 14", job["bound_lb"] == 14,
          f"得到 {job['bound_lb']}")
    ws.watch_bound_line(job, '#Bound 846.10s best:19 next:[9,18] bool_core')
    check("下界只增不减（回退的行被忽略）", job["bound_lb"] == 14,
          f"得到 {job['bound_lb']}")

    # ---- 2) 端到端：假求解进程 + 停止 ----
    key = {"prefix": PREFIX, "config_sha256": "cfg123", "code_sha256": "code456"}
    child = (
        "import sys,time\n"
        "print('[exact] bounds-key ' + %r, flush=True)\n"
        "sys.stdout.write(%r)\n"
        "sys.stdout.flush()\n"
        "time.sleep(600)\n"
        % (json.dumps(key, ensure_ascii=False), REAL_LOG)
    )
    job = ws.start_job("solve", "stopdemo", [sys.executable, "-c", child])
    deadline = time.time() + 20
    while time.time() < deadline and job.get("bound_lb", 0) < 14:
        time.sleep(0.1)
    check("服务端从作业日志里收到下界 14", job.get("bound_lb") == 14,
          f"得到 {job.get('bound_lb')}")
    check("服务端收到 bounds-key", bool(job.get("bound_key")))

    ws.job_stop(job)
    deadline = time.time() + 20
    while time.time() < deadline and job["status"] == "running":
        time.sleep(0.1)
    check("作业已停止", job["status"] == "stopped", job["status"])
    check("停止后写出了 bounds.json", os.path.isfile(BOUNDS))

    rec = json.load(open(BOUNDS, encoding="utf-8")) if os.path.isfile(BOUNDS) else {}
    check("lb=14", rec.get("lb") == 14, json.dumps(rec, ensure_ascii=False)[:160])
    check("status=stopped", rec.get("status") == "stopped", str(rec.get("status")))
    check("指纹来自子进程的 bounds-key",
          rec.get("config_sha256") == "cfg123" and rec.get("code_sha256") == "code456")

    # ---- 3) 已有的更强记录不被覆盖 ----
    before = open(BOUNDS, "rb").read()
    job2 = {"bound_key": key, "bound_lb": 12, "status": "stopped",
            "log": [], "log_next": 0, "log_base": 0}
    ws.persist_job_bound(job2)
    check("较弱的下界不会覆盖已存的更强记录",
          open(BOUNDS, "rb").read() == before)
    # 同配置的更强下界则应当覆盖，并保留 best
    ws.persist_job_bound({"bound_key": key, "bound_lb": 17, "status": "stopped",
                          "log": [], "log_next": 0, "log_base": 0})
    rec2 = json.load(open(BOUNDS, encoding="utf-8"))
    check("更强的下界会覆盖", rec2.get("lb") == 17, str(rec2.get("lb")))
    # 换了配置（指纹不同）时不复用旧记录字段
    ws.persist_job_bound({"bound_key": {"prefix": PREFIX,
                                        "config_sha256": "other",
                                        "code_sha256": "other"},
                          "bound_lb": 3, "status": "stopped",
                          "log": [], "log_next": 0, "log_base": 0})
    rec3 = json.load(open(BOUNDS, encoding="utf-8"))
    check("换配置后重写为独立记录",
          rec3.get("lb") == 3 and rec3.get("config_sha256") == "other"
          and rec3.get("best") is None, json.dumps(rec3, ensure_ascii=False))

    print()
    # ---- 4) save_prev_bound 的“只增不减 + best 兜底” ----
    import layout_exact as le

    p2 = os.path.join(OUT, "merge")
    cfg_key = {"config": 1}
    le.save_prev_bound(p2, cfg_key, 5, 7, "OPTIMAL", 1.0, applied=["obj<=7"])
    r1 = json.load(open(p2 + ".bounds.json", encoding="utf-8"))
    check("第一次写入 lb=5/best=7", r1["lb"] == 5 and r1["best"] == 7, str(r1))

    # 这一轮没找到解（UNKNOWN），但把界抬到了 9；best 必须保住旧记录里的 7
    le.save_prev_bound(p2, cfg_key, 9, None, "UNKNOWN", 2.0, applied=["obj<=19"])
    r2 = json.load(open(p2 + ".bounds.json", encoding="utf-8"))
    check("UNKNOWN 没解时 lb 抬到 9", r2["lb"] == 9, str(r2["lb"]))
    check("UNKNOWN 没解时 best 仍保留 7", r2["best"] == 7, str(r2["best"]))
    check("applied 描述抬到当前界的这一轮", r2["applied"] == ["obj<=19"],
          str(r2["applied"]))

    # 更弱的一轮：lb 不回落，且 applied 沿用“界来自哪一轮”的记录
    le.save_prev_bound(p2, cfg_key, 4, 6, "FEASIBLE", 3.0, applied=[])
    r3 = json.load(open(p2 + ".bounds.json", encoding="utf-8"))
    check("更弱的 lb 不回落（仍为 9）", r3["lb"] == 9, str(r3["lb"]))
    check("界沿用旧记录时 applied 也沿用", r3["applied"] == ["obj<=19"],
          str(r3["applied"]))
    check("更好的 best 会被采纳（7 -> 6）", r3["best"] == 6, str(r3["best"]))

    le.save_prev_bound(p2, cfg_key, 4, 8, "FEASIBLE", 4.0, applied=[])
    r4 = json.load(open(p2 + ".bounds.json", encoding="utf-8"))
    check("更差的 best 不覆盖（仍为 6）", r4["best"] == 6, str(r4["best"]))

    print()
    if FAIL:
        print(f"失败 {len(FAIL)} 项: {FAIL}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
