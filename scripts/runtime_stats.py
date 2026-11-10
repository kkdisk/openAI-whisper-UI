"""比較工具的資源快照；GPU 記憶體為整張卡取樣，非程序歸屬。"""

import ctypes
import os
import subprocess
import threading
import time


def peak_ram_bytes():
    counters = memory_counters()
    return counters.PeakWorkingSetSize if counters else None


def memory_snapshot():
    """工作集可能在系統記憶體吃緊時被縮減；private commit 不受縮減影響。"""
    counters = memory_counters()
    if counters is None:
        return None
    return {
        "working_set_bytes": counters.WorkingSetSize,
        "peak_working_set_bytes": counters.PeakWorkingSetSize,
        "private_bytes": counters.PagefileUsage,
        "peak_private_bytes": counters.PeakPagefileUsage,
    }


def memory_counters():
    if os.name != "nt":
        return None
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t)
            for name in (
                "PeakWorkingSetSize",
                "WorkingSetSize",
                "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage",
                "QuotaPeakNonPagedPoolUsage",
                "QuotaNonPagedPoolUsage",
                "PagefileUsage",
                "PeakPagefileUsage",
            )
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    current = ctypes.windll.kernel32.GetCurrentProcess
    current.restype = wintypes.HANDLE
    query = ctypes.windll.psapi.GetProcessMemoryInfo
    query.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    if query(current(), ctypes.byref(counters), counters.cb):
        return counters
    return None


class RuntimeStats:
    def __init__(self, device, index=0):
        self.device, self.index = device, index
        self.samples = []
        self.stop = threading.Event()
        self.thread = None

    def sample_gpu(self):
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "-i",
                    str(self.index),
                    "--query-gpu=memory.used",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=2,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if result.returncode == 0:
                self.samples.append(int(result.stdout.strip()))
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass

    def poll(self):
        while not self.stop.wait(0.25):
            self.sample_gpu()

    def __enter__(self):
        if self.device == "cuda":
            self.sample_gpu()
            self.thread = threading.Thread(target=self.poll, daemon=True)
            self.thread.start()
        self.started_cpu = time.process_time()
        return self

    def __exit__(self, *args):
        self.cpu_sec = time.process_time() - self.started_cpu
        self.stop.set()
        if self.thread:
            self.thread.join(3)

    def report(self):
        return {
            "process_cpu_sec": self.cpu_sec,
            "process_peak_working_set_bytes": peak_ram_bytes(),
            "device_vram_baseline_mib": self.samples[0] if self.samples else None,
            "device_vram_sampled_peak_mib": max(self.samples) if self.samples else None,
            "gpu_samples": len(self.samples),
            "notice": (
                "RAM 為此程序生命週期峰值；VRAM 為整卡約 250ms 加查詢延遲的取樣峰值，"
                "含其他程序，可能漏掉尖峰。"
            ),
        }
