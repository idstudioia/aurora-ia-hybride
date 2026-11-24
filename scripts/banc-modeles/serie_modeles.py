"""The same bench for several local models, one after the other, in the TRIAL AURORA (8812, never 8765): for each
model, the others are taken off the graphics card, the trial AURORA is switched to it, then conversation_modeles.py
runs (and, when asked, the contest mission of scripts/mission-concours). The trial AURORA gets its first model back
at the end. A model that takes more than the time allowed is stopped and noted.

Rule of the preselection, written before its measure (22/11/2026): one draw per model; the models are ranked by the
number of turns passed out of 14, then by the median time of a whole request on the turns the model writes; a model
that does not hold entirely on the graphics card is put aside; qwen3.5:9b, the model AURORA runs today, is kept as
the reference whatever its rank, with the three best others.
Usage: python -X utf8 serie_modeles.py <tirages> <missions> <dossier> <préfixe> <modèle> [<modèle> ...]"""
import os
import subprocess
import sys
import time

ICI = os.path.dirname(os.path.abspath(__file__))
MISSIONS = os.path.join(ICI, '..', 'mission-concours')
sys.path.insert(0, MISSIONS)
sys.path.insert(0, ICI)
from conversation_modeles import modeles_charges  # noqa: E402  (what Ollama holds at that moment)
from essai_api import appel  # noqa: E402
DELAI_TIRAGE_S = 420   # per draw of the conversation, and per run of the mission


def nom_de_fichier(modele):
    return ''.join(c if c.isalnum() or c in '.-' else '_' for c in modele)


def liberer_la_carte():
    """Every chat model leaves the card (the embedding model stays: the memory of the trial AURORA uses it).
    With GARDER_LA_CARTE=1 nothing leaves it (22/11/2026: the real AURORA was in use, and only the verdicts of the
    checks were read): the times of such a run are not comparable with the others."""
    if os.environ.get('GARDER_LA_CARTE'):
        return
    for charge in modeles_charges():
        if 'bge' not in str(charge['nom']):
            subprocess.run(['ollama', 'stop', charge['nom']], capture_output=True, timeout=60)


def lancer(commande, journal, delai):
    """One script with a time limit; its output goes to a file. True when it ended by itself."""
    with open(journal, 'w', encoding='utf-8') as sortie:
        try:
            subprocess.run(commande, stdout=sortie, stderr=subprocess.STDOUT, timeout=delai, cwd=ICI)
            return True
        except subprocess.TimeoutExpired:
            sortie.write(f'\n*** arrêté après {delai} s ***\n')
            return False


if __name__ == '__main__':
    tirages, missions, dossier, prefixe = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3], sys.argv[4]
    os.makedirs(dossier, exist_ok=True)
    jeton = appel('/api/bootstrap')['token']
    depart = appel('/api/settings', jeton=jeton).get('local_model')
    python = [sys.executable, '-X', 'utf8']
    try:
        for modele in sys.argv[5:]:
            base = os.path.join(dossier, f'{prefixe}-{nom_de_fichier(modele)}')
            liberer_la_carte()
            appel('/api/settings', {'local_model': modele}, jeton, 'PATCH')
            debut = time.time()
            fini = lancer(python + [os.path.join(ICI, 'conversation_modeles.py'), str(tirages), base + '.json'],
                          base + '.txt', DELAI_TIRAGE_S * tirages)
            ligne = f'{modele} : conversation {"finie" if fini else "ARRÊTÉE"} en {time.time() - debut:.0f} s'
            if missions and fini:
                debut = time.time()
                fini = lancer(python + [os.path.join(MISSIONS, 'mission_serie.py'), str(missions)], base + '-mission.txt',
                              DELAI_TIRAGE_S * missions)
                ligne += f' · mission {"finie" if fini else "ARRÊTÉE"} en {time.time() - debut:.0f} s'
            print(ligne, flush=True)
    finally:
        liberer_la_carte()
        appel('/api/settings', {'local_model': depart}, jeton, 'PATCH')
        print('AURORA d’essai remise sur', appel('/api/settings', jeton=jeton).get('local_model'), flush=True)
