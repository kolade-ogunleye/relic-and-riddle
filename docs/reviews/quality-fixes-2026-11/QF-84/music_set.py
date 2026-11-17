"""QF-84 replacement set: three tracks per mood from the approved tracks the project holds, by measured rules.

    python music_set.py <repo root> [--write]

Reads real_data.json beside this file (real_data.py's measurements of each track's audio). Prints, per track, whether
the rule keeps it in rotation or the reason it is retired; `--write` writes the reasons to <root>/data/music/
rotation.json ({"retired": {file: reason}}, LF). library.json is never written: every host rewrites its own copy, and
the release deployer refuses a release that changes a file production changed differently. The rule, generic and
the same for every mood, keeps a track that a phone plays and that sits steadily under a voice:
- under 75% of its first minute's power below 120 Hz (a phone speaker reproduces almost nothing there);
- loudness range at most 15 LU and at most 4 s of its first minute more than 30 dB below its loudest second;
- then the three per mood with the least power below 120 Hz (ties: the smaller loudness range).
Only approved (reviewed) tracks are considered; a track awaiting review is left as it is. Listening is still the
operator's: the rule only removes measured misfits, it does not judge the music.
"""

import json
import sys
from pathlib import Path

SUB_MAX, LRA_MAX, QUIET_MAX, PER_MOOD = 0.75, 15.0, 4, 3


def verdicts(tracks):
    """{file: None (kept) | reason (retired)} for every approved, measured track."""
    out, ranked = {}, {}
    for t in tracks:
        if not t.get("reviewed") or t.get("sub120_share") is None:
            continue
        if t["sub120_share"] >= SUB_MAX:
            out[t["file"]] = (f"QF-84: {t['sub120_share']:.0%} of its first minute's power is below 120 Hz, which a "
                              "phone speaker does not play")
        elif t["lra_lu"] > LRA_MAX or t["quiet_seconds_first_60"] > QUIET_MAX:
            out[t["file"]] = (f"QF-84: loudness range {t['lra_lu']:.1f} LU and {t['quiet_seconds_first_60']} s near "
                              "silence in its first minute: not a steady bed under a voice")
        else:
            ranked.setdefault(t["mood"], []).append(t)
    for mood, pool in ranked.items():
        pool.sort(key=lambda t: (t["sub120_share"], t["lra_lu"], t["file"]))
        for i, t in enumerate(pool):
            out[t["file"]] = None if i < PER_MOOD else (
                f"QF-84: qualifies, but the {mood} set keeps the {PER_MOOD} with the least power below 120 Hz "
                f"({t['sub120_share']:.0%} here)")
    return out


if __name__ == "__main__":
    root = Path(sys.argv[1]).resolve()
    here = Path(__file__).resolve().parent
    measured = json.loads((here / "real_data.json").read_text(encoding="utf-8"))["tracks"]
    rule = verdicts(measured)
    if "--write" in sys.argv:
        retired = {f: reason for f, reason in sorted(rule.items()) if reason}
        rotation = {"_about": "QF-84: approved library tracks out of rotation, with the measured reason "
                              "(docs/reviews/quality-fixes-2026-11/QF-84/music_set.py). A track not listed rotates.",
                    "retired": retired}
        with open(root / "data" / "music" / "rotation.json", "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(rotation, indent=2, ensure_ascii=False) + "\n")
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    print(json.dumps(rule, indent=1, ensure_ascii=False))
