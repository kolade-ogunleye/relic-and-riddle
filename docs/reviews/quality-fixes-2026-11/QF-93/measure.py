"""python measure.py <repo root>; generated inputs, no models or network needed.

Adapter timings are machine-specific end-to-end 1024-square float32 MatMul
latency, including transfers. They are not Real-ESRGAN/depth-model timings.
The tiny ONNX protobuf is generated from the official ONNX schema without
adding the optional onnx package. It has no weights or downloaded assets.
"""

import importlib
import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent


def _varint(value):
    data = bytearray()
    while value > 127:
        data.append((value & 127) | 128)
        value >>= 7
    data.append(value)
    return bytes(data)


def _field(number, value):
    if isinstance(value, int):
        return _varint(number << 3) + _varint(value)
    if isinstance(value, str):
        value = value.encode()
    return _varint((number << 3) | 2) + _varint(len(value)) + value


def model_bytes(size=1024):
    # ModelProto/GraphProto/NodeProto/ValueInfoProto from onnx/onnx.proto.
    shape = _field(1, _field(1, size)) * 2
    tensor_type = _field(1, _field(1, 1) + _field(2, shape))

    def value(name):
        return _field(1, name) + _field(2, tensor_type)

    node = _field(1, "a") + _field(1, "b") + _field(2, "y") + _field(4, "MatMul")
    graph = (_field(1, node) + _field(2, "generated_matmul") +
             _field(11, value("a")) + _field(11, value("b")) + _field(12, value("y")))
    return _field(1, 8) + _field(7, graph) + _field(8, _field(2, 13))


def caller_selection(helper):
    """Capture actual caller provider arguments with integrated-first mocked DXGI."""
    from src import parallax, upscale
    captured = []

    def session(*args, **kwargs):
        captured.append(kwargs["providers"])
        return SimpleNamespace(get_providers=lambda: ["DmlExecutionProvider", "CPUExecutionProvider"])

    fake = SimpleNamespace(get_available_providers=lambda: ["DmlExecutionProvider", "CPUExecutionProvider"],
                           SessionOptions=lambda: SimpleNamespace(), InferenceSession=session)
    upscale._session = None
    parallax._session.cache_clear()
    with patch.dict(sys.modules, {"onnxruntime": fake}):
        if helper:
            adapters = [helper.Adapter(0, "Intel UHD Graphics", 128 << 20),
                        helper.Adapter(1, "NVIDIA RTX fixture", 8 << 30)]
            with patch.object(helper, "enumerate_adapters", return_value=adapters), \
                    patch.object(helper.config, "ONNX_DML_DEVICE", "auto"):
                upscale._onnx_session()
                parallax._session()
        else:
            upscale._onnx_session()
            parallax._session()
    upscale._session = None
    parallax._session.cache_clear()
    return captured


def benchmark(helper):
    import onnxruntime as ort
    if helper is None:
        return {"available": False, "reason": "Parent has no adapter enumeration helper", "timings": []}
    adapters = helper.enumerate_adapters()
    chosen = helper.choose_adapter(adapters, "auto")
    output = {"available": "DmlExecutionProvider" in ort.get_available_providers(),
              "auto_adapter": None if chosen is None else {"index": chosen.index, "name": chosen.name},
              "timings": []}
    rng = np.random.default_rng(93)
    values = {name: rng.random((1024, 1024), dtype=np.float32) for name in ("a", "b")}
    with tempfile.TemporaryDirectory(prefix="qf93-") as folder:
        model = Path(folder) / "matmul.onnx"
        model.write_bytes(model_bytes())
        options = ort.SessionOptions()
        options.enable_mem_pattern = False
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        cpu = ort.InferenceSession(str(model), sess_options=options, providers=["CPUExecutionProvider"])
        expected = cpu.run(None, values)[0]
        del cpu
        for adapter in adapters:
            entry = {"index": adapter.index, "name": adapter.name, "discrete": adapter.discrete,
                     "software": adapter.software}
            output["timings"].append(entry)
            if adapter.software or not output["available"]:
                entry["status"] = "software excluded" if adapter.software else "DirectML provider unavailable"
                continue
            session = helper.create_session(model, device=str(adapter.index))
            entry["providers"] = session.get_providers()
            if "DmlExecutionProvider" not in entry["providers"]:
                entry["status"] = "CPU fallback, not a GPU timing"
                continue
            for _ in range(3):
                result = session.run(None, values)[0]
            np.testing.assert_allclose(result, expected, rtol=1e-4, atol=1e-3)
            elapsed = []
            for _ in range(15):
                start = time.perf_counter()
                session.run(None, values)
                elapsed.append(1000 * (time.perf_counter() - start))
            entry.update(status="ok", median_ms=round(statistics.median(elapsed), 3), samples=15)
            del session
    return output


def main():
    try:
        helper = importlib.import_module("src.onnx_session")
    except ModuleNotFoundError as exc:
        if exc.name != "src.onnx_session":
            raise
        helper = None
    providers = caller_selection(helper)
    result = {
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "measurement": "generated MatMul 1024x1024 float32; 3 warmups, 15 measured; machine-specific",
        "production_callers_with_integrated_first": providers,
        "both_callers_choose_discrete": all(isinstance(p[0], tuple) and p[0][1].get("device_id") == "1"
                                            for p in providers),
        "machine": benchmark(helper),
    }
    text = json.dumps(result, indent=2)
    (HERE / "measurements.json").write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
