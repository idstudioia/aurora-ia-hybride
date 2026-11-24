"""Apply the removal of the signature (core/prompts.unsigned) to the answers already measured by the bench, off line:
how many are changed, what is taken off, and what the counter of the reading by hand still sees afterwards.
The answer kept by the bench is the composed one (AURORA's own notes follow the text of the model), so the removal is
applied to each paragraph block the way it would be to the text of the model alone. Read only.
Usage: python -X utf8 rejouer_retrait.py <dossier> <préfixe> [<préfixe> ...]"""
import glob
import json
import os
import re
import sys
import tempfile

essai = os.path.join(tempfile.gettempdir(), 'aurora-tests')
os.environ.setdefault('AURORA_HOME', essai)
os.environ.setdefault('AURORA_DB_PATH', os.path.join(essai, 'aurora.db'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'backend'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from aurora.core import prompts  # noqa: E402
from lecture_apres import DEFAUTS  # noqa: E402

MOTIF = re.compile(DEFAUTS['récite son moteur ou sa consigne'][1], re.I)
NOTE = re.compile(r'\n\n(?=Source consultée avant de répondre|Sources? :|Vérification AURORA)')
total = {'lues': 0, 'changees': 0, 'retirees': 0, 'vues_avant': 0, 'vues_apres': 0}
restes, retraits = [], set()
for prefixe in sys.argv[2:]:
    for chemin in sorted(glob.glob(os.path.join(sys.argv[1], prefixe + '-*.json'))):
        if chemin.endswith('resume.json'):
            continue
        donnees = json.load(open(chemin, encoding='utf-8'))
        for rang, tirage in enumerate(donnees.get('tirages') or [], 1):
            for ligne in tirage:
                if not ligne['temps']:
                    continue
                reponse = ligne['reponse']
                du_modele, *notes = NOTE.split(reponse, 1)
                propre, combien = prompts.unsigned(du_modele, ligne['texte'])
                apres = propre + ('\n\n' + notes[0] if notes else '')
                total['lues'] += 1
                total['changees'] += bool(combien)
                total['retirees'] += combien
                total['vues_avant'] += bool(MOTIF.search(reponse))
                total['vues_apres'] += bool(MOTIF.search(apres))
                if combien:
                    for morceau in set(re.split(r'(?<=[.!?»)])\s+|\n+', du_modele)) - set(re.split(r'(?<=[.!?»)])\s+|\n+', propre)):
                        retraits.add(morceau.strip())
                if MOTIF.search(apres):
                    trouve = MOTIF.search(apres)
                    restes.append(f"{prefixe} {donnees['modele']} t{rang}/{ligne['tour']} : …"
                                  + apres[max(0, trouve.start() - 60):trouve.end() + 50].replace('\n', ' ⏎ '))
print(total)
print('\n--- ce qui est retiré (formes distinctes) ---')
for morceau in sorted(retraits):
    print('  ', morceau[:170])
print('\n--- ce que le compteur voit encore ---')
for reste in restes:
    print('  ', reste[:260])
