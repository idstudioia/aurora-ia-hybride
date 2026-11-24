"""Read the answers of one result file of conversation_modeles.py, to check by eye what the code judged.
Usage: python -X utf8 lire_tirage.py <resultat.json> [tirage] [tours, ex. 8,12,14] [longueur]"""
import json
import sys

donnees = json.load(open(sys.argv[1], encoding='utf-8'))
tirage = int(sys.argv[2]) if len(sys.argv) > 2 else 1
voulus = {int(n) for n in sys.argv[3].split(',')} if len(sys.argv) > 3 and sys.argv[3] != 'tous' else None
longueur = int(sys.argv[4]) if len(sys.argv) > 4 else 500
print(donnees['modele'], '· tirage', tirage, '·', donnees.get('ollama'))
for ligne in donnees['tirages'][tirage - 1]:
    if voulus and ligne['tour'] not in voulus:
        continue
    temps = ligne['temps'][-1] if ligne['temps'] else {}
    print(f"\n--- {ligne['tour']}. {ligne['texte']}\n    [{'réussi' if ligne['reussi'] else 'RATÉ'}] route {ligne['route']} · {ligne['duree_s']} s · "
          f"{len(ligne['temps'])} écriture(s) · 1er mot {temps.get('ttft_ms')} · lecture {temps.get('prompt_eval_ms')} · "
          f"{temps.get('tokens_per_s')} /s · origine {ligne['origine']} · contrôles {ligne['controles_ok']} {ligne['controles_rates']}")
    print('    ' + ' '.join(ligne['reponse'].split())[:longueur])
