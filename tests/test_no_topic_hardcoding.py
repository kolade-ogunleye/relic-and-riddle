"""The pipeline is topic-agnostic: no code path may name a topic.

The documentary once shipped Derinkuyu's act titles, cover copy and shot rules to every topic, and the
motion style keywords named four topics. Topic knowledge belongs in data (the topic bank, fact
sheets, credits), never in code. This scans every string literal in the application, except
docstrings (which may cite an example), for the proper names of the topics in the bank.
"""
import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Capitalised words in topic titles and places that are ordinary English, not a topic's own name.
GENERIC = set("""
acoustic amber analog ancient anomaly british byzantine carbon catacombs cathedral caves century computer
concrete dancing desert detector disappearance earthquake event gallery golden granite great greek haven
headquarters healing hypogeum isles kitty lesser library lighthouse lines manuscript mechanism monolith
mountains museum mystery nanotubes nuclear obelisk oblast operation outer palace peninsula plague plain
plateau quarries region roman sanctuary shipwreck singing skull snake steel structure sunken surgery temple
thermal trepanation under underground unfinished unsolved vanishing weapon whispering
""".split())


def topic_names() -> set:
    bank = json.loads((ROOT / "data/topics/topic_bank.json").read_text(encoding="utf-8"))
    topics = bank.get("topics", bank) if isinstance(bank, dict) else bank
    names = set()
    for t in topics:
        loc = t.get("location") or {}
        text = " ".join(str(x) for x in (t.get("title", ""), loc.get("name", ""), loc.get("city", "")))
        names |= {w.lower() for w in re.findall(r"\b[A-Z][a-z]{4,}\b", text)}
    return names - GENERIC


def code_strings(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
                docstrings.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            yield node.lineno, node.value


def test_no_code_path_names_a_topic():
    names = topic_names()
    assert len(names) > 20  # the scan really has the bank's names
    hits = []
    for path in sorted((ROOT / "src").glob("*.py")) + [ROOT / "main.py"]:
        for line, value in code_strings(path):
            found = set(re.findall(r"[a-z]+", value.lower())) & names
            if found:
                hits.append(f"{path.relative_to(ROOT)}:{line} {sorted(found)}")
    assert not hits, "Topic names in code (move topic knowledge to data):\n" + "\n".join(hits)
