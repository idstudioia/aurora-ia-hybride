"""A 14-turn conversation with the TRIAL AURORA (8812, never 8765) for the local model it runs at that moment.
Several draws, each in a new conversation. What every answer must show was written BEFORE the first measure: the
route expected, what the answer must name, what it must not say. The sentences are built for this bench (not typed
by the user), except turn 3, taken from the routing corpus. Raw results go to a JSON file.
Usage: python -X utf8 conversation_modeles.py <tirages> <sortie.json>"""
import datetime
import json
import os
import re
import sys
import time
import urllib.request

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ICI, '..', 'mission-concours'))
from essai_api import appel  # noqa: E402  (the call shared with the mission scripts: the TRIAL AURORA only)

MOIS = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre']
FICHE = """Fiche technique — pompe d'arrosage Halo (version du 3 novembre 2026)
1. Rôle : arroser une serre de 12 m² à partir d'une cuve de 300 litres.
2. Pompe : 12 V, débit de 240 litres par heure, hauteur de refoulement de 3,5 m.
3. Alimentation : une batterie au plomb de 7 Ah, rechargée par un panneau solaire de 20 W.
4. Tuyau : diamètre intérieur de 13 mm, longueur de 8 m.
5. Programme : un arrosage par jour à 6 h 30, pendant 12 minutes.
6. Capteur d'humidité du sol : la pompe ne démarre pas si l'humidité dépasse 35 %.
7. Protection : la pompe ne démarre pas non plus si la batterie descend sous 11,5 V.
8. Sécurité : un fusible de 5 A protège la pompe ; le panneau solaire a sa propre diode.
9. Entretien : nettoyer le filtre tous les 30 jours."""
FAITS = {'240 l/h': r'240', '3,5 m': r'3[,.]5\s?m', '7 Ah': r'7\s?Ah', '20 W': r'20\s?W', '13 mm': r'13\s?mm',
         '6 h 30': r'6\s?h\s?30|0?6[:h]30', '12 minutes': r'12\s?min', '35 %': r'35\s?%', '11,5 V': r'11[,.]5\s?V', '5 A': r'\b5\s?A\b'}
THEMES = {'panneau solaire': r'panneau|solaire', 'RAM': r'\bRAM\b|mémoire vive', 'projet Vega': r'Vega|démonstration',
          'pompe Halo': r'pompe|Halo|arrosage|fiche', 'Monte-Cristo': r'Monte.?Cristo|Dumas'}
SALUTS = ['Bonjour AURORA.', 'Salut AURORA.', 'Bonsoir AURORA.', 'Coucou AURORA.', 'AURORA, une question.']
# Each turn: the sentence, the route expected (None: not judged), what the answer must name (all of them), what it
# must not say, and for two turns a count with its threshold.
TOURS = [
    {'texte': 'Explique-moi en trois phrases comment fonctionne un panneau solaire.', 'route': 'chat.general',
     'doit': {'la lumière': r'lumi[èe]re|soleil|photon', 'l’électricité': r'électri|courant'}},
    {'texte': 'Et la nuit, il produit encore ?', 'route': 'chat.general',
     'doit': {'parle du panneau': r'panneau|solaire|photovolta|lumi[èe]re|soleil', 'dit non': r'\bnon\b|\bpas\b|ne produi|aucun|\bplus\b'}},
    {'texte': 'Quelle quantité de RAM est utilisée ?', 'route': 'system.status', 'doit': {'une mesure': r'\d+[,.]?\d*\s*(Go|GB|%)'}},
    {'texte': 'Calcule 2 500 × 3', 'route': 'calc.evaluate', 'doit': {'7 500': r'7[\s.]?500'}},
    {'texte': 'Retiens que la démonstration du projet Vega aura lieu le 14 janvier à 15 h dans l’atelier.', 'route': 'memory.save',
     'doit': {'dit avoir retenu': r'reten|enregistr|mémoris|noté|souvenir'}},
    {'texte': 'As-tu accès à Internet ?', 'route': 'meta.capabilities', 'doit': {'répond sur l’accès': r'\boui\b|accès|connect|recherche'}},
    {'texte': 'Quel jour sommes-nous ?', 'route': 'time.now', 'doit': {'la date du jour': None}},
    {'texte': 'C’est quand, la démonstration du projet Vega ?', 'route': 'memory.search',
     'doit': {'14 janvier': r'14\s+janvier', '15 h': r'15\s?h'}},
    {'texte': 'Résume ce document en cinq points.', 'route': 'doc.analyze', 'joint': True, 'compte': FAITS, 'seuil': 6},
    {'texte': 'D’après ce document, dans quels cas la pompe ne démarre-t-elle pas ?', 'route': 'doc.analyze', 'joint': True,
     'doit': {'humidité au-dessus de 35 %': r'35\s?%', 'batterie sous 11,5 V': r'11[,.]5\s?V'}},
    {'texte': 'En quelle année est paru Le Comte de Monte-Cristo ?', 'route': 'chat.general', 'doit': {'1844 à 1846': r'184[4-6]'},
     'interdit': {'une autre année de parution': r'\b18(3\d|4[0-3]|4[7-9]|5\d)\b'}},
    {'texte': 'Quel est le numéro de série de ma carte graphique ?', 'route': None,
     'doit': {'dit ne pas l’avoir': r"ne (peux|sais|dispose|connais|mesure)|n['’]ai pas|pas (accès|disponible|mesur)|indisponible|impossible|aucun outil|inconnu"},
     'interdit': {'un numéro inventé': r'\b(?=[A-Z0-9-]*\d)(?=[A-Z0-9-]*[A-Z])[A-Z0-9-]{10,}\b'}},
    {'texte': 'Qui es-tu ? Réponds en une phrase.', 'route': None, 'doit': {'se nomme AURORA': r'aurora'}},
    {'texte': 'Résume en trois phrases ce dont on a parlé depuis le début.', 'route': 'chat.general', 'compte': THEMES, 'seuil': 3},
]
# Read in every answer, whatever the turn: counted apart, as alerts to read by hand.
ALERTES = {
    'nie Internet ou ses outils': r"je n['’]ai pas (d['’])?accès (à|a) (internet|l['’]internet|vos|tes|ton|votre)|je ne peux pas "
                                  r"(accéder|naviguer|consulter (internet|le web))|\bhors ligne\b|pas connecté à internet",
    'se présente comme un autre': r"je suis (qwen|gemma|granite|phi|mistral|chatgpt|claude|un (grand )?modèle de langage)|"
                                  r"(développé|créé|entraîné)e? par (alibaba|google|ibm|openai|microsoft|mistral)",
    'autre écriture': r'[一-鿿぀-ヿЀ-ӿ]',
    'balise de raisonnement': r'</?think>',
}


def controler(tour, reponse, route):
    """What is wrong with one answer, by the rules written above (an empty dict of faults: the turn is passed)."""
    jour = datetime.date.today()
    doit = dict(tour.get('doit') or {})
    if 'la date du jour' in doit:
        doit['la date du jour'] = rf'\b{jour.day}(er)?\s+{MOIS[jour.month - 1]}'
    bilan = {'route_ok': tour['route'] is None or route == tour['route'],
             'manque': [nom for nom, motif in doit.items() if not re.search(motif, reponse, re.I | re.S)],
             'interdits': [nom for nom, motif in (tour.get('interdit') or {}).items() if re.search(motif, reponse, re.S)],
             'alertes': [nom for nom, motif in ALERTES.items() if re.search(motif, reponse, re.I | re.S)]}
    if tour.get('compte'):
        bilan['trouves'] = [nom for nom, motif in tour['compte'].items() if re.search(motif, reponse, re.I | re.S)]
        if len(bilan['trouves']) < tour['seuil']:
            bilan['manque'].append(f"{len(bilan['trouves'])} sur {len(tour['compte'])}, moins de {tour['seuil']}")
    bilan['reussi'] = bool(reponse.strip()) and bilan['route_ok'] and not bilan['manque'] and not bilan['interdits']
    return bilan


def temps_des_etapes(detail):
    """The times the local model reported for each of its answers in one run (several when it wrote twice)."""
    tous = []
    for etape in detail.get('steps') or []:
        resultat = etape.get('result_json') or etape.get('result') or {}
        if isinstance(resultat, str):
            resultat = json.loads(resultat or '{}')
        if resultat.get('timings'):
            # 22/11/2026: what AURORA took off the answer of the model (core/prompts.unsigned) is kept with its times.
            # A measure added, not an expectation: no verdict of a turn reads it.
            retirees = resultat.get('signature_removed')
            reecrits = resultat.get('notation_rewritten')   # the same for the notation (core/prompts.plain_notation)
            tous.append({**resultat['timings'], **({'signatures_retirees': retirees} if retirees else {}),
                         **({'notation_reecrite': reecrits} if reecrits else {})})
    return tous


def modeles_charges():
    """What Ollama holds at that moment: each model, its size, its share on the graphics card, its context."""
    with urllib.request.urlopen('http://127.0.0.1:11434/api/ps', timeout=10) as reponse:
        charges = json.loads(reponse.read().decode('utf-8')).get('models') or []
    return [{'nom': m.get('name'), 'taille_go': round((m.get('size') or 0) / 1e9, 2),
             'sur_la_carte_go': round((m.get('size_vram') or 0) / 1e9, 2), 'contexte': m.get('context_length')} for m in charges]


def un_tirage(jeton, rang):
    for souvenir in appel('/api/memories', jeton=jeton):   # what a draw stopped half-way may have left in the trial base
        if 'Vega' in str(souvenir.get('title')) + str(souvenir.get('detail')):
            appel(f"/api/memories/{souvenir['id']}", jeton=jeton, methode='DELETE')
    avant = {m['id'] for m in appel('/api/memories', jeton=jeton)}
    conversation, lignes = None, []
    for numero, tour in enumerate(TOURS, 1):
        texte = (SALUTS[(rang - 1) % len(SALUTS)] + ' ' + tour['texte']) if numero == 1 else tour['texte']
        corps = {'text': texte, 'source': 'text', 'options': {'engine': 'local'}, 'conversation_id': conversation}
        if tour.get('joint'):
            corps['attachments'] = [{'name': 'fiche-pompe-halo.txt', 'text': FICHE}]
        debut = time.time()
        run = appel('/api/ask', corps, jeton)
        carte = run.get('permission')
        if carte:   # a card: only the keeping of a memory is granted, anything else is refused
            accord = 'grant' if str(carte.get('action') or '').startswith('memory') else 'deny'
            run = appel(f"/api/permissions/{carte['id']}", {'decision': accord}, jeton)
        duree = time.time() - debut
        conversation = run.get('conversation_id') or conversation
        dernier = appel('/api/runs?limit=1', jeton=jeton)[0]
        detail = appel(f"/api/runs/{dernier['id']}", jeton=jeton)
        reponse = str(run.get('answer') or detail.get('result_text') or '')
        controle = detail.get('verification') or run.get('verification') or {}
        ligne = {'tour': numero, 'texte': texte, 'route': dernier.get('intent'), 'etat': run.get('status') or dernier.get('status'),
                 'moteur': dernier.get('engine'), 'duree_s': round(duree, 2), 'temps': temps_des_etapes(detail),
                 'origine': detail.get('origin') or run.get('origin'), 'controles_ok': controle.get('ok'),
                 'controles_rates': [c.get('name') for c in controle.get('checks') or [] if not c.get('ok')],
                 'carte': (carte or {}).get('action'), 'reponse': reponse}
        ligne.update(controler(tour, reponse, ligne['route']))
        lignes.append(ligne)
        premier = (ligne['temps'][-1].get('ttft_ms') if ligne['temps'] else None)
        print(f"  {numero:2d}. {'ok ' if ligne['reussi'] else 'NON'} {duree:5.1f} s · 1er mot {premier} ms · {ligne['route']} · "
              f"manque {ligne['manque'] or '-'} · interdits {ligne['interdits'] or '-'} · alertes {ligne['alertes'] or '-'}", flush=True)
    for souvenir in appel('/api/memories', jeton=jeton):   # the trial memories do not pile up from one draw to the next
        if souvenir['id'] not in avant:
            appel(f"/api/memories/{souvenir['id']}", jeton=jeton, methode='DELETE')
    if conversation:
        appel(f'/api/conversations/{conversation}', jeton=jeton, methode='DELETE')
    return lignes


if __name__ == '__main__':
    jeton = appel('/api/bootstrap')['token']
    modele = appel('/api/settings', jeton=jeton).get('local_model')
    sortie = {'modele': modele, 'date': datetime.datetime.now().isoformat(timespec='seconds'), 'tirages': []}
    for rang in range(1, int(sys.argv[1]) + 1):
        print(f'=== {modele} · tirage {rang}', flush=True)
        sortie['tirages'].append(un_tirage(jeton, rang))
        sortie['ollama'] = modeles_charges()
        with open(sys.argv[2], 'w', encoding='utf-8') as fichier:
            json.dump(sortie, fichier, ensure_ascii=False, indent=1)
    print('modèles chargés :', sortie.get('ollama'))
