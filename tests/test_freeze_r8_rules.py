"""Design-freeze cycle 0 round 8, lane RULES (operator rulings 1 and 2 of 2026-11-17; official panel 1 consensus 3, 5
and 7). Every test is built from a panel measurement on synthetic inputs (CI has no output/), and every rule has a
guard that fails when a renderer bypasses it."""
from __future__ import annotations

import re

from src import brand_system
from src import video_composer as vc

# ---------------------------------------------------------------- captions (consensus 5)
# The Richat long run's first sentence (run 1792740961_long, words.json): the 16:9 burst ended "is situated near".
RICHAT = ("The Richat Structure is situated near Ouadane on the Adrar Plateau within the Adrar Region of Mauritania. "
          "Visible from space as a massive desert landmark, the concentric formation has served as a visual reference "
          "point for space shuttle crews.")


def _timed(text: str) -> list[dict]:
    words, at = [], 0.25
    for w in text.split():
        dur = round(0.11 + 0.055 * len(re.sub(r"[^\w]", "", w)), 3)
        words.append({"word": w, "start": round(at, 3), "dur": dur})
        at += dur + (0.49 if re.search(r"[.!?]$", w) else 0.22 if re.search(r"[,;:]$", w) else 0.04)
    return words


def _mid_sentence_ends(chunks: list[list[str]]) -> list[str]:
    return [c[-1] for c in chunks[:-1] if not re.search(r"[.!?,;:]$", c[-1])]


def test_the_caption_tails_hold_the_heading_engines_never_end_set():
    """One never-end set: every caption path's tail set (DANGLING_ENDS: the freeze-pass builder, the round-4 base, the
    16:9 bursts, the merges, the sidecar cues) holds brand_system.LINE_END_NEVER."""
    assert brand_system.LINE_END_NEVER <= vc.DANGLING_ENDS <= vc.DANGLING_TAILS
    assert all(vc._hard_dangles(w) for w in brand_system.LINE_END_NEVER)


def test_no_caption_path_ends_on_a_never_end_word_on_the_richat_narration():
    words = _timed(RICHAT)
    raw = [w["word"] for w in words]
    paths = {
        "9:16": [[w["word"] for w in p] for p in vc.caption_phrases(words)],
        "16:9": [[w["word"] for w in p] for p in vc.caption_phrases(words, None, True)],
        "cues": [t.split() for _, _, t in vc.partition_sidecar_cues(words)],
    }
    # the bursts' round-4 base never cuts on one (its cues are HEAD's own and may end on THE: the cue builder moves
    # those cuts, test_head_regression_gate's "article" kind)
    for name, cuts in (("base 9:16", vc.round4_burst_cuts(words)), ("base 16:9", vc.round4_burst_cuts(words, None, True))):
        assert not [raw[k] for k in cuts if vc._bare(raw[k]) in brand_system.LINE_END_NEVER], name
    for name, chunks in paths.items():
        assert [w for c in chunks for w in c] == raw, name           # every word, in order
        ends = _mid_sentence_ends(chunks)
        assert not [e for e in ends if vc._bare(e) in brand_system.LINE_END_NEVER], (name, ends)
