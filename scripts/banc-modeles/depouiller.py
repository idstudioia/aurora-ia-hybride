"""The tables of the bench, from the result files of conversation_modeles.py: per model, how many turns are passed,
how steady the verdicts are from one draw to the next, and the times on the turns the model writes itself.
Usage: python -X utf8 depouiller.py <dossier> <préfixe> [resume.json]"""
import glob
import json
import os
import re
import statistics
import sys


def mediane(valeurs):
    valeurs = [v for v in valeurs if v is not None]
    return round(statistics.median(valeurs), 1) if valeurs else None


def resume_du_modele(donnees):
    """One line of the tables for one model, from its draws."""
    tirages = donnees['tirages']
    ecrits = [ligne for tirage in tirages for ligne in tirage if ligne['temps']]   # the turns the model wrote
    derniers = [ligne['temps'][-1] for ligne in ecrits]
    par_tour = {}
    for tirage in tirages:
        for ligne in tirage:
            par_tour.setdefault(ligne['tour'], []).append(ligne)
    charge = next((m for m in donnees.get('ollama') or [] if m['nom'] == donnees['modele']), {})
    return {
        'modele': donnees['modele'], 'tirages': len(tirages),
        'reussis_par_tirage': [sum(ligne['reussi'] for ligne in tirage) for tirage in tirages],
        'reussis_tours_ecrits': [sum(ligne['reussi'] for ligne in tirage if ligne['temps']) for tirage in tirages],
        'tours_ecrits_par_tirage': [sum(1 for ligne in tirage if ligne['temps']) for tirage in tirages],
        'par_tour': {tour: ''.join('1' if ligne['reussi'] else '0' for ligne in lignes) for tour, lignes in par_tour.items()},
        'tours_stables': sum(1 for lignes in par_tour.values() if len({ligne['reussi'] for ligne in lignes}) == 1),
        'alertes': sorted({f"tour {ligne['tour']} : {alerte}" for tirage in tirages for ligne in tirage for alerte in ligne['alertes']}),
        'nb_alertes': sum(len(ligne['alertes']) for tirage in tirages for ligne in tirage),
        'controles_aurora_rates': sum(1 for tirage in tirages for ligne in tirage if ligne['controles_ok'] is False),
        'premier_mot_ms': mediane(t.get('ttft_ms') for t in derniers),
        'lecture_ms': mediane(t.get('prompt_eval_ms') for t in derniers),
        'debit': mediane(t.get('tokens_per_s') for t in derniers),
        'ecriture_ms': mediane(t.get('eval_ms') for t in derniers),
        'par_tour_s': {tour: mediane(ligne['duree_s'] for ligne in lignes) for tour, lignes in par_tour.items()},
        'par_tour_premier_mot_ms': {tour: mediane(ligne['temps'][-1].get('ttft_ms') for ligne in lignes if ligne['temps'])
                                    for tour, lignes in par_tour.items()},
        'routes': {tour: sorted({str(ligne['route']) for ligne in lignes}) for tour, lignes in par_tour.items()},
        'ecrit_par_tour': {tour: sum(1 for ligne in lignes if ligne['temps']) for tour, lignes in par_tour.items()},
        'route_ok_par_tour': {tour: sum(1 for ligne in lignes if ligne['route_ok']) for tour, lignes in par_tour.items()},
        'faits_du_resume': [len(ligne.get('trouves') or []) for tirage in tirages for ligne in tirage if ligne['tour'] == 9],
        'demande_s': mediane(ligne['duree_s'] for ligne in ecrits),
        'demande_etendue_s': [min((ligne['duree_s'] for ligne in ecrits), default=None), max((ligne['duree_s'] for ligne in ecrits), default=None)],
        'chargement_s': round(max((t.get('load_ms') or 0 for ligne in ecrits for t in ligne['temps']), default=0) / 1000, 1),
        'conversation_s': [round(sum(ligne['duree_s'] for ligne in tirage), 1) for tirage in tirages],
        'longueur': mediane(len(ligne['reponse']) for ligne in ecrits),
        'taille_go': charge.get('taille_go'), 'sur_la_carte_go': charge.get('sur_la_carte_go'), 'contexte': charge.get('contexte'),
    }


def missions_du_modele(chemin):
    """What mission_serie.py printed for one model: per run, its duration, its turns, the points named out of 13,
    whether the card to keep the summary came, and whether AURORA's own checks passed."""
    if not os.path.exists(chemin):
        return []
    texte, runs = open(chemin, encoding='utf-8').read(), []
    for bloc in texte.split('=== tirage ')[1:]:
        duree = re.search(r': (\d+) s, état final (\w+), (\d+) tours', bloc)
        points = re.search(r'nommés : (\d+) sur (\d+)', bloc)
        runs.append({'duree_s': int(duree.group(1)) if duree else None, 'etat': duree.group(2) if duree else None,
                     'tours': int(duree.group(3)) if duree else None, 'points': int(points.group(1)) if points else None,
                     'sur': int(points.group(2)) if points else None, 'carte': 'carte : oui' in bloc,
                     'controles': 'contrôles : True' in bloc, 'renvois': bloc.count('renvoi :')})
    return runs


if __name__ == '__main__':
    lignes = []
    for chemin in sorted(glob.glob(os.path.join(sys.argv[1], sys.argv[2] + '-*.json'))):
        if chemin.endswith('resume.json'):
            continue
        donnees = json.load(open(chemin, encoding='utf-8'))
        if donnees.get('tirages'):
            lignes.append(resume_du_modele(donnees))
            lignes[-1]['missions'] = missions_du_modele(chemin[:-5] + '-mission.txt')
    lignes.sort(key=lambda r: (-statistics.mean(r['reussis_par_tirage']), r['demande_s'] or 9999))
    for r in lignes:
        print(f"{r['modele'][:44]:44s} réussis {r['reussis_par_tirage']} (écrits {r['reussis_tours_ecrits']} sur {r['tours_ecrits_par_tirage']}) · "
              f"stables {r['tours_stables']}/14 · demande {r['demande_s']} s {r['demande_etendue_s']} · 1er mot {r['premier_mot_ms']} ms · "
              f"lecture {r['lecture_ms']} ms · {r['debit']} /s · chargement {r['chargement_s']} s · conversation {r['conversation_s']} s · "
              f"{r['sur_la_carte_go']}/{r['taille_go']} Go ctx {r['contexte']} · alertes {r['nb_alertes']} · contrôles ratés {r['controles_aurora_rates']}")
        print('   par tour :', ' '.join(f"{tour}:{vu}" for tour, vu in sorted(r['par_tour'].items())), '·', r['alertes'] or '')
        if r['missions']:
            print('   missions :', [(m['duree_s'], m['tours'], f"{m['points']}/{m['sur']}", 'carte' if m['carte'] else 'SANS carte',
                                     m['controles'], m['renvois']) for m in r['missions']])
    if len(sys.argv) > 3:
        with open(sys.argv[3], 'w', encoding='utf-8') as fichier:
            json.dump(lignes, fichier, ensure_ascii=False, indent=1)
