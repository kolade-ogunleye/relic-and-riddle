"""Explicit DirectML selection, warnings, fallback, and both production callers."""

import logging
import sys
from types import SimpleNamespace

import pytest


def _module():
    from src import onnx_session
    return onnx_session


def _adapters(module):
    return [module.Adapter(0, "Intel UHD Graphics", 128 << 20),
            module.Adapter(1, "NVIDIA RTX test", 8 << 30),
            module.Adapter(2, "Microsoft Basic Render Driver", 0, software=True)]


def _runtime(monkeypatch, module, *, available=True, fail=False, silent=False):
    calls = []

    def session(model, **kwargs):
        calls.append((model, kwargs))
        dml = isinstance(kwargs["providers"][0], tuple)
        if dml and fail:
            raise RuntimeError("adapter initialization failed")
        names = ["DmlExecutionProvider", "CPUExecutionProvider"] if dml and not silent else ["CPUExecutionProvider"]
        return SimpleNamespace(get_providers=lambda: names)

    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(
        SessionOptions=lambda: SimpleNamespace(enable_mem_pattern=True, execution_mode="sequential"),
        get_available_providers=lambda: ["DmlExecutionProvider", "CPUExecutionProvider"] if available
        else ["CPUExecutionProvider"], InferenceSession=session))
    monkeypatch.setattr(module, "enumerate_adapters", lambda: _adapters(module))
    return calls


def test_auto_selects_discrete_second_adapter(monkeypatch, caplog):
    module = _module()
    calls = _runtime(monkeypatch, module)
    monkeypatch.setattr(module.config, "ONNX_DML_DEVICE", "auto")
    with caplog.at_level(logging.INFO):
        module.create_session("model.onnx")
    assert calls[0][1]["providers"][0] == ("DmlExecutionProvider", {"device_id": "1"})
    assert not calls[0][1]["sess_options"].enable_mem_pattern
    assert "NVIDIA RTX test" in caplog.text


def test_explicit_integrated_adapter_is_honoured_and_warned(monkeypatch, caplog):
    module = _module()
    calls = _runtime(monkeypatch, module)
    module.create_session("model.onnx", device="0")
    assert calls[0][1]["providers"][0][1] == {"device_id": "0"}
    assert "integrated" in caplog.text and "Intel UHD Graphics" in caplog.text


def test_explicit_index_is_not_list_position(monkeypatch):
    module = _module()
    calls = _runtime(monkeypatch, module)
    monkeypatch.setattr(module, "enumerate_adapters", lambda: [module.Adapter(4, "NVIDIA RTX", 8 << 30)])
    module.create_session("model.onnx", device="4")
    assert calls[0][1]["providers"][0][1] == {"device_id": "4"}


@pytest.mark.parametrize("device", ["-1", "nvidia", "1.5", "99", "2"])
def test_invalid_missing_or_software_choice_falls_back_explicitly(monkeypatch, caplog, device):
    module = _module()
    calls = _runtime(monkeypatch, module)
    module.create_session("model.onnx", device=device)
    assert len(calls) == 1 and calls[0][1]["providers"] == ["CPUExecutionProvider"]
    assert "falling back to CPU" in caplog.text


@pytest.mark.parametrize("reason", ["provider", "enumeration", "empty", "initialization", "silent"])
def test_unavailable_directml_warns_and_returns_cpu(monkeypatch, caplog, reason):
    module = _module()
    calls = _runtime(monkeypatch, module, available=reason != "provider",
                     fail=reason == "initialization", silent=reason == "silent")
    if reason == "enumeration":
        def broken():
            raise OSError("DXGI unavailable")
        monkeypatch.setattr(module, "enumerate_adapters", broken)
    elif reason == "empty":
        monkeypatch.setattr(module, "enumerate_adapters", list)
    session = module.create_session("model.onnx", device="auto")
    assert session.get_providers() == ["CPUExecutionProvider"]
    assert "CPU" in caplog.text
    assert len(calls) == (2 if reason == "initialization" else 1)


def test_auto_excludes_software_and_recognises_intel_discrete():
    module = _module()
    adapters = [module.Adapter(0, "Intel Iris Xe Graphics", 1 << 30),
                module.Adapter(1, "Intel Arc A770", 16 << 30),
                module.Adapter(2, "Software Renderer", 32 << 30, software=True)]
    assert module.choose_adapter(adapters).index == 1


def test_both_image_models_use_the_shared_helper(monkeypatch):
    module = _module()
    from src import parallax, upscale
    calls = []
    sentinel = SimpleNamespace(get_providers=lambda: ["DmlExecutionProvider"])

    def create(model):
        calls.append(model)
        return sentinel

    monkeypatch.setattr(module, "create_session", create)
    monkeypatch.setattr(upscale, "_session", None)
    parallax._session.cache_clear()
    try:
        assert upscale._onnx_session() is sentinel
        assert parallax._session() is sentinel
        assert calls == [upscale.ONNX_MODEL, parallax.MODEL_PATH]
    finally:
        parallax._session.cache_clear()
