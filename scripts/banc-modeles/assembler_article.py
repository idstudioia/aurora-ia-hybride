"""Put the measured tables into the article: the figures of the page come from the result files, never typed by hand.
Reads preselection-resume.json, final-resume.json, fiches-modeles.json and the journal of the preselection in the
results folder, and writes them between the two marks of the page (/*DONNEES*/ ... /*FIN*/).
Usage: python -X utf8 assembler_article.py <dossier des résultats> <article.html>"""
import json
import os
import re
import sys

dossier, page = sys.argv[1], sys.argv[2]


def lire(nom):
    chemin = os.path.join(dossier, nom)
    return json.load(open(chemin, encoding='utf-8')) if os.path.exists(chemin) else []


fiches, preselection, final = lire('fiches-modeles.json'), lire('preselection-resume.json'), lire('final-resume.json')
journal = os.path.join(dossier, 'preselection-journal.txt')
journal = open(journal, encoding='utf-8-sig', errors='replace').read() if os.path.exists(journal) else ''
vus = {r['modele'] for r in preselection}
for fiche in fiches:   # a model with no result: stopped for being too slow, or never loaded
    if fiche['modele'] not in vus:
        arrete = re.search(re.escape(fiche['modele']) + r' : conversation ARR', journal) is not None
        preselection.append({'modele': fiche['modele'], 'arrete': arrete, 'erreur': not arrete})
places = lire('places-sur-la-carte.json') or {}   # measured apart by place_sur_la_carte.py: {model: [in memory, on the card]}
for ligne in preselection:
    if ligne.get('tours_ecrits_par_tirage') and not sum(ligne['tours_ecrits_par_tirage']):
        ligne['erreur'] = True   # the model never wrote: Ollama could not run it
    if ligne['modele'] in places:
        ligne['taille_go'], ligne['sur_la_carte_go'] = places[ligne['modele']]
suite = os.path.join(dossier, 'final-journal.txt')   # the final models in the order they were measured
suite = open(suite, encoding='utf-8-sig', errors='replace').read() if os.path.exists(suite) else ''
ordre = re.findall(r'^(\S+) : conversation', suite, flags=re.M)
final.sort(key=lambda r: ordre.index(r['modele']) if r['modele'] in ordre else 99)
donnees = json.dumps({'preselection': preselection, 'final': final, 'fiches': fiches, 'jetons': lire('jetons-lus.json') or {},
                      'avant_apres': lire('avant-apres.json')}, ensure_ascii=False)   # avant-apres.json: comparer.py
texte = open(page, encoding='utf-8').read()
nouveau, combien = re.subn(r'/\*DONNEES\*/.*?/\*FIN\*/', lambda _: '/*DONNEES*/' + donnees + '/*FIN*/', texte, count=1, flags=re.S)
if combien != 1:
    sys.exit('marques /*DONNEES*/ ... /*FIN*/ introuvables dans la page')
with open(page, 'w', encoding='utf-8', newline='\n') as fichier:
    fichier.write(nouveau)
print(f'{len(preselection)} modèles en présélection, {len(final)} en finale, {len(fiches)} fiches · page de {len(nouveau) // 1024} Ko')
