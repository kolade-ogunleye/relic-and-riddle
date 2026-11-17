import sys, subprocess, logging, time
from pathlib import Path
root, out = sys.argv[1], Path(sys.argv[2])
only = sys.argv[3:]
fast = "--fast" in only
if fast: only.remove("--fast")
sizes = ((1080, 1920), (1920, 1080))
if "--tall" in only: only.remove("--tall"); sizes = ((1080, 1920),)
if "--wide" in only: only.remove("--wide"); sizes = ((1920, 1080),)
out.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, root)
from src import graphics, geo
if fast:
    graphics.VIDEO_FPS = 1
    graphics.graded_frame = lambda frame, work, vignette=False: None
    def _enc(frames, path, n, width=1080, height=1920):
        last = None
        for f in frames: last = f
        last.convert("RGB").save(str(path) + ".png")
        return path
    graphics._encode = _enc
P = lambda lat, lon, c, l: {"lat": lat, "lon": lon, "country": c, "label": l, "approximate": False}
COORDS = {
 "alexandria": (31.2001, 29.9187), "giza": (29.9792, 31.1342), "luxor": (25.7188, 32.6573), "aswan": (24.0889, 32.8998), "abu simbel": (22.3372, 31.6258),
 "athens": (37.9715, 23.7257), "delphi": (38.4824, 22.5010), "olympia": (37.6383, 21.6300), "mycenae": (37.7308, 22.7561), "knossos": (35.2980, 25.1631),
 "reykjavik": (64.1466, -21.9426), "thingvellir": (64.2559, -21.1299), "vik": (63.4186, -19.0060), "hofn": (64.2539, -15.2082), "akureyri": (65.6835, -18.0878),
 "jakarta": (-6.1754, 106.8272), "borobudur": (-7.6079, 110.2038), "ubud": (-8.5069, 115.2625), "komodo": (-8.5500, 119.4833), "labuan bajo": (-8.4964, 119.8877),
 "khiva": (41.3783, 60.3639), "bukhara": (39.7747, 64.4286), "samarkand": (39.6542, 66.9597), "tashkent": (41.2995, 69.2401),
}
COORDS.update({
 "marrakesh": (31.6295, -7.9811), "fes": (34.0331, -5.0003), "chefchaouen": (35.1688, -5.2636), "tangier": (35.7595, -5.8340),
 "hanoi": (21.0285, 105.8542), "hue": (16.4637, 107.5909), "da nang": (16.0544, 108.2022), "ho chi minh city": (10.7769, 106.7009),
})
_real = geo.locate
def fake_locate(topic):
    n = topic["location"]["name"].lower(); c = topic["location"]["country"]
    if n in COORDS:
        la, lo = COORDS[n]
        return {"lat": la, "lon": lo, "country": c, "label": topic["location"]["name"], "approximate": False, "qid": "x", "matched": "name"}
    return _real(topic)
geo.locate = fake_locate
if hasattr(graphics, "locate"): graphics.locate = fake_locate
R = lambda c, pl: [{"place": n, "country": c, "label": l} for n, l in pl]
E = lambda y, l, a=False: {"year": y, "label": l, "approximate": a}
TL = {
 "pisa": [E(1173, "Work begins on the bell tower"), E(1178, "The tower starts to lean"), E(1372, "The bell chamber is finished"), E(1990, "The tower closes for repairs"), E(2001, "It reopens to visitors")],
 "westminster": [E(960, "Benedictine monks settle the site", True), E(1066, "William the Conqueror is crowned here"), E(1245, "Henry III begins the Gothic rebuilding"), E(1953, "Elizabeth II is crowned in the abbey"), E(1987, "Named a World Heritage Site")],
 "sagrada": [E(1882, "The first stone is laid"), E(1883, "Antoni Gaudi takes over the project"), E(1926, "Gaudi dies with one tower finished"), E(2010, "Pope Benedict XVI consecrates the basilica"), E(2026, "The Tower of Jesus Christ is completed")],
}
jobs = {
 "montstmichel": lambda o, w, h: graphics.render_map(P(48.6361, -1.5115, "France", "Mont-Saint-Michel"), o, width=w, height=h, narration=""),
 "cabodaroca": lambda o, w, h: graphics.render_map(P(38.7804, -9.4989, "Portugal", "Cabo da Roca"), o, width=w, height=h, narration=""),
 "dubrovnik": lambda o, w, h: graphics.render_map(P(42.6407, 18.1077, "Croatia", "Dubrovnik Old Town"), o, width=w, height=h, narration=""),
 "neum": lambda o, w, h: graphics.render_map(P(42.923, 17.616, "Bosnia and Herzegovina", "Neum"), o, width=w, height=h, narration=""),
 "trieste": lambda o, w, h: graphics.render_map(P(45.6495, 13.7768, "Italy", "Piazza Unita, Trieste"), o, width=w, height=h, narration=""),
 "malmo": lambda o, w, h: graphics.render_map(P(55.605, 13.0038, "Sweden", "Malmo Castle"), o, width=w, height=h, narration=""),
 "lamu": lambda o, w, h: graphics.render_map(P(-2.2717, 40.902, "Kenya", "Lamu Old Town"), o, width=w, height=h, narration=""),
 "portarthur": lambda o, w, h: graphics.render_map(P(-43.1468, 147.851, "Australia", "Port Arthur, Tasmania"), o, width=w, height=h, narration=""),
 "valletta": lambda o, w, h: graphics.render_map(P(35.8989, 14.5146, "Malta", "Valletta"), o, width=w, height=h, narration=""),
 "aitutaki": lambda o, w, h: graphics.render_map(P(-18.856, -159.785, "Cook Islands", "Aitutaki Lagoon"), o, width=w, height=h, narration=""),
 "borabora": lambda o, w, h: graphics.render_map(P(-16.5004, -151.7415, "France", "Bora Bora"), o, width=w, height=h, narration=""),
 "palmyra": lambda o, w, h: graphics.render_map(P(5.8885, -162.0787, "United States", "Palmyra Atoll"), o, width=w, height=h, narration=""),
 "stpierre": lambda o, w, h: graphics.render_map(P(46.7811, -56.1764, "France", "Saint-Pierre"), o, width=w, height=h, narration=""),
 "bermuda": lambda o, w, h: graphics.render_map(P(32.2949, -64.783, "United Kingdom", "Hamilton, Bermuda"), o, width=w, height=h, narration=""),
 "christmas": lambda o, w, h: graphics.render_map(P(-10.4475, 105.6904, "Australia", "Flying Fish Cove, Christmas Island"), o, width=w, height=h, narration=""),
 "lampedusa": lambda o, w, h: graphics.render_map(P(35.5022, 12.6046, "Italy", "Lampedusa"), o, width=w, height=h, narration=""),
 "aruba": lambda o, w, h: graphics.render_map(P(12.5211, -69.9683, "Netherlands", "Oranjestad, Aruba"), o, width=w, height=h, narration=""),
 "mayotte": lambda o, w, h: graphics.render_map(P(-12.7806, 45.2279, "France", "Mamoudzou, Mayotte"), o, width=w, height=h, narration=""),
 "kwajalein": lambda o, w, h: graphics.render_map(P(8.72, 167.731, "Marshall Islands", "Kwajalein Atoll"), o, width=w, height=h, narration=""),
 "minicoy": lambda o, w, h: graphics.render_map(P(8.283, 73.049, "India", "Minicoy, Lakshadweep"), o, width=w, height=h, narration=""),
 "niue": lambda o, w, h: graphics.render_map(P(-19.0544, -169.9187, "Niue", "Alofi, Niue"), o, width=w, height=h, narration=""),
 "guam": lambda o, w, h: graphics.render_map(P(13.4745, 144.7504, "United States", "Hagatna, Guam"), o, width=w, height=h, narration=""),
 "funchal": lambda o, w, h: graphics.render_map(P(32.65, -16.908, "Portugal", "Funchal, Madeira"), o, width=w, height=h, narration=""),
 "pontadelgada": lambda o, w, h: graphics.render_map(P(37.7412, -25.6756, "Portugal", "Ponta Delgada, Azores"), o, width=w, height=h, narration=""),
 "grenada": lambda o, w, h: graphics.render_map(P(12.0561, -61.7486, "Grenada", "St George's, Grenada"), o, width=w, height=h, narration=""),
 "koror": lambda o, w, h: graphics.render_map(P(7.341, 134.479, "Palau", "Koror"), o, width=w, height=h, narration=""),
 "macau": lambda o, w, h: graphics.render_map(P(22.1987, 113.5439, "China", "Ruins of St Paul's, Macau"), o, width=w, height=h, narration=""),
 "melilla": lambda o, w, h: graphics.render_map(P(35.2923, -2.9381, "Spain", "Melilla la Vieja"), o, width=w, height=h, narration=""),
 "oecusse": lambda o, w, h: graphics.render_map(P(-9.205, 124.36, "Timor-Leste", "Pante Macassar, Oecusse"), o, width=w, height=h, narration=""),
 "pointroberts": lambda o, w, h: graphics.render_map(P(48.985, -123.07, "United States", "Point Roberts"), o, width=w, height=h, narration=""),
 "jungholz": lambda o, w, h: graphics.render_map(P(47.573, 10.448, "Austria", "Jungholz"), o, width=w, height=h, narration=""),
 "luxembourg": lambda o, w, h: graphics.render_map(P(49.6116, 6.1319, "Luxembourg", "Luxembourg City"), o, width=w, height=h, narration=""),
 "brunei": lambda o, w, h: graphics.render_map(P(4.8895, 114.942, "Brunei", "Omar Ali Saifuddien Mosque"), o, width=w, height=h, narration=""),
 "seychelles": lambda o, w, h: graphics.render_map(P(-4.6191, 55.4513, "Seychelles", "Victoria, Mahe"), o, width=w, height=h, narration=""),
 "vaals": lambda o, w, h: graphics.render_map(P(50.7543, 6.0207, "Netherlands", "Vaalserberg"), o, width=w, height=h, narration=""),
 "leticia": lambda o, w, h: graphics.render_map(P(-4.215, -69.94, "Colombia", "Leticia"), o, width=w, height=h, narration=""),
 "prespa": lambda o, w, h: graphics.render_map(P(40.87, 21.05, "Greece", "Prespa Lakes"), o, width=w, height=h, narration=""),
 "domea": lambda o, w, h: graphics.render_map(P(-80.42, 77.12, "Antarctica", "Kunlun Station, Dome A"), o, width=w, height=h, narration=""),
 "capecolumbia": lambda o, w, h: graphics.render_map(P(83.11, -69.95, "Canada", "Cape Columbia"), o, width=w, height=h, narration=""),
 "arcticdrift": lambda o, w, h: graphics.render_map(P(86.5, 100.0, "", "Fram's drift, Arctic Ocean"), o, width=w, height=h, narration=""),
 "lomaloma": lambda o, w, h: graphics.render_map(P(-17.29, -178.97, "Fiji", "Lomaloma, Vanua Balavu"), o, width=w, height=h, narration=""),
 "adak": lambda o, w, h: graphics.render_map(P(51.88, -176.65, "United States", "Adak, Aleutian Islands"), o, width=w, height=h, narration=""),
 "kanton": lambda o, w, h: graphics.render_map(P(-2.81, -171.67, "Kiribati", "Kanton Island"), o, width=w, height=h, narration=""),
 "tonga": lambda o, w, h: graphics.render_map(P(-21.139, -175.204, "Tonga", "Nuku'alofa"), o, width=w, height=h, narration=""),
 "route_morocco": lambda o, w, h: graphics.render_route(R("Morocco", [("Marrakesh", "Jemaa el-Fnaa, Marrakesh"), ("Fes", "Fes el-Bali"), ("Chefchaouen", "Chefchaouen"), ("Tangier", "Tangier")]), o, width=w, height=h, narration="north to the strait"),
 "route_vietnam": lambda o, w, h: graphics.render_route(R("Vietnam", [("Hanoi", "Hoan Kiem, Hanoi"), ("Hue", "Hue Imperial City"), ("Da Nang", "Da Nang"), ("Ho Chi Minh City", "Ho Chi Minh City")]), o, width=w, height=h, narration="down the coast"),
}
for k, v in TL.items():
    jobs["tl_" + k] = (lambda ev: lambda o, w, h: graphics.render_timeline(ev, o, width=w, height=h))(v)
for name, fn in jobs.items():
    if only and name not in only:
        continue
    for w, h in sizes:
        lg = logging.getLogger(); [lg.removeHandler(x) for x in list(lg.handlers)]
        fh = logging.FileHandler(str(out / f"log_{name}_{w}x{h}.txt"), mode="w"); fh.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
        lg.addHandler(fh); lg.setLevel(logging.INFO)
        mp4 = out / f"{name}_{w}x{h}.mp4"
        t = time.time()
        try:
            o = fn(mp4, w, h)
            if not o:
                print("NONE", name, w, flush=True); continue
            if fast:
                print("ok", mp4.name, round(time.time() - t, 1), flush=True); continue
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-sseof", "-0.1", "-i", str(o), "-frames:v", "1", str(mp4) + ".png"], check=True)
            print("ok", mp4.name, round(time.time() - t, 1), flush=True)
        except Exception as e:
            import traceback; traceback.print_exc()
            print("FAIL", name, w, repr(e)[:300], flush=True)
