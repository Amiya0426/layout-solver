#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地 WebUI 服务端（只用标准库）

启动:
    python webui_server.py --port 8765
浏览器打开:
    http://127.0.0.1:8765/

功能:
    - 浏览/新建/保存 config.<name>.json
    - 调用 layout_exact.py / layout_solver.py 求解
      · 求解过程中每找到可行解就落盘 solN.svg/txt，前端可随时刷新结果
      · 支持暂停(挂起进程)/继续/停止
    - 调用 layout_viz.py 生成 SVG / 字符画
    - 浏览 result/<name>/ 下的结果
"""

import argparse
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

import proc_ctl


SOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SOURCE_DIR)
UI_DIR = os.path.join(PROJECT_ROOT, "ui")
CONFIG_DIR = os.path.join(PROJECT_ROOT, "configs")
RESULT_DIR = os.path.join(PROJECT_ROOT, "result")
NAME_RE = re.compile(r"^[A-Za-z0-9_.\-\u4e00-\u9fff]+$")

JOBS = {}
JOBS_LOCK = threading.Lock()
LOG_MAX_LINES = 4000     # 每个作业在内存里保留的最大日志行数
LOG_TRIM_STEP = 1000     # 超过上限时一次性丢弃的行数
JOB_KEEP = 40            # 最多保留多少个作业记录


def safe_name(name):
    name = (name or "").strip()
    if name.startswith("config."):
        name = name[len("config."):]
    if name.endswith(".json"):
        name = name[:-5]
    if not name or not NAME_RE.match(name):
        raise ValueError("非法题目名（只允许字母/数字/下划线/横线/点/中文）")
    return name


def config_path(name):
    return os.path.join(CONFIG_DIR, f"config.{safe_name(name)}.json")


def inside_root(path):
    real = os.path.realpath(path)
    return real == PROJECT_ROOT or real.startswith(PROJECT_ROOT + os.sep)


def inside_dir(path, base):
    """path 是否落在 base 目录内（用于把 /static/ 限制在 webui/ 里）。"""
    real = os.path.realpath(path)
    base = os.path.realpath(base)
    return real == base or real.startswith(base + os.sep)


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def list_problems():
    out = []
    if not os.path.isdir(CONFIG_DIR):
        return out
    for fn in sorted(os.listdir(CONFIG_DIR)):
        if fn.startswith("config.") and fn.endswith(".json"):
            name = fn[len("config."):-len(".json")]
            files = []
            rdir = os.path.join(RESULT_DIR, name)
            if os.path.isdir(rdir):
                files = sorted(os.listdir(rdir))
            out.append({"name": name,
                        "file": os.path.join("configs", fn),
                        "results": files})
    return out


def count_solutions(name):
    """读取当前 solutions.jsonl 中的可行解数量。"""
    jl = os.path.join(RESULT_DIR, name, f"{name}.solutions.jsonl")
    if not os.path.isfile(jl):
        return None
    count = 0
    try:
        with open(jl, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    count += 1
    except Exception:
        return None
    return count


def job_log(job, line):
    """追加日志行（带序号，线程安全，自动裁剪）。"""
    with JOBS_LOCK:
        job["log"].append(line)
        job["log_next"] += 1
        extra = len(job["log"]) - LOG_MAX_LINES
        if extra > 0:
            del job["log"][:extra]
            job["log_base"] += extra


def job_snapshot(job, since=None, tail=None):
    """把作业状态打包给前端；since 给定时只回增量日志。"""
    with JOBS_LOCK:
        base = job["log_base"]
        lines = job["log"]
        truncated = False
        if since is None:
            payload = list(lines)
            start = base
        elif since < base:
            # 客户端落后太多（日志已被裁剪），整段重发
            payload = list(lines)
            start = base
            truncated = True
        else:
            offset = min(max(since - base, 0), len(lines))
            payload = lines[offset:]
            start = base + offset
        if tail is not None and len(payload) > tail:
            dropped = len(payload) - tail
            payload = payload[-tail:]
            start += dropped
            truncated = True
        snap = {
            "id": job["id"],
            "kind": job["kind"],
            "name": job["name"],
            "status": job["status"],
            "quiet": job["quiet"],
            "paused": job["paused"],
            "returncode": job["returncode"],
            "started": job["started"],
            "ended": job["ended"],
            "pid": job["pid"],
            "cmd": job["cmd"],
            "log_from": start,
            "log_next": job["log_next"],
            "truncated": truncated,
            "lines": payload,
        }
    return snap


def job_stop(job, grace=2.0):
    """请求结束一个作业；即使子进程还没启动（proc is None）也一定生效。

    关键点：作业在 Popen 返回之前就已经以 status="running" 登记进 JOBS。
    若在这个窗口里只置 stopped 标志而拿不到 proc，子进程随后照常启动并跑完，
    而终态却被标成 stopped —— 表现为“点了停止但进程还在写结果”。
    因此这里用 stopping 标志让 worker 在 Popen 返回后立刻自行终止。
    """
    with JOBS_LOCK:
        job["stopping"] = True
        if job["status"] != "running":
            return {"ok": True, "stopped": True, "message": "进程已结束"}
        proc = job["proc"]
        job["stopped"] = True
        was_paused = job["paused"]
        job["paused"] = False
    if was_paused:
        proc_ctl.resume(job["pid"])   # 先恢复再结束，避免挂起状态干扰
    if proc is None:
        # 子进程尚未创建：交给 worker 在 Popen 返回后立即处理
        return {"ok": True, "stopped": True,
                "message": "求解正在启动，已请求立即终止"}
    try:
        proc.terminate()
    except Exception as e:  # noqa: BLE001
        job_log(job, f"[server] 停止失败: {e}")
        return {"ok": False, "message": str(e)}
    try:
        proc.wait(timeout=grace)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
    job_log(job, "[server] 已请求停止求解（已找到的可行解仍保留在结果里）")
    return {"ok": True, "stopped": True}


def start_job(kind, name, cmd, quiet=False):
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "kind": kind,
        "name": name,
        "status": "running",
        "log": [],
        "log_base": 0,
        "log_next": 0,
        "returncode": None,
        "started": time.time(),
        "ended": None,
        "cmd": cmd,
        "quiet": bool(quiet),
        "paused": False,
        "stopped": False,
        "stopping": False,
        "pid": None,
        "proc": None,
    }
    with JOBS_LOCK:
        JOBS[job_id] = job
        # 只保留最近若干个作业，避免内存无限增长
        if len(JOBS) > JOB_KEEP:
            finished = sorted(
                (j for j in JOBS.values() if j["status"] != "running"),
                key=lambda j: j["started"])
            for old in finished[:max(0, len(JOBS) - JOB_KEEP)]:
                JOBS.pop(old["id"], None)

    def worker():
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        code = None
        try:
            proc = subprocess.Popen(
                cmd, cwd=PROJECT_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", env=env,
            )
            with JOBS_LOCK:
                job["proc"] = proc
                job["pid"] = proc.pid
                stopping = job["stopping"]
            if stopping:
                # 停止请求发生在 Popen 返回之前，这里补上终止
                job_stop(job)
            for line in proc.stdout:
                job_log(job, line.rstrip("\n"))
            proc.wait()
            code = proc.returncode
        except Exception as e:  # noqa: BLE001
            job_log(job, f"[server error] {e}")
        finally:
            with JOBS_LOCK:
                job["proc"] = None
                job["returncode"] = code
                if job["stopped"]:
                    job["status"] = "stopped"
                elif code == 0:
                    job["status"] = "done"
                else:
                    job["status"] = "error"
                job["ended"] = time.time()
            if code is None:
                job_log(job, "[server] 进程启动失败")
            # 被停止/异常退出时，JSONL 里可能已经有解但还没渲染出图：后台补齐
            if kind == "solve" and (job["stopped"] or code not in (0, None)):
                try:
                    repair = start_repair(name)
                    job_log(job, f"[server] 已启动后台补图（作业 {repair['id']}），"
                                  f"已找到的可行解稍后会在“结果”页补齐可视化")
                except Exception as e:  # noqa: BLE001
                    job_log(job, f"[server] 后台补图启动失败: {e}")

    threading.Thread(target=worker, daemon=True).start()
    return job


def start_repair(name):
    """后台静默补全可视化：只补缺失的 solN.svg/txt，并重画最优解 SVG。"""
    cmd = [sys.executable, os.path.join(SOURCE_DIR, "layout_viz.py"),
           config_path(name), "--only-missing", "--prune", "--best"]
    return start_job("render", name, cmd, quiet=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "LayoutWebUI/1.0"

    def log_message(self, fmt, *args):
        pass

    # ---------------- helpers ----------------
    def send_json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_text(self, text, code=200, ctype="text/plain; charset=utf-8"):
        data = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_file(self, path):
        if not os.path.isfile(path):
            self.send_text("not found", 404)
            return
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

    # ---------------- GET ----------------
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        path = u.path
        if path in ("/", "/index.html"):
            return self.send_file(os.path.join(UI_DIR, "index.html"))
        if path.startswith("/static/"):
            rel = unquote(path[len("/static/"):])
            f = os.path.join(UI_DIR, rel)
            # 必须限制在 webui/ 内：早先只校验“在项目根内”，
            # /static/../config.x.json 之类可以读到根目录下任意文件
            if not inside_dir(f, UI_DIR):
                return self.send_text("forbidden", 403)
            return self.send_file(f)
        if path == "/file":
            rel = unquote(q.get("path", [""])[0])
            f = os.path.join(PROJECT_ROOT, rel)
            ext = os.path.splitext(f)[1].lower()
            # /file 只用于结果页取 SVG/字符画；不限制扩展名就等于开放
            # “读项目根下任意文件”（含 webui_server.py、config.*.json）
            if not inside_root(f) or ext not in (".svg", ".txt"):
                return self.send_text("forbidden", 403)
            return self.send_file(f)
        if path == "/api/problems":
            return self.send_json({"problems": list_problems()})
        if path == "/api/config":
            name = q.get("name", [""])[0]
            cfg = read_json(config_path(name))
            if cfg is None:
                return self.send_json({"error": "not found"}, 404)
            return self.send_json(cfg)
        if path == "/api/job":
            jid = q.get("id", [""])[0]
            job = JOBS.get(jid)
            if not job:
                return self.send_json({"error": "no such job"}, 404)
            since_raw = q.get("since", [None])[0]
            since = None
            if since_raw not in (None, ""):
                try:
                    since = int(since_raw)
                except ValueError:
                    since = None
            return self.send_json(job_snapshot(job, since))
        if path == "/api/jobs":
            name = q.get("name", [""])[0]
            kind = q.get("kind", [""])[0]
            limit = int(q.get("limit", ["5"])[0] or 5)
            with JOBS_LOCK:
                jobs = [job for job in JOBS.values()
                        if (not name or job["name"] == name)
                        and (not kind or job["kind"] == kind)]
            jobs.sort(key=lambda j: j["started"], reverse=True)
            return self.send_json(
                {"jobs": [job_snapshot(j, tail=40) for j in jobs[:limit]]})
        if path == "/api/results":
            name = safe_name(q.get("name", [""])[0])
            return self.send_json(collect_results(name))
        if path == "/api/files":
            name = safe_name(q.get("name", [""])[0])
            rdir = os.path.join(RESULT_DIR, name)
            files = []
            if os.path.isdir(rdir):
                for fn in sorted(os.listdir(rdir)):
                    full = os.path.join(rdir, fn)
                    files.append({
                        "name": fn,
                        "size": os.path.getsize(full),
                        "path": os.path.relpath(full, PROJECT_ROOT).replace("\\", "/"),
                    })
            return self.send_json({"files": files})
        return self.send_text("not found", 404)

    # ---------------- POST ----------------
    def do_POST(self):
        u = urlparse(self.path)
        try:
            body = self.read_body()
        except Exception as e:
            return self.send_json({"error": f"bad json: {e}"}, 400)

        if u.path == "/api/config":
            try:
                name = safe_name(body.get("name", ""))
            except ValueError as e:
                return self.send_json({"error": str(e)}, 400)
            cfg = body.get("config")
            if not isinstance(cfg, dict):
                return self.send_json({"error": "config must be object"}, 400)
            path = config_path(name)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            return self.send_json({"ok": True, "name": name, "file": os.path.basename(path)})

        if u.path == "/api/solve":
            try:
                name = safe_name(body.get("name", ""))
            except ValueError as e:
                return self.send_json({"error": str(e)}, 400)
            path = config_path(name)
            if not os.path.isfile(path):
                return self.send_json({"error": "config not found"}, 404)
            mode = body.get("mode", "exact")
            timeout = float(body.get("timeout", 120))
            max_solutions = int(body.get("max_solutions", 200) or 0)
            if mode == "exact":
                cmd = [sys.executable, os.path.join(SOURCE_DIR, "layout_exact.py"),
                       path, "--time-limit", str(timeout),
                       "--workers", str(int(body.get("workers", 8))),
                       "--max-solutions", str(max_solutions),
                       "--verbose"]
                if body.get("hint"):
                    cmd.append("--hint")
                    if body.get("hint_strict"):
                        cmd.append("--hint-strict")
            else:
                cmd = [sys.executable, os.path.join(SOURCE_DIR, "layout_solver.py"),
                       path, "--timeout", str(timeout),
                       "--seed", str(int(body.get("seed", 7))),
                       "--max-solutions", str(max_solutions)]
            job = start_job("solve", name, cmd)
            return self.send_json({"job": job["id"]})

        if u.path == "/api/render":
            # 后台静默补全缺失的 solN.svg/txt 与最优解 SVG，不占用求解日志面板
            try:
                name = safe_name(body.get("name", ""))
            except ValueError as e:
                return self.send_json({"error": str(e)}, 400)
            if not os.path.isfile(config_path(name)):
                return self.send_json({"error": "config not found"}, 404)
            job = start_repair(name)
            return self.send_json({"job": job["id"]})

        if u.path in ("/api/pause", "/api/resume", "/api/stop"):
            job = JOBS.get(body.get("id", ""))
            if not job:
                return self.send_json({"error": "no such job"}, 404)
            return self.handle_control(u.path.rsplit("/", 1)[-1], job)

        return self.send_text("not found", 404)

    def handle_control(self, action, job):
        """暂停 / 继续 / 停止正在运行的求解进程。"""
        with JOBS_LOCK:
            proc = job.get("proc")
            running = job["status"] == "running" and proc is not None
            pid = job["pid"]
            paused = job["paused"]

        if action == "pause":
            if not running:
                return self.send_json({"error": "当前没有正在运行的进程"}, 409)
            if paused:
                return self.send_json({"ok": True, "paused": True,
                                       "message": "已经是暂停状态"})
            ok, msg = proc_ctl.suspend(pid)
            if ok:
                with JOBS_LOCK:
                    job["paused"] = True
                job_log(job, "[server] 已暂停求解：进程挂起，不再占用 CPU"
                              "（注意：挂起期间仍计入 --time-limit/--timeout）")
            else:
                job_log(job, f"[server] 暂停失败: {msg}")
            return self.send_json({"ok": ok, "paused": bool(ok), "message": msg})

        if action == "resume":
            if not running:
                return self.send_json({"error": "当前没有正在运行的进程"}, 409)
            if not paused:
                return self.send_json({"ok": True, "paused": False,
                                       "message": "进程未处于暂停状态"})
            ok, msg = proc_ctl.resume(pid)
            if ok:
                with JOBS_LOCK:
                    job["paused"] = False
                job_log(job, "[server] 已继续求解")
            else:
                job_log(job, f"[server] 继续失败: {msg}")
            return self.send_json({"ok": ok, "paused": not ok, "message": msg})

        # stop
        return self.send_json(job_stop(job))


def collect_results(name):
    rdir = os.path.join(RESULT_DIR, name)
    base = os.path.join(rdir, name)
    solutions = []
    jl = base + ".solutions.jsonl"
    if os.path.isfile(jl):
        for line in open(jl, encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    solutions.append(json.loads(line))
                except Exception:
                    pass
    sols = []
    for i, s in enumerate(solutions):
        svg = f"{name}.sol{i}.svg"
        txt = f"{name}.sol{i}.txt"
        has_svg = os.path.isfile(os.path.join(rdir, svg))
        has_txt = os.path.isfile(os.path.join(rdir, txt))
        sols.append({
            "index": i,
            "cost": s.get("cost"),
            "state": s.get("state", {}),
            "paths": s.get("paths", []),
            "svg": f"result/{name}/{svg}" if has_svg else None,
            "txt": f"result/{name}/{txt}" if has_txt else None,
            "ready": bool(has_svg and has_txt),
        })
    files = []
    if os.path.isdir(rdir):
        for fn in sorted(os.listdir(rdir)):
            files.append(fn)
    return {
        "name": name,
        "solutions": sols,
        "files": files,
        "best_svg": f"result/{name}/{name}.svg"
                    if os.path.isfile(base + ".svg") else None,
    }


class Server(ThreadingHTTPServer):
    """关闭 SO_REUSEADDR 的 HTTP 服务。

    标准库 HTTPServer 默认 allow_reuse_address = 1（SO_REUSEADDR）。在 Windows 上
    这个选项会让**第二个**进程也能绑定同一个端口且不报错，于是旧的僵尸服务继续
    接管请求、新进程却毫无提示——表现为浏览器一直 404 或页面空白。关掉它，
    端口被占用时就会明确失败。
    """
    allow_reuse_address = False
    daemon_threads = True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    # 路径可能被移动过，先自检一遍，避免浏览器侧只看到 404 却不知原因
    for label, path in (("前端目录", UI_DIR), ("配置目录", CONFIG_DIR)):
        if not os.path.isdir(path):
            print(f"[警告] {label}不存在: {path}")
    try:
        srv = Server((args.host, args.port), Handler)
    except OSError as e:
        # 最常见的原因：端口已被占用（例如上一个服务还在后台跑）。
        # 这时必须明确说出来，否则用户只会看到一个空白/404 的页面。
        print(f"[错误] 无法在 {args.host}:{args.port} 启动服务: {e}")
        print("       常见原因是该端口已被占用。请先结束旧进程，或换一个端口：")
        print(f"         netstat -ano | findstr :{args.port}     (Windows 查占用)")
        print(f"         python {os.path.join('src', 'webui_server.py')} "
              f"--port {args.port + 1}")
        raise SystemExit(1)
    print(f"WebUI: http://{args.host}:{args.port}/", flush=True)
    print("Ctrl+C 停止", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
