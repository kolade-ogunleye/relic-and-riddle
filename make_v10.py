import os
import re

content = open('build_v7_anton56.py', encoding='utf-8').read()

# Replace font size with 120
content = content.replace('FONT_SIZE      = 56', 'FONT_SIZE      = 120')
content = content.replace("ASS_FILE       = 'v7_captions.ass'", "ASS_FILE       = 'v10_captions_120pt.ass'")
content = content.replace("OUTPUT_MP4     = 'derinkuyu_v7_anton56.mp4'", "OUTPUT_MP4     = 'derinkuyu_v10_anton120.mp4'")
content = content.replace('DERINKUYU V7 -- Anton 56pt Zero-Overlap Captions', 'DERINKUYU V10 -- Anton 120pt Single-Line Captions')

# For 120pt Anton, max words = 2, max characters = 11 to guarantee NO overflow!
content = content.replace(
    'if len(cur) >= 3 or len(candidate) > 22:',
    'if len(cur) >= 2 or len(candidate) > 11:'
)

# For 120pt, MarginV at 440, Outline padding 15
content = content.replace(
    'Style: Cap,{font},{FONT_SIZE},{COL_PAST},&H000000FF,{COL_OUTLINE},{COL_BACK},-1,0,0,0,100,100,0.5,0,3,10,0,2,80,80,{MARGIN_V},1',
    'Style: Cap,{font},{FONT_SIZE},{COL_PAST},&H000000FF,{COL_OUTLINE},{COL_BACK},-1,0,0,0,100,100,0.5,0,3,15,0,2,30,30,{MARGIN_V},1'
)

open('build_v10_anton120.py', 'w', encoding='utf-8').write(content)
print('build_v10_anton120.py written successfully')
