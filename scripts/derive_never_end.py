"""Derives the caption never-end set (data/never_end_words.json) from real, pinned word lists.

Cycle 0 round 8 (rules2, F4): caption bursts ended on ACROSS, ALONG, UPON, ALONGSIDE, MOST and BOTH while the record
counted zero, because the breaker's tail set and the record's set were two hand-kept lists. The never-end set is the
English CLOSED CLASS (prepositions, articles and determiners, quantifiers, conjunctions), derived here, never added to
by hand:

- the lexicon: spaCy's English STOP_WORDS (spacy==3.8.18), scikit-learn's ENGLISH_STOP_WORDS (the Glasgow list) and
  the WordNet-derived lemma index the pinned English model ships (data/nlp/en_core_web_sm/lemmatizer), so a string
  is a word only when one of those real lists has it;
- the classes, by the pinned en_core_web_sm tagger's Penn Treebank tags:
  * prepositions: tagged IN or TO in each of three preposition frames, and IN / TO alone (or RB alone when WordNet
    lists the word as an adverb: ALONGSIDE, ATOP); a phrasal particle (UP, OUT: RP in the frames) is not one;
  * determiners and quantifiers: a stop word tagged DT / PDT / WDT / PRP$ alone or DT / PDT before a noun phrase, or
    a degree quantifier (RBS / RBR / JJS alone: MOST, LEAST, MORE, LESS);
  * conjunctions: a stop word tagged CC alone (subordinators are IN, above);
- plus brand_system.LINE_END_NEVER, the heading engine's ruled list, which the set always holds.

Run: python scripts/derive_never_end.py [--check]   (--check: exit 1 when the committed file differs)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "never_end_words.json"
MODEL = ROOT / "data" / "nlp" / "en_core_web_sm"
DET_FRAME = "They carried {} the stones."
CORPUS_DIRS = ("data/fact_sheets", "data/script_library", "storyboard", "data/topics")
MIN_USES, MAJORITY = 2, 0.5


def corpus_texts() -> list[str]:
    """The repository's own committed prose (fact sheets, the script library, storyboards, topics): real English the
    pinned tagger reads in context, where its tags are reliable (a word alone, it tags a mastodon IN)."""
    import subprocess
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    files = [f for f in files if f.endswith(".json") and any(k in f for k in CORPUS_DIRS)]
    texts: list[str] = []

    def walk(o):
        if isinstance(o, str):
            if len(o.split()) >= 6:
                texts.append(o)
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    for f in sorted(files):
        try:
            walk(json.loads((ROOT / f).read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return list(dict.fromkeys(texts))


def derive() -> dict:
    import collections

    import spacy
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS
    from spacy.lang.en.stop_words import STOP_WORDS
    from spacy.lookups import Lookups

    sys.path.insert(0, str(ROOT))
    from src.brand_system import LINE_END_NEVER

    nlp = spacy.load(MODEL, exclude=["ner", "senter", "lemmatizer"])
    index = Lookups().from_disk(MODEL / "lemmatizer" / "lookups").get_table("lemma_index")
    wordnet = set().union(*[set(v) for v in index.values()])
    stops = {w for w in set(STOP_WORDS) | set(ENGLISH_STOP_WORDS) if w.isalpha()}
    words_ok = stops | wordnet

    # 1. the published stop lists, each word tagged alone (their words are all function or common words, so the
    #    tagger's prior is sound) and before a noun phrase
    stop_words = sorted(stops)
    alone = {w: d[0].tag_ for w, d in zip(stop_words, nlp.pipe(stop_words, batch_size=1000)) if len(d) == 1}
    pos = DET_FRAME.split().index("{}")
    det_tags = {w: d[pos].tag_ for w, d in zip(stop_words, nlp.pipe([DET_FRAME.format(w) for w in stop_words]))
                if len(d) > pos}
    prep = {w for w in stop_words if alone.get(w) in ("IN", "TO")}
    det = {w for w in stop_words if alone.get(w) in ("DT", "PDT", "WDT", "PRP$") or det_tags.get(w) in ("DT", "PDT")}
    quant = {w for w in stop_words if alone.get(w) in ("RBS", "RBR", "JJS")}
    conj = {w for w in stop_words if alone.get(w) == "CC"}

    # 2. the repository's prose read in context: a real word (a stop list's or the WordNet index's) that the tagger
    #    and parser read as a preposition (IN / TO heading a "prep" with its object), a determiner or a conjunction
    #    in most of its uses (at least MIN_USES of them)
    counts: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for doc in nlp.pipe(corpus_texts(), batch_size=64):
        for t in doc:
            w = t.text.lower()
            if not w.isalpha() or w not in words_ok:
                continue
            c = counts[w]
            c["n"] += 1
            if t.tag_ in ("IN", "TO") and t.dep_ == "prep" and any(ch.dep_ == "pobj" for ch in t.children):
                c["prep"] += 1
            elif t.tag_ in ("DT", "PDT", "WDT", "PRP$"):
                c["det"] += 1
            elif t.tag_ == "CC":
                c["conj"] += 1
    for w, c in counts.items():
        if c["n"] < MIN_USES:
            continue
        for key, cls in (("prep", prep), ("det", det), ("conj", conj)):
            if c[key] / c["n"] >= MAJORITY:
                cls.add(w)
    classes = {"preposition": sorted(prep), "determiner": sorted(det), "quantifier": sorted(quant),
               "conjunction": sorted(conj), "ruled": sorted(LINE_END_NEVER)}
    words = sorted(set().union(*map(set, classes.values())))
    return {"about": "The caption never-end set: the English closed class derived by scripts/derive_never_end.py from "
                     "spaCy 3.8.18 STOP_WORDS and scikit-learn ENGLISH_STOP_WORDS (tagged alone) and the repository's "
                     "committed prose (tagged and parsed in context; real words only: a stop list's or the pinned "
                     "en_core_web_sm lemma index's), plus brand_system.LINE_END_NEVER. Never edited by hand; "
                     "brand_system.NEVER_END_WORDS reads it.",
            "sources": {"spacy": spacy.__version__, "model": "en_core_web_sm " + nlp.meta.get("version", ""),
                        "stop_words": len(stop_words), "corpus_words": sum(c["n"] for c in counts.values()),
                        "min_uses": MIN_USES, "majority": MAJORITY},
            "classes": classes, "words": words}


def main(argv) -> int:
    got = derive()
    text = json.dumps(got, indent=1, ensure_ascii=False) + "\n"
    if "--check" in argv:
        same = OUT.is_file() and json.loads(OUT.read_text(encoding="utf-8"))["words"] == got["words"]
        print("never_end_words.json is current" if same else "never_end_words.json differs from the derivation")
        return 0 if same else 1
    OUT.write_bytes(text.encode("utf-8"))
    print(f"{len(got['words'])} words -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
