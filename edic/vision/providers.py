"""Pick the best ONNX Runtime execution providers available on this machine."""
import logging

import onnxruntime as ort

log = logging.getLogger("edic")

# fastest first; DirectML covers NVIDIA/AMD/Intel on Windows without a CUDA install
PREFERRED = ["CUDAExecutionProvider", "DmlExecutionProvider", "CoreMLExecutionProvider", "CPUExecutionProvider"]


CPU = "CPUExecutionProvider"
PREFERENCE = "auto"                       # auto | cpu | gpu (the Settings choice)


def set_preference(pref: str):
    global PREFERENCE
    PREFERENCE = pref if pref in ("auto", "cpu", "gpu") else "auto"


def gpu_providers() -> list[str]:
    have = ort.get_available_providers()
    return [p for p in PREFERRED if p in have and p != CPU]


def providers(force_cpu: bool = False) -> list[str]:
    """auto and gpu use a graphics provider when there is one (and fall back to the CPU); cpu never does."""
    if force_cpu or PREFERENCE == "cpu":
        return [CPU]
    return gpu_providers() + [CPU]


def describe() -> dict:
    p = providers()
    gpus = gpu_providers()
    return {"providers": p, "accelerated": p[0] != CPU, "device": p[0].replace("ExecutionProvider", ""),
            "gpu_available": bool(gpus), "gpu_providers": [g.replace("ExecutionProvider", "") for g in gpus], "preference": PREFERENCE}


def session(path, force_cpu: bool = False) -> ort.InferenceSession:
    """Open a model on the best provider. If a GPU provider fails to start (old driver, no DirectX 12 device,
    virtual machine) fall back to the CPU instead of failing: a slower app beats a broken one."""
    opts = ort.SessionOptions()
    opts.log_severity_level = 3
    chosen = providers(force_cpu)
    try:
        return ort.InferenceSession(str(path), sess_options=opts, providers=chosen)
    except Exception as e:                       # noqa: BLE001 - any provider start-up error means "use the CPU"
        if chosen == ["CPUExecutionProvider"]:
            raise
        log.warning("%s failed to start (%s); using the CPU", chosen[0], e)
        return ort.InferenceSession(str(path), sess_options=opts, providers=["CPUExecutionProvider"])
