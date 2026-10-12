content = open('build_v7_anton56.py', encoding='utf-8').read()

swaps = [
    ('FONT_SIZE      = 56',                        'FONT_SIZE      = 68'),
    ("ASS_FILE       = 'v7_captions.ass'",         "ASS_FILE       = 'v8_captions.ass'"),
    ("OUTPUT_MP4     = 'derinkuyu_v7_anton56.mp4'",  "OUTPUT_MP4     = 'derinkuyu_v8_anton68.mp4'"),
    ('DERINKUYU V7 -- Anton 56pt Zero-Overlap Captions',
     'DERINKUYU V8 -- Anton 68pt Zero-Overlap Captions'),
    # Also adjust max chars per candidate slightly for 68pt if needed: max 3 words, max 20 chars
    ('len(candidate) > 22', 'len(candidate) > 20'),
]
for old, new in swaps:
    content = content.replace(old, new)

open('build_v8_anton68.py', 'w', encoding='utf-8').write(content)
print('build_v8_anton68.py written successfully')
