# Managed-account application acceptance

## Operator-provided receipt: 2026-10-22

The operator returned the JSON receipt from the one-time diagnostic task
`HNSHL-content-client-win-01-probe-20261022T054920Z-7c2c32363459`.
The protected original is retained on production at
`C:\ProgramData\HNSHL\ContentRuntime\logs\Pipeline\app-probe-20261022T054920Z-7c2c32363459.json`.
This record summarizes the operator-provided evidence; the daily SSH account
cannot independently read that protected file. The operator confirmed removal of
only the completed diagnostic task and its staged script.

| Check | Result |
| --- | --- |
| Identity/session | `lab\rr-pipeline$`, Session 0 |
| Configuration | Unchanged |
| Azure Speech native library | Passed; no synthesis API called |
| Depth inference | Passed; finite normalized 192x320 output, DirectML provider present |
| NVENC | Passed; native exit 0 |
| Real-ESRGAN | Failed; native exit 4294967295, no output dimensions |
| Provider authentication | Passed for Azure Speech, licensed ElevenLabs Creator quota and both configured Gemini keys |
| Paid generation | Not tested; `generation_verified=false` |
| Overall | Attention required; probe exit 2 |

Depth model SHA-256:
`afb6a5c28f3b6bf1618c6e43f02073ef9dfdc70e937502d51603e57b0a1df10c`.
The provider list was `DmlExecutionProvider`, `CPUExecutionProvider`; this proves
inference completed with a DirectML-enabled session, not that every graph node
was placed on the GPU.

## Upscaler investigation

A separate synthetic-image invocation through daily-user SSH reproduced native
exit 4294967295 with `vkEnumeratePhysicalDevices failed -3` and
`invalid gpu device`, producing no image. It used the same application command
and working directory, with no provider calls or application configuration load.
This supplementary diagnostic is not a substitute for managed-account evidence.

The model `.bin` and `.param` files and system Vulkan loader are present.
Executable SHA-256:
`07e49f7cbb4ede01ae4dd4c399d3a7e5846e3d2085c3128eff881e55cb7b1a0c`.
The evidence points toward Vulkan device exposure/driver compatibility; the
specific host cause is not established. DirectML and NVENC success do not prove
Vulkan compatibility. Driver/GPU exposure investigation belongs to the lab.

The application currently returns the original image after an upscaler failure,
then resizes it normally. That fallback does not add detail and has not been
accepted as a substitute for upscaling. Application options are to supply sources
with sufficient native crop resolution or implement/test a different upscaler;
neither has been declared complete by this receipt.

Do not repeat paid authentication/generation checks to diagnose this local Vulkan
failure. Preserve the original receipt. Keep production in dry-run mode. Telegram
fixes, paid generation, full workflow/human approval and release promotion remain
separate acceptance gates.
