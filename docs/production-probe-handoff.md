# Application probe under the managed production identity

This is an application acceptance request to the lab operator. It does not ask
the application agent to change managed accounts, task registration, ACLs or
Wazuh. Existing production posts live (operator activation, 2026-10-25; approval
still decides each post); the probe starts no episode run.

Run the released `scripts/runtime_probe.py` through the approved lab canary
mechanism as `LAB\rr-pipeline$` in Session 0, using the production application's
existing Python and the approved FFmpeg PATH. The daily user's SSH session cannot
substitute for that identity or inspect its protected receipts.

Example command for that identity runner (stage the script from the reviewed
application artifact first):

```powershell
<operator-home>\relic-and-riddle\.venv\Scripts\python.exe C:\approved-artifact\scripts\runtime_probe.py --app-root <operator-home>\relic-and-riddle --report C:\ProgramData\HNSHL\ContentRuntime\logs\Pipeline\app-probe-UNIQUE-UTC.json --providers
```

Use a new report filename for each attempt; previous receipts are never replaced.
The report must confirm the expected identity, Session 0, configuration unchanged,
and the result of actual cached-model DirectML depth inference, native Azure
Speech loading, NVENC encoding and a small Real-ESRGAN invocation. The latter
reports whether original-image resizing is still the fallback. A failed Vulkan
test is an explicit application/host handoff, not evidence that upscaling worked.

`--providers` performs authentication/quota checks only. Actual licensed voice
generation requires the separate explicit `--paid-voice` option: one short
sample for each configured ElevenLabs/Azure provider, retaining the short audio
for review. ElevenLabs is gated by the existing paid-plan/remaining-character
checks; unlicensed and edge-tts fallback are forbidden. Preserve the character
receipt; subscription characters are not falsely reported as a per-call USD
charge. No provider caps are increased. This does not validate a full episode or
paid video generation, and does not approve a replacement video API.

Return the safe JSON report and native integer exit code through the lab's normal
evidence process. Do not return configuration files, tokens, key values, full
provider errors or raw task XML. No second bot, publication, reboot or production
task restart is needed. The script defaults to no paid synthesis.
