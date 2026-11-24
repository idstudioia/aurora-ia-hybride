"""Démarrer AURORA avec Windows (E3 ; ID Studio IA, le 16/11/2026 : « Passer à l'application »).

Un petit fichier de commande, posé dans le dossier « Démarrage » de la session Windows, lance les serveurs d'AURORA à
l'ouverture de session : sans fenêtre, ou avec la fenêtre d'AURORA. Rien d'autre n'est touché : ni le registre, ni une
tâche planifiée. Retirer le fichier retire le démarrage automatique ; Windows le montre aussi dans le Gestionnaire des
tâches, « Applications de démarrage ».

L'état dit ce qui est réellement sur le disque, jamais un réglage mémorisé. Seul un fichier écrit par AURORA est
remplacé ou retiré : un autre fichier du même nom est laissé tel quel, et dit.
Seule la page Paramètres appelle ces fonctions, par deux routes du service : ni le chat, ni l'agent.
"""
import os
import re
import sys
from pathlib import Path

FICHIER = 'AURORA-demarrage.cmd'
MARQUE = 'rem aurora-demarrage fenetre='
LANCEUR = 'start-aurora.ps1'
INTERDITS = '"%!^&<>|\r\n'          # ce qu'un fichier de commande lirait autrement que comme un chemin


def racine() -> Path:
    """Le dossier de cette copie d'AURORA : celui du lanceur start-aurora.ps1."""
    return Path(__file__).resolve().parents[3]


def dossier() -> Path | None:
    """Le dossier « Démarrage » de la session Windows. La variable AURORA_STARTUP_DIR le remplace pour les essais :
    un essai n'écrit jamais dans le vrai dossier."""
    force = os.environ.get('AURORA_STARTUP_DIR')
    if force:
        return Path(force)
    if sys.platform != 'win32' or not os.environ.get('APPDATA'):
        return None
    return Path(os.environ['APPDATA']) / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs' / 'Startup'


def etat() -> dict:
    """Ce qui est sur le disque : {disponible, pourquoi, actif, fenetre, cette_copie, autre, fichier, lance}.
    « autre » : un fichier de ce nom existe, mais AURORA ne l'a pas écrit."""
    ici = dossier()
    vue = {'disponible': False, 'pourquoi': '', 'actif': False, 'fenetre': False, 'cette_copie': False, 'autre': False,
           'fichier': '', 'lance': str(racine() / LANCEUR)}
    if ici is None:
        return {**vue, 'pourquoi': 'Le démarrage avec Windows n’existe que sous Windows.'}
    vue['fichier'] = str(ici / FICHIER)
    if not ici.is_dir():
        return {**vue, 'pourquoi': f'Le dossier « Démarrage » de Windows est introuvable : {ici}'}
    if not (racine() / LANCEUR).is_file():
        return {**vue, 'pourquoi': f'Le lanceur d’AURORA est introuvable : {racine() / LANCEUR}'}
    vue['disponible'] = True
    chemin = ici / FICHIER
    if not chemin.is_file():
        return vue
    try:
        texte = chemin.read_bytes()[:8192].decode('utf-8', errors='replace')
    except OSError as erreur:
        return {**vue, 'disponible': False, 'pourquoi': f'Le fichier de démarrage ne se lit pas : {erreur}'}
    marque = re.search(re.escape(MARQUE) + r'([01])\s*$', texte, re.MULTILINE)
    if not marque:
        return {**vue, 'autre': True,
                'pourquoi': 'Un fichier de ce nom existe déjà dans le dossier « Démarrage », et AURORA ne l’a pas écrit : il est laissé tel quel.'}
    return {**vue, 'actif': True, 'fenetre': marque.group(1) == '1', 'cette_copie': f'"{racine() / LANCEUR}"' in texte}


def regler(actif: bool, fenetre: bool = False) -> dict:
    """Pose le fichier de démarrage (sans la fenêtre d'AURORA, ou avec), ou le retire. Rend l'état relu sur le
    disque. ValueError, en français, quand ce n'est pas possible : rien n'est alors changé."""
    avant = etat()
    if not avant['disponible'] or avant['autre']:
        raise ValueError(avant['pourquoi'] or 'Le démarrage avec Windows n’est pas disponible.')
    chemin = Path(avant['fichier'])
    if not actif:
        if avant['actif']:
            chemin.unlink()
        return etat()
    lanceur = str(racine() / LANCEUR)
    if any(c in lanceur for c in INTERDITS):
        raise ValueError(f'Le dossier d’AURORA contient un caractère qu’un fichier de commande ne sait pas écrire : {lanceur}')
    lignes = ['@echo off',
              'rem AURORA : demarrage avec Windows, regle dans AURORA (Parametres, Demarrage).',
              'rem Supprimer ce fichier retire le demarrage automatique.',
              f'{MARQUE}{int(bool(fenetre))}',
              'chcp 65001 >nul',
              f'powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{lanceur}" -Hidden'
              + ('' if fenetre else ' -SansFenetre'), '']
    provisoire = chemin.with_name(chemin.name + '.provisoire')
    provisoire.write_bytes('\r\n'.join(lignes).encode('utf-8'))
    os.replace(provisoire, chemin)
    return etat()
