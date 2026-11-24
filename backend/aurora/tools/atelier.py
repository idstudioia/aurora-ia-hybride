"""L'atelier d'AURORA (15/11/2026). ID Studio IA : « toutes les pièces d'un coup […] ou par étapes, en mode progressif […] avec
une ligne de progression, style animation, on voit que ça travaille petit à petit jusqu'à arriver à la finalité du
robot. Par AURORA, bien sûr. »

AURORA lance le programme de fabrication d'UN projet (tools/projets.programme : le premier .py de sa rubrique
« Fabrication », seulement pour un projet rangé dans son propre dossier) et suit ce qu'il dit, ligne par ligne :
    AURORA-ETAPE {"n": 3, "sur": 13, "quoi": "Dôme et bac du corps"}     au début de chaque étape
    AURORA-FIN {"modele": "sortie/robot/….glb", "resume": "…", …}        à la fin
Le reste de sa sortie est gardé comme journal (les dernières lignes).

Ce que ce module ne fait pas : il ne passe AUCUN argument au programme, ne lance qu'un programme à la fois par
projet, ne lit pas le clavier, et coupe un programme qui dure trop. Le programme tourne avec le Python d'AURORA,
isolé (« -I »), sans les variables d'AURORA (son jeton, sa base). Une construction affichée « finie » l'est : le
programme est sorti sans erreur ET a dit sa ligne de fin.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import projets
from .apps import launch_env
from .base import now_iso

log = logging.getLogger('aurora.tools.atelier')

MARK_STEP, MARK_END = 'AURORA-ETAPE ', 'AURORA-FIN '
TIMEOUT_S = 600.0
JOURNAL = 30                 # lignes gardées
_LOCK = threading.Lock()
_JOBS: dict[str, dict] = {}  # identifiant du projet → sa dernière construction (en mémoire : perdue à l'arrêt du service)


def _vue(job: dict) -> dict:
    """Ce que l'interface reçoit : une copie, avec la durée lue à l'horloge."""
    fin = job['t_fin'] if job['t_fin'] is not None else time.monotonic()
    return {'projet': job['projet'], 'titre': job['titre'], 'programme': job['programme'], 'etat': job['etat'],
            'n': job['n'], 'sur': job['sur'], 'quoi': job['quoi'], 'etapes': list(job['etapes']),
            'journal': list(job['journal'][-12:]), 'debut': job['debut'], 'secondes': round(fin - job['t_debut'], 1),
            'resultat': dict(job['resultat']) if job['resultat'] else None, 'erreur': job['erreur']}


def _inside(base: Path, value) -> str | None:
    """Un chemin dit par le programme, gardé seulement s'il reste dans le dossier du projet et que le fichier existe."""
    try:
        relative = projets._stays_inside(str(value))
    except ValueError:
        return None
    target = (base / relative).resolve()
    return relative if base in target.parents and target.is_file() else None


def _result(base: Path, said) -> dict | None:
    """La ligne de fin du programme, réduite à ce qu'AURORA comprend ; None si elle ne nomme pas un modèle qui existe."""
    if not isinstance(said, dict):
        return None
    modele = _inside(base, said.get('modele'))
    if modele is None:
        return None
    out = {'modele': modele, 'taille': (base / modele).stat().st_size, 'resume': str(said.get('resume') or '')[:400]}
    rapport = _inside(base, said.get('rapport'))
    if rapport:
        out['rapport'] = rapport
    for key in ('pieces', 'coques', 'masse_g', 'moteurs_qui_depassent'):
        if isinstance(said.get(key), int) and not isinstance(said.get(key), bool):
            out[key] = said[key]
    return out


def _follow(job: dict, proc: subprocess.Popen, base: Path) -> None:
    """Lit la sortie du programme jusqu'à sa fin et tient l'état à jour. Tourne dans son propre fil."""
    killer = threading.Timer(TIMEOUT_S, proc.kill)
    killer.daemon = True
    killer.start()
    said_end = None
    try:
        for raw in proc.stdout:
            line = raw.rstrip('\r\n')
            if line.startswith(MARK_STEP):
                try:
                    step = json.loads(line[len(MARK_STEP):])
                    n, total, what = int(step['n']), int(step['sur']), str(step['quoi'])[:80]
                except (ValueError, KeyError, TypeError):
                    continue
                if 1 <= n <= total <= 200:
                    with _LOCK:
                        job.update(n=n, sur=total, quoi=what)
                        job['etapes'].append(what)
            elif line.startswith(MARK_END):
                try:
                    said_end = json.loads(line[len(MARK_END):])
                except ValueError:
                    said_end = None
            elif line.strip():
                with _LOCK:
                    job['journal'].append(line[:300])
                    del job['journal'][:-JOURNAL]
        code = proc.wait()
    except Exception:
        log.exception('Atelier : lecture du programme interrompue')
        proc.kill()
        code = proc.wait()
    finally:
        killer.cancel()
    result = _result(base, said_end) if code == 0 else None
    with _LOCK:
        job['t_fin'] = time.monotonic()
        if result:
            job.update(etat='fini', resultat=result, n=job['sur'] or job['n'])
        else:
            lasted = job['t_fin'] - job['t_debut']
            why = ('il a duré plus de dix minutes et a été arrêté' if lasted >= TIMEOUT_S else
                   f'il s’est arrêté avec une erreur (code {code})' if code else
                   'il n’a pas dit sa ligne de fin, ou le modèle qu’elle nomme n’existe pas')
            last = job['journal'][-1] if job['journal'] else ''
            job.update(etat='echec', erreur=f"Le programme « {job['programme']} » n’a pas fini : {why}."
                       + (f' Sa dernière ligne : {last}' if last else ''))


def lancer(project_id: str) -> dict | None:
    """Lance la construction d'un projet, ou rend celle qui tourne déjà (jamais deux à la fois). None : projet
    inconnu. ValueError, avec un message pour la personne : pas de programme, projet personnel, fichier absent."""
    found = projets.programme(project_id, 'fabrication')
    if found is None:
        return None
    with _LOCK:
        running = _JOBS.get(found['projet'])
        if running and running['etat'] == 'en_cours':
            return _vue(running)
        job = {'projet': found['projet'], 'titre': found['titre'], 'programme': found['programme'], 'etat': 'en_cours',
               'n': 0, 'sur': 0, 'quoi': 'Démarrage', 'etapes': [], 'journal': [], 'debut': now_iso(),
               't_debut': time.monotonic(), 't_fin': None, 'resultat': None, 'erreur': None}
        _JOBS[found['projet']] = job
    try:
        proc = subprocess.Popen(
            [sys.executable, '-X', 'utf8', '-I', str(found['path'])], cwd=str(found['dossier']),
            env={**launch_env(), 'PYTHONIOENCODING': 'utf-8'}, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', bufsize=1,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except OSError as exc:
        with _LOCK:
            job.update(etat='echec', t_fin=time.monotonic(),
                       erreur=f"Le programme « {found['programme']} » n’a pas pu être lancé ({type(exc).__name__}).")
        return _vue(job)
    threading.Thread(target=_follow, args=(job, proc, found['dossier']), daemon=True, name='aurora-atelier').start()
    with _LOCK:
        return _vue(job)


def etat(project_id: str) -> dict | None:
    """Où en est la construction d'un projet : celle qui tourne ou la dernière finie ; sinon « jamais » (avec le nom
    du programme) ou « indisponible » (avec la raison). None : projet inconnu. Rien n'est lancé ici."""
    try:
        found = projets.programme(project_id, 'fabrication')
    except ValueError as exc:
        return {'projet': project_id, 'etat': 'indisponible', 'raison': str(exc)}
    if found is None:
        return None
    with _LOCK:
        job = _JOBS.get(found['projet'])
        return _vue(job) if job else {'projet': found['projet'], 'titre': found['titre'], 'programme': found['programme'],
                                      'etat': 'jamais'}


def garder_le_modele(project_id: str) -> tuple[dict | None, str | None, bool]:
    """Sur le clic de la personne : le modèle de la dernière construction FINIE est donné à AURORA comme n'importe
    quel modèle 3D (tools/lab.import_model : copié dans ses modèles, rien d'écrasé, le même fichier redonné rouvre sa
    scène). (scène, None, déjà connu) ou (None, la raison, False)."""
    from . import lab   # lazy: the models are owned by another tool
    with _LOCK:
        job = _JOBS.get(project_id)
        result = dict(job['resultat']) if job and job['etat'] == 'fini' and job['resultat'] else None
    found = projets.programme(project_id, 'fabrication') if result else None
    if not result or found is None:
        return None, 'Aucune construction finie pour ce projet : lancez-la d’abord.', False
    path = found['dossier'] / result['modele']
    try:
        data = path.read_bytes()
    except OSError:
        return None, f"Le modèle « {result['modele']} » n’est plus sur le disque : relancez la construction.", False
    return lab.import_model(path.name, data)
