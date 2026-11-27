"""Corpus proof, freeze cycle 6 CAPTIONS stream (panel 7, classes C1 to C3), on the production call, both formats.

    python captions_c6.py --dev <checkout holding output/run_*> [--tree <tree>] [--data <collected.json>]
                          [--save <collected.json>] [--quiet] [--counts-only]

`--collect <tree> <dev> <out.json>` is the collector: it imports <tree> (nothing of this tree) and builds every run's
caption file as production builds it: 9:16 as compose_video calls build_subtitles (first_frame_whole), 16:9 as the
documentary does (width 1920, height 1080). Per burst: show / hide, the drawn lines, the words in order matched to
the narration (punctuation kept), each word's first visible time, the words drawn gold, the drawn size; per run the
composer's record. With the tree's own legality functions it also ENUMERATES, for every 9:16 burst the composer
draws on a forced break (clip_size.no_legal_form), whether remedy 1 (one stranded word moved to the neighbouring
burst) or remedy 2 (the pair of bursts re-cut) has a legal form (lander ruling, panel 7 C1): the record is true only
where neither has.

The judge reads the words through THIS tree's line_policy (one vocabulary for base and tip) and prints, per format:

- C1 (9:16 two-line bursts): a line of one function word; a figure parted from its unit or from its hedge across the
  two lines; a forced break (recorded) and whether the enumeration proves it forced. FAIL: a C1 line not recorded
  forced, or recorded forced with a remedy available.
- C2 (burst cuts, both formats): cuts inside a phrase unit (line_policy.phrase_unit, and a noun phrase over two
  pre-modifiers), bursts ending mid-sentence on a preposition / article / conjunction (never, last resort), on an
  auxiliary or copula (weak), or on an adjective parted from its noun; the rate per cut; bursts under 0.8 s; one-word
  bursts. Reported (the target is base -> tip and 16:9 at or below 9:16).
- C3 (gold): a hedge word drawn gold; a figure drawn gold with its unit bone; per clip, a key term drawn gold at one
  mention and bone at another (the accent switching); whether the clip records its accent rule. FAIL: a gold hedge,
  a gold figure without its unit.
- words: every spoken word shown, in order, none hidden at its burst's end (FAIL otherwise); words on screen more
  than 250 ms before they are spoken (reported, and how many the composer records).
"""
from __future__ import annotations

import collections
import json
import re
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
BS = chr(92)


def _key(word: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", unicodedata.normalize("NFKD", str(word)).upper())


def _ts(text: str) -> float:
    h, m, r = text.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + float(r)


TOKEN = re.compile(r"\{([^}]*)\}|([^{]+)")


def parse_text(body: str) -> list[tuple[str, str, bool]]:
    """(word, colour, visible) for each word of a CapText event, in order (override tags read left to right)."""
    out, colour, alpha = [], "", "00"
    for tag, text in TOKEN.findall(body):
        if tag:
            for c in re.findall(BS * 2 + r"c(&H[0-9A-Fa-f]+&)", tag):
                colour = c.upper()
            al = re.findall(BS * 2 + r"alpha&H([0-9A-Fa-f]{2})&", tag)
            if al:
                alpha = al[0].upper()
                # an eased reveal (\t(0,70,\alpha&H00&)) is visible within the event
                if BS + "t(" in tag and al[-1] == "00":
                    alpha = "00"
            continue
        for w in text.split():
            out.append((w, colour, alpha != "FF"))
    return out


def collect(tree: str, dev: str, out: str) -> None:
    sys.dont_write_bytecode = True
    sys.path.insert(0, tree)
    from src import video_composer as vc
    gold_col = vc._col_emphasis().upper()
    res: dict = {}
    for run in sorted(Path(dev).glob("output/run_*")):
        if not (run / "words.json").is_file():
            continue
        words = json.loads((run / "words.json").read_text(encoding="utf-8"))
        vj = run / "verified_script.json"
        emph = (json.loads(vj.read_text(encoding="utf-8")).get("emphasis_words") or None) if vj.is_file() else None
        row: dict = {"emphasis": emph, "n_words": len(words)}
        for fmt, kw in (("916", {"first_frame_whole": vc.CAPTION_FIRST_FRAME_WHOLE_S}),
                        ("169", {"width": 1920, "height": 1080})):
            with tempfile.TemporaryDirectory() as td:
                ass = Path(td) / "c.ass"
                comp = vc.VideoComposer()
                try:
                    comp.build_subtitles([dict(w) for w in words], ass, emphasis=emph, **kw)
                except Exception as ex:   # noqa: BLE001 - the judge fails on it
                    row[fmt] = {"error": f"{type(ex).__name__}: {ex}"[:300]}
                    continue
                text = ass.read_text(encoding="utf-8")
            row[fmt] = _parse(text, words, gold_col)
            rate = getattr(comp, "last_reading_rate", None) or {}
            row[fmt]["rate"] = json.loads(json.dumps({k: v for k, v in rate.items() if k != "bursts"}, default=str))
            if fmt == "916":
                row[fmt]["forced_proof"] = _forced_proof(vc, words, emph, row[fmt], rate)
        res[run.name] = row
        print(run.name, flush=True)
    Path(out).write_text(json.dumps({"tree": str(tree), "runs": res}, ensure_ascii=True), encoding="utf-8",
                         newline="\n")


def _parse(text: str, words: list[dict], gold_col: str) -> dict:
    boxes, evs = [], []
    for line in text.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        part = line[len("Dialogue:"):].split(",", 9)
        a, b, style, body = _ts(part[1]), _ts(part[2]), part[3].strip(), part[9]
        if style == "CapBox":
            boxes.append((a, b))
        elif style == "CapText":
            m = re.search(BS * 2 + r"pos\(([-\d.]+),([-\d.]+)\)", body)
            f = re.search(BS * 2 + r"fs(\d+)", body)
            evs.append((a, b, float(m.group(2)) if m else 0.0, parse_text(body), int(f.group(1)) if f else None))
    bursts, wi = [], 0
    for a, b in boxes:
        mine = [x for x in evs if a - 1e-3 <= x[0] < b - 1e-3]
        ys = sorted({x[2] for x in mine})
        last = {y: max((x for x in mine if x[2] == y), key=lambda x: x[0]) for y in ys}
        lines = [[w for w, _, _ in last[y][3]] for y in ys]
        gold = [_key(w) for y in ys for w, c, _ in last[y][3] if c == gold_col]
        hidden_end = sum(not vis for y in ys for _, _, vis in last[y][3])
        flat = [w for ln in lines for w in ln]
        first_vis: list[float | None] = [None] * len(flat)
        for y_i, y in enumerate(ys):
            off = sum(len(lines[k]) for k in range(y_i))
            for x in sorted((x for x in mine if x[2] == y), key=lambda x: x[0]):
                for j, (_, _, vis) in enumerate(x[3]):
                    if vis and off + j < len(first_vis) and first_vis[off + j] is None:
                        first_vis[off + j] = x[0]
        want, got, j = _key(" ".join(flat)), "", wi
        while j < len(words) and len(got) < len(want):
            got += _key(words[j]["word"])
            j += 1
        ok = got == want
        raw = [str(x["word"]) for x in words[wi:j]] if ok else None
        starts = [float(x["start"]) for x in words[wi:j]] if ok else None
        if ok:
            wi = j
        bursts.append({"show": a, "hide": b, "lines": [" ".join(ln) for ln in lines], "k": len(lines[0]) if lines else 0,
                       "raw": raw, "starts": starts, "first_vis": first_vis, "gold": gold,
                       "gold_words": [w for y in ys for w, c, _ in last[y][3] if c == gold_col],
                       "hidden_at_end": hidden_end, "fs": mine[0][4] if mine else None})
    return {"bursts": bursts, "words_matched": wi, "n_words": len(words)}


C1_HEDGES = ("almost", "about", "nearly", "roughly", "around", "approximately", "over", "under", "some", "circa",
             "estimated")
C1_HEDGE_PAIRS = (("more", "than"), ("less", "than"), ("fewer", "than"), ("up", "to"), ("at", "least"))


def c1_faults(lp, never_end, raw: list[str], k: int) -> list[str]:
    """The C1 faults of a two-line burst broken before word k (this collector's own reading, the judge's too): a line
    of one function word (the one closed-class vocabulary: NEVER_END, LINE_END_FUNCTION); a hedge on line 1 parted
    from its figure on line 2 (MORE THAN / 590); a figure on line 1 parted from its unit on line 2 (590 / FEET)."""
    l1, l2 = raw[:k], raw[k:]
    out = []

    def fn(w: str) -> bool:
        return (lp.is_function(w) or lp.bare(w) in never_end) and not lp.closes(w)
    if any(len(x) == 1 and fn(x[0]) for x in (l1, l2)):
        out.append("lone_function_word_line")
    tail = [lp.bare(w) for w in l1[-2:]]
    if l2 and (lp.is_number(l2[0]) or lp.bare(l2[0]) in lp.SPELLED_NUMBERS) and not lp.closes(l1[-1]) and (
            (tail and tail[-1] in C1_HEDGES) or tuple(tail[-2:]) in C1_HEDGE_PAIRS):
        out.append("hedge_parted_from_figure")
    if l1 and l2 and lp.is_number(l1[-1]) and not lp.closes(l1[-1]) and lp.bare(l2[0]) in lp.UNITS | lp.AREA_WORDS:
        out.append("figure_parted_from_unit")
    return out


def _forced_proof(vc, words: list[dict], emph, cap: dict, rate: dict) -> list[dict]:
    """For each 9:16 burst drawn with a C1 fault (c1_faults) or recorded on a forced break (clip_size.no_legal_form):
    every remedy the lander's ruling allows, enumerated here (not the composer's own search). With each back-to-back
    neighbour (no pause break between them; never the hook burst shown whole on frame 0) the pair's words are re-cut
    at every other place or joined into one burst. Remedy 1: the cut moves by one word; remedy 2: any other. Legal:
    every burst is two words or more, not function words only, holds no clause punctuation inside, sets at the clip's
    size on legal lines (_clip_form; one line not over one burst's caps), is up CAPTION_SPLIT_MIN_S or more on the
    delivered timing (burst_timings, dwell, with its neighbours); the new cut parts no bond, name, unit, figure group,
    key term, atomic pair or phrase unit (noun_phrase_cut; remedy 1 may part the stranded word's own lead-in bond,
    lead_in_bonds, as the lander's example does); the window's plate-height changes do not grow. A remedy
    whose new cut ends a burst on a function word (the lander's own remedy-1 example) counts as a remedy."""
    clip = (rate.get("clip_size") or {})
    px = clip.get("px")
    if px is None:
        return []
    bursts = cap["bursts"]
    if any(b["raw"] is None for b in bursts):
        return []
    raw_all = [str(w.get("word", "")) for w in words]
    protected = (vc.name_bonds(raw_all) | vc.caption_bonds(words) | vc.unit_links(words)
                 | vc.figure_group_tail_links(words) | vc.emphasis_bonds(words, emph))
    never_end = {str(w).lower() for w in vc.NEVER_END}
    # the lander's remedy 1 moves a stranded lead-in word (OF) to the previous burst: its lead-in bond may part
    hard = vc.caption_bonds(words, names=False) | vc.name_bonds(raw_all)
    lead_only = vc.lead_in_bonds(raw_all, hard) - (hard | vc.unit_links(words) | vc.figure_group_tail_links(words))
    phrases, at = [], 0
    for b in bursts:
        n = len(b["raw"])
        phrases.append(words[at:at + n])
        at += n
    pos = {id(w): k for k, w in enumerate(words)}
    timing = vc.burst_timings(phrases, dwell=True)
    lead = vc.CAPTION_LEAD_FRAMES / vc.VIDEO_FPS
    hook = float(words[0]["start"]) - lead <= vc.CAPTION_FIRST_FRAME_WHOLE_S + 1e-9
    forced = {_key(e.get("text", "").replace(" / ", " ")) for e in clip.get("no_legal_form") or []}

    def lines_of(q) -> int | None:
        raw = tuple(str(w["word"]) for w in q)
        form = vc._clip_form(raw, px)
        if form is None:
            return None
        if form[0] == "one":
            return None if (len(raw) > 1 and vc.over_caps(list(raw))) else 1
        return 2

    def bad(q) -> bool:
        raw = [str(w["word"]) for w in q]
        return (len(q) < 2 or vc._function_only(q) or lines_of(q) is None
                or any(vc.CLAUSE_PUNCT.search(w) or vc.TERMINAL_PUNCT.search(w) for w in raw[1:-1]))

    def changes(seq) -> int:
        lc = [lines_of(q) or 2 for q in seq]
        return sum(u != v for u, v in zip(lc, lc[1:]))

    out = []
    for i, b in enumerate(bursts):
        faults = c1_faults(lp_tree(vc), never_end, b["raw"], b["k"]) if len(b["lines"]) == 2 else []
        key = _key(" ".join(b["raw"]))
        if not faults and key not in forced:
            continue
        found = []
        for j in (i - 1, i + 1):
            lo, hi = min(i, j), max(i, j)
            if lo < 0 or hi >= len(bursts) or (hook and lo == 0):
                continue
            if timing[lo][1] < timing[hi][0] - 1e-3 or vc._pause_break(words, pos[id(phrases[lo][-1])]):
                continue
            pair = phrases[lo] + phrases[hi]
            c0 = len(phrases[lo])
            wl, wh = max(0, lo - 1), min(len(phrases), hi + 2)
            before = changes(phrases[wl:wh])
            for c in [*range(1, len(pair)), None]:
                if c == c0:
                    continue
                if c is None:
                    parts, fe = [pair], False
                else:
                    k = pos[id(pair[c - 1])]
                    if ((k in protected and not (abs(c - c0) == 1 and k in lead_only))
                            or vc._is_atomic_boundary(raw_all[k], raw_all[k + 1])
                            or vc.noun_phrase_cut(words, k) is not None):
                        continue
                    parts, fe = [pair[:c], pair[c:]], vc.never_end_end(raw_all[k])
                    if fe and abs(c - c0) != 1:
                        continue   # a function-word burst end only for the stranded word moved (remedy 1)
                if any(bad(q) for q in parts):
                    continue
                ctx = phrases[wl:lo] + parts + phrases[hi + 1:wh]
                prev = phrases[:wl][-1:]
                tm = vc.burst_timings(prev + ctx, dwell=True)[len(prev):]
                first = lo - wl
                if any(h - s < vc.CAPTION_SPLIT_MIN_S - 1e-6 for s, h, _ in tm[first:first + len(parts)]):
                    continue
                if changes(ctx) > before:
                    continue
                found.append({"kind": "remedy_1" if c is not None and abs(c - c0) == 1 else "remedy_2",
                              "function_end": bool(fe),
                              "form": " | ".join(" ".join(str(w["word"]) for w in q) for q in parts)})
        out.append({"text": " / ".join(b["lines"]), "key": key, "faults": faults, "recorded": key in forced,
                    "remedies": found})
    return out


def lp_tree(vc):
    return vc._line_policy


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "--collect":
    collect(sys.argv[2], sys.argv[3], sys.argv[4])
    raise SystemExit(0)

sys.path.insert(0, str(ROOT))
from src import line_policy as lp  # noqa: E402
from src import video_composer as vc  # noqa: E402  (the judge's one vocabulary: wide_cut_fault)

MIN_S = 0.8
STOP = ".?!…"
HEDGE_WORDS = {"almost", "about", "over", "nearly", "up", "to", "under", "more", "than", "roughly", "around",
               "approximately", "some", "estimated", "at", "least", "fewer", "less", "between", "circa", "c", "ca",
               "perhaps", "as", "many", "much"}


def _arg(flag: str, default: str | None = None) -> str | None:
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


def _ends_sentence(word: str) -> bool:
    return str(word).rstrip("\"'”’)").endswith(tuple(STOP))


def _np_cut(words: list[str], k: int) -> str | None:
    """A cut after words[k] inside a phrase unit (line_policy.phrase_unit), or inside a noun phrase an article or
    preposition opened over two pre-modifiers (A SUBSURFACE IGNEOUS | INTRUSION), read inside its sentence."""
    if not 0 <= k < len(words) - 1:
        return None
    lo = k
    while lo > 0 and not _ends_sentence(words[lo - 1]):
        lo -= 1
    got = lp.phrase_unit(words[lo:k], words[k], words[k + 1])
    if got or k < 1:
        return got
    w0 = words[k - 1]
    if not w0.strip("\"'(")[:1].islower() or lp.closes(w0) or lp.is_function(w0) or _ends_sentence(w0):
        return None
    lo2 = k - 1
    while lo2 > 0 and not _ends_sentence(words[lo2 - 1]):
        lo2 -= 1
    return lp.phrase_unit(words[lo2:k - 1], words[k], words[k + 1])


def _hedge_lead(line1: list[str]) -> bool:
    """Line 1 ends on a figure's hedge (MORE THAN / 590): its last one or two words are a hedge phrase."""
    tail = [lp.bare(w) for w in line1[-2:]]
    return bool(tail) and (tail[-1] in ("almost", "about", "nearly", "roughly", "around", "approximately", "over",
                                        "under", "some", "circa", "estimated") or tail[-2:] in (
        ["more", "than"], ["less", "than"], ["fewer", "than"], ["up", "to"], ["at", "least"]))


def judge(data: dict) -> tuple[list[tuple], collections.Counter]:
    table, tot = [], collections.Counter()
    for run, v in sorted(data["runs"].items()):
        for fmt in ("916", "169"):
            cap = v.get(fmt)
            if cap is None:
                continue
            if "error" in cap:
                tot[f"{fmt} build error UNRECORDED"] += 1
                table.append((run, fmt, cap["error"], "FAIL"))
                continue
            bursts, rate = cap["bursts"], cap.get("rate") or {}
            tot[f"{fmt} clips"] += 1
            tot[f"{fmt} bursts"] += len(bursts)
            # words: all shown, in order, none hidden at the end
            if cap["words_matched"] != cap["n_words"] or any(b["raw"] is None for b in bursts):
                tot[f"{fmt} words not all shown in order UNRECORDED"] += 1
                table.append((run, fmt, f"words matched {cap['words_matched']} of {cap['n_words']}", "FAIL"))
            hid = sum(b["hidden_at_end"] for b in bursts)
            if hid:
                tot[f"{fmt} words hidden at burst end UNRECORDED"] += hid
                table.append((run, fmt, f"{hid} words hidden at their burst's end", "FAIL"))
            early_rec = {_key(e.get("word", "")) for e in rate.get("hook_whole_early") or []}
            for i, b in enumerate(bursts):
                for j, (st, fv) in enumerate(zip(b["starts"] or [], b["first_vis"] or [])):
                    if fv is not None and fv - st < -0.25:
                        rec = i == 0 and _key(b["raw"][j]) in early_rec
                        tot[f"{fmt} words shown > 250 ms early"] += 1
                        tot[f"{fmt} words shown > 250 ms early ({'recorded' if rec else 'not recorded'})"] += 1
            # C2: cuts, ends, short and one-word bursts
            for i, b in enumerate(bursts):
                raw = b["raw"] or " ".join(b["lines"]).split()
                dur = b["hide"] - b["show"]
                tot[f"{fmt} under 0.8 s"] += dur < MIN_S - 1e-6
                tot[f"{fmt} one-word bursts"] += len(raw) == 1
                if i + 1 >= len(bursts) or not raw or _ends_sentence(raw[-1]):
                    continue
                nxt = bursts[i + 1]["raw"] or " ".join(bursts[i + 1]["lines"]).split()
                if not nxt:
                    continue
                tot[f"{fmt} mid-sentence cuts"] += 1
                seq = raw + nxt
                k = len(raw) - 1
                unit = _np_cut(seq, k) or (vc.wide_cut_fault(seq, k) == "phrase_unit" and "article_modifier")
                cls = lp.end_class(raw[-1])
                if unit:
                    tot[f"{fmt} C2 cut inside a phrase unit"] += 1
                    table.append((run, fmt, f"C2 phrase unit ({unit}): {' '.join(raw)} | {' '.join(nxt)}", "reported"))
                if cls in ("never", "last_resort"):
                    tot[f"{fmt} C2 burst ends on a preposition/article/conjunction"] += 1
                    table.append((run, fmt, f"C2 function end: {' '.join(raw)} | {' '.join(nxt)}", "reported"))
                elif cls == "weak":
                    tot[f"{fmt} C2 burst ends on an auxiliary/copula"] += 1
                    table.append((run, fmt, f"C2 auxiliary end: {' '.join(raw)} | {' '.join(nxt)}", "reported"))
                tot[f"{fmt} C2 any (unit or function or auxiliary end)"] += bool(unit or cls)
            # C1: 9:16 two-line bursts (the collector's c1_faults and its enumeration of the remedies)
            if fmt == "916":
                for p in cap.get("forced_proof") or []:
                    if not p["faults"]:
                        continue   # a recorded forced break with no C1 fault (a name parted): not this class
                    tot["916 C1 bursts"] += 1
                    for f in p["faults"]:
                        tot[f"916 C1 {f}"] += 1
                    rem = p["remedies"]
                    if not p["recorded"]:
                        tot["916 C1 not recorded UNRECORDED"] += 1
                        table.append((run, fmt, f"C1 {p['faults']}: {p['text']}", "FAIL"))
                    elif rem:
                        tot["916 C1 recorded forced but a remedy exists UNRECORDED"] += 1
                        table.append((run, fmt, f"C1 forced, {rem[0]['kind']} exists: {p['text']} -> "
                                      f"{rem[0]['form']}", "FAIL"))
                    else:
                        tot["916 C1 recorded forced, proved (no remedy 1 or 2)"] += 1
                        table.append((run, fmt, f"C1 forced, proved: {p['text']}", "pass (recorded, proved)"))
                for e in (rate.get("line_split") or []) + (rate.get("recut") or []):
                    if isinstance(e, dict) and e.get("strand_remedy"):
                        tot[f"916 C1 remedied ({e['strand_remedy']}{', function end' if e.get('function_end') else ''})"] += 1
                        table.append((run, fmt, f"C1 remedied {e['strand_remedy']}: {e['was']} -> {e['into']}",
                                      "fixed"))
            # C3: gold
            accent = rate.get("accent")
            tot[f"{fmt} clips recording their accent rule"] += bool(accent and accent.get("rule"))
            mentions: dict[str, set[bool]] = collections.defaultdict(set)
            terms = [t for t in (v.get("emphasis") or [])[:3]]
            tkeys = [[_key(x) for x in t.split() if _key(x)] for t in terms]
            for b in bursts:
                raw = b["raw"] or " ".join(b["lines"]).split()
                gold = set(b["gold"])
                keys = [_key(w) for w in raw]
                for j, w in enumerate(raw):
                    # a quantity's hedge drawn gold (ALMOST 50): a hedge word before a figure, not a range joiner
                    # between two figures (94 TO 104)
                    fig = lambda x: lp.is_number(x) or lp.bare(x) in lp.SPELLED_NUMBERS   # noqa: E731
                    if (_key(w) in gold and lp.bare(w) in HEDGE_WORDS and not fig(w) and j + 1 < len(raw)
                            and (fig(raw[j + 1]) or lp.bare(raw[j + 1]) in HEDGE_WORDS)
                            and not (j and fig(raw[j - 1]))
                            and not (lp.bare(w) in ("to", "and", "or") and not (j and lp.bare(raw[j - 1]) == "up"))):
                        tot[f"{fmt} C3 hedge word gold UNRECORDED"] += 1
                        table.append((run, fmt, f"C3 hedge gold: {w} in {' '.join(raw)}", "FAIL"))
                for j, w in enumerate(raw):
                    if _key(w) in gold and (lp.is_number(w) or lp.bare(w) in lp.SPELLED_NUMBERS):
                        if j + 1 < len(raw) and lp.bare(raw[j + 1]) in lp.UNITS | lp.AREA_WORDS and _key(raw[j + 1]) not in gold:
                            tot[f"{fmt} C3 figure gold, unit bone UNRECORDED"] += 1
                            table.append((run, fmt, f"C3 unit bone: {w} {raw[j + 1]}", "FAIL"))
                for tk in tkeys:
                    if not tk:
                        continue
                    for j in range(len(keys) - len(tk) + 1):
                        if keys[j:j + len(tk)] == tk:
                            mentions[" ".join(tk)].add(all(x in gold for x in tk))
            mixed = [t for t, s in mentions.items() if len(s) > 1]
            tot[f"{fmt} C3 key terms gold at one mention, bone at another"] += len(mixed)
            for t in mixed:
                table.append((run, fmt, f"C3 accent switches: {t}", "reported"))
    return table, tot


def main() -> int:
    if _arg("--data"):
        data = json.loads(Path(_arg("--data")).read_text(encoding="utf-8"))
    else:
        tree = _arg("--tree", str(ROOT))
        with tempfile.TemporaryDirectory() as td:
            out = Path(_arg("--save") or (Path(td) / "c.json"))
            subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), "--collect", str(tree),
                            str(_arg("--dev", ".")), str(out)], check=True, stdout=subprocess.DEVNULL)
            data = json.loads(out.read_text(encoding="utf-8"))
    table, tot = judge(data)
    if "--quiet" not in sys.argv and "--counts-only" not in sys.argv:
        print("run | format | measure | result")
        for row in table:
            print(" | ".join(row))
    print(f"\ntree: {data.get('tree')}")
    for k in sorted(tot):
        print(f"{k}: {tot[k]}")
    for fmt in ("916", "169"):
        n = max(1, tot[f"{fmt} mid-sentence cuts"])
        print(f"{fmt} C2 phrase-unit cut rate: {tot[f'{fmt} C2 cut inside a phrase unit'] / n:.3f} "
              f"(function/auxiliary end rate {(tot[f'{fmt} C2 burst ends on a preposition/article/conjunction'] + tot[f'{fmt} C2 burst ends on an auxiliary/copula']) / n:.3f})")
    bad = sum(v for k, v in tot.items() if k.endswith("UNRECORDED"))
    print(f"unrecorded violations: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
