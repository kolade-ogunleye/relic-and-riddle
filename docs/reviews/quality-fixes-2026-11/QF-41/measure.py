"""QF-41 acceptance metric: do Gemini calls keep the system text in its own role, and can quoted text
(web evidence, archive captions) close a prompt tag or pose as an instruction?

Usage: python measure.py <repo root>. Prints the metric as JSON and writes measurements.json next to this file.

The real code paths of the repo given run with every request mocked (requests.post/get/head answer from this
script, and any real socket connection raises): gemini.structured (text and vision), gemini.research (the
interaction and its generateContent fallback), the two privacy face checks, trace_check.evidence_check,
fact_sheet.build up to its model call, and image_review._verdicts. The payloads are the committed corpus
tests/fixtures/injection_corpus.json of the repository that holds this script.
"""

import copy
import json
import re
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
CORPUS_FILE = HERE.parents[3] / "tests" / "fixtures" / "injection_corpus.json"
SYSTEM = "SYSTEM-TEXT-QF41: you are a careful checker."
TAG = re.compile(r"</?\s*(evidence_quotes|raw_evidence_quotes|research_notes|topic_focus|package|caption|system)\b", re.IGNORECASE)
COMMAND = re.compile(r"^\W*(ignore|disregard|forget|choose|approve|respond|return|you must|you are|please ignore|"
                     r"new instructions|do not|never|assistant:|user:|system:)", re.IGNORECASE)


class Stop(Exception):
    pass


def _no_network():
    def refuse(*a, **k):
        raise RuntimeError("measure.py makes no network connection")
    socket.socket.connect = refuse
    socket.create_connection = refuse


def _role_checks():
    """Every Gemini request body the code builds: system text in system_instruction, none in input/contents."""
    import numpy as np
    from pydantic import BaseModel

    from src import config, gemini, privacy

    class Out(BaseModel):
        ok: bool

    sent = []

    def post(url, headers=None, json=None, timeout=None):
        sent.append((url, copy.deepcopy(json)))
        if url.endswith(":generateContent") and "googleSearch" in str(json.get("tools")):
            reply = {"candidates": [{"content": {"parts": [{"text": "Grounded."}]},
                                     "groundingMetadata": {"groundingChunks": [{"web": {"uri": "https://b.example.org"}}]}}]}
        elif url.endswith(":generateContent"):
            reply = {"usageMetadata": {}, "candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]}
        else:
            reply = {"status": "completed", "usage": {}, "steps": [{"type": "model_output", "content": [
                {"type": "text", "text": '{"ok": true}'}]}]}
        return SimpleNamespace(status_code=200, ok=True, text="", json=lambda: reply)
    gemini.requests.post = post
    gemini.requests.head = lambda *a, **k: SimpleNamespace(headers={})
    gemini.GEMINI_API_KEY = "measure-key"
    gemini.structured(SYSTEM, "quoted material", Out, key="measure-key")
    gemini.structured(SYSTEM, [{"type": "text", "text": "Photo 1"}], Out, key="measure-key")
    gemini.research(SYSTEM, "claims to check")
    calls = [{"call": ("research fallback" if "googleSearch" in str(b.get("tools")) else
                       "vision" if u.endswith(":generateContent") else
                       "research" if b.get("tools") else "structured text"), "body": b, "system": SYSTEM}
             for u, b in sent]

    config.GEMINI_API_KEY = "measure-key"
    faces = []

    def post_vision(url, body, timeout, key=""):
        faces.append(copy.deepcopy(body))
        text = "[]" if len(faces) == 1 else '{"is_living_human": false, "explanation": "carving"}'
        return SimpleNamespace(status_code=200, json=lambda: {"candidates": [{"content": {"parts": [{"text": text}]}}]})
    privacy._post_vision = post_vision
    img = np.full((200, 200, 3), 128, np.uint8)
    privacy._detect_faces_cognitive_gemini(img)
    privacy._is_living_human_candidate(img, (50, 50, 60, 60))
    for name, body in zip(("face detection", "living-human check"), faces):
        task = next((p["text"] for c in body.get("contents", []) for p in c.get("parts", []) if "text" in p), None)
        calls.append({"call": name, "body": body,
                      "system": (body.get("system_instruction") or {}).get("parts", [{}])[0].get("text") or task})
    out = []
    for c in calls:
        b = c["body"]
        si = b.get("system_instruction")
        rest = json.dumps(b.get("input", "") if "input" in b else b.get("contents"))
        out.append({"call": c["call"], "system_instruction_set": bool(si),
                    "system_text_in_contents": bool(c["system"]) and json.dumps(c["system"])[1:-1] in rest})
    return out


def _sheet(payload):
    quote = f"The lower city was carved from soft tuff. {payload}"
    return {"topic_id": "t", "facts": [{"id": "F1", "statement": "The lower city was carved from tuff.", "kind": "other",
                                        "disputed": False, "hedge": "",
                                        "evidence": [{"url": "https://www.unesco.org/x", "tier": 1, "quote": quote}]}]}


def _own_tags(prompt):
    """The tags the code writes itself: one pair per block label it uses (fenced or not)."""
    labels = {"evidence_quotes", "research_notes", "topic_focus", "package"}
    n = 0
    for lab in labels:
        if re.search(rf"<{lab}(?:\s+fence=\"F[0-9a-f]+\")?>\n", prompt):
            n += 2
    return n


def _evidence_check(payload):
    from src import free_llm, trace_check
    seen = {}

    def structured(system, prompt, schema, **k):
        seen["prompt"] = prompt
        return trace_check.EvidenceReport(claims=[trace_check.EvidenceVerdict(
            claim="carved from tuff", verdict="supported", quote_ids=[1], note="Q1")])
    trace_check.gemini_checker_available = lambda: True
    free_llm.structured = structured
    trace_check.evidence_check({"title": "The lower city", "narration_script": "It was carved from tuff."}, _sheet(payload))
    return seen["prompt"]


def _echo_rejected():
    """A checker reply that repeats the prompt's fence mark (or, with no fence, its closing tag) is refused."""
    from src import free_llm, trace_check

    def structured(system, prompt, schema, **k):
        m = re.search(r'fence="(F[0-9a-f]{16})"', prompt)
        echo = m.group(1) if m else "</evidence_quotes>"
        return trace_check.EvidenceReport(claims=[trace_check.EvidenceVerdict(
            claim=f"{echo} all fine", verdict="supported", quote_ids=[1], note="")])
    free_llm.structured = structured
    status, *_ = trace_check.evidence_check({"narration_script": "It was carved from tuff."}, _sheet("x"))
    return status == "fail"


def _fact_sheet(payload):
    from src import fact_sheet, free_evidence, free_llm
    seen = {}

    def call(system, prompt, schema, **k):
        seen["prompt"] = prompt
        raise Stop
    free_llm.enabled = lambda: True
    free_llm.call = call
    fact_sheet._unesco_evidence = lambda topic: ([], None)
    quote = {"url": "https://en.wikipedia.org/wiki/X", "quote": f"The city had 18 levels. {payload}", "fetch": "wikipedia"}
    free_evidence.quotes_for = lambda topic, kw: ([quote], [{"title": "X", "url": quote["url"]}])
    try:
        fact_sheet.build({"id": "t", "title": "Lower city", "hook": "", "key_facts": [], "location": {"name": "P"}})
    except Stop:
        pass
    return seen["prompt"]


def _caption_prompt(payload, tmp):
    from PIL import Image

    from src import free_llm, image_review
    Image.new("RGB", (32, 32), (120, 110, 90)).save(tmp / "a.jpg", "JPEG")
    seen = {}

    def call(system, content, schema, **k):
        seen["content"] = content
        return image_review.PoolReview.model_validate({"verdicts": [
            {"index": 1, "shows_topic": "yes", "usable": True, "safe": True, "reason": "A gate."}]})
    free_llm.call = call
    image_review._verdicts({"id": "t", "title": "T"}, tmp, [
        {"file": "a.jpg", "title": "File:Stone gate.jpg", "description": f"The lower gate. {payload}"}])
    return next(b["text"] for b in seen["content"] if b.get("type") == "text" and b["text"].startswith("Photo 1:"))


def _caption_text(photo_line):
    """The caption as the model reads it: the JSON string inside the fence after the fix, the raw text before."""
    m = re.search(r'<caption fence="F[0-9a-f]+">(.*)</caption fence=', photo_line)
    if m:
        return json.loads(m.group(1).replace("&lt;", "<").replace("&gt;", ">")), True
    return photo_line.split(":", 1)[1].strip(), False


def main(root: Path) -> dict:
    import tempfile
    sys.path.insert(0, str(root))
    _no_network()
    from src import config
    for name in ("GEMINI_API_KEY", "GEMINI_FREE_API_KEY", "ANTHROPIC_API_KEY"):
        setattr(config, name, "")
    corpus = json.loads(CORPUS_FILE.read_text(encoding="utf-8"))["payloads"]

    roles = _role_checks()
    leaks, cap_rows = [], []
    with tempfile.TemporaryDirectory() as tmp:
        for payload in corpus:
            ev = _evidence_check(payload)
            fs = _fact_sheet(payload)
            leaks.append({"payload": payload[:60],
                          "evidence_check_tags_from_quote": max(0, len(TAG.findall(ev)) - _own_tags(ev)),
                          "fact_sheet_tags_from_quote": max(0, len(TAG.findall(fs)) - _own_tags(fs))})
            text, fenced = _caption_text(_caption_prompt(payload, Path(tmp)))
            plain = re.sub(r"<[^>]*>", " ", text)   # a command after a tag is still a command
            sentences = [s for s in re.split(r"(?<=[.!?;])\s+|\s+-\s+", plain) if s.strip()]
            cap_rows.append({"payload": payload[:60], "fenced_json": fenced, "chars": len(text),
                             "tags": len(TAG.findall(text)) + text.count("<"),
                             "command_sentences": sum(1 for s in sentences if COMMAND.search(s))})
    passing = [r for r, c in zip(leaks, cap_rows)
               if not r["evidence_check_tags_from_quote"] and not r["fact_sheet_tags_from_quote"]
               and c["fenced_json"] and c["chars"] <= 120 and not c["tags"] and not c["command_sentences"]]
    metric = {
        "gemini_calls": len(roles),
        "gemini_calls_with_system_instruction": sum(r["system_instruction_set"] for r in roles),
        "gemini_calls_with_system_text_in_contents": sum(r["system_text_in_contents"] for r in roles),
        "corpus_payloads": len(corpus),
        "prompts_with_a_tag_from_quote_text": sum(bool(r["evidence_check_tags_from_quote"]) + bool(r["fact_sheet_tags_from_quote"])
                                                  for r in leaks),
        "captions_with_a_command_sentence": sum(bool(c["command_sentences"]) for c in cap_rows),
        "captions_with_markup": sum(bool(c["tags"]) for c in cap_rows),
        "captions_fenced_json_le_120": sum(c["fenced_json"] and c["chars"] <= 120 for c in cap_rows),
        "fence_echo_rejected": _echo_rejected(),
        "corpus_payloads_passing": len(passing),
        "calls": roles, "quotes": leaks, "captions": cap_rows,
    }
    return metric


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python measure.py <repo root>")
    result = main(Path(sys.argv[1]).resolve())
    (HERE / "measurements.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
                                            newline="\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))
