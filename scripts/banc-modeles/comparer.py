"""Before and after a change of AURORA, with the same bench: for each model measured twice (two prefixes in the
results folder), the times, the turns passed, the signatures counted by lecture_apres.py and the missions, side by
side. The figures come from the result files; a summary goes to a JSON file for the article.
Usage: python -X utf8 comparer.py <dossier> <préfixe avant> <préfixe après> [sortie.json]"""
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from depouiller import mediane, missions_du_modele, resume_du_modele  # noqa: E402
from lecture_apres import defauts_du_modele  # noqa: E402
from serie_modeles import nom_de_fichier  # noqa: E402

SIGNATURE = 'récite son moteur ou sa consigne'


def lecture_par_tour(donnees):
    """Median time the model spent reading its prompt, per turn it wrote (ms)."""
    par_tour = {}
    for tirage in donnees['tirages']:
        for ligne in tirage:
            if ligne['temps']:
                par_tour.setdefault(ligne['tour'], []).append(ligne['temps'][-1].get('prompt_eval_ms'))
    return {tour: mediane(valeurs) for tour, valeurs in sorted(par_tour.items())}


def une_mesure(dossier, prefixe, modele):
    chemin = os.path.join(dossier, f'{prefixe}-{nom_de_fichier(modele)}.json')
    if not os.path.exists(chemin):
        return None
    donnees = json.load(open(chemin, encoding='utf-8'))
    resume = resume_du_modele(donnees)
    missions = missions_du_modele(chemin[:-5] + '-mission.txt')
    lecture = lecture_par_tour(donnees)
    return {'reussis': resume['reussis_par_tirage'], 'demande_s': resume['demande_s'], 'premier_mot_ms': resume['premier_mot_ms'],
            'lecture_ms': resume['lecture_ms'], 'debit': resume['debit'], 'conversation_s': resume['conversation_s'],
            'lecture_par_tour_ms': lecture, 'par_tour_s': resume['par_tour_s'],
            'lecture_totale_s': round(sum(v or 0 for v in lecture.values()) / 1000, 1),
            'signatures': defauts_du_modele(donnees)[SIGNATURE]['vus'], 'alertes': resume['nb_alertes'],
            'controles_rates': resume['controles_aurora_rates'], 'faits_du_resume': resume['faits_du_resume'],
            'missions_points': [m['points'] for m in missions], 'missions_s': [m['duree_s'] for m in missions],
            'missions_tours': [m['tours'] for m in missions]}


if __name__ == '__main__':
    dossier, avant, apres = sys.argv[1], sys.argv[2], sys.argv[3]
    journal = open(os.path.join(dossier, f'{apres}-journal.txt'), encoding='utf-8-sig', errors='replace').read()
    lignes = []
    for modele in dict.fromkeys(ligne.split(' : ')[0] for ligne in journal.splitlines() if ' : conversation' in ligne):
        deux = {'modele': modele, 'avant': une_mesure(dossier, avant, modele), 'apres': une_mesure(dossier, apres, modele)}
        if not deux['avant'] or not deux['apres']:
            continue
        lignes.append(deux)
        a, b = deux['avant'], deux['apres']
        print(f"{modele}\n   demande {a['demande_s']} → {b['demande_s']} s · 1er mot {a['premier_mot_ms']} → {b['premier_mot_ms']} ms · "
              f"lecture {a['lecture_ms']} → {b['lecture_ms']} ms · conversation {a['conversation_s']} → {b['conversation_s']} s\n"
              f"   réussis {a['reussis']} → {b['reussis']} · signatures {a['signatures']} → {b['signatures']} · alertes {a['alertes']} → "
              f"{b['alertes']} · contrôles ratés {a['controles_rates']} → {b['controles_rates']} · faits {a['faits_du_resume']} → {b['faits_du_resume']}\n"
              f"   lecture par tour : " + ' · '.join(f"{t}: {a['lecture_par_tour_ms'].get(t)} → {b['lecture_par_tour_ms'].get(t)}" for t in a['lecture_par_tour_ms'])
              + f"\n   missions : points {a['missions_points']} → {b['missions_points']} · durée {a['missions_s']} → {b['missions_s']} s")
    if len(sys.argv) > 4:
        with open(sys.argv[4], 'w', encoding='utf-8') as fichier:
            json.dump(lignes, fichier, ensure_ascii=False, indent=1)
