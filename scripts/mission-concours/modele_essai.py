"""The local model of the TRIAL AURORA (8812, never 8765): read it, or change it for a comparison.
Usage: python -X utf8 modele_essai.py [nom du modèle]"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from essai_api import appel  # noqa: E402

jeton = appel('/api/bootstrap')['token']
if len(sys.argv) > 1:
    appel('/api/settings', {'local_model': sys.argv[1]}, jeton, 'PATCH')
print('modèle local de l’AURORA d’essai :', appel('/api/settings', jeton=jeton).get('local_model'))
