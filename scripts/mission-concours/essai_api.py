"""What the scripts of this folder share: one call to the TRIAL AURORA (service 8812, interface 5174, temporary
home). Never the real AURORA (8765): these scripts create and remove memories."""
import json
import urllib.request

BASE, ORIGINE = 'http://127.0.0.1:8812', 'http://127.0.0.1:5174'


def appel(chemin, corps=None, jeton=None, methode=None, delai=900):
    entetes = {'Origin': ORIGINE, 'Content-Type': 'application/json'}
    if jeton:
        entetes['X-Aurora-Token'] = jeton
    requete = urllib.request.Request(BASE + chemin, data=json.dumps(corps).encode('utf-8') if corps is not None else None,
                                     headers=entetes, method=methode or ('POST' if corps is not None else 'GET'))
    with urllib.request.urlopen(requete, timeout=delai) as reponse:
        texte = reponse.read().decode('utf-8')
        return json.loads(texte) if texte else None
