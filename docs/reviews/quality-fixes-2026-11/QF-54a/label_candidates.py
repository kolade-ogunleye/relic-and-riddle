"""Writes candidate_labels.json: a shows_topic label for every file the base plan or the QF-54a plan admits on
the recorded sample (share-alike included), so both plans are scored by the same rule. Run by hand:

    python label_candidates.py <repo root> <base image_queries.json, e.g. before.json>

The label comes from each file's title, description and Commons categories, not its pixels: "yes" when the file
sits in the topic's own category tree, "partly" for an explanatory panel or reconstruction of the object, and an
explicit verdict, read individually, for every other file. A file with no rule stops the script.
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from measure import admitted, plan

TOPIC_CATEGORY = {
    "antikythera_mechanism": r"antikythera mechanism",
    "kailasa_temple_monolith": r"kailash temple|kailasa temple|kailasanatha",
    "voynich_manuscript": r"^voynich manuscript$",
    "hal_saflieni_hypogeum": r"saflieni",
    "zhang_heng_seismoscope": r"houfeng|zhang heng",
}
READ_INDIVIDUALLY = {
    "File:Kailasa Temple.jpg": ("no", "categorised 'Cave 10, Ellora - Interior': another cave"),
    "File:Ellora vishwakarma Cave-10.jpg": ("no", "Cave 10 (Vishvakarma): another cave"),
    "File:Statues outside the Ellora Cave 18 -2.jpeg": ("no", "Cave 18: another cave"),
    "File:Mappa Voynich Monti Sibillini.jpg": ("no", "a modern map 'decoded' from the manuscript, not the manuscript"),
    "File:Alabaster figurine with head -Ħal Saflieni.jpg": ("partly", "an artefact found in the hypogeum, in a museum"),
    "File:Headless alabaster figurine -Ħal Saflieni.jpg": ("partly", "an artefact found in the hypogeum, in a museum"),
    "File:Caves above Waghora waterfall, Ellora, India.jpg": ("no", "the caves above the Waghora waterfall: not Kailasa"),
    "File:Ellora caves Waghora Waterfall- vrvbajel0924 (1).jpg": ("no", "the Waghora waterfall: not Kailasa"),
}
PANEL = re.compile(r"przyklad plansz|rekonstrukcji")


def main() -> None:
    sample = json.loads((HERE / "commons_sample.json").read_text(encoding="utf-8"))
    base_specs = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    specs = json.loads((ROOT / "data/topics/image_queries.json").read_text(encoding="utf-8"))
    labels = {}
    for topic, recorded in sample["topics"].items():
        base = admitted(sample, recorded, recorded["base_queries"], base_specs[topic], allow_sa=True)
        queries, keywords = plan(ROOT, topic, specs[topic])
        new = admitted(sample, recorded, queries, {**specs[topic], "keywords": keywords}, allow_sa=True)
        found = {**base, **new}
        for title in sorted(found):
            categories = [c.casefold() for c in found[title].get("categories", [])]
            if title in READ_INDIVIDUALLY:
                label, basis = READ_INDIVIDUALLY[title]
            elif PANEL.search(title):
                label, basis = "partly", "an explanatory panel or reconstruction of the object"
            elif any(re.search(TOPIC_CATEGORY[topic], c) for c in categories):
                label, basis = "yes", "in the topic's own Commons category tree"
            else:
                raise SystemExit(f"no rule for {topic} {title} {categories}")
            labels[title] = {"topic": topic, "shows_topic": label, "basis": basis}
    doc = {"labelled_by": "Claude (QF-54a builder), 2026-11-09, from each file's title, description and Commons "
                          "categories, not its pixels",
           "scope": "every file the base plan or the QF-54a plan admits on the recorded sample (share-alike included)",
           "labels": labels}
    (HERE / "candidate_labels.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False, sort_keys=True) + "\n",
                                                    encoding="utf-8", newline="\n")
    print(len(labels), dict(Counter(v["shows_topic"] for v in labels.values())))


if __name__ == "__main__":
    main()
