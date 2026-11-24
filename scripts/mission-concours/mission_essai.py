"""Real trial of the agent in the TRIAL AURORA (8812, temporary home): a contest-like mission on fictitious data.
Nothing touches the real AURORA (8765).  Usage: python -X utf8 mission_essai.py semer | lancer [texte] | retirer"""
import json
import os
import sys
import time

MARQUE = 'essai-mission'
SOUVENIRS = [
    ('Décision Orion : le capteur', 'Le 3 novembre 2026, pour le projet Orion, il a été décidé de garder le capteur de '
     'température DS18B20 plutôt que le DHT22, plus précis à basse température.', 'decision'),
    ('Décision Orion : le budget', 'Le 5 novembre 2026, le budget du projet Orion a été plafonné à 450 euros, '
     'batterie comprise.', 'decision'),
    ('Décision Orion : la démonstration', 'La démonstration du projet Orion est fixée au 12 décembre 2026, dans '
     'l’atelier.', 'decision'),
    ('Recette du pain', 'Pour le pain : 500 g de farine, 350 g d’eau, 10 g de sel, 5 g de levure.', 'fact'),
    ('Projet Lyra', 'Le projet Lyra est une station météo de jardin, sans rapport avec Orion.', 'project'),
]
NOTE_V1 = """Note de conception du projet Orion — version 1 (2 novembre 2026)
1. But : un boîtier autonome qui mesure la température d'une serre et prévient par un voyant.
2. Alimentation : une batterie de 2000 mAh, rechargée par un panneau solaire de 5 W.
3. Boîtier : imprimé en PLA, étanche aux éclaboussures.
4. Capteur : un DHT22 placé à 1,5 m du sol.
5. Autonomie visée : 3 jours sans soleil.
6. Calendrier : prototype prêt le 20 novembre 2026."""
NOTE_V2 = """Note de conception du projet Orion — version 2 (9 novembre 2026)
1. But : un boîtier autonome qui mesure la température d'une serre et prévient par un voyant.
2. Alimentation : une batterie de 3000 mAh, rechargée par un panneau solaire de 5 W.
3. Boîtier : imprimé en PETG, étanche aux éclaboussures.
4. Capteur : un DS18B20 placé à 1,5 m du sol.
5. Autonomie visée : 5 jours sans soleil.
6. Calendrier : prototype prêt le 28 novembre 2026.
7. Sécurité : un fusible de 2 A protège la batterie."""
MISSION = ('En mode agent : retrouve dans ta mémoire les décisions du projet Orion, compare les deux versions de la '
           'note de conception jointes, puis prépare un résumé en cinq points.')


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from essai_api import appel  # noqa: E402  (the call shared by the scripts of this folder)


def court(texte, taille=260):
    texte = ' '.join(str(texte or '').split())
    return texte if len(texte) <= taille else texte[:taille] + '…'


jeton = appel('/api/bootstrap')['token']
ordre = sys.argv[1]
if ordre == 'semer':
    deja = {m['title'] for m in appel('/api/memories', jeton=jeton)}
    for titre, detail, sorte in SOUVENIRS:
        if titre not in deja:
            appel('/api/memories', {'title': titre, 'detail': detail, 'category': MARQUE, 'kind': sorte}, jeton)
    print('souvenirs dans l’AURORA d’essai :', len(appel('/api/memories', jeton=jeton)))
    reglages = appel('/api/settings', jeton=jeton)
    print('mode :', reglages.get('mode'), '· modèle local :', reglages.get('local_model'), '· terminal :', reglages.get('shell_enabled'))
elif ordre == 'retirer':
    for m in appel('/api/memories', jeton=jeton):
        if m.get('category') == MARQUE:
            appel(f"/api/memories/{m['id']}", jeton=jeton, methode='DELETE')
    print('souvenirs d’essai retirés ; il en reste', len(appel('/api/memories', jeton=jeton)))
elif ordre == 'mission':   # the whole mission, with the keeping: « accorder » or « refuser » answers the card
    choix = sys.argv[2] if len(sys.argv) > 2 else 'accorder'
    debut = time.time()
    run = appel('/api/ask', {'text': MISSION.replace('en cinq points.', 'en cinq points et enregistre ce résumé dans ta mémoire.'),
                             'source': 'text', 'options': {'engine': 'local'},
                             'attachments': [{'name': 'note-orion-v1.txt', 'text': NOTE_V1},
                                             {'name': 'note-orion-v2.txt', 'text': NOTE_V2}]}, jeton)
    print(f"état après {time.time() - debut:.0f} s : {run.get('status')}")
    carte = run.get('permission')
    if carte and choix == 'attendre':   # the card is left on the screen: the person answers it in the interface
        print('carte laissée à l’écran, conversation n°', run.get('conversation_id'))
    elif carte:
        print('carte :', carte.get('action'), '·', court(carte.get('question') or carte.get('title'), 120))
        print('texte montré sur la carte :\n' + str(((carte.get('preview') or {}).get('params') or {}).get('content')))
        suite = appel(f"/api/permissions/{carte['id']}", {'decision': 'grant' if choix == 'accorder' else 'deny'}, jeton)
        print(f"après « {choix} » ({time.time() - debut:.0f} s) : {suite.get('status')}")
    gardes = [m for m in appel('/api/memories', jeton=jeton) if m.get('source') == 'conversation']
    print('souvenirs écrits par une demande :', [(m['id'], court(m['title'], 60), m.get('kind')) for m in gardes])
elif ordre in ('lancer', 'dernier'):
    texte = sys.argv[2] if len(sys.argv) > 2 else MISSION
    debut = time.time()
    if ordre == 'lancer':
        run = appel('/api/ask', {'text': texte, 'source': 'text', 'options': {'engine': 'local'},
                                 'attachments': [{'name': 'note-orion-v1.txt', 'text': NOTE_V1},
                                                 {'name': 'note-orion-v2.txt', 'text': NOTE_V2}]}, jeton)
        numero = appel('/api/runs?limit=1', jeton=jeton)[0]['id']
    else:
        dernier = appel('/api/runs?limit=1', jeton=jeton)[0]
        numero, run = dernier.get('id') or dernier.get('run_id'), {}
    detail = appel(f"/api/runs/{numero}", jeton=jeton)
    run = run or detail
    print('clés :', sorted(detail)[:30])
    print(f"demande n° {numero} · état {run.get('status')} · intention {(run.get('decision') or {}).get('intent')} · {time.time() - debut:.0f} s")
    for etape in detail.get('steps') or []:
        resultat = etape.get('result') or {}
        tour = resultat.get('agent') or {}
        ligne = f"  [{etape.get('status')}] {etape.get('title')}"
        if tour:
            ligne += f" → {tour.get('action') or '?'} ({tour.get('status')}) {court(tour.get('reason') or tour.get('note'), 150)}"
            if tour.get('params'):
                ligne += f" · params {court(json.dumps(tour['params'], ensure_ascii=False), 120)}"
        elif etape.get('tool'):
            ligne += f" · {court(resultat.get('summary') or etape.get('error'), 170)}"
        print(ligne)
    print('--- réponse ---')
    print(run.get('answer') or detail.get('answer'))
    print('--- contrôles ---')
    print(court(json.dumps((detail.get('verification') or run.get('verification') or {}), ensure_ascii=False), 700))
    print('origine :', court(json.dumps(detail.get('origin') or run.get('origin') or {}, ensure_ascii=False), 200))
