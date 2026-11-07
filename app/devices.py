"""共用裝置政策；CPU 路徑不查詢或初始化 CUDA。"""

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from app.schemas import AppError

CUDA_ERRORS = {
    "cuda_unavailable": "未偵測到指定的 NVIDIA CUDA 裝置。",
    "cuda_runtime": "CUDA 驅動或 DLL 無法載入，請檢查 CUDA／cuDNN 相容性。",
    "cuda_compute": "指定 GPU 不支援要求的運算精度。",
    "cuda_oom": "GPU 顯存不足，請釋放顯存或手動選擇 CPU。",
}
_dll_handles = []


def prepare_cuda_runtime():
    """只註冊目前 venv 的 NVIDIA wheel DLL；保留 handle 至程序退出。"""
    if os.name != "nt" or _dll_handles:
        return
    root = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    for package in ("cublas", "cudnn", "cuda_runtime", "cuda_nvrtc"):
        directory = root / package / "bin"
        if directory.is_dir():
            _dll_handles.append(os.add_dll_directory(str(directory)))
            # CTranslate2 的延遲 LoadLibrary 也會使用 PATH 搜尋依賴。
            os.environ["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")


def cuda_error(exc):
    if isinstance(exc, AppError):
        return exc.code if exc.code in CUDA_ERRORS else None
    message = str(exc).lower()
    if any(x in message for x in ("out of memory", "alloc_failed")) and any(
        x in message for x in ("cuda", "cublas", "cudnn", "gpu")
    ):
        return "cuda_oom"
    if any(
        x in message
        for x in ("cublas", "cudnn", "cuda driver", "cuda runtime", "cuda error", "cuda failed")
    ):
        return "cuda_runtime"
    return None


@dataclass(frozen=True)
class DeviceSelection:
    device: str
    compute_type: str
    device_index: int = 0
    fallback_reason: str | None = None

    def model_kwargs(self):
        return {
            "device": self.device,
            "compute_type": self.compute_type,
            "device_index": self.device_index,
        }


def select_device(requested="cpu", compute_type="auto", device_index=0):
    if requested == "cpu":
        return DeviceSelection("cpu", "int8")
    import ctranslate2

    try:
        prepare_cuda_runtime()
        if ctranslate2.get_cuda_device_count() <= device_index:
            raise AppError("cuda_unavailable", CUDA_ERRORS["cuda_unavailable"])
        supported = ctranslate2.get_supported_compute_types("cuda", device_index)
        precision = compute_type
        if precision == "auto":
            precision = next((p for p in ("int8_float16", "float16") if p in supported), None)
        if precision not in supported:
            raise AppError("cuda_compute", CUDA_ERRORS["cuda_compute"])
        return DeviceSelection("cuda", precision, device_index)
    except Exception as exc:
        code = cuda_error(exc)
        if code is None:
            raise
        if requested == "auto":
            return DeviceSelection("cpu", "int8", fallback_reason=code)
        raise AppError(code, CUDA_ERRORS[code]) from exc
