#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
跨平台的“暂停 / 恢复 / 终止子进程”小工具（只用标准库）。

用途：WebUI 里对正在运行的求解进程做暂停/继续。

- Windows：优先用 ntdll 的 NtSuspendProcess / NtResumeProcess；
  不可用时退回到 kernel32 的 SuspendThread / ResumeThread 逐线程挂起。
- POSIX：SIGSTOP / SIGCONT。

所有函数都返回 (ok, message)，永不抛出，方便服务端直接回显原因。
"""

import os
import signal
import sys

IS_WINDOWS = os.name == "nt"

if IS_WINDOWS:  # pragma: no cover - 只在 Windows 上编译
    import ctypes
    from ctypes import wintypes

    PROCESS_SUSPEND_RESUME = 0x0800
    TH32CS_SNAPTHREAD = 0x00000004
    THREAD_SUSPEND_RESUME = 0x0002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class THREADENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", ctypes.c_long),
            ("tpDeltaPri", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
        ]

    def _kernel32():
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.CloseHandle.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k.Thread32First.restype = wintypes.BOOL
        k.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(THREADENTRY32)]
        k.Thread32Next.restype = wintypes.BOOL
        k.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(THREADENTRY32)]
        k.OpenThread.restype = wintypes.HANDLE
        k.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.SuspendThread.restype = wintypes.DWORD
        k.SuspendThread.argtypes = [wintypes.HANDLE]
        k.ResumeThread.restype = wintypes.DWORD
        k.ResumeThread.argtypes = [wintypes.HANDLE]
        return k

    def _ntdll():
        try:
            n = ctypes.WinDLL("ntdll")
        except OSError as e:
            return None, str(e)
        for name in ("NtSuspendProcess", "NtResumeProcess"):
            if not hasattr(n, name):
                return None, f"ntdll 缺少 {name}"
            fn = getattr(n, name)
            fn.restype = ctypes.c_long
            fn.argtypes = [wintypes.HANDLE]
        return n, ""

    def _handle(pid):
        k = _kernel32()
        h = k.OpenProcess(PROCESS_SUSPEND_RESUME, False, pid)
        if not h:
            raise OSError(ctypes.get_last_error(), "OpenProcess 失败（权限不足或进程已退出）")
        return k, h

    def _nt_apply(pid, suspend):
        n, err = _ntdll()
        if n is None:
            return False, err
        k, h = _handle(pid)
        try:
            rc = (n.NtSuspendProcess if suspend else n.NtResumeProcess)(h)
            if rc != 0:
                return False, f"ntdll 返回 0x{rc & 0xFFFFFFFF:08X}"
            return True, "NtSuspendProcess" if suspend else "NtResumeProcess"
        finally:
            k.CloseHandle(h)

    def _thread_ids(pid):
        k = _kernel32()
        snap = k.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
        if snap == INVALID_HANDLE_VALUE:
            raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot 失败")
        ids = []
        try:
            entry = THREADENTRY32()
            entry.dwSize = ctypes.sizeof(THREADENTRY32)
            ok = k.Thread32First(snap, ctypes.byref(entry))
            while ok:
                if entry.th32OwnerProcessID == pid:
                    ids.append(entry.th32ThreadID)
                ok = k.Thread32Next(snap, ctypes.byref(entry))
        finally:
            k.CloseHandle(snap)
        return ids

    def _thread_apply(pid, suspend):
        k = _kernel32()
        ids = _thread_ids(pid)
        if not ids:
            return False, "未找到该进程的线程"
        done = 0
        for tid in ids:
            h = k.OpenThread(THREAD_SUSPEND_RESUME, False, tid)
            if not h:
                continue
            try:
                rc = (k.SuspendThread if suspend else k.ResumeThread)(h)
                if rc != 0xFFFFFFFF:
                    done += 1
            finally:
                k.CloseHandle(h)
        if done == 0:
            return False, "SuspendThread/ResumeThread 全部失败"
        return True, f"线程级 {'挂起' if suspend else '恢复'} {done}/{len(ids)} 个线程"


def suspend(pid):
    """挂起进程（暂停）。返回 (ok, message)。"""
    if not pid:
        return False, "无进程号"
    if IS_WINDOWS:
        try:
            return _nt_apply(int(pid), True)
        except OSError as e:
            try:
                return _thread_apply(int(pid), True)
            except OSError as e2:
                return False, f"{e}; 退回线程级也失败: {e2}"
    try:
        os.kill(pid, signal.SIGSTOP)
        return True, "SIGSTOP"
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def resume(pid):
    """恢复进程（继续）。返回 (ok, message)。"""
    if not pid:
        return False, "无进程号"
    if IS_WINDOWS:
        try:
            return _nt_apply(int(pid), False)
        except OSError as e:
            try:
                return _thread_apply(int(pid), False)
            except OSError as e2:
                return False, f"{e}; 退回线程级也失败: {e2}"
    try:
        os.kill(pid, signal.SIGCONT)
        return True, "SIGCONT"
    except Exception as e:  # noqa: BLE001
        return False, str(e)


if __name__ == "__main__":  # 手工自测：python proc_ctl.py <pid> suspend|resume
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(0)
    action = sys.argv[2].lower()
    fn = suspend if action in ("suspend", "pause", "stop2") else resume
    ok, msg = fn(int(sys.argv[1]))
    print(("OK " if ok else "FAIL ") + msg)
    sys.exit(0 if ok else 1)
