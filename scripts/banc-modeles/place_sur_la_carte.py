"""Does a model hold entirely on the graphics card? One short request to the model through the ollama command (its
usual context, as AURORA uses it), then what Ollama reports for it: its size in memory and the share on the card.
The model is taken off the card afterwards.  Usage: python -X utf8 place_sur_la_carte.py <modèle> [<modèle> ...]"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conversation_modeles import modeles_charges  # noqa: E402  (what Ollama holds at that moment)

for modele in sys.argv[1:]:
    subprocess.run(['ollama', 'run', modele, 'Réponds par un seul mot : bonjour.'], capture_output=True, timeout=400)
    for charge in modeles_charges():
        if charge['nom'] == modele:
            part = 100 * charge['sur_la_carte_go'] / charge['taille_go'] if charge['taille_go'] else 0
            print(f"{modele} : {charge['taille_go']} Go en mémoire, dont {charge['sur_la_carte_go']} Go sur la carte "
                  f"({part:.0f} %), contexte {charge['contexte']}", flush=True)
    subprocess.run(['ollama', 'stop', modele], capture_output=True, timeout=60)
