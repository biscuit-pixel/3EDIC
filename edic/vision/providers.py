"""Pick the best ONNX Runtime execution providers available on this machine."""
import onnxruntime as ort

# fastest first; DirectML covers NVIDIA/AMD/Intel on Windows without a CUDA install
PREFERRED = ["CUDAExecutionProvider", "DmlExecutionProvider", "CoreMLExecutionProvider", "CPUExecutionProvider"]


def providers(force_cpu: bool = False) -> list[str]:
    if force_cpu:
        return ["CPUExecutionProvider"]
    have = ort.get_available_providers()
    chosen = [p for p in PREFERRED if p in have]
    return chosen or ["CPUExecutionProvider"]


def describe() -> dict:
    p = providers()
    return {"providers": p, "accelerated": p[0] != "CPUExecutionProvider", "device": p[0].replace("ExecutionProvider", "")}


def session(path, force_cpu: bool = False) -> ort.InferenceSession:
    opts = ort.SessionOptions()
    opts.log_severity_level = 3
    return ort.InferenceSession(str(path), sess_options=opts, providers=providers(force_cpu))
