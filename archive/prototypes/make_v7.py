content = open('build_v6_anton.py', encoding='utf-8').read()

swaps = [
    ('FONT_SIZE      = 46',                        'FONT_SIZE      = 56'),
    ("ASS_FILE       = 'v6_captions.ass'",         "ASS_FILE       = 'v7_captions.ass'"),
    ("OUTPUT_MP4     = 'derinkuyu_v6_anton.mp4'",  "OUTPUT_MP4     = 'derinkuyu_v7_anton56.mp4'"),
    ('DERINKUYU V6 -- Anton 46pt Zero-Overlap Captions',
     'DERINKUYU V7 -- Anton 56pt Zero-Overlap Captions'),
]
for old, new in swaps:
    content = content.replace(old, new)

open('build_v7_anton56.py', 'w', encoding='utf-8').write(content)
print('build_v7_anton56.py written')
