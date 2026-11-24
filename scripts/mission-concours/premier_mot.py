"""How long before the first word: several plain questions in a row to the TRIAL AURORA (8812, never 8765), with
the times the local model reports for each answer (kept with the step since 22/11/2026).
Usage: python -X utf8 premier_mot.py [combien]"""
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from essai_api import appel  # noqa: E402

QUESTIONS = ['Explique en deux phrases ce qu’est une étoile filante.', 'Pourquoi le ciel est-il bleu ? Réponds en deux phrases.',
             'En deux phrases, à quoi sert un fusible ?', 'Donne-moi en deux phrases la différence entre le PLA et le PETG.',
             'En deux phrases, pourquoi une batterie se décharge-t-elle ?', 'Explique en deux phrases ce qu’est un capteur.',
             'En deux phrases, à quoi sert un panneau solaire ?', 'Pourquoi fait-il plus froid en altitude ? Deux phrases.']

jeton = appel('/api/bootstrap')['token']
combien = int(sys.argv[1]) if len(sys.argv) > 1 else 6
lignes = []
for rang, question in enumerate(QUESTIONS[:combien], 1):
    debut = time.time()
    appel('/api/ask', {'text': question, 'source': 'text', 'options': {'engine': 'local'}}, jeton)
    total = time.time() - debut
    numero = appel('/api/runs?limit=1', jeton=jeton)[0]['id']
    temps = {}
    for etape in appel(f'/api/runs/{numero}', jeton=jeton)['steps']:
        resultat = etape.get('result_json') or {}
        if isinstance(resultat, str):
            resultat = json.loads(resultat or '{}')
        if resultat.get('timings'):
            temps = resultat['timings']
    lignes.append(temps)
    print(f"{rang}. premier mot {temps.get('ttft_ms')} ms · chargement {temps.get('load_ms')} ms · lecture de la "
          f"consigne {temps.get('prompt_eval_ms')} ms · {temps.get('tokens_per_s')} mots/s · demande entière {total:.1f} s", flush=True)
suite = [t for t in lignes[1:] if t.get('ttft_ms')]   # the first one may load the model
if suite:
    print('à partir de la 2e : premier mot médian', round(statistics.median(t['ttft_ms'] for t in suite)), 'ms ; lecture de '
          'la consigne médiane', round(statistics.median(t.get('prompt_eval_ms') or 0 for t in suite)), 'ms')
