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
 "alhambra": [E(1238, "Muhammad I founds the Nasrid palace"), E(1492, "Boabdil hands Granada to Ferdinand and Isabella"), E(1526, "Charles V begins his own palace"), E(1829, "Washington Irving lives in the palace"), E(1984, "Named a World Heritage Site")],
 "colosseum": [E(72, "Vespasian begins the amphitheatre"), E(80, "Titus opens it with 100 days of games"), E(1349, "An earthquake brings down the south side"), E(1749, "Pope Benedict XIV declares it sacred"), E(1980, "Named a World Heritage Site")],
 "stonehenge": [E(-3000, "The first ditch and bank are dug", True), E(-2500, "The sarsen stones are raised", True), E(1918, "Cecil Chubb gives it to the nation"), E(1986, "Named a World Heritage Site")],
}

jobs = {
 "bonifacio": lambda o, w, h: graphics.render_map(P(41.387, 9.159, "France", "Bonifacio Citadel, Corsica"), o, width=w, height=h, narration="12 km from Sardinia"),
 "dover": lambda o, w, h: graphics.render_map(P(51.129, 1.322, "United Kingdom", "Dover Castle"), o, width=w, height=h, narration="34 km from France"),
 "manama": lambda o, w, h: graphics.render_map(P(26.2335, 50.573, "Bahrain", "Qal'at al-Bahrain"), o, width=w, height=h, narration=""),
 "singapore": lambda o, w, h: graphics.render_map(P(1.2895, 103.852, "Singapore", "Marina Bay, Singapore"), o, width=w, height=h, narration=""),
 "miraflores": lambda o, w, h: graphics.render_map(P(8.9967, -79.5917, "Panama", "Miraflores Locks"), o, width=w, height=h, narration="between two oceans"),
 "thursday": lambda o, w, h: graphics.render_map(P(-10.5833, 142.2167, "Australia", "Thursday Island, Torres Strait"), o, width=w, height=h, narration=""),
 "nagurskoye": lambda o, w, h: graphics.render_map(P(80.803, 47.65, "Russia", "Nagurskoye, Franz Josef Land"), o, width=w, height=h, narration=""),
 "qaanaaq": lambda o, w, h: graphics.render_map(P(77.467, -69.23, "Greenland", "Qaanaaq"), o, width=w, height=h, narration=""),
 "esperanza": lambda o, w, h: graphics.render_map(P(-63.397, -56.997, "Antarctica", "Esperanza Base"), o, width=w, height=h, narration=""),
 "concordia": lambda o, w, h: graphics.render_map(P(-75.1, 123.33, "Antarctica", "Concordia Station"), o, width=w, height=h, narration=""),
 "heard": lambda o, w, h: graphics.render_map(P(-53.019, 73.392, "Australia", "Atlas Cove, Heard Island"), o, width=w, height=h, narration=""),
 "noronha": lambda o, w, h: graphics.render_map(P(-3.854, -32.424, "Brazil", "Fernando de Noronha"), o, width=w, height=h, narration=""),
 "galapagos": lambda o, w, h: graphics.render_map(P(-0.743, -90.313, "Ecuador", "Puerto Ayora, Galapagos"), o, width=w, height=h, narration=""),
 "chatham": lambda o, w, h: graphics.render_map(P(-43.954, -176.559, "New Zealand", "Waitangi, Chatham Islands"), o, width=w, height=h, narration=""),
 "wrangel": lambda o, w, h: graphics.render_map(P(71.24, -179.4, "Russia", "Wrangel Island"), o, width=w, height=h, narration=""),
 "clipperton": lambda o, w, h: graphics.render_map(P(10.3, -109.217, "France", "Clipperton Atoll"), o, width=w, height=h, narration=""),
 "wake": lambda o, w, h: graphics.render_map(P(19.283, 166.647, "United States", "Wake Island"), o, width=w, height=h, narration=""),
 "cocos": lambda o, w, h: graphics.render_map(P(-12.188, 96.829, "Australia", "West Island, Cocos (Keeling)"), o, width=w, height=h, narration=""),
 "fakaofo": lambda o, w, h: graphics.render_map(P(-9.38, -171.247, "New Zealand", "Fakaofo, Tokelau"), o, width=w, height=h, narration=""),
 "nauru": lambda o, w, h: graphics.render_map(P(-0.5228, 166.9315, "Nauru", "Yaren, Nauru"), o, width=w, height=h, narration=""),
 "monaco": lambda o, w, h: graphics.render_map(P(43.731, 7.42, "Monaco", "Monte Carlo Casino"), o, width=w, height=h, narration=""),
 "kaliningrad": lambda o, w, h: graphics.render_map(P(54.7065, 20.511, "Russia", "Konigsberg Cathedral, Kaliningrad"), o, width=w, height=h, narration=""),
 "nakhchivan": lambda o, w, h: graphics.render_map(P(39.209, 45.412, "Azerbaijan", "Momine Khatun Mausoleum, Nakhchivan"), o, width=w, height=h, narration=""),
 "cabinda": lambda o, w, h: graphics.render_map(P(-5.55, 12.2, "Angola", "Cabinda"), o, width=w, height=h, narration=""),
 "busingen": lambda o, w, h: graphics.render_map(P(47.696, 8.69, "Germany", "Busingen am Hochrhein"), o, width=w, height=h, narration=""),
 "goldentri": lambda o, w, h: graphics.render_map(P(20.354, 100.083, "Thailand", "Golden Triangle, Sop Ruak"), o, width=w, height=h, narration="three countries meet"),
 "treriks": lambda o, w, h: graphics.render_map(P(69.06, 20.548, "Sweden", "Treriksroset"), o, width=w, height=h, narration="three countries meet"),
 "saotome": lambda o, w, h: graphics.render_map(P(0.3365, 6.7273, "Sao Tome and Principe", "Sao Tome"), o, width=w, height=h, narration=""),
 "northpole": lambda o, w, h: graphics.render_map(P(89.95, 0.0, "", "North Pole"), o, width=w, height=h, narration=""),
 "gibraltar": lambda o, w, h: graphics.render_map(P(36.144, -5.342, "United Kingdom", "Rock of Gibraltar"), o, width=w, height=h, narration="14 km from Africa"),
 "route_iceland": lambda o, w, h: graphics.render_route(R("Iceland", [("Reykjavik", "Hallgrimskirkja, Reykjavik"), ("Thingvellir", "Thingvellir"), ("Vik", "Vik"), ("Hofn", "Hofn"), ("Akureyri", "Akureyri")]), o, width=w, height=h, narration="around the ring road"),
 "route_indo": lambda o, w, h: graphics.render_route(R("Indonesia", [("Jakarta", "Jakarta"), ("Borobudur", "Borobudur"), ("Ubud", "Ubud, Bali"), ("Labuan Bajo", "Labuan Bajo")]), o, width=w, height=h, narration="across the islands"),
}
for k, v in TL.items():
    jobs["tl_" + k] = (lambda ev: lambda o, w, h: graphics.render_timeline(ev, o, width=w, height=h))(v)

FRESH = {
 "lesotho": (P(-29.38, 27.68, "Lesotho", "Thaba Bosiu"), ""),
 "vatican": (P(41.9022, 12.4539, "Vatican", "St Peter's Basilica"), ""),
 "liecht": (P(47.1394, 9.5246, "Liechtenstein", "Vaduz Castle"), ""),
 "gambia": (P(13.3167, -16.3611, "Gambia", "Kunta Kinteh Island"), ""),
 "akrotiri": (P(34.585, 32.987, "United Kingdom", "RAF Akrotiri"), ""),
 "male": (P(4.1755, 73.5093, "Maldives", "Male Friday Mosque"), ""),
 "tristan": (P(-37.067, -12.311, "United Kingdom", "Edinburgh of the Seven Seas"), ""),
 "ascension": (P(-7.926, -14.416, "United Kingdom", "Georgetown, Ascension"), ""),
 "stkilda": (P(57.813, -8.57, "United Kingdom", "Hirta, St Kilda"), ""),
 "diego": (P(-7.313, 72.411, "United Kingdom", "Diego Garcia"), ""),
 "hongkong": (P(22.2783, 114.1747, "China", "Victoria Harbour"), ""),
 "zanzibar": (P(-6.1659, 39.1917, "Tanzania", "Stone Town, Zanzibar"), ""),
 "longyear": (P(78.2232, 15.6267, "Norway", "Longyearbyen"), ""),
 "nemo": (P(-48.876, -123.393, "", "Point Nemo"), ""),
 "baarle": (P(51.4386, 4.9289, "Belgium", "Baarle-Hertog"), ""),
}
for k_, (pl_, nar_) in FRESH.items():
    jobs[k_] = (lambda pl, nar: lambda o, w, h: graphics.render_map(pl, o, width=w, height=h, narration=nar))(pl_, nar_)
TL2 = {
 "angkor": [E(1113, "Suryavarman II begins the temple"), E(1150, "The temple is finished", True), E(1431, "The Khmer capital moves south"), E(1860, "Henri Mouhot reaches the ruins"), E(1992, "Named a World Heritage Site")],
 "hagia": [E(537, "Justinian opens the great church"), E(558, "An earthquake brings down the dome"), E(1453, "Mehmed the Conqueror makes it a mosque"), E(1934, "It becomes a museum"), E(2020, "It is a mosque again")],
 "machu": [E(1450, "Pachacuti builds the royal estate", True), E(1572, "The site is abandoned", True), E(1911, "Hiram Bingham reaches the ruins"), E(1983, "Named a World Heritage Site")],
 "notredame": [E(1163, "Maurice de Sully lays the first stone"), E(1345, "The cathedral is finished"), E(1804, "Napoleon is crowned emperor inside"), E(2019, "Fire destroys the spire"), E(2024, "The cathedral reopens")],
}
for k_, v_ in TL2.items():
    jobs["tl_" + k_] = (lambda ev: lambda o, w, h: graphics.render_timeline(ev, o, width=w, height=h))(v_)
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
