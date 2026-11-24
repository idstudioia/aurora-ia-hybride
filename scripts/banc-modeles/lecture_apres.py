"""What the reading by hand added AFTER the measure (22/11/2026): faults seen in the answers that the expectations
written before the measure did not look for. They are counted here by code so that every answer is read the same
way, and they are reported apart in the article: they never change the 'passed' verdict of a turn.
Only the turns a model wrote are read. The counts are added to the summary file under 'apres_lecture'.
Usage: python -X utf8 lecture_apres.py <dossier> <préfixe> <resume.json>"""
import glob
import json
import os
import re
import sys

JOURS = r'\b(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)\b'
# name of the fault -> (the turns it is looked for in, or None for every written turn; the pattern)
DEFAUTS = {
    'récite son moteur ou sa consigne': (None, r"produite? par\s*:?\s*(l['’])?\s*IA locale|modèle (qwen|gemma|granite)[\w.:-]+|"
                                               r"le format exige|je vais répondre|\(Note\s*:|selon (ma|la) consigne"),
    'cite un numéro interne ou vide': (None, r'\[n\]|Souvenir\s+n?°?\s*\d+'),
    'jour ou année inventés (tour 8)': ([8], JOURS + r'|janvier\s+20\d\d'),
    'ajoute un cas absent de la fiche (tour 10)': ([10], r'fusible|filtre|diode|trois cas|\b3\.\s'),
    'réponse démesurée (plus de 1 200 caractères pour une date)': ([11], r'(?s)^.{1200,}'),
}


def defauts_du_modele(donnees):
    """Per fault: in how many written answers it is seen, out of how many it was looked for, and where."""
    bilan = {}
    for nom, (tours, motif) in DEFAUTS.items():
        vus, lus, ou = 0, 0, []
        for rang, tirage in enumerate(donnees['tirages'], 1):
            for ligne in tirage:
                if not ligne['temps'] or (tours and ligne['tour'] not in tours):
                    continue
                lus += 1
                if re.search(motif, ligne['reponse'], re.I):
                    vus += 1
                    ou.append(f"tirage {rang}, tour {ligne['tour']}")
        bilan[nom] = {'vus': vus, 'lus': lus, 'ou': ou}
    return bilan


if __name__ == '__main__':
    resume = json.load(open(sys.argv[3], encoding='utf-8'))
    for chemin in sorted(glob.glob(os.path.join(sys.argv[1], sys.argv[2] + '-*.json'))):
        if chemin.endswith('resume.json'):
            continue
        donnees = json.load(open(chemin, encoding='utf-8'))
        if not donnees.get('tirages'):
            continue
        bilan = defauts_du_modele(donnees)
        print(donnees['modele'])
        for nom, compte in bilan.items():
            print(f"   {compte['vus']:2d} sur {compte['lus']:2d} · {nom} · {compte['ou'] or ''}")
        for ligne in resume:
            if ligne['modele'] == donnees['modele']:
                ligne['apres_lecture'] = bilan
    with open(sys.argv[3], 'w', encoding='utf-8') as fichier:
        json.dump(resume, fichier, ensure_ascii=False, indent=1)
