# QF-93: explicit DirectML adapter selection

Both production image models now use `src.onnx_session.create_session`.
`ONNX_DML_DEVICE=auto` prefers a discrete GPU and excludes software adapters;
a numeric value selects that exact DXGI index. The helper logs the GPU name,
warns about integrated/unclassified hardware, and explicitly warns on CPU fallback.
The upscaler's session-creation lock and both inference locks remain in place.
No new package or deployed environment change is needed.

The enumerator uses `CreateDXGIFactory1`, `EnumAdapters1`, and `GetDesc1` via
ctypes, releases every COM interface, and preserves native indices. Its discrete
classification uses GPU names and dedicated memory; unusual hardware can be
selected explicitly. Fresh ONNX session options use their default sequential
execution, with memory patterns disabled as required by DirectML.

Reproduce with the repository virtual environment:

```text
python docs/reviews/quality-fixes-2026-11/QF-93/measure.py <repo root>
python -m pytest -q -n 4 --dist worksteal tests/test_qf93_directml_device.py tests/test_upscale.py tests/test_parallax.py
```

The measurement captures both real production callers with a mocked
integrated-first adapter list. Parent: both provide no device ID (implicit index
0); candidate: both explicitly request index 1. It then enumerates this machine
and times every hardware adapter using generated float32 1024-square MatMul.
The ONNX protobuf and deterministic input arrays are generated locally: no
downloaded model, extra dependency, external request, or ignored input is required.

| DXGI index | Adapter | Median milliseconds |
| --- | --- | ---: |
| 0 | Intel UHD Graphics | 78.422 |
| 1 | NVIDIA RTX 5000 Ada Generation Laptop GPU (auto choice) | 7.718 |
| 2 | Intel UHD Graphics | 16.994 |
| 3 | Microsoft Basic Render Driver | software excluded |

These timings are machine-specific, include input/output transfers, and use
three warmups plus fifteen measured executions. Output is checked against CPU
inference. They are a selection diagnostic, not upscaler/depth-model throughput.
Other local tests were running during measurement; clocks and load affect timing.
The parent exposes no adapter enumeration helper, so its evidence records actual
caller arguments but does not claim a per-adapter benchmark.

Evidence: `before.json`, `after.json`, and `measurements.json`. The candidate
measurements record source commit `116cd00`; the final commit adds evidence/docs
only. Parent regression: all 15 failed (helper absent), 53.27 s. Candidate plus
existing upscaler/parallax coverage: 29 passed, one pre-existing missing-depth-model
skip, 97.92 s. No existing test expectations or skips changed. Ruff: no new
findings per file.

References:
- [ONNX Runtime DirectML device IDs and session constraints](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html)
- [DXGI adapter enumeration](https://learn.microsoft.com/en-us/windows/win32/direct3d11/overviews-direct3d-11-devices-enum)
- [DXGI adapter description](https://learn.microsoft.com/en-us/windows/win32/api/dxgi/ns-dxgi-dxgi_adapter_desc1)
- [ONNX protobuf schema used by the generated benchmark](https://github.com/onnx/onnx/blob/main/onnx/onnx.proto)
