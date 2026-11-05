import sys, os
from PIL import Image, ImageDraw
R = "./maps_work/r11rev1"
src = sys.argv[1]  # after|before
for j in sys.argv[2:]:
    a = f"{R}/{src}/{j}_1080x1920.mp4.png"; b = f"{R}/{src}/{j}_1920x1080.mp4.png"
    o = Image.new("RGB", (540 + 10 + 1066, 960 + 24), (40, 40, 40))
    if os.path.exists(a): o.paste(Image.open(a).convert("RGB").resize((540, 960)), (0, 24))
    if os.path.exists(b): o.paste(Image.open(b).convert("RGB").resize((1066, 600)), (550, 24))
    ImageDraw.Draw(o).text((6, 6), f"{src} {j}", fill=(255, 255, 255))
    os.makedirs(f"{R}/v/p_{src}", exist_ok=True)
    o.save(f"{R}/v/p_{src}/{j}.jpg", quality=88)
