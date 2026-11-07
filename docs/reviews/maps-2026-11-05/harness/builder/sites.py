import time, sys
sys.path.insert(0, "/home/user/relic-and-riddle")
from src import geo
S = {"gibraltar": (36.144, -5.342), "fakaofo": (-9.38, -171.247), "saotome": (0.3365, 6.7273), "ascension": (-7.94, -14.37),
     "tristan": (-37.067, -12.311), "stkilda": (57.813, -8.57), "diego": (-7.313, 72.411), "kaliningrad": (54.7065, 20.511),
     "singapore": (1.2895, 103.852), "monaco": (43.731, 7.42), "lesotho": (-29.31, 27.48), "liecht": (47.14, 9.52),
     "vatican": (41.9029, 12.4534), "gambia": (13.45, -16.58), "akrotiri": (34.59, 32.98), "busingen": (47.696, 8.69),
     "male": (4.1755, 73.5093), "clipperton": (10.3, -109.217), "galapagos": (-0.743, -90.313), "wake": (19.283, 166.647),
     "torshavn": (62.0107, -6.7741), "norfolk": (-29.056, 167.96), "kerguelen": (-49.35, 70.22), "rangiroa": (-14.97, -147.63),
     "capehorn": (-55.98, -67.27), "northpole": (89.95, 0.0), "wrangel": (71.24, -179.4), "nauru": (-0.5228, 166.9315),
     "bonifacio": (41.387, 9.159), "cocos": (-12.188, 96.829), "llivia": (42.4637, 1.9814), "pitcairn": (-25.066, -130.1015),
     "bikini": (11.6, 165.38), "dover": (51.129, 1.322), "concordia": (-75.1, 123.33), "heard": (-53.019, 73.392)}
HINT = {"gibraltar": "United Kingdom", "busingen": "Germany", "fakaofo": "New Zealand", "kaliningrad": "Russia", "monaco": "Monaco"}
with geo.detail_view() as on:
    print("detail", on)
    for k, (la, lo) in S.items():
        t = time.time()
        h = geo.site_home(lo, la, hint=HINT.get(k, ""))
        c = h.country
        print(f"{k:12s} {c['display'] if c else None!s:40s} {h.km:6.2f} km  {time.time()-t:.2f}s")
