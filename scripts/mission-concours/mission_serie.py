"""Several runs of the contest mission in the TRIAL AURORA (8812), scored by code: which differences of the two
notes and which decisions of the memory the answer names, how many turns, how long, and what the card kept.
Usage: python -X utf8 mission_serie.py <tirages> [accorder|refuser]"""
import os
import re
import sys
import time

DOSSIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DOSSIER)
from essai_api import appel  # noqa: E402  (the TRIAL AURORA only, never 8765)
ATTENDU = {   # what a right answer names (each read on the two notes or in the three memories)
    'batterie 2000 → 3000 mAh': r'2000.{0,80}3000', 'boîtier PLA → PETG': r'PLA.{0,80}PETG',
    'capteur DHT22 → DS18B20': r'DHT22.{0,120}DS18B20|DS18B20.{0,120}DHT22', 'autonomie 3 → 5 jours': r'\b3 jours.{0,80}5 jours',
    'prototype 20 → 28 novembre': r'20 novembre.{0,80}28 novembre', 'fusible de 2 A': r'fusible.{0,30}2 ?A',
    'décision : budget de 450 €': r'450', 'décision : démonstration le 12 décembre': r'12 décembre',
}


def notes():
    source = open(DOSSIER + '\\mission_essai.py', encoding='utf-8').read()
    v1 = re.search(r'NOTE_V1 = """(.*?)"""', source, re.S).group(1)
    v2 = re.search(r'NOTE_V2 = """(.*?)"""', source, re.S).group(1)
    return v1, v2


jeton = appel('/api/bootstrap')['token']
v1, v2 = notes()
choix = sys.argv[2] if len(sys.argv) > 2 else 'accorder'
DEMANDE = open(DOSSIER + '\\demande_demo.txt', encoding='utf-8').read().strip()   # the sentence of the demonstration
if len(sys.argv) > 3:   # another wording of the mission
    DEMANDE = sys.argv[3]
ATTENDU.update({'état final : 3000 mAh': r'3000', 'état final : PETG': r'PETG', 'état final : DS18B20': r'DS18B20',
                'état final : 5 jours': r'5 jours', 'état final : 28 novembre': r'28 novembre'})
for tirage in range(1, int(sys.argv[1]) + 1):
    avant = {m['id'] for m in appel('/api/memories', jeton=jeton)}
    debut = time.time()
    run = appel('/api/ask', {'text': DEMANDE, 'source': 'text', 'options': {'engine': 'local'},
                             'attachments': [{'name': 'note-orion-v1.txt', 'text': v1},
                                             {'name': 'note-orion-v2.txt', 'text': v2}]}, jeton)
    duree = time.time() - debut
    carte = run.get('permission')
    reponse = str(run.get('answer') or '')
    garde = str(((carte or {}).get('preview') or {}).get('params', {}).get('content') or '')
    if carte:
        run = appel(f"/api/permissions/{carte['id']}", {'decision': 'grant' if choix == 'accorder' else 'deny'}, jeton)
    reponse = str(run.get('answer') or reponse or garde)   # the answer is on the run once it is finished
    nouveaux = [m for m in appel('/api/memories', jeton=jeton) if m['id'] not in avant]
    histoire = [(h.get('action') or h.get('title'), h.get('status')) for h in (run.get('agent') or {}).get('history') or []]
    for h in (run.get('agent') or {}).get('history') or []:
        if h.get('status') == 'refused':
            print('   renvoi :', ' '.join(str(h.get('summary') or '').split())[:170])
    trouves = [nom for nom, motif in ATTENDU.items() if re.search(motif, reponse, re.S | re.I)]
    manque = [nom for nom in ATTENDU if nom not in trouves]
    print(f"\n=== tirage {tirage} : {duree:.0f} s, état final {run.get('status')}, {len(histoire)} tours {histoire}")
    print(f"   nommés : {len(trouves)} sur {len(ATTENDU)} ; manque : {manque or 'rien'}")
    print(f"   carte : {'oui, ' + str(len(garde)) + ' caractères' if carte else 'NON'} ; question retirée du texte gardé : "
          f"{'?' not in garde[-80:]} ; souvenirs écrits : {[(m['id'], m.get('kind')) for m in nouveaux]}")
    controle = (run.get('verification') or {})
    print('   contrôles :', controle.get('ok'), [c['name'] for c in controle.get('checks') or [] if not c.get('ok')])
    for m in nouveaux:   # the trial memories do not pile up
        appel(f"/api/memories/{m['id']}", jeton=jeton, methode='DELETE')
