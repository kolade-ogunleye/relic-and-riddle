import re

content = open('build_v5_final.py', encoding='utf-8').read()

swaps = [
    ("FONT_NAME      = 'Montserrat'",       "FONT_NAME      = 'Anton'"),
    ("FONT_FILE_PREF = os.path.join(FONTS_DIR, 'Montserrat-Variable.ttf')",
     "FONT_FILE_PREF = os.path.join(FONTS_DIR, 'Anton-Regular.ttf')"),
    ("FONT_FILE_FALL = os.path.join(FONTS_DIR, 'BebasNeue-Regular.ttf')",
     "FONT_FILE_FALL = os.path.join(FONTS_DIR, 'Montserrat-Variable.ttf')"),
    ("FONT_SIZE      = 44",  "FONT_SIZE      = 46"),
    ("ASS_FILE       = 'v5_captions.ass'",  "ASS_FILE       = 'v6_captions.ass'"),
    ("OUTPUT_MP4     = 'derinkuyu_v5_final.mp4'", "OUTPUT_MP4     = 'derinkuyu_v6_anton.mp4'"),
    ("DERINKUYU V5 FINAL -- Zero-Overlap Single-Line Captions",
     "DERINKUYU V6 -- Anton 46pt Zero-Overlap Captions"),
]

for old, new in swaps:
    content = content.replace(old, new)

open('build_v6_anton.py', 'w', encoding='utf-8').write(content)
print('build_v6_anton.py written OK')
