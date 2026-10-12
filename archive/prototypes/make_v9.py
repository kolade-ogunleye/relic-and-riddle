import os
import re

content = open('build_v7_anton56.py', encoding='utf-8').read()

# Replace font size with 100
content = content.replace('FONT_SIZE      = 56', 'FONT_SIZE      = 100')
content = content.replace("ASS_FILE       = 'v7_captions.ass'", "ASS_FILE       = 'v9_captions_100pt.ass'")
content = content.replace("OUTPUT_MP4     = 'derinkuyu_v7_anton56.mp4'", "OUTPUT_MP4     = 'derinkuyu_v9_anton100.mp4'")
content = content.replace('DERINKUYU V7 -- Anton 56pt Zero-Overlap Captions', 'DERINKUYU V9 -- Anton 100pt Single-Line Captions')

# For 100pt Anton, max words must be 2, and max characters must be 12 to ensure NO clipping off screen!
# In V7: if len(cur) >= 3 or len(candidate) > 22:
content = content.replace(
    'if len(cur) >= 3 or len(candidate) > 22:',
    'if len(cur) >= 2 or len(candidate) > 13:'
)

# Also adjust Outline/Padding in ASS style for 100pt
# Style: Cap,{font},{FONT_SIZE},{COL_PAST},&H000000FF,{COL_OUTLINE},{COL_BACK},-1,0,0,0,100,100,0.5,0,3,10,0,2,80,80,{MARGIN_V},1
# For 100pt, MarginV at 440, Outline padding 14
content = content.replace(
    'Style: Cap,{font},{FONT_SIZE},{COL_PAST},&H000000FF,{COL_OUTLINE},{COL_BACK},-1,0,0,0,100,100,0.5,0,3,10,0,2,80,80,{MARGIN_V},1',
    'Style: Cap,{font},{FONT_SIZE},{COL_PAST},&H000000FF,{COL_OUTLINE},{COL_BACK},-1,0,0,0,100,100,0.5,0,3,14,0,2,40,40,{MARGIN_V},1'
)

open('build_v9_anton100.py', 'w', encoding='utf-8').write(content)
print('build_v9_anton100.py written successfully')
