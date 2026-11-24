"""Apply the rewriting of simple mathematical notation (core/prompts.plain_notation) to the answers already measured
by the bench, off line: how many answers and pieces are rewritten, what is left as the model wrote it, and which
turns change of verdict when the same expectations (conversation_modeles.controler) read the rewritten answer.
The answer kept by the bench is the composed one: the rewriting is applied to the text of the model, which stands
before AURORA's own notes (the same cut as rejouer_retrait.py). Read only.
Usage: python -X utf8 rejouer_notation.py <dossier> [<sortie.json>]"""
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
from conversation_modeles import TOURS, controler  # noqa: E402

NOTE = re.compile(r'\n\n(?=Source consultée avant de répondre|Sources? :|Vérification AURORA)')
total = {'rédigées': 0, 'avec notation': 0, 'réponses réécrites': 0, 'morceaux réécrits': 0, 'morceaux laissés': 0,
         'tours ratés devenus réussis': 0, 'tours réussis devenus ratés': 0, 'tours encore ratés': 0}
par_modele, formes, laisses, changes, encore = {}, {}, {}, [], []
for chemin in sorted(glob.glob(os.path.join(sys.argv[1], '*.json'))):
    try:
        donnees = json.load(open(chemin, encoding='utf-8'))
    except ValueError:
        continue
    if not isinstance(donnees, dict) or not isinstance(donnees.get('tirages'), list):
        continue
    nom = os.path.basename(chemin)[:-5]
    for rang, tirage in enumerate(donnees['tirages'], 1):
        for ligne in tirage:
            if not isinstance(ligne, dict) or not ligne.get('temps'):
                continue
            total['rédigées'] += 1
            reponse = ligne.get('reponse') or ''
            du_modele, *notes = NOTE.split(reponse, 1)
            avant = [m.group(0) for m in prompts._MATH_PIECE.finditer(du_modele)]
            if not avant:
                continue
            total['avec notation'] += 1
            propre, combien = prompts.plain_notation(du_modele, ligne.get('texte') or '')
            apres = propre + ('\n\n' + notes[0] if notes else '')
            reste = [m.group(0) for m in prompts._MATH_PIECE.finditer(propre)]
            total['réponses réécrites'] += bool(combien)
            total['morceaux réécrits'] += combien
            total['morceaux laissés'] += len(reste)
            modele = str(donnees.get('modele'))
            compte = par_modele.setdefault(modele, {'réponses': 0, 'morceaux': 0, 'ratés devenus réussis': 0})
            compte['réponses'] += bool(combien)
            compte['morceaux'] += combien
            for morceau in avant:
                if morceau not in reste:
                    ecrit = prompts.plain_notation(morceau)[0]
                    formes[f'{morceau}  →  {ecrit}'] = formes.get(f'{morceau}  →  {ecrit}', 0) + 1
            for morceau in reste:
                laisses[morceau] = laisses.get(morceau, 0) + 1
            verdict = controler(TOURS[ligne['tour'] - 1], apres, ligne.get('route'))
            cas = {'tirage': f"{nom} t{rang}/{ligne['tour']}", 'modele': modele, 'morceaux': combien,
                   'reussi_avant': bool(ligne.get('reussi')), 'reussi_apres': bool(verdict['reussi']),
                   'manque_avant': ligne.get('manque'), 'manque_apres': verdict['manque']}
            if cas['reussi_apres'] and not cas['reussi_avant']:
                total['tours ratés devenus réussis'] += 1
                compte['ratés devenus réussis'] += 1
                changes.append(cas)
            elif cas['reussi_avant'] and not cas['reussi_apres']:
                total['tours réussis devenus ratés'] += 1
                changes.append(cas)
            elif not cas['reussi_apres']:
                total['tours encore ratés'] += 1
                encore.append(cas)
for cle, valeur in total.items():
    print(f'{cle:32s} {valeur}')
print('par modèle :', par_modele)
print(f'\n--- formes réécrites : {len(formes)} ---')
for forme, combien in sorted(formes.items(), key=lambda p: (-p[1], p[0])):
    print(f'   ×{combien:<3d} {forme}')
print(f'\n--- morceaux laissés comme le modèle les a écrits : {len(laisses)} ---')
for morceau, combien in sorted(laisses.items(), key=lambda p: (-p[1], p[0])):
    print(f'   ×{combien:<3d} {morceau}')
print('\n--- tours qui changent de verdict ---')
for cas in changes:
    print(f"   {cas['tirage']} · {cas['modele']} · {cas['morceaux']} morceaux · {'raté' if not cas['reussi_avant'] else 'réussi'} → "
          f"{'réussi' if cas['reussi_apres'] else 'RATÉ'} · manquait {cas['manque_avant']} · manque {cas['manque_apres']}")
print('\n--- tours encore ratés après la réécriture ---')
for cas in encore:
    print(f"   {cas['tirage']} · {cas['modele']} · manque {cas['manque_apres']}")
if len(sys.argv) > 2:
    with open(sys.argv[2], 'w', encoding='utf-8') as fichier:
        json.dump({'total': total, 'par_modele': par_modele, 'formes': formes, 'laisses': laisses, 'changes': changes,
                   'encore': encore}, fichier, ensure_ascii=False, indent=1)
