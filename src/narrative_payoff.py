"""Offline noun continuity between the opening and the stated payoff (QF-15).

The committed, hash-verified spaCy English pipeline (data/nlp, MIT) tags each sentence; nothing is downloaded and no
provider is called. A lemma links the two sentences when it is a noun in one of them and a noun or an adjective in
the other: the small tagger reads "concrete" in "Modern concrete crumbles" as an adjective and the same word in
"the finished concrete" as a noun (review 2 of QF-15, finding 2). A lemma that is only a verb, an adverb, a number or
an adjective in both sentences never links them. No synonym list: a payoff names the opening's own noun.
"""

import hashlib
import json
import threading
from pathlib import Path

from . import config

_lock = threading.RLock()
_pipeline = None
_cache: dict[str, tuple[frozenset[str], frozenset[str]]] = {}
_NOMINAL = frozenset({"NOUN", "PROPN"})
_ADJECTIVAL = frozenset({"ADJ"})


def _load():
    global _pipeline
    if _pipeline is None:
        import spacy
        directory = Path(config.PAYOFF_NLP_DIR)
        manifest = json.loads((directory.parent / "manifest.json").read_text(encoding="utf-8"))
        for name, expected in manifest["files"].items():
            path = (directory / name).resolve()
            if not path.is_relative_to(directory.resolve()):
                raise ValueError("Invalid noun model manifest path")
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError("Noun model hash mismatch")
        _pipeline = spacy.load(directory, exclude=["ner", "parser", "senter"])
    return _pipeline


def _lemmas(sentence: str) -> tuple[frozenset[str], frozenset[str]]:
    """The sentence's content lemmas (casefolded, alphabetic, not stop words): (nouns, adjectives)."""
    with _lock:
        if sentence not in _cache:
            doc = _load()(sentence)
            words = [t for t in doc if t.is_alpha and not t.is_stop]
            _cache[sentence] = (frozenset(t.lemma_.casefold() for t in words if t.pos_ in _NOMINAL),
                                frozenset(t.lemma_.casefold() for t in words if t.pos_ in _ADJECTIVAL))
            if len(_cache) > 2048:
                _cache.pop(next(iter(_cache)))
        return _cache[sentence]


def nouns(sentence: str) -> frozenset[str]:
    """Noun lemmas only: shared verbs, adjectives and numbers do not qualify."""
    return _lemmas(sentence)[0]


def shared_nouns(hook: str, payoff: str) -> frozenset[str]:
    """The lemmas that link the payoff to the hook: a noun in one sentence, a noun or an adjective in the other."""
    hook_nouns, hook_adjectives = _lemmas(hook)
    payoff_nouns, payoff_adjectives = _lemmas(payoff)
    return (hook_nouns & (payoff_nouns | payoff_adjectives)) | (payoff_nouns & hook_adjectives)
