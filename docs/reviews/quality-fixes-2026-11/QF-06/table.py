"""The per-cue table of QF-06's evidence, from the measurements beside this file:

    python table.py    # prints it and writes cues_before_after.txt beside this file

before.json / measurements.json: measure.py on the start commit 96e8db4 and on the fix (generated narration and
music stand-ins, the default); before_real_audio.json / after_real_audio.json: the same with RR_FIXTURE_REPO (each
run's own voiceover and music track). No src import: it only formats.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _fmt(v, f="{:+.2f}"):
    return "-" if v is None else f.format(v)


def main():
    out = [("QF-06 per-cue master limiter gain reduction (GR, dB) and narration loudness change (dN, LU), "
            "before (96e8db4) -> after (the fix)."),
           ("GR: the limiter's largest gain reduction inside the cue; no-SFX: the same window with every one-shot "
            "effect silenced (what the narration and music do on their own); dN: the narration's largest momentary "
            "loudness change against that no-SFX master while it speaks ('-': no narration in the cue). Cues come from "
            "each code's own effects layer, so a cue's end can differ before and after."), ""]
    for label, b, a in (("generated stand-ins (default, committed inputs only)", "before.json", "measurements.json"),
                        ("real voiceover and music (RR_FIXTURE_REPO)", "before_real_audio.json",
                         "after_real_audio.json")):
        if not (HERE / b).is_file() or not (HERE / a).is_file():
            continue
        before = json.loads((HERE / b).read_text(encoding="utf-8"))
        after = json.loads((HERE / a).read_text(encoding="utf-8"))
        out.append(f"=== {label} ===")
        for rb, ra in zip(before["rows"], after["rows"]):
            out.append(f"{rb['run']} [{rb['path']}]  master gain {rb['gain_db']:+.2f} -> {ra['gain_db']:+.2f} dB; "
                       f"bed bus after: {ra.get('bed_bus')}")
            out.append(f"  {'cue':<18} {'t (s)':>8} | {'GR':>5} {'no-SFX':>6} {'dN':>6} | "
                       f"{'GR':>5} {'no-SFX':>6} {'dN':>6}")
            for cb in rb["cues"]:
                ca = min(ra["cues"], key=lambda c: abs(c["t"] - cb["t"]), default=None)
                if ca is None or abs(ca["t"] - cb["t"]) > 0.05:
                    ca = None
                out.append(f"  {cb['what'][:18]:<18} {cb['t']:>8.3f} | {cb['gr_db']:>5.2f} {cb['gr_no_sfx_db']:>6.2f} "
                           f"{_fmt(cb['narration_delta_lu']):>6} | "
                           + (f"{ca['gr_db']:>5.2f} {ca['gr_no_sfx_db']:>6.2f} {_fmt(ca['narration_delta_lu']):>6}"
                              if ca else "   (no cue here)"))
            for ca in ra["cues"]:
                if not any(abs(ca["t"] - cb["t"]) <= 0.05 for cb in rb["cues"]):
                    out.append(f"  {ca['what'][:18]:<18} {ca['t']:>8.3f} | {'(no cue here)':>19} | "
                               f"{ca['gr_db']:>5.2f} {ca['gr_no_sfx_db']:>6.2f} {_fmt(ca['narration_delta_lu']):>6}")
            out.append(f"  max GR {rb['max_gr_db']:.2f} -> {ra['max_gr_db']:.2f} dB; max |dN| "
                       f"{rb['max_abs_narration_delta_lu']:.2f} -> {ra['max_abs_narration_delta_lu']:.2f} LU")
        sb, sa = before["summary"], after["summary"]
        out.append(f"  ALL RUNS: max GR {sb['max_gr_db_at_any_cue']:.2f} -> {sa['max_gr_db_at_any_cue']:.2f} dB; "
                   f"max |dN| {sb['max_abs_narration_delta_lu_at_any_cue']:.2f} -> "
                   f"{sa['max_abs_narration_delta_lu_at_any_cue']:.2f} LU; hit f0 min {sb['hit_f0_hz_min']} -> "
                   f"{sa['hit_f0_hz_min']} Hz; effect peaks {sb['effect_peak_dbfs_range']} -> "
                   f"{sa['effect_peak_dbfs_range']} dBFS")
        out.append("")
    if (HERE / "before.json").is_file() and (HERE / "measurements.json").is_file():
        lb = json.loads((HERE / "before.json").read_text(encoding="utf-8"))["effect_levels"]
        la = json.loads((HERE / "measurements.json").read_text(encoding="utf-8"))["effect_levels"]
        out.append("=== each effect as it enters the bed (palette cues in a dry room times gain x 0.40; the whoosh and "
                   "the hit before their cue gains), dBFS ===")
        out.append(f"  {'effect':<28} {'RMS before':>10} {'after':>7} {'change':>7} | {'peak before':>11} {'after':>7}")
        for k in lb:
            b, a = lb[k], la[k]
            out.append(f"  {k:<28} {b['rms_dbfs']:>10.1f} {a['rms_dbfs']:>7.1f} {a['rms_dbfs'] - b['rms_dbfs']:>+7.1f} | "
                       f"{b['peak_dbfs']:>11.1f} {a['peak_dbfs']:>7.1f}")
    text = "\n".join(out) + "\n"
    (HERE / "cues_before_after.txt").write_text(text, encoding="utf-8", newline="\n")
    print(text, end="")


if __name__ == "__main__":
    main()
