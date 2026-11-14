"""QF-11 acceptance: FFmpeg decodes of the mix per master, and the release gate's sound checks and figures.

    python measure.py <repo root>

Runs the code of <repo root> (on the base too: its gate has no sound checks, and its gain search decodes the mix
more often). Inputs are committed: the generated programmes and masters of this commit's
tests/test_qf11_audio_qa.py (a voice-like programme over a pink-noise bed; the same with a 0.8 s dropout, a 1.3 s
silent end, a real master's 0.13 s silent start and a 50 Hz drone; a corrupt file), and real_data*.json beside this
file (written by real_data.py over the development runs and a production-sample copy, counts only for production;
this script only reads them). Writes nothing outside a temporary folder; prints JSON.
"""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
from src import release
from src import video_composer as vc

here = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("qf11_fixtures", here.parents[3] / "tests/test_qf11_audio_qa.py")
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
FIELDS = ("integrated_lufs", "true_peak_dbtp", "loudness_range_lu", "max_momentary_lufs", "max_short_term_lufs",
          "peak_to_loudness_lu", "phone", "sibilance", "astats", "silence_intervals")


def decodes_of(run):
    found = []
    real = vc._run_ffmpeg
    vc._run_ffmpeg = lambda cmd: found.append(1) or real(cmd)
    try:
        run()
    finally:
        vc._run_ffmpeg = real
    return len(found)


out = {"root": "given", "fixtures": {}, "mastering": {}}
with tempfile.TemporaryDirectory(prefix="qf11-") as temp:
    temp = Path(temp)
    for kind in ("good", "lead", "dropout", "tail", "subbass", "corrupt"):
        (temp / kind).mkdir()
        path = temp / kind / "m.mp4"
        if kind == "corrupt":
            path.write_bytes(b"not a video")
        else:
            fixtures.master(path, kind)
        report = release.write_release_report({"master_video_path": str(path), "script_payload": {}}, dry_run=True)
        checks = {c["name"]: c for c in report["checks"]}
        sound = {n: c["status"] + (" (note)" if c.get("minor") else "") for n, c in checks.items()
                 if n.startswith("audio_")}
        blocking = sorted(c["name"] for c in release.blocking_checks(report, dry_run=False)
                          if c["name"].startswith("audio_"))
        measured = (checks.get("audio_measurement") or {}).get("measurement") or {}
        out["fixtures"][kind] = {"sound_checks": sound, "blocking": blocking,
                                 "fields": sorted(f for f in FIELDS if f in measured),
                                 "phone_worst_window_lu": (measured.get("phone") or {}).get("worst_window_loss_lu")}
    (temp / "mix").mkdir()
    for label, bed in (("voice_over_bed", fixtures.BED), ("voice_over_clicks",
                                                          "aevalsrc='0.9*lt(mod(t,0.05),0.0006)':s=48000:d={d}")):
        vo, bed_path = fixtures._programme(temp / "mix", bed)
        composer = vc.VideoComposer()
        gains = []
        n = decodes_of(lambda c=composer, g=gains, v=vo, b=bed_path: g.append(c.calibrate_gain(v, b, end=6.0)))
        integrated, peak = vc.VideoComposer._measure(vo, bed_path,
                                                     vc._audio_chain(0, 1, end=6.0) + vc._master_chain(gains[0]))
        card = decodes_of(lambda c=composer, v=vo, b=bed_path, g=gains[0]: c.end_card_level(v, b, 5.0, 1.0, g))
        out["mastering"][label] = {"gain_decodes": n, "end_card_level_decodes": card,
                                   "integrated_lufs": integrated, "true_peak_dbtp": peak}

real = {}
for name in ("real_data_base.json", "real_data.json"):
    if (here / name).is_file():
        data = json.loads((here / name).read_text(encoding="utf-8"))
        real[name] = {env: {k: data[env].get(k) for k in ("mastering_summary", "gate_summary", "time_limit")}
                      for env in ("dev", "prod") if env in data}
out["real_data"] = real

f = out["fixtures"]
after = real.get("real_data.json", {})
out["acceptance"] = {
    "good_master_measured_with_every_field": f["good"]["sound_checks"].get("audio_measurement") == "pass"
    and len(f["good"]["fields"]) == len(FIELDS),
    "dropout_and_silent_end_block": f["dropout"]["blocking"] == ["audio_dropout"]
    and f["tail"]["blocking"] == ["audio_dropout"],
    "real_short_silent_start_passes": f["lead"]["sound_checks"].get("audio_dropout") == "pass",
    "phone_loss_is_a_note_not_a_block": f["subbass"]["sound_checks"].get("audio_phone") == "pass (note)"
    and f["subbass"]["blocking"] == [],
    "unmeasurable_master_fails_closed": f["corrupt"]["sound_checks"].get("audio_measurement") == "fail",
    "one_decode_for_the_gain": out["mastering"]["voice_over_bed"]["gain_decodes"] == 1,
    "one_decode_for_the_card_level": out["mastering"]["voice_over_bed"]["end_card_level_decodes"] == 1,
    "verified_when_the_limiter_could_cost_more": out["mastering"]["voice_over_clicks"]["gain_decodes"] >= 2
    and abs(out["mastering"]["voice_over_clicks"]["integrated_lufs"] + 14.0) <= 0.5,
    "committed_real_data_no_false_rejects": bool(after) and all(
        (after[e].get("gate_summary") or {}).get("measurement_failed") == 0
        and not any((after[e].get("gate_summary") or {}).get("failed", {}).values()) for e in after),
}
out["acceptance_met"] = all(out["acceptance"].values())
sys.stdout.reconfigure(newline="\n")
print(json.dumps(out, indent=1))
