"""Windows 工作程序的作業物件與啟動握手。"""

import ctypes
import multiprocessing as mp
import os
import time
from ctypes import wintypes


class JobObject:
    def __init__(self, process):
        self.handle = None
        if os.name != "nt":
            raise RuntimeError("第一版工作程序僅支援 Windows。")

        class Basic(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_uint64)
                for name in (
                    "ReadOperationCount",
                    "WriteOperationCount",
                    "OtherOperationCount",
                    "ReadTransferCount",
                    "WriteTransferCount",
                    "OtherTransferCount",
                )
            ]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", Basic),
                ("IoInfo", IO),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel = kernel
        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Extended()
        limits.BasicLimitInformation.LimitFlags = 0x2000
        try:
            if not kernel.SetInformationJobObject(
                self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            if not kernel.AssignProcessToJobObject(self.handle, wintypes.HANDLE(process.sentinel)):
                raise ctypes.WinError(ctypes.get_last_error())
        except Exception:
            self.close()
            raise

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def worker_entry(gate, cancel, sender, target, arguments):
    """綁定前不可載入引擎；父程序消失則退出。"""
    try:
        parent = mp.parent_process()
        while not gate.wait(0.1):
            if parent is None or not parent.is_alive():
                os._exit(1)
        if cancel.is_set():
            return

        def emit(kind, payload):
            sender.send((kind, payload))

        target(*arguments, emit, cancel.is_set)
        sender.send(("done", {}))
    except Exception as exc:
        from app.schemas import AppError

        if isinstance(exc, AppError):
            sender.send(("error", {"code": exc.code, "message": exc.message}))
        else:
            sender.send(
                ("error", {"code": "worker_failed", "message": "轉錄工作失敗，請檢查模型與音軌。"})
            )
    finally:
        sender.close()


def stop_process(process, cancel, grace: float):
    cancel.set()
    process.join(grace)
    if process.is_alive():
        process.terminate()
        process.join(2)
    if process.is_alive():
        process.kill()
        process.join(0.5)
    return not process.is_alive()


def blocking_test_worker(emit, cancelled):
    """不檢查取消旗標，供強制終止測試使用。"""
    emit("ready", {})
    while True:
        time.sleep(0.1)
