"""Run every candidate subject source over every photo; keep raw outputs (masks at 320 px, detector boxes)."""
import json, re, sys, time
from pathlib import Path
import numpy as np
from PIL import Image, ImageOps
Image.MAX_IMAGE_PIXELS = None
HERE = Path(__file__).resolve().parent
photos = json.load(open(HERE / "photos_all.json"))
import os
if os.environ.get("SUBSET"):
    keep = {o["sha256"] for o in json.load(open(HERE / os.environ["SUBSET"]))}
    photos = [o for o in photos if o["sha256"] in keep]
SIDE = int(os.environ.get("DSIDE", "0"))
which = sys.argv[1]
provider = sys.argv[2] if len(sys.argv) > 2 else "dml"
CACHE = HERE / "thumbs"; CACHE.mkdir(exist_ok=True)

def thumb(o):
    f = CACHE / (o["sha256"][:20] + ".png")
    if not f.is_file():
        with Image.open(o["path"]) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((1280, 1280), Image.LANCZOS)
        im.save(f)
    return Image.open(f).convert("RGB")

def clean_title(t):
    t = re.sub(r"^File:", "", t or "")
    t = re.sub(r"\.(jpe?g|png|tiff?|gif|webp)$", "", t, flags=re.I)
    t = re.sub(r"[\(\[].*?[\)\]]", " ", t)
    t = " ".join(w for w in re.split(r"[\s_,;:]+", t) if w and not re.search(r"\d", w))
    return t.strip()

def prompts(o):
    topic = " ".join(w for w in o["topic"].split("_") if not w.isdigit())
    title = clean_title(o["title"])
    out = [p for p in (title, topic) if p]
    return list(dict.fromkeys(p.lower() for p in out))

out = {}
dst = HERE / "raw" / (which + os.environ.get("TAG", "")); dst.mkdir(parents=True, exist_ok=True)
times = []
if which in ("u2net", "isnet", "birefnet"):
    import onnxruntime as ort
    f = {"u2net": "u2net.onnx", "isnet": "isnet-general-use.onnx",
         "birefnet": "BiRefNet-general-bb_swin_v1_tiny-epoch_232.onnx"}[which]
    so = ort.SessionOptions()
    if provider == "cpu6":
        so.intra_op_num_threads = 6; so.inter_op_num_threads = 1
    provs = ["DmlExecutionProvider", "CPUExecutionProvider"] if provider == "dml" else ["CPUExecutionProvider"]
    sess = ort.InferenceSession(str(HERE / "models" / f), so, providers=provs)
    side = 320 if which == "u2net" else 1024
    mean, std = ((0.5, 0.5, 0.5), (1.0, 1.0, 1.0)) if which == "isnet" else ((0.485, 0.456, 0.406), (0.229, 0.224, 0.225))
    for o in photos[: int(sys.argv[3]) if len(sys.argv) > 3 else None]:
        im = thumb(o)
        t0 = time.perf_counter()
        a = np.asarray(im.resize((side, side), Image.LANCZOS), dtype=np.float32)
        a = a / max(a.max(), 1e-6)
        a = (a - np.array(mean, np.float32)) / np.array(std, np.float32)
        x = a.transpose(2, 0, 1)[None].astype(np.float32)
        y = sess.run(None, {sess.get_inputs()[0].name: x})[0][0, 0]
        if which == "birefnet":
            y = 1 / (1 + np.exp(-y))
        y = (y - y.min()) / max(y.max() - y.min(), 1e-6)
        times.append(time.perf_counter() - t0)
        Image.fromarray((y * 255).astype(np.uint8)).resize((320, 320), Image.BILINEAR).save(dst / (o["sha256"][:20] + ".png"))
else:
    import torch
    torch.set_num_threads(6 if provider == "cpu6" else int(os.environ.get("THREADS","16")))
    from transformers import AutoProcessor
    if which == "gdino":
        from transformers import AutoModelForZeroShotObjectDetection
        mdir = HERE / "models/hf/IDEA-Research__grounding-dino-tiny"
        proc = AutoProcessor.from_pretrained(mdir); model = AutoModelForZeroShotObjectDetection.from_pretrained(mdir).eval()
        if SIDE: proc.image_processor.size = {"shortest_edge": SIDE, "longest_edge": int(SIDE * 1.333)}
    else:
        from transformers import Owlv2ForObjectDetection
        mdir = HERE / "models/hf/google__owlv2-base-patch16-ensemble"
        proc = AutoProcessor.from_pretrained(mdir); model = Owlv2ForObjectDetection.from_pretrained(mdir).eval()
    for o in photos[: int(sys.argv[3]) if len(sys.argv) > 3 else None]:
        im = thumb(o); W, H = im.size
        ps = prompts(o)
        t0 = time.perf_counter()
        with torch.no_grad():
            if which == "gdino":
                text = " . ".join(ps) + " ."
                inp = proc(images=im, text=text, return_tensors="pt")
                res = model(**inp)
                r = proc.post_process_grounded_object_detection(res, inp.input_ids, threshold=0.15, text_threshold=0.15,
                                                                target_sizes=[(H, W)])[0]
                labels = r.get("text_labels", r.get("labels"))
            else:
                inp = proc(text=[ps], images=im, return_tensors="pt")
                res = model(**inp)
                S = max(W, H)
                r = proc.post_process_object_detection(res, threshold=0.02, target_sizes=torch.tensor([(S, S)]))[0]
                labels = [ps[int(i)] for i in r["labels"]]
        times.append(time.perf_counter() - t0)
        boxes = [[float(v) for v in b] for b in r["boxes"]]
        dets = sorted(({"box": [b[0] / W, b[1] / H, b[2] / W, b[3] / H], "score": float(s), "label": str(l)}
                       for b, s, l in zip(boxes, r["scores"], labels)), key=lambda d: -d["score"])[:20]
        out[o["sha256"]] = {"prompts": ps, "dets": dets}
    json.dump(out, open(dst / "dets.json", "w"), indent=0)
json.dump({"n": len(times), "mean_s": float(np.mean(times)), "median_s": float(np.median(times)), "provider": provider},
          open(dst / f"time_{provider}.json", "w"))
print(which, provider, len(times), np.mean(times))

import os, sys as _s
_s.stdout.flush(); os._exit(0)
