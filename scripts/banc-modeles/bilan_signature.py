"""Before and after the removal of the signature, per model, from the result files of the bench: written answers,
answers the counter of lecture_apres.py sees as signed (all, then without turn 13 « Qui es-tu ? »), signatures
AURORA took off (kept with the times of the step), what a wider pattern still finds, turns passed. Read only.
Usage: python -X utf8 bilan_signature.py <dossier> <préfixe avant> <préfixe après> [sortie.json]"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lecture_apres import defauts_du_modele  # noqa: E402
from serie_modeles import nom_de_fichier  # noqa: E402

LARGE = re.compile(r"(produite?s?|rédigée?s?|générée?s?|écrite?s?) par\s*:?\s*\**\s*(l['’]\s*)?IA locale|\[IA locale|"
                   r"(réponse|résumé)s? (est|sont) (produit|rédigé)\w* par", re.I)


def mesure(dossier, prefixe, modele):
    chemin = os.path.join(dossier, f'{prefixe}-{nom_de_fichier(modele)}.json')
    donnees = json.load(open(chemin, encoding='utf-8'))
    compte = defauts_du_modele(donnees)['récite son moteur ou sa consigne']
    ecrites = [(rang, ligne) for rang, tirage in enumerate(donnees['tirages'], 1) for ligne in tirage if ligne['temps']]
    restes = [f"t{rang}/{ligne['tour']}" for rang, ligne in ecrites if LARGE.search(ligne['reponse'])]
    return {'ecrites': len(ecrites), 'signees': compte['vus'],
            'signees_hors_13': len([lieu for lieu in compte['ou'] if not lieu.endswith('tour 13')]),
            'ou': [lieu.replace('tirage ', 't').replace(', tour ', '/') for lieu in compte['ou']],
            'retirees': sum(temps.get('signatures_retirees', 0) for _, ligne in ecrites for temps in ligne['temps']),
            'reponses_nettoyees': sum(1 for _, ligne in ecrites if any(t.get('signatures_retirees') for t in ligne['temps'])),
            'motif_large': restes,
            'reussis': [sum(1 for ligne in tirage if ligne['reussi']) for tirage in donnees['tirages']]}


if __name__ == '__main__':
    dossier, avant, apres = sys.argv[1], sys.argv[2], sys.argv[3]
    lignes = []
    for modele in ('qwen3.5:9b', 'gemma4:e2b', 'gemma4:e4b', 'granite4.2:8b'):
        deux = {'modele': modele, 'avant': mesure(dossier, avant, modele), 'apres': mesure(dossier, apres, modele)}
        lignes.append(deux)
        a, b = deux['avant'], deux['apres']
        print(f"{modele:15s} signées {a['signees']:2d} sur {a['ecrites']} → {b['signees']:2d} sur {b['ecrites']} · hors tour 13 : "
              f"{a['signees_hors_13']:2d} → {b['signees_hors_13']:2d} · retirées par le code {b['retirees']} dans "
              f"{b['reponses_nettoyees']} réponses · réussis {a['reussis']} → {b['reussis']}")
        print(f"{'':15s} encore vues : {b['ou']} · motif large : {b['motif_large']}")
    total = {cle: (sum(l['avant'][cle] for l in lignes), sum(l['apres'][cle] for l in lignes))
             for cle in ('ecrites', 'signees', 'signees_hors_13')}
    print('total', total, '· retirées', sum(l['apres']['retirees'] for l in lignes))
    if len(sys.argv) > 4:
        with open(sys.argv[4], 'w', encoding='utf-8') as fichier:
            json.dump(lignes, fichier, ensure_ascii=False, indent=1)
