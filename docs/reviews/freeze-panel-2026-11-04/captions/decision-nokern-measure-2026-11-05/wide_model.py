import json
import sys
from collections import Counter

sys.path.insert(0,".")
sys.path.insert(0,"docs/reviews/freeze-panel-2026-11-04/fixtures")
import fixtures as fx

from src import video_composer as vc
from src.typography import get_font

with open(sys.argv[1]) as fh:
    d=json.load(fh)
upm,asc,desc=vc._font_vmetrics(vc.CAPTION_WIDE_FONT,700)
fs=vc.ass_font_size(vc.CAPTION_WIDE_FONT,52,700); k=fs*upm/(asc+desc)/1000
f=get_font(vc.CAPTION_WIDE_FONT,1000,weight=700)
texts={}
for c in fx.load("captions"):
    ph=vc.caption_phrases(fx.words(c),list(c.get("emphasis_words") or []),True,1440)
    texts[c["topic"]]=[" ".join(vc._ass_escape(w) for w in vc._phrase_words(p)) for p in ph]
rows=[r for r in d["rows"] if r["format"]=="16:9"]
i={t:0 for t in texts}
out={"kern":[],"unk":[]}
for r in rows:
    t=texts[r["topic"]][i[r["topic"]]]; i[r["topic"]]+=1
    assert t.upper()==r["text"],(t,r["text"])
    for var, acc in out.items():
        L=f.getlength(t[:-1]) if var=="kern" else sum(f.getlength(c) for c in t[:-1])
        ink=(L+f.getmask(t[-1]).getbbox()[2]-f.getmask(t[0]).getbbox()[0])*k
        acc.append((r["ink"]-round(ink),t,r["ink"],ink))
for var,v in out.items():
    a=[abs(x[0]) for x in v]
    print(var,dict(sorted(Counter(x[0] for x in v).items())),"<=2:",sum(x<=2 for x in a),"of",len(a),"max",max(a))
    for x in sorted(v,key=lambda x:-abs(x[0]))[:3]: print("   ",x)
