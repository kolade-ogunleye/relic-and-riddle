"""Focused production application checks; run through the lab's approved identity runner.

Does not register tasks, change Windows ACLs, start a bot or publish content.
Paid voice calls require a separate explicit option; no full episode is produced.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def process_identity():
    if os.name != "nt":
        raise RuntimeError("This acceptance probe requires the Windows production identity")
    import ctypes
    session = ctypes.c_uint()
    if not ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
        raise OSError("Cannot determine process session")
    account = subprocess.run(["whoami"], capture_output=True, text=True, check=True, timeout=15).stdout.strip()
    return account, session.value


def probe_local(app_root, work):
    from src import config
    import numpy as np
    import onnxruntime as ort
    import azure.cognitiveservices.speech as speech
    from PIL import Image
    from src import parallax, upscale
    checks = {}
    speech.SpeechConfig(subscription="offline-native-library-check", region=config.AZURE_SPEECH_REGION)
    checks["azure_speech_native"] = {"passed": True, "api_called": False}
    if not parallax.MODEL_PATH.is_file():
        checks["depth"] = {"passed": False, "reason": "Cached depth model missing"}
    else:
        rgb = np.random.default_rng(17).integers(0, 256, (192, 320, 3), dtype=np.uint8)
        depth = parallax.depth_map(rgb)
        providers = parallax._session().get_providers()
        valid = (depth.shape == rgb.shape[:2] and bool(np.isfinite(depth).all()) and
                 float(depth.min()) >= 0 and float(depth.max()) <= 1.0001)
        checks["depth"] = {"passed": valid and "DmlExecutionProvider" in providers,
                           "providers": providers, "shape": list(depth.shape),
                           "model_sha256": hashlib.sha256(parallax.MODEL_PATH.read_bytes()).hexdigest()}
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
               "-i", "testsrc2=size=320x240:rate=10", "-frames:v", "10", "-c:v", "h264_nvenc", "-f", "null", "NUL"]
    native = subprocess.run(command, capture_output=True, timeout=90)
    checks["nvenc"] = {"passed": native.returncode == 0, "exit_code": native.returncode}
    binary = Path(config.REALESRGAN_BIN)
    if not binary.is_file():
        checks["upscaler"] = {"passed": False, "reason": "Real-ESRGAN executable missing",
                              "current_fallback": "original-image resizing"}
    else:
        source, target = work / "upscale-source.png", work / "upscale-result.png"
        Image.fromarray(np.random.default_rng(23).integers(0, 256, (192, 128, 3), dtype=np.uint8)).save(source)
        result = subprocess.run([str(binary), "-i", str(source), "-o", str(target), "-n", "realesrgan-x4plus"],
                                cwd=binary.parent, capture_output=True, timeout=120)
        dimensions = list(Image.open(target).size) if target.is_file() else None
        checks["upscaler"] = {"passed": result.returncode == 0 and dimensions == [512, 768],
                              "exit_code": result.returncode, "dimensions": dimensions,
                              "current_fallback": "original-image resizing"}
    return checks


def probe_voices(work):
    """Use existing paid-plan quota checks and refuse unlicensed/free fallbacks."""
    from src import config
    from src.audio_engine import AudioEngine
    import soundfile as sf
    text = "Production voice check. This short recording verifies narration access."
    results = {}
    for provider in ("elevenlabs", "azure"):
        configured = config.is_configured(config.ELEVENLABS_API_KEY if provider == "elevenlabs" else config.AZURE_SPEECH_KEY)
        if not configured:
            results[provider] = {"passed": False, "reason": "Not configured"}
            continue
        engine = AudioEngine(azure_key="" if provider == "elevenlabs" else None,
                             eleven_key="" if provider == "azure" else None)
        quota = engine.voice_quota() if provider == "elevenlabs" else None
        destination = work / (provider + "-voice.mp3")
        words = engine.generate_narration(text, destination, licensed_only=True, allow_paid=True)
        audio = sf.info(destination)
        results[provider] = {"passed": bool(words and audio.duration > 0 and engine.last_provider == provider and engine.last_licensed),
                             "provider": engine.last_provider, "licensed": engine.last_licensed,
                             "characters_requested": len(text), "audio_seconds": audio.duration,
                             "subscription_characters_remaining_before": (quota["limit"] - quota["used"]) if quota else None}
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--expected-account", default="LAB\\rr-pipeline$")
    parser.add_argument("--providers", action="store_true", help="Read-only provider authentication/quota checks")
    parser.add_argument("--paid-voice", action="store_true", help="Explicitly synthesize one short licensed sample per configured voice provider")
    args = parser.parse_args(argv)
    if args.report.exists():
        parser.error("Report must not already exist")
    report = {"contract_version": 1, "started_at": datetime.now(timezone.utc).isoformat(), "checks": {}}
    prior_logging_level = logging.root.manager.disable
    logging.disable(logging.CRITICAL)  # Raw SDK exception messages do not belong in acceptance receipts.
    try:
        account, session = process_identity()
        report.update(account=account, session_id=session)
        if account.lower() != args.expected_account.lower() or session != 0:
            raise RuntimeError("Probe requires the expected managed account in Session 0")
        app = args.app_root.resolve()
        os.environ["RELIC_APP_ROOT"] = str(app)
        os.environ["RELIC_ENVIRONMENT"] = "production"
        sys.path.insert(0, str(app))
        from src import config
        if config.TELEGRAM_BOT_TOKEN.split(":", 1)[0] != "8837268021":
            raise RuntimeError("Production bot identity mismatch")
        before = hashlib.sha256((app / ".env").read_bytes()).hexdigest()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        # Keep paid samples as review evidence; only synthetic local probe media
        # lives in a temporary subdirectory and is cleaned automatically.
        with tempfile.TemporaryDirectory(dir=args.report.parent, prefix="local-probe-") as temporary:
            report["checks"].update(probe_local(app, Path(temporary)))
        if args.providers:
            from src.ops import PreFlightHealthChecker
            passed, message = PreFlightHealthChecker().probe_live_services()
            report["checks"]["provider_authentication"] = {"passed": passed, "summary": message, "generation_verified": False}
        if args.paid_voice:
            output = args.report.parent / (args.report.stem + "-voices")
            output.mkdir(exist_ok=False)
            report["checks"]["voice_generation"] = probe_voices(output)
        report["configuration_unchanged"] = before == hashlib.sha256((app / ".env").read_bytes()).hexdigest()
        voice_checks = report["checks"].get("voice_generation", {})
        report["passed"] = (report["configuration_unchanged"] and
                            all(value.get("passed", False) for name, value in report["checks"].items() if name != "voice_generation") and
                            all(value.get("passed", False) for value in voice_checks.values()))
    except Exception as exc:
        # No raw SDK messages, signed URLs, credentials or native process output.
        report.update(passed=False, error_type=type(exc).__name__)
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps(report, indent=2))
    logging.disable(prior_logging_level)
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
