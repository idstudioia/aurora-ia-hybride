"""Add to the file of tokens read (jetons-lus.json) the models of a second reading, without losing the first ones:
the TRIAL AURORA only gives back its latest requests, so a model measured later is read apart and merged here.
Usage: python -X utf8 ajouter_jetons.py <jetons-lus.json> <nouvelle-lecture.json> <modèle> [<modèle> ...]"""
import json
import sys

garde = json.load(open(sys.argv[1], encoding='utf-8'))
nouveau = json.load(open(sys.argv[2], encoding='utf-8'))
for modele in sys.argv[3:]:
    if modele in nouveau:
        garde[modele] = nouveau[modele]
        print(modele, ':', {tour: (v['fois'], v['jetons'], v['lecture_max_ms']) for tour, v in nouveau[modele].items()})
    else:
        print(modele, ': absent de la nouvelle lecture')
with open(sys.argv[1], 'w', encoding='utf-8') as fichier:
    json.dump(garde, fichier, ensure_ascii=False, indent=1)
