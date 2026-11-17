"""QF-60b real-data check: which reconstruction verdicts grant the exception on the real beats of 22 runs.

    python real_data.py <repo root> [--dev <output dir>] [--prod <copy of the production sample's output dir>]

Reads only (never writes) every run folder under the development output (default <root>/output) and the optional
production-sample COPY that has the pre-voice record (picture_lines.json), the timed words (words.json) and the
voiceover. The code under test is <root>'s own src, so the same script measures QF-60 (84c3eeb), round 3 (1f7de19a)
and this commit. The prose lists come from this script's own tree (tests/test_qf60b_reconstruction_reason.py).

Each run is split into beats by <root>'s src.scene_planner.segment_beats at the pipeline's voice length (ffprobe of
the voiceover) and with 1 and 3 s of tail silence after the last word, as QF-60 measured it. For every sentence j,
the record is copied to a temporary folder (never into the run) with only sentence j given a reconstruction verdict;
picture_coverage.placed_verdicts places it on the beats. Every beat whose words lie inside sentence j (QF-60's own
alignment oracle, not the code under test) then goes through the three checks (the planner's grant, the generation
gate with generation off, the relaxed nothing-added rule); a beat is granted only when all three agree.

Round 4 (structured cause, operator decision A). Per beat:
- prose only: a verdict with reconstruction true and a kind but no structured cause or subject words, its reason
  each of the test's prose lists (round 1-3 placeholders, paraphrases, hedges, contradictions, scripts, the 34
  VERSION 4-style and 20 VERSION 5-style reasons, round 3's probes) and the four round-3 genuine templates naming
  the sentence's longest word: must grant nothing ("prose_granted");
- structured, genuine: every kind with every cause it allows (6 pairs), the subject words the sentence's longest word
  of four or more letters that is not a stop word of this tree (an oracle: the longest word; the stop words only skip
  "inside", "construction", ...), a reviewer's reason in the round-3 template: granted ("structured_granted",
  compared with QF-60's count of the same checks: a grant lost would show there); the control is QF-60's own
  measurement verdict (mechanism_in_use, its reason) with the structure added;
- structured, mismatched: every kind with every cause it does not allow (14 pairs): must grant nothing;
- structured, subject words of another sentence of the run (the longest word of the next sentence that the
  sentence j does not contain): must grant nothing;
- wrong sentence: beats of another sentence, with the verdict placement hands them while only sentence j holds a
  genuine structured reconstruction verdict (mechanism_in_use, no_longer_operating): must stay 0.
Per sentence, on its own placed verdict (planner): the narration sentence copied into the reason and each of the
137 real model-written "reason" strings, prose only, under each kind: must grant nothing.

Prints JSON of counts only (decisions\\PROD-SAMPLE-counts-only.md): no run, sentence, reason or file name.
"""

import argparse
import importlib.util
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
from src import (
    image_gen,
    image_quarantine,
    picture_coverage,
    reconstruction,
    scene_planner,
)

here_root = Path(__file__).resolve().parents[4]
sys.path.append(str(here_root / "tests"))   # the test file imports the pre-voice test's fixture
spec = importlib.util.spec_from_file_location("qf60b_lists", here_root / "tests/test_qf60b_reconstruction_reason.py")
lists = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lists)
QF60_REASON = "The working state cannot be photographed; only the remains survive."
PROSE = [*lists.PLACEHOLDERS, *lists.SUBJECTLESS, *(g[2] for g in lists.GENUINE), *(r[1] for r in lists.REALISTIC),
         *(r[1] for r in lists.REWRITTEN), *(r[2] for r in lists.RESTATED + lists.COPIED + lists.CONTRARY),
         *(r[2] for r in lists.GRANTED_EDGE), *lists.HEDGED, *lists.SCRIPTS, *(p[2] for p in lists.ROUND3_PROBES),
         QF60_REASON]
TEMPLATES = {   # round 3's genuine templates, naming the subject word: prose only here, and the reviewer's reason
    "original_appearance": "The {w} was destroyed long ago, so its original state is lost.",
    "unphotographed_interior": "The {w} was sealed in antiquity and no photograph of it exists.",
    "mechanism_in_use": "The {w} no longer works; only corroded fragments of it survive.",
    "construction": "How the {w} was quarried, hauled and raised happened long before any camera.",
}
KINDS = sorted(lists.COMPATIBLE)
ALLOWED, MISMATCHED = lists.ALLOWED, lists.MISMATCHED
MODES = ("voice", "tail_1s", "tail_3s")
STOP = getattr(reconstruction, "_STOP_WORDS", frozenset())


def norm(text) -> list[str]:
    return "".join(c.lower() if c.isalnum() else " " for c in str(text or "")).split()


def voiceover(run: Path) -> Path | None:
    from src.audio_engine import voiceover_file
    path = voiceover_file(run)
    return path if path.is_file() else None


def runs(folder: Path | None) -> list[Path]:
    if folder is None or not folder.is_dir():
        return []
    return sorted(d for d in folder.glob("run_*") if (d / "picture_lines.json").is_file()
                  and (d / "words.json").is_file() and voiceover(d) is not None)


def duration(path: Path) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", str(path)]
    return float(subprocess.check_output(cmd, timeout=60).decode().strip())


def subject(sentence, avoid: str = "") -> str:
    """The sentence's longest word of four or more letters (the first of equal length) that is not a stop word of
    this tree and not a word of `avoid`: the oracle's subject words."""
    away = set(norm(avoid))
    words = [w for w in norm(sentence) if w.isalpha() and len(w) >= 4 and w not in STOP and w not in away]
    return max(words, key=len) if words else "subject"


def prose_only(v: dict, kind: str, reason: str) -> dict:
    out = {**v, "kind": "generable", "visual": picture_coverage.NAMED_SUBJECT, "reconstruction": True,
           "reconstruction_kind": kind, "reconstruction_reason": reason}
    out.pop("reconstruction_cause", None)
    out.pop("reconstruction_subject_words", None)
    return out


def structured(v: dict, kind: str, cause: str, words: str) -> dict:
    return {**v, "kind": "generable", "visual": picture_coverage.NAMED_SUBJECT, "reconstruction": True,
            "reconstruction_kind": kind, "reconstruction_cause": cause, "reconstruction_subject_words": words,
            "reconstruction_reason": TEMPLATES[kind].format(w=words)}


def control(v: dict) -> dict:
    """QF-60's own valid verdict (mechanism_in_use, its measurement reason), the structure added."""
    return {**structured(v, "mechanism_in_use", "no_longer_operating", subject(v.get("sentence"))),
            "reconstruction_reason": QF60_REASON}


def ordinary(v: dict) -> dict:
    return {**v, "reconstruction": False, "reconstruction_kind": None, "reconstruction_reason": "",
            "reconstruction_cause": None, "reconstruction_subject_words": ""}


def granted(beat: str, v: dict | None) -> bool:
    if v is None or image_gen.illustration_reason(beat, verdict=v) != image_gen.RECONSTRUCTION_REASON:
        return False
    spec = {"line": beat, "required": ["bronze gears"], "why_illustrated": image_gen.RECONSTRUCTION_REASON,
            **reconstruction.bind(v)}
    with patch.object(image_gen, "is_available", lambda: False):
        _, info = image_gen.illustrate_beat(beat, dict(spec), None, reason=image_gen.RECONSTRUCTION_REASON)
    if info.get("reason") != "generation off or no key":
        return False
    return "may show more" in image_quarantine._nothing_added_rule(spec)


def owners(beats: list[dict], words: list[dict], verdicts: list[dict]) -> list[set]:
    """QF-60/real_data.py's oracle: the record sentences each beat's tokens lie in (-1: a token with no sentence)."""
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
    return {"beats": 0, "beats_inside_one_sentence": 0, "control_granted": 0,
            "prose_reasons": len(PROSE) + len(TEMPLATES), "prose_checks": 0, "prose_granted": 0,
            "structured_pairs": len(ALLOWED), "structured_checks": 0, "structured_granted": 0,
            "structured_lost_where_control_granted": 0,
            "mismatched_pairs": len(MISMATCHED), "mismatched_checks": 0, "mismatched_granted": 0,
            "other_subject_checks": 0, "other_subject_granted": 0,
            "wrong_sentence_checks": 0, "wrong_sentence_granted": 0}


def measure(folder: Path | None) -> dict:
    out = {"runs": 0, "sentences": 0, "model_reasons": 0, "model_reasons_granted": 0,
           "sentence_copied_granted": {kind: 0 for kind in KINDS},
           **{mode: empty() for mode in MODES}}
    for run in runs(folder):
        out["runs"] += 1
        record = json.loads((run / "picture_lines.json").read_text(encoding="utf-8"))
        verdicts = [v for v in record.get("verdicts") or [] if isinstance(v, dict)]
        words = json.loads((run / "words.json").read_text(encoding="utf-8"))
        out["sentences"] += len(verdicts)
        for i, v in enumerate(verdicts):
            reason, sentence = v.get("reason"), str(v.get("sentence") or "")
            if not sentence.split():
                continue
            at = {"placement": {"by": picture_coverage.PLACEMENT_BY, "sentence_index": i, "beat": sentence}}
            if isinstance(reason, str) and reason.strip():
                out["model_reasons"] += 1
                out["model_reasons_granted"] += any(
                    image_gen.illustration_reason(sentence, verdict={**prose_only(v, kind, reason), **at})
                    == image_gen.RECONSTRUCTION_REASON for kind in KINDS)
            for kind in KINDS:   # the narration sentence itself copied into the reason
                out["sentence_copied_granted"][kind] += image_gen.illustration_reason(
                    sentence, verdict={**prose_only(v, kind, sentence), **at}) == image_gen.RECONSTRUCTION_REASON
        last = max(w["start"] + w.get("dur", 0.0) for w in words)
        totals = {"voice": duration(voiceover(run)), "tail_1s": last + 1.0, "tail_3s": last + 3.0}
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
                for j in range(len(verdicts)):
                    sentence = str(verdicts[j].get("sentence") or "")
                    words_j = subject(sentence)
                    other = (str(verdicts[(j + 1) % len(verdicts)].get("sentence") or "") if len(verdicts) > 1
                             else "")
                    elsewhere = subject(other, avoid=sentence) if other else "subject"
                    trial = [structured(v, "mechanism_in_use", "no_longer_operating", words_j) if i == j
                             else ordinary(v) for i, v in enumerate(verdicts)]
                    (copy / "picture_lines.json").write_text(json.dumps({"verdicts": trial}), encoding="utf-8")
                    mapped = picture_coverage.placed_verdicts(copy, beats)
                    for k, (b, v) in enumerate(zip(beats, mapped)):
                        if own[k] != {j}:
                            m["wrong_sentence_checks"] += 1
                            m["wrong_sentence_granted"] += granted(b["text"], v)
                            continue
                        if v is None:   # no certain place: nothing can be granted (QF-60); counted as refusals
                            n = len(PROSE) + len(TEMPLATES)
                            m["prose_checks"] += n
                            m["structured_checks"] += len(ALLOWED)
                            m["mismatched_checks"] += len(MISMATCHED)
                            m["other_subject_checks"] += len(ALLOWED)
                            continue
                        base_ok = granted(b["text"], control(v))
                        m["control_granted"] += base_ok
                        for reason in [*PROSE, *(t.format(w=words_j) for t in TEMPLATES.values())]:
                            m["prose_checks"] += 1
                            m["prose_granted"] += granted(b["text"], prose_only(v, "mechanism_in_use", reason))
                        for kind, cause in ALLOWED:
                            ok = granted(b["text"], structured(v, kind, cause, words_j))
                            m["structured_checks"] += 1
                            m["structured_granted"] += ok
                            m["structured_lost_where_control_granted"] += base_ok and not ok
                            m["other_subject_checks"] += 1
                            m["other_subject_granted"] += granted(b["text"], structured(v, kind, cause, elsewhere))
                        for kind, cause in MISMATCHED:
                            m["mismatched_checks"] += 1
                            m["mismatched_granted"] += granted(b["text"], structured(v, kind, cause, words_j))
    return out


def add(a: dict, b: dict) -> dict:
    fixed = ("prose_reasons", "structured_pairs", "mismatched_pairs")
    out = {}
    for k in [*a, *(k for k in b if k not in a)]:
        x, y = a.get(k), b.get(k)
        if isinstance(x if x is not None else y, dict):
            out[k] = add(x or {}, y or {})
        else:
            out[k] = x if k in fixed else (x or 0) + (y or 0)
    return out


dev = measure((args.dev or root / "output").resolve())
prod = measure(args.prod.resolve() if args.prod else None)
print(json.dumps({"dev": dev, "prod": prod, "total": add(dev, prod)}, indent=1))
