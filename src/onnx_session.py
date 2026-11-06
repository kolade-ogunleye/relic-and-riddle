"""DirectML adapter selection shared by the image and depth models."""

import ctypes
import logging
import sys
import uuid
from dataclasses import dataclass

from . import config

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Adapter:
    index: int
    name: str
    dedicated_bytes: int
    vendor_id: int = 0
    software: bool = False

    @property
    def discrete(self):
        name = self.name.lower()
        integrated = any(part in name for part in ("uhd", "iris", "integrated", "radeon(tm) graphics"))
        return not self.software and not integrated and (
            "nvidia" in name or "arc" in name or self.dedicated_bytes >= 512 * 1024 * 1024
        )


def enumerate_adapters():
    """DXGI order (the DirectML device_id), including holes from software adapters.

    EnumAdapters1 retains the legacy adapter order; never sort/reindex this list.
    COM interfaces are released before returning. No optional package is needed.
    """
    if sys.platform != "win32":
        return []

    class Desc(ctypes.Structure):
        _fields_ = [("description", ctypes.c_wchar * 128),
                    ("vendor", ctypes.c_uint32), ("device", ctypes.c_uint32),
                    ("subsystem", ctypes.c_uint32), ("revision", ctypes.c_uint32),
                    ("video", ctypes.c_size_t), ("system", ctypes.c_size_t),
                    ("shared", ctypes.c_size_t), ("luid_low", ctypes.c_uint32),
                    ("luid_high", ctypes.c_int32), ("flags", ctypes.c_uint32)]

    def method(obj, index, result, *args):
        table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(result, ctypes.c_void_p, *args)(table[index])

    def release(obj):
        method(obj, 2, ctypes.c_ulong)(obj)

    factory = ctypes.c_void_p()
    dll = ctypes.WinDLL("dxgi")
    create = dll.CreateDXGIFactory1
    create.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    create.restype = ctypes.c_int32
    iid = ctypes.create_string_buffer(uuid.UUID("770aae78-f26f-4dba-a829-253c83d1b387").bytes_le)
    hr = create(iid, ctypes.byref(factory))
    if hr < 0:
        raise OSError(f"CreateDXGIFactory1 failed: 0x{hr & 0xffffffff:08x}")
    adapters = []
    try:
        enum = method(factory, 12, ctypes.c_int32, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))
        for index in range(128):
            adapter = ctypes.c_void_p()
            hr = enum(factory, index, ctypes.byref(adapter))
            if hr & 0xffffffff == 0x887A0002:  # DXGI_ERROR_NOT_FOUND
                break
            if hr < 0:
                raise OSError(f"EnumAdapters1 failed: 0x{hr & 0xffffffff:08x}")
            try:
                desc = Desc()
                hr = method(adapter, 10, ctypes.c_int32, ctypes.POINTER(Desc))(adapter, ctypes.byref(desc))
                if hr < 0:
                    raise OSError(f"GetDesc1 failed: 0x{hr & 0xffffffff:08x}")
                adapters.append(Adapter(index, desc.description, desc.video, desc.vendor, bool(desc.flags & 2)))
            finally:
                release(adapter)
    finally:
        release(factory)
    return adapters


def choose_adapter(adapters, device="auto"):
    hardware = [a for a in adapters if not a.software]
    device = str(device).strip().lower()
    if device == "auto":
        return max(hardware, key=lambda a: (a.discrete, a.dedicated_bytes, -a.index), default=None)
    if not device.isdecimal():
        raise ValueError("ONNX_DML_DEVICE must be auto or a nonnegative DXGI adapter index")
    return next((a for a in hardware if a.index == int(device)), None)


def create_session(model, *, device=None):
    """Use a named DirectML adapter, or explicitly report the CPU fallback."""
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.log_severity_level = 3
    # Fresh SessionOptions default to sequential execution, as DirectML requires.
    options.enable_mem_pattern = False
    adapter = None
    requested = config.ONNX_DML_DEVICE if device is None else device
    if "DmlExecutionProvider" in ort.get_available_providers():
        try:
            adapter = choose_adapter(enumerate_adapters(), requested)
        except (OSError, ValueError) as exc:
            log.warning("DirectML adapter selection failed: %s", exc)
    if adapter is not None:
        if not adapter.discrete:
            log.warning("DirectML selected integrated or unclassified GPU %s (adapter %d)", adapter.name, adapter.index)
        log.info("DirectML adapter %d: %s", adapter.index, adapter.name)
        try:
            session = ort.InferenceSession(str(model), sess_options=options,
                                           providers=[("DmlExecutionProvider", {"device_id": str(adapter.index)}),
                                                      "CPUExecutionProvider"])
            if "DmlExecutionProvider" in session.get_providers():
                return session
            log.warning("DirectML unavailable for %s; session fell back to CPU", adapter.name)
            return session
        except Exception as exc:  # noqa: BLE001 -- ORT provider failures use several pybind exception classes.
            log.warning("DirectML session failed on %s: %s; falling back to CPU", adapter.name, exc)
    else:
        log.warning("No DirectML adapter available for ONNX_DML_DEVICE=%s; falling back to CPU", requested)
    return ort.InferenceSession(str(model), sess_options=options, providers=["CPUExecutionProvider"])
