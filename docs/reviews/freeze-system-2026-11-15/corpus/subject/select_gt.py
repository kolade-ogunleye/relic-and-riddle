import json, re
from pathlib import Path
from PIL import Image, ImageDraw, ImageOps, ImageFont
Image.MAX_IMAGE_PIXELS=None
pool=json.load(open("pool.json"))
named={"richat_structure_sahara_eye":[2,5,6,8,10,14,17,19],"singing_colossi_of_memnon":[1,9,12],"voynich_manuscript":[11,13,14],
"trepanation_miracle":[1],"unfinished_obelisk_aswan":[5,10,11,18],"nazca_lines_runway_paradox":[2,7,8,10],"amber_room_heist":[5,7,8,18],
"wieliczka_salt_mine":[1,5,6,15],"gobekli_tepe_enclosure_d":[1,6],"flannan_isle_lighthouse_disappearance":[1,2,3,6],
"kailasa_temple_monolith":[1,2,4],"roman_self_healing_concrete":[1,3,6,11],"tunguska_event_1908":[10],"odessa_catacombs":[8,12],
"hal_saflieni_hypogeum":[6],"greek_fire_siphon":[9]}
sel=[]
by={}
for o in pool:
    p=Path(o["path"]); by.setdefault(p.parent.name,[]).append(o)
for topic,items in sorted(by.items()):
    items=sorted(items,key=lambda o:o["path"])
    if not re.match(r"[a-z_]+$",topic) or topic in("upscaled","privacy"):
        continue
    pick=set(named.get(topic,[]))
    nums=[int(Path(o["path"]).stem[:2]) for o in items if Path(o["path"]).stem[:2].isdigit()]
    for n in nums[::4]: pick.add(n)
    for o in items:
        s=Path(o["path"]).stem
        if s[:2].isdigit() and int(s[:2]) in pick: sel.append(o)
# the Nazca wing frame (derived copy) and one ext_crop
for o in pool:
    if "upscaled" in o["path"] and "06_commons_6dc8" in o["path"]: sel.append(o)
# antikythera (named; not in the dev pool)
import os
ak=Path(os.environ["RR_DEV"])/"assets"/"antikythera_mechanism"
import hashlib
for p in sorted(ak.glob("*_commons.jpg"))[::3]:
    sel.append({"path":str(p),"runs":[],"sha256":hashlib.sha256(p.read_bytes()).hexdigest()})
seen=set(); out=[]
for o in sel:
    if o["sha256"] in seen: continue
    seen.add(o["sha256"]); out.append(o)
for i,o in enumerate(out):
    p=Path(o["path"]); o["id"]=f"{i:03d}"; o["name"]=f"{p.parent.name}/{p.name}"
json.dump(out,open("gt_select.json","w"),indent=1)
print(len(out))
Path("grid").mkdir(exist_ok=True)
try: font=ImageFont.truetype("arial.ttf",18)
except: font=ImageFont.load_default()
for o in out:
    with Image.open(o["path"]) as im:
        im=ImageOps.exif_transpose(im).convert("RGB")
    o["size"]=im.size
    im.thumbnail((1100,1100))
    w,h=im.size
    d=ImageDraw.Draw(im)
    for k in range(1,10):
        x=w*k/10; y=h*k/10
        d.line([(x,0),(x,h)],fill=(255,0,255),width=1); d.line([(0,y),(w,y)],fill=(255,0,255),width=1)
        d.text((x+2,2),f"{k/10:.1f}",fill=(255,255,0),font=font,stroke_width=2,stroke_fill=(0,0,0))
        d.text((2,y+2),f"{k/10:.1f}",fill=(255,255,0),font=font,stroke_width=2,stroke_fill=(0,0,0))
    im.save(f"grid/{o['id']}.jpg",quality=88)
json.dump(out,open("gt_select.json","w"),indent=1)
