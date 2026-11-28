import json, sys
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
from PIL import Image, ImageOps
Image.MAX_IMAGE_PIXELS=None
HERE=Path(__file__).resolve().parent
def one(o):
    f=HERE/"thumbs"/(o["sha256"][:20]+".png")
    if f.is_file(): return 0
    with Image.open(o["path"]) as im:
        im.draft("RGB",(2560,2560))
        im=ImageOps.exif_transpose(im).convert("RGB")
    im.thumbnail((1280,1280),Image.LANCZOS); im.save(f); return 1
if __name__=="__main__":
    ph=json.load(open(HERE/"photos_all.json"))
    with ProcessPoolExecutor(6) as ex: print(sum(ex.map(one,ph)))
