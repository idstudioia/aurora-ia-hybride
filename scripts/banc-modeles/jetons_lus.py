"""How many tokens each model was given to read, per turn of the bench: read back from the runs the TRIAL AURORA
(8812, never 8765) kept, since each step written by a model keeps its count of tokens and what they were made of
(the instruction, the memories, the recent conversation, the request). Written after the measure: it adds a
reading of what was measured, it changes no verdict.
Usage: python -X utf8 jetons_lus.py <combien de demandes remonter> <sortie.json>"""
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'mission-concours'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conversation_modeles import TOURS  # noqa: E402
from essai_api import appel  # noqa: E402

PHRASES = {tour['texte']: rang for rang, tour in enumerate(TOURS, 1)}
jeton = appel('/api/bootstrap')['token']
par_modele = {}
for run in appel(f'/api/runs?limit={int(sys.argv[1])}', jeton=jeton):
    texte = str(run.get('input_text') or '')
    tour = next((rang for phrase, rang in PHRASES.items() if texte.endswith(phrase)), None)
    if tour is None:
        continue
    for etape in appel(f"/api/runs/{run['id']}", jeton=jeton).get('steps') or []:
        resultat = etape.get('result_json') or etape.get('result') or {}
        if isinstance(resultat, str):
            resultat = json.loads(resultat or '{}')
        if resultat.get('timings') and resultat.get('input_tokens'):
            parts = {p['key']: p['tokens'] for p in (resultat.get('context') or {}).get('parts') or []}
            par_modele.setdefault(resultat.get('model'), {}).setdefault(tour, []).append(
                {'jetons': resultat['input_tokens'], 'lecture_ms': resultat['timings'].get('prompt_eval_ms'), 'parts': parts,
                 'ecrits': resultat.get('output_tokens')})
sortie = {}
for modele, tours in par_modele.items():
    sortie[modele] = {tour: {'fois': len(lus), 'jetons': round(statistics.median(l['jetons'] for l in lus)),
                             'consigne': round(statistics.median(l['parts'].get('system', 0) for l in lus)),
                             'lecture_ms': round(statistics.median(l['lecture_ms'] or 0 for l in lus)),
                             'lecture_min_ms': round(min(l['lecture_ms'] or 0 for l in lus)),
                             'lecture_max_ms': round(max(l['lecture_ms'] or 0 for l in lus)),
                             'ecrits': round(statistics.median(l['ecrits'] or 0 for l in lus))}
                      for tour, lus in sorted(tours.items())}
    print(modele, {tour: (v['fois'], v['jetons'], v['lecture_ms']) for tour, v in sortie[modele].items()})
with open(sys.argv[2], 'w', encoding='utf-8') as fichier:
    json.dump(sortie, fichier, ensure_ascii=False, indent=1)
