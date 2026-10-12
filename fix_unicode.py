import re

path = r'C:\Users\omokolade\GitHub\New folder\build_v5_final.py'
content = open(path, encoding='utf-8').read()

replacements = {
    '\u2192': '->',
    '\u2705': '[OK]',
    '\u26a0': '[WARN]',
    '\u2714': '[OK]',
    '\u274c': '[ERR]',
    '\u2588': '#',
    '\u2013': '-',
    '\u2014': '--',
    '\u2018': "'",
    '\u2019': "'",
    '\u201c': '"',
    '\u201d': '"',
    '\u2500': '-',
    '\u2502': '|',
    '\u256d': '+',
    '\u256e': '+',
    '\u2570': '+',
    '\u256f': '+',
    '\u2015': '-',
    '\u25ba': '>',
}

for k, v in replacements.items():
    content = content.replace(k, v)

open(path, 'w', encoding='utf-8').write(content)
print('Done - all non-ASCII chars replaced')
