# Upscaler: Real-ESRGAN x4plus on ONNX Runtime

Low-resolution stills are upscaled 4x before rendering (`src/upscale.py`). The production VM
exposes DirectML but not Vulkan to the guest (see [runtime acceptance](runtime-acceptance.md)),
so the Vulkan build `realesrgan-ncnn-vulkan` fails there with `vkEnumeratePhysicalDevices failed -3`.
The application therefore runs the same model through ONNX Runtime, DirectML first, CPU second;
the Vulkan build remains a fallback where it works, then plain resizing.

## Model provenance

| Item | Value |
| --- | --- |
| Source | Official release `v0.1.0` of [xinntao/Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN/releases/tag/v0.1.0), asset `RealESRGAN_x4plus.pth` |
| Licence | BSD-3-Clause (repository licence) |
| Source size / SHA-256 | 67,040,989 bytes (matches the release asset size) / `4fa0d38905f75ac06eb49a7951b426670021be3018265fd191d2125df9d682f1` |
| Conversion | `scripts/convert_realesrgan_onnx.py`: RRDBNet (64 features, 23 blocks, 4x), `torch.load(weights_only=True)`, strict state-dict load, ONNX opset 17, dynamic height/width |
| Output | `tools/models/realesrgan_x4plus.onnx`, 67,051,636 bytes, SHA-256 `1dd781a70b7e849b473bf8cdb801f8cf1abfc42438de313048227691a854a3d3` |

`tools/` is not tracked by Git (binaries and models); the file is deployed with the release's
tools and verified by its SHA-256, and the runtime probe reports the hash it loaded.

## Validation (development workstation, 2026-10-22)

- Conversion: every weight matched; ONNX vs PyTorch maximum absolute difference 2.6e-6.
- Quality: on a 320x240 photo crop, ONNX output vs `realesrgan-ncnn-vulkan` output: PSNR 44.6 dB
  (the same model; differences are numeric precision only).
- Speed on DirectML (RTX 5000 Ada): first call about 15 s (one-time graph compilation per
  process), then 0.5 s for 320x240 and 1.5 s for 640x480 to 2560x1920.
- Tiling: 192 px tiles with 16 px context, reflect-padded to whole tiles so DirectML compiles one
  shape; only tile centres are kept. Tests check exact equality with a whole-image upscale at
  sizes smaller than, equal to and larger than a tile.

## Production acceptance

Run the focused probe under the pipeline identity (`docs/production-probe-handoff.md`). The
`upscaler` check passes only with real 4x output (512x768 from a 128x192 sample) from a
DirectML-enabled session; `upscaler_vulkan_info` records the Vulkan build's result for reference.
