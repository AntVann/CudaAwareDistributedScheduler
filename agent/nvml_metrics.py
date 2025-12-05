import os
import time
from typing import List, TypedDict


class GpuSample(TypedDict):
    index: int
    name: str
    mem_total_mb: int
    mem_used_mb: int
    utilization: float
    temperature: int


def _sample_fake_gpu_metrics(num_gpus: int, mem_mb: int) -> List[GpuSample]:
    """
    Deterministic fake metrics for environments without GPUs.
    """
    stamp = time.time()
    samples: List[GpuSample] = []
    for idx in range(num_gpus):
        utilization = ((stamp / (idx + 1)) % 1.0) * 100.0
        mem_used = int((stamp * (idx + 2)) % mem_mb)
        temperature = int(30 + ((stamp + idx * 3) % 40))
        samples.append(
            {
                "index": idx,
                "name": f"FakeGPU-{idx}",
                "mem_total_mb": mem_mb,
                "mem_used_mb": mem_used,
                "utilization": round(utilization, 2),
                "temperature": temperature,
            }
        )
    return samples


def _sample_nvml_metrics() -> List[GpuSample]:
    """
    Collect real GPU metrics via NVML. Falls back to fake if NVML is missing or fails.
    """
    import pynvml as nvml

    nvml.nvmlInit()
    count = nvml.nvmlDeviceGetCount()
    samples: List[GpuSample] = []
    for idx in range(count):
        handle = nvml.nvmlDeviceGetHandleByIndex(idx)
        mem = nvml.nvmlDeviceGetMemoryInfo(handle)
        util = nvml.nvmlDeviceGetUtilizationRates(handle)
        temp = nvml.nvmlDeviceGetTemperature(handle, nvml.NVML_TEMPERATURE_GPU)
        name_raw = nvml.nvmlDeviceGetName(handle)
        name = name_raw.decode() if hasattr(name_raw, "decode") else str(name_raw)

        samples.append(
            {
                "index": idx,
                "name": name,
                "mem_total_mb": int(mem.total / 1024 / 1024),
                "mem_used_mb": int(mem.used / 1024 / 1024),
                "utilization": float(util.gpu),
                "temperature": int(temp),
            }
        )
    nvml.nvmlShutdown()
    return samples


def sample_gpu_metrics(
    mode: str = "auto", fake_gpu_count: int = 2, fake_gpu_mem_mb: int = 24576
) -> List[GpuSample]:
    """
    mode:
      - auto (default): try NVML, fall back to fake
      - real: require NVML; if unavailable, raise
      - fake: always fake
    """
    mode = mode.lower()
    if mode == "fake":
        return _sample_fake_gpu_metrics(fake_gpu_count, fake_gpu_mem_mb)

    if mode in ("auto", "real"):
        try:
            return _sample_nvml_metrics()
        except Exception:
            if mode == "real":
                raise

    # fallback when NVML unavailable or failed in auto mode
    return _sample_fake_gpu_metrics(fake_gpu_count, fake_gpu_mem_mb)
