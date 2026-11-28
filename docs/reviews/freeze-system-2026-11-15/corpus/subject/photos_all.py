"""All photos to evaluate: the dev pool (353) + antikythera assets; with their topic and title."""
import json, re, hashlib
from pathlib import Path
import os
DEV=Path(os.environ["RR_DEV"])  # the dev checkout
def credits_title(topic_dir: Path, stem: str):
    f=topic_dir/"credits.json"
    try: d=json.loads(f.read_text(encoding="utf-8"))
    except Exception: return ""
    for e in d.get("images",[]):
        if e.get("file","").split(".")[0]==stem[:len(e.get("file","").split(".")[0])] and stem.startswith(e.get("file","").split(".")[0]):
            return e.get("title","")
    return ""
def main():
    pool=json.load(open("pool.json"))
    topics=sorted([p for p in (DEV/"assets").iterdir() if p.is_dir()], key=lambda p:-len(p.name))
    out=[]
    for o in pool:
        p=Path(o["path"]); run=o["runs"][0]
        topic=next((t for t in topics if t.name in run), None)
        stem=p.stem.split("_commons")[0]+"_commons"
        title=credits_title(topic, stem) if topic else ""
        out.append({"path":str(p),"sha256":o["sha256"],"topic":topic.name if topic else "","title":title})
    ak=DEV/"assets"/"antikythera_mechanism"
    for p in sorted(ak.glob("*_commons.jpg")):
        out.append({"path":str(p),"sha256":hashlib.sha256(p.read_bytes()).hexdigest(),"topic":ak.name,"title":credits_title(ak,p.stem)})
    json.dump(out,open("photos_all.json","w"),indent=1)
    print(len(out), sum(1 for o in out if not o["title"]))
main()
