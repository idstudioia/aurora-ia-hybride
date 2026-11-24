"""What Ollama says of each local model, without loading it: family, size in parameters, quantization, the context it
was built for, and the first line of its licence.  Usage: python -X utf8 fiches_modeles.py <sortie.json> <modèle> ..."""
import json
import sys
import urllib.request

fiches = []
for modele in sys.argv[2:]:
    requete = urllib.request.Request('http://127.0.0.1:11434/api/show', data=json.dumps({'model': modele}).encode('utf-8'),
                                     headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(requete, timeout=30) as reponse:
            fiche = json.loads(reponse.read().decode('utf-8'))
    except OSError as erreur:   # a model Ollama cannot describe
        fiches.append({'modele': modele, 'erreur': str(erreur)})
        print(fiches[-1], flush=True)
        continue
    details, infos = fiche.get('details') or {}, fiche.get('model_info') or {}
    licence = next((ligne.strip() for ligne in str(fiche.get('license') or '').splitlines() if ligne.strip()), None)
    fiches.append({'modele': modele, 'famille': details.get('family'), 'parametres': details.get('parameter_size'),
                   'quantification': details.get('quantization_level'),
                   'contexte_prevu': next((v for k, v in infos.items() if k.endswith('.context_length')), None),
                   'capacites': fiche.get('capabilities'), 'licence': (licence or '')[:90]})
    print(fiches[-1], flush=True)
with open(sys.argv[1], 'w', encoding='utf-8') as fichier:
    json.dump(fiches, fichier, ensure_ascii=False, indent=1)
