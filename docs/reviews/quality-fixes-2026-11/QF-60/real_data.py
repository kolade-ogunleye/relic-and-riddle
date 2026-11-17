"""QF-60 real-data check: does a structured reconstruction verdict reach the beats of its own sentence, and no other?

    python real_data.py <repo root> [--dev <output dir>] [--prod <copy of the production sample's output dir>]

Reads only (never writes) every run folder under the development output (default <root>/output) and the optional
production-sample COPY that has the pre-voice record (picture_lines.json), the timed words (words.json) and the
voiceover. The code under test is <root>'s own src, so the same script measures any commit.

Each run is split into beats by the real src.scene_planner.segment_beats at four voice lengths: the pipeline's own
(ffprobe of the run's voiceover, as src.audio_engine.get_duration measures it), and the last word's end plus 0, 1
and 3 s of tail silence (a new take can run past its last word; with a longer tail the short final beat is no longer
folded into the one before). Each beat's verdict comes from <root>'s own mapping, as its scene_planner wires the
reconstruction verdict: picture_coverage.placed_verdicts (word position) where it exists, else
picture_coverage.beat_verdicts (the first sentence containing the beat's words).

For every sentence j of a run, the record is copied to a temporary folder (never into the run) with ONLY sentence j
given a valid reconstruction verdict (reconstruction true, an allowed kind, a reason, a generable named subject)
and every other sentence ordinary (reconstruction false). Every beat then goes through the three places that
decide a reconstruction: the planner's grant (image_gen.illustration_reason), the generation gate
(image_gen.illustrate_beat with generation off, so nothing is requested) and the reference comparison's relaxed
nothing-added rule (image_quarantine._nothing_added_rule); a beat is granted only when all three agree.

The beat's own sentence comes from this script's own oracle, not from the code under test: the beat's words are
found in the timeline by walking it in order, and the timeline's tokens are aligned with the record sentences'
tokens (difflib over every matching block; a token in no block has no sentence). A beat whose tokens all lie in
sentence j is "inside j"; any other beat granted from sentence j's verdict is a WRONG-sentence grant (must be 0).
Also: the beat's own valid verdict with one field made invalid (must be granted nowhere), and every OTHER
sentence's valid verdict handed to the beat directly, as the first-containing-sentence lookup handed it (must be
granted nowhere). The old keyword regex is measured on the record sentences without any verdict.

Prints JSON of counts only (decisions\\PROD-SAMPLE-counts-only.md): no run, sentence or file name.
"""

import argparse
import json
import subprocess
import sys
import tempfile
from difflib import SequenceMatcher
from pathlib import Path
from unittest.mock import patch

ap = argparse.ArgumentParser()
ap.add_argument("root", type=Path)
ap.add_argument("--dev", type=Path, help="development output dir (default <root>/output)")
ap.add_argument("--prod", type=Path)
args = ap.parse_args()
root = args.root.resolve()
sys.path.insert(0, str(root))
from src import image_gen, image_quarantine, picture_coverage, scene_planner

try:
    from src import reconstruction
except ImportError:          # the base: no structured verdict at all
    reconstruction = None
VERDICT_AWARE = reconstruction is not None
BY_POSITION = hasattr(picture_coverage, "placed_verdicts")
MODES = ("voice", "tail_0s", "tail_1s", "tail_3s")


def norm(text) -> list[str]:
    return "".join(c.lower() if c.isalnum() else " " for c in str(text or "")).split()


def voiceover(run: Path) -> Path | None:
    """The run's voiceover as the pipeline finds it (src.audio_engine.voiceover_file: the WAV, else the MP3)."""
    from src.audio_engine import voiceover_file
    path = voiceover_file(run)
    return path if path.is_file() else None


def runs(folder: Path | None) -> list[Path]:
    if folder is None or not folder.is_dir():
        return []
    return sorted(d for d in folder.glob("run_*") if (d / "picture_lines.json").is_file()
                  and (d / "words.json").is_file() and voiceover(d) is not None)


def duration(path: Path) -> float:
    """src.audio_engine.AudioEngine.get_duration's own command."""
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", str(path)]
    return float(subprocess.check_output(cmd, timeout=60).decode().strip())


def valid(v: dict) -> dict:
    return {**v, "kind": "generable", "visual": picture_coverage.NAMED_SUBJECT, "reconstruction": True,
            "reconstruction_kind": "mechanism_in_use",
            "reconstruction_reason": "The working state cannot be photographed; only the remains survive."}


def ordinary(v: dict) -> dict:
    return {**v, "reconstruction": False, "reconstruction_kind": None, "reconstruction_reason": ""}


def granted(beat: str, v: dict | None) -> bool:
    if v is None or image_gen.illustration_reason(beat, verdict=v) != image_gen.RECONSTRUCTION_REASON:
        return False
    spec = {"line": beat, "required": ["bronze gears"], "why_illustrated": image_gen.RECONSTRUCTION_REASON}
    if hasattr(reconstruction, "bind"):
        spec.update(reconstruction.bind(v))
    else:
        spec["reconstruction_verdict"] = dict(v)
    with patch.object(image_gen, "is_available", lambda: False):
        _, info = image_gen.illustrate_beat(beat, dict(spec), None, reason=image_gen.RECONSTRUCTION_REASON)
    if info.get("reason") != "generation off or no key":
        return False
    return "may show more" in image_quarantine._nothing_added_rule(spec)


def mapping(run: Path, beats: list[dict]) -> list[dict | None]:
    """The verdict <root>'s scene_planner hands the reconstruction checks for each beat."""
    if BY_POSITION:
        return picture_coverage.placed_verdicts(run, beats)
    return picture_coverage.beat_verdicts(run, beats)


def owners(beats: list[dict], words: list[dict], verdicts: list[dict]) -> list[set]:
    """This script's own oracle: the record sentences each beat's tokens lie in (-1: a token with no sentence)."""
    script, sentence_of = [], []
    for i, v in enumerate(verdicts):
        tokens = norm(v.get("sentence"))
        script += tokens
        sentence_of += [i] * len(tokens)
    voice, token_of_word = [], []
    for w in words:
        tokens = norm(w["word"])
        token_of_word.append(list(range(len(voice), len(voice) + len(tokens))))
        voice += tokens
    to_script = {}
    for a, b, size in SequenceMatcher(None, script, voice, autojunk=False).get_matching_blocks():
        to_script.update({b + i: a + i for i in range(size)})
    out, at = [], 0
    for beat in beats:
        n, text = 0, " ".join(beat["text"].split())
        while at + n < len(words) and " ".join(w["word"] for w in words[at:at + n]) != text:
            n += 1
        if " ".join(w["word"] for w in words[at:at + n]) != text:
            out.append({-1})
            continue
        tokens = [t for w in range(at, at + n) for t in token_of_word[w]]
        out.append({sentence_of[to_script[t]] if t in to_script else -1 for t in tokens} or {-1})
        at += n
    return out


def empty() -> dict:
    return {"beats": 0, "beats_inside_one_sentence": 0, "beats_spanning_or_unaligned": 0,
            "valid_verdict_granted": 0, "valid_verdict_refused": 0, "refused_unplaced": 0,
            "multi_beat_sentence_beats": 0, "multi_beat_sentence_beats_granted": 0,
            "wrong_sentence_granted": 0, "wrong_sentence_checks": 0,
            "invalid_verdict_granted": 0, "invalid_verdict_checks": 0,
            "other_sentence_verdict_granted": 0, "other_sentence_verdict_checks": 0}


def measure(folder: Path | None) -> dict:
    out = {"runs": 0, "runs_timeline_differs_from_script": 0, "sentences": 0, "sentences_flagged_without_verdict": 0,
           "flagged_by_kind": {}}
    if VERDICT_AWARE:
        out.update({mode: empty() for mode in MODES})
    for run in runs(folder):
        out["runs"] += 1
        record = json.loads((run / "picture_lines.json").read_text(encoding="utf-8"))
        verdicts = [v for v in record.get("verdicts") or [] if isinstance(v, dict)]
        words = json.loads((run / "words.json").read_text(encoding="utf-8"))
        out["runs_timeline_differs_from_script"] += (
            [t for v in verdicts for t in norm(v.get("sentence"))] != [t for w in words for t in norm(w["word"])])
        for v in verdicts:
            out["sentences"] += 1
            if image_gen.illustration_reason(str(v.get("sentence") or "")):
                out["sentences_flagged_without_verdict"] += 1
                kind = str(v.get("kind"))
                out["flagged_by_kind"][kind] = out["flagged_by_kind"].get(kind, 0) + 1
        if not VERDICT_AWARE:
            continue
        last = max(w["start"] + w.get("dur", 0.0) for w in words)
        totals = {"voice": duration(voiceover(run)), "tail_0s": last, "tail_1s": last + 1.0, "tail_3s": last + 3.0}
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / run.name
            copy.mkdir()
            (copy / "words.json").write_text(json.dumps(words), encoding="utf-8")
            for mode in MODES:
                m = out[mode]
                beats = scene_planner.segment_beats(words, totals[mode])
                own = owners(beats, words, verdicts)
                m["beats"] += len(beats)
                m["beats_inside_one_sentence"] += sum(len(o) == 1 and -1 not in o for o in own)
                m["beats_spanning_or_unaligned"] += sum(len(o) != 1 or -1 in o for o in own)
                inside_count = {j: sum(o == {j} for o in own) for j in range(len(verdicts))}
                for j in range(len(verdicts)):
                    trial = [valid(v) if i == j else ordinary(v) for i, v in enumerate(verdicts)]
                    (copy / "picture_lines.json").write_text(json.dumps({"verdicts": trial}), encoding="utf-8")
                    mapped = mapping(copy, beats)
                    for k, (b, v) in enumerate(zip(beats, mapped)):
                        ok = granted(b["text"], v)
                        if own[k] == {j}:
                            m["valid_verdict_granted" if ok else "valid_verdict_refused"] += 1
                            m["refused_unplaced"] += (not ok) and v is None
                            if inside_count[j] > 1:
                                m["multi_beat_sentence_beats"] += 1
                                m["multi_beat_sentence_beats_granted"] += ok
                            if v is not None:
                                for bad in ({**v, "reconstruction": False}, {**v, "reconstruction": "true"},
                                            {**v, "reconstruction_kind": "interior"},
                                            {**v, "reconstruction_reason": ""},
                                            {**v, "reconstruction_reason": "\u200b"},
                                            {**v, "kind": "shown"}, {**v, "visual": picture_coverage.ABSTRACT}):
                                    m["invalid_verdict_checks"] += 1
                                    m["invalid_verdict_granted"] += granted(b["text"], bad)
                        else:
                            m["wrong_sentence_checks"] += 1
                            m["wrong_sentence_granted"] += ok
                            m["other_sentence_verdict_checks"] += 1
                            m["other_sentence_verdict_granted"] += granted(b["text"], trial[j])
    return out


def add(a: dict, b: dict) -> dict:
    return {k: (add(a[k], b[k]) if isinstance(a[k], dict) and k in MODES else a[k] + b[k])
            for k in a if isinstance(a[k], int) or k in MODES}


dev = measure((args.dev or root / "output").resolve())
prod = measure(args.prod.resolve() if args.prod else None)
print(json.dumps({"verdict_aware": VERDICT_AWARE, "placement_by_word_position": BY_POSITION, "dev": dev,
                  "prod": prod, "total": add(dev, prod)}, indent=1))
