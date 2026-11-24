"""Count the words of each limited answer of the contest form and compare with its limit.
Each answer sits between « <!-- debut:name:limit --> » and « <!-- fin --> » in the given Markdown file.
Usage: python -X utf8 compter_mots.py <formulaire.md>"""
import re
import sys

texte = open(sys.argv[1], encoding='utf-8').read()
trop = 0
for nom, limite, reponse in re.findall(r'<!-- debut:(\w+):(\d+) -->\n(.*?)\n<!-- fin -->', texte, re.S):
    mots = len(reponse.split())
    trop += mots > int(limite)
    print(f"{nom:15s} {mots:4d} mots sur {limite} au plus · {'TROP LONG' if mots > int(limite) else 'tient'}")
sys.exit(1 if trop else 0)
