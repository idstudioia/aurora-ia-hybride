"""Les moteurs sans fenêtre de FreeCAD et de KiCad (15/11/2026).

ID Studio IA : « on avait pas dit aussi qu'il nous faut la connexion au moteur FreeCAD et KiCad », puis « Oui, branche-les ».

Ce module fait vérifier UN fichier déclaré d'un projet par le moteur du logiciel qui l'ouvre :
  - une pièce (.step, .stp, .fcstd) par FreeCAD (freecadcmd) : est-ce un solide valide et fermé, son volume, ses cotes ;
  - un schéma (.kicad_sch) par KiCad (kicad-cli sch erc) : le contrôle des règles électriques ;
  - une carte (.kicad_pcb) par KiCad (kicad-cli pcb drc) : le contrôle des règles de dessin.

Ce qu'il ne fait pas, et pourquoi :
  - il ne lance que le moteur d'un logiciel que la personne a mis dans ses applications autorisées (tools/apps.py),
    avec des arguments fixes, sans console et sans fenêtre : ce n'est pas un terminal ;
  - il ne donne jamais au moteur le fichier du projet lui-même, mais une copie dans un dossier de passage (KiCad écrit
    à côté du fichier qu'il ouvre) : rien du projet n'est modifié ;
  - il coupe le moteur au bout de deux minutes ;
  - il ne conclut pas à la place du moteur : les chiffres et les violations rendus sont ceux que le moteur a dits, et
    chaque résultat dit ce que ce contrôle ne prouve pas.

Le dernier résultat de chaque fichier est gardé (dans AURORA_HOME, pas dans le projet) avec l'empreinte du fichier
vérifié. C'est ce qui permet à un projet de faire CALCULER une vérification : elle nomme ses fichiers (« controle »
dans projet.json), et elle n'est « faite » que si le moteur a dit « conforme » de chacun tel qu'il est aujourd'hui
(etat_calcule, appelé par tools/projets.py). verifier_tout les fait tous vérifier d'un coup.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
from datetime import datetime
from pathlib import Path, PurePosixPath

from ..config import aurora_home
from . import apps, projets
from .base import now_iso

log = logging.getLogger('aurora.tools.moteurs_cao')

TIMEOUT_S = 120.0
GARDES = 'controles-projets'             # dans AURORA_HOME : un fichier par projet, ce que les moteurs ont dit
_LOCK = threading.Lock()
MAX_COPIE = 80_000_000                   # octets : un fichier plus gros n'est pas copié, donc pas vérifié
MAX_A_COTE = 60                          # fichiers KiCad copiés avec celui qu'on vérifie (ses feuilles, ses tables)
A_COTE = ('.kicad_pro', '.kicad_sch', '.kicad_pcb', '.kicad_sym', '.kicad_dru')
TABLES = ('sym-lib-table', 'fp-lib-table')
GENRES = {'.step': ('freecad', 'piece'), '.stp': ('freecad', 'piece'), '.fcstd': ('freecad', 'piece'),
          '.kicad_sch': ('kicad', 'schema'), '.kicad_pcb': ('kicad', 'carte')}
LIMITES = {
    'piece': 'FreeCAD ne dit pas que la pièce est assez solide, ni qu’elle s’imprime bien.',
    'schema': 'Ce contrôle cherche des erreurs de branchement : il ne dit pas que le circuit fait ce qu’on attend de lui.',
    'carte': 'Ce contrôle vérifie les règles de dessin : il ne dit pas que la carte fonctionne.',
}
MARK = 'AURORA-CAO '
# Ce que FreeCAD exécute : ce texte, et lui seul. Le fichier à lire lui est dit par une variable ; il écrit une ligne.
SCRIPT_FREECAD = r'''
import json, os, sys
chemin = os.environ.get('AURORA_CAO_FICHIER', '')
dit = {}
try:
    import FreeCAD, Part
    dit['version'] = '.'.join(FreeCAD.Version()[:3])
    if chemin.lower().endswith('.fcstd'):
        document = FreeCAD.openDocument(chemin)
        formes = [(o.Label, o.Shape) for o in document.Objects if hasattr(o, 'Shape') and not o.Shape.isNull()]
    else:
        forme = Part.Shape()
        forme.read(chemin)
        formes = [(os.path.basename(chemin), forme)]
    dit['objets'] = []
    for nom, forme in formes[:40]:
        boite = forme.BoundBox
        dit['objets'].append({'nom': nom, 'solides': len(forme.Solids), 'valide': bool(forme.isValid()),
                              'ferme': bool(forme.Shells) and all(s.isClosed() for s in forme.Shells),
                              'volume_mm3': forme.Volume, 'surface_mm2': forme.Area,
                              'boite_mm': [boite.XLength, boite.YLength, boite.ZLength]})
except Exception as exc:
    dit['erreur'] = type(exc).__name__ + ' : ' + str(exc)
print('AURORA-CAO ' + json.dumps(dit), flush=True)
sys.exit(0)
'''


def _fr(value: float, digits: int = 1) -> str:
    return f'{value:.{digits}f}'.replace('.', ',')


def moteur(outil: str) -> tuple[Path | None, str]:
    """Le moteur sans fenêtre d'un logiciel de conception : (son fichier, '') ; ou (None, pourquoi, en français).
    Il n'existe pour AURORA que si le logiciel est dans les applications autorisées de la personne."""
    label = apps.CAO[outil][0]
    listed = [a for a in apps.allowed_apps() if apps.design_tool(a) == outil]
    if not listed:
        return None, (f'{label} n’est pas dans vos applications autorisées : ajoutez-le dans Paramètres pour qu’AURORA '
                      'puisse se servir de son moteur.')
    engine = apps.engine_of(listed[0])
    if engine is None:
        return None, f'Le moteur de {label} ({apps.MOTEURS[outil][0]}) est introuvable à côté de « {listed[0].get("name", label)} ».'
    return engine, ''


def _run(args: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Lance le moteur : arguments fixes, sans console ni fenêtre, sans les variables d'AURORA, coupé à TIMEOUT_S.
    Remplacé dans les essais : aucun moteur n'y est lancé."""
    return subprocess.run(args, cwd=str(cwd), env={**apps.launch_env(), **(env or {})}, stdin=subprocess.DEVNULL,
                          capture_output=True, timeout=TIMEOUT_S, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def _said(done: subprocess.CompletedProcess) -> str:
    """Les derniers mots du moteur, pour dire pourquoi il a échoué."""
    text = (done.stderr or b'').decode('utf-8', 'replace').strip() or (done.stdout or b'').decode('utf-8', 'replace').strip()
    return ' '.join(text.split())[-240:]


def _piece(engine: Path, copy: Path, work: Path) -> dict:
    """Une pièce lue par FreeCAD : chaque forme du fichier, si elle est un solide valide et fermé, son volume, ses cotes."""
    script = work / 'verifier_piece.py'
    script.write_text(SCRIPT_FREECAD, encoding='utf-8')
    done = _run([str(engine), str(script)], cwd=work, env={'AURORA_CAO_FICHIER': str(copy)})
    line = next((l for l in (done.stdout or b'').decode('utf-8', 'replace').splitlines() if l.startswith(MARK)), None)
    try:
        said = json.loads(line[len(MARK):]) if line else None
    except ValueError:
        said = None
    if not isinstance(said, dict):
        raise ValueError(f'FreeCAD n’a pas rendu de résultat lisible. {_said(done)}'.strip())
    if said.get('erreur'):
        raise ValueError(f'FreeCAD n’a pas pu lire ce fichier : {str(said["erreur"])[:200]}')
    objets = []
    for o in said.get('objets') if isinstance(said.get('objets'), list) else []:
        try:
            objets.append({'nom': str(o['nom'])[:80], 'solides': int(o['solides']), 'valide': bool(o['valide']), 'ferme': bool(o['ferme']),
                           'volume_mm3': round(float(o['volume_mm3']), 1), 'surface_mm2': round(float(o['surface_mm2']), 1),
                           'boite_mm': [round(float(v), 2) for v in list(o['boite_mm'])[:3]]})
        except (KeyError, TypeError, ValueError):
            continue
    if not objets:
        raise ValueError('FreeCAD n’a trouvé aucune forme dans ce fichier.')
    bons = [o for o in objets if o['solides'] >= 1 and o['valide'] and o['ferme']]
    if len(objets) == 1:
        o = objets[0]
        resume = (f"{o['solides']} solide{'s' if o['solides'] > 1 else ''}, {'valide' if o['valide'] else 'NON valide'} et "
                  f"{'fermé' if o['ferme'] else 'NON fermé'} · {_fr(o['volume_mm3'] / 1000)} cm³ · {' × '.join(_fr(v) for v in o['boite_mm'])} mm")
    else:
        resume = f'{len(objets)} formes, dont {len(bons)} solides valides et fermés'
    return {'moteur': f"FreeCAD {str(said.get('version') or '')[:20]}".strip(), 'objets': objets,
            'verdict': 'conforme' if len(bons) == len(objets) else 'a_revoir', 'resume': resume}


def _kicad(engine: Path, copy: Path, work: Path, genre: str) -> dict:
    """Un schéma (règles électriques) ou une carte (règles de dessin) contrôlé par KiCad : ses violations, telles
    que KiCad les écrit dans son rapport."""
    report = work / 'rapport-aurora.json'
    command = ['sch', 'erc'] if genre == 'schema' else ['pcb', 'drc']
    done = _run([str(engine), *command, '--format', 'json', '--output', str(report), str(copy)], cwd=work)
    try:
        said = json.loads(report.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        said = None
    if not isinstance(said, dict):
        raise ValueError(f'KiCad n’a pas rendu de rapport pour ce fichier. {_said(done)}'.strip())
    listed = said.get('sheets') if genre == 'schema' else [said]
    found = [v for sheet in (listed if isinstance(listed, list) else []) if isinstance(sheet, dict)
             for v in (sheet.get('violations') if isinstance(sheet.get('violations'), list) else []) if isinstance(v, dict)]
    found.sort(key=lambda v: v.get('severity') != 'error')        # les erreurs d'abord : la liste rendue est coupée à 20
    loose = said.get('unconnected_items') if genre == 'carte' and isinstance(said.get('unconnected_items'), list) else []
    errors = sum(1 for v in found if v.get('severity') == 'error')
    warnings = sum(1 for v in found if v.get('severity') == 'warning')
    violations = [{'gravite': 'erreur' if v.get('severity') == 'error' else 'avertissement', 'texte': str(v.get('description') or '')[:200],
                   'ou': ', '.join(str(i.get('description') or '') for i in v.get('items', []) if isinstance(i, dict))[:160]} for v in found[:20]]
    resume = f"{errors} erreur{'s' if errors > 1 else ''}, {warnings} avertissement{'s' if warnings > 1 else ''}"
    if genre == 'carte':
        resume += f", {len(loose)} liaison{'s' if len(loose) > 1 else ''} non connectée{'s' if len(loose) > 1 else ''}"
    return {'moteur': f"KiCad {str(said.get('kicad_version') or '')[:20]}".strip(), 'erreurs': errors, 'avertissements': warnings,
            'non_connectes': len(loose), 'violations': violations, 'autres': max(0, len(found) - len(violations)),
            'verdict': 'conforme' if not errors and not loose else 'a_revoir', 'resume': resume}


def _copy(source: Path, work: Path, outil: str) -> Path:
    """La copie de passage du fichier (et, pour KiCad, des fichiers KiCad rangés à côté de lui : ses feuilles, son
    projet, ses tables). ValueError si c'est trop gros."""
    if source.stat().st_size > MAX_COPIE:
        raise ValueError('Ce fichier est trop gros pour être donné au moteur.')
    copy = work / source.name
    shutil.copyfile(source, copy)
    if outil == 'kicad':
        total, count = source.stat().st_size, 0
        for beside in sorted(source.parent.iterdir()):
            if beside == source or not beside.is_file() or (beside.suffix.lower() not in A_COTE and beside.name not in TABLES):
                continue
            total, count = total + beside.stat().st_size, count + 1
            if count > MAX_A_COTE or total > MAX_COPIE:
                break
            shutil.copyfile(beside, work / beside.name)
    return copy


# ── Ce que les moteurs ont dit, gardé (15/11/2026, ID Studio IA : « Oui, fais-le », calculer les vérifications du projet par ces
# moteurs). Le dernier résultat de chaque fichier est gardé dans AURORA_HOME, jamais dans le dossier du projet, avec
# l'empreinte du fichier vérifié. Une vérification qui nomme des fichiers à contrôler n'est « faite » que si chaque
# résultat gardé dit « conforme » ET porte l'empreinte du fichier d'aujourd'hui : un fichier réécrit est à revérifier.
def _empreinte(path: Path) -> str:
    """Ce qui dit qu'un fichier est resté le même depuis son contrôle : sa taille et sa date d'écriture (il n'est pas lu)."""
    stat = path.stat()
    return f'{stat.st_size}-{stat.st_mtime_ns}'


def gardes(project_id: str) -> dict:
    """Ce que les moteurs ont dit des fichiers d'un projet, tel que gardé : chemin → {'empreinte', 'verdict', 'resume',
    'moteur', 'date'}. {} si rien n'est gardé, ou si ce qui est gardé est illisible (tout est alors à revérifier)."""
    try:
        data = json.loads((Path(aurora_home()) / GARDES / f'{project_id}.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def _garder(project_id: str, chemin: str, empreinte: str, result: dict) -> None:
    """Garde le dernier résultat d'un fichier (écriture en deux temps : un fichier gardé n'est jamais à moitié écrit)."""
    folder = Path(aurora_home()) / GARDES
    with _LOCK:
        kept = gardes(project_id)
        kept[chemin] = {'empreinte': empreinte, **{k: result[k] for k in ('verdict', 'resume', 'moteur', 'date')}}
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / f'{project_id}.json.tmp'
        tmp.write_text(json.dumps(kept, ensure_ascii=False, indent=1), encoding='utf-8')
        os.replace(tmp, folder / f'{project_id}.json')


def _jour(date: str) -> str:
    """« 15/11/2026 à 09:33 », à l'heure de ce PC, d'une date gardée ; '' si elle est illisible."""
    try:
        return datetime.fromisoformat(date.replace('Z', '+00:00')).astimezone().strftime('%d/%m/%Y à %H:%M')
    except (AttributeError, ValueError):
        return ''


def etat_calcule(declare: str, files: list[tuple[str, Path]], kept: dict) -> dict:
    """L'état d'une vérification qui fait contrôler des fichiers : {'etat', 'calcule': {'conformes', 'total', 'texte',
    'fichiers'}}. « faite » seulement si le moteur a dit « conforme » de chaque fichier tel qu'il est aujourd'hui ;
    sinon « bloquante » si elle est déclarée bloquante, « à faire » autrement. Aucun moteur n'est lancé ici."""
    lines = []
    for chemin, path in files:
        said = kept.get(chemin) or {}
        try:
            now = _empreinte(path) if path.is_file() else None
        except OSError:
            now = None
        if Path(chemin).suffix.lower() not in GENRES:
            state = 'non_verifiable'
        elif now is None:
            state = 'absent'
        elif not said:
            state = 'jamais'
        elif said.get('empreinte') != now:
            state = 'change'
        else:
            state = 'conforme' if said.get('verdict') == 'conforme' else 'a_revoir'
        current = state in ('conforme', 'a_revoir')
        lines.append({'chemin': chemin, 'etat': state, 'resume': str(said.get('resume') or '')[:200] if current else '',
                      'moteur': str(said.get('moteur') or '')[:40] if current else '',
                      'date': str(said.get('date') or '')[:30] if current else ''})
    good = [l for l in lines if l['etat'] == 'conforme']
    plural = 's' if len(good) > 1 else ''
    texte = f'{len(good)} fichier{plural} sur {len(lines)} conforme{plural}'
    if good:
        texte += f", dit par {', '.join(sorted({l['moteur'] for l in good if l['moteur']}))}"
        last = _jour(max(l['date'] for l in good))
        texte += f' le {last}' if last else ''
    texte += '.'
    for state, label in (('a_revoir', 'À revoir'), ('change', 'Réécrit depuis son contrôle, à revérifier'), ('jamais', 'Pas encore vérifié'),
                         ('absent', 'Absent du dossier'), ('non_verifiable', 'D’un genre qu’aucun moteur ne vérifie')):
        names = [PurePosixPath(l['chemin']).name for l in lines if l['etat'] == state]
        if names:
            texte += f" {label} : {', '.join(names[:6])}{'…' if len(names) > 6 else ''}."
    done = bool(lines) and len(good) == len(lines)
    return {'etat': 'faite' if done else ('bloquante' if declare == 'bloquante' else 'a_faire'),
            'calcule': {'conformes': len(good), 'total': len(lines), 'texte': texte, 'fichiers': lines}}


def verifier_tout(project_id: str) -> dict | None:
    """Fait vérifier, l'un après l'autre, chaque fichier qu'une vérification du projet fait contrôler. None : ce projet
    n'existe pas. ValueError : aucune de ses vérifications ne nomme de fichier. Un fichier que son moteur n'a pas pu
    vérifier est rendu avec sa raison, et les autres sont faits quand même."""
    state = projets.load(project_id)
    if state is None:
        return None
    chemins = list(dict.fromkeys(c for v in state['liste_verifications'] for c in v.get('controle', [])))
    if not chemins:
        raise ValueError('Aucune vérification de ce projet ne nomme de fichier à faire contrôler par un moteur.')
    done, refused = [], []
    for chemin in chemins:
        try:
            said = verifier(project_id, chemin)
        except ValueError as exc:
            refused.append({'fichier': chemin, 'raison': str(exc)})
            continue
        if said is not None:
            done.append({k: said[k] for k in ('fichier', 'outil', 'verdict', 'resume', 'moteur')})
    return {'projet': project_id, 'date': now_iso(), 'verifies': done, 'refuses': refused}


def verifier(project_id: str, chemin: str) -> dict | None:
    """Fait vérifier un fichier déclaré d'un projet par le moteur de son logiciel. None : ce projet ne déclare pas ce
    fichier. ValueError, avec un message pour la personne : fichier absent ou d'un genre non vérifiable, logiciel non
    autorisé, moteur introuvable, moteur en échec ou trop long. Le résultat dit ce que le contrôle ne prouve pas."""
    declared = projets.fichier(project_id, chemin)
    if declared is None:
        return None
    suffix = Path(chemin).suffix.lower()
    if suffix not in GENRES:
        raise ValueError('AURORA ne sait faire vérifier qu’une pièce (.step, .stp, .fcstd), un schéma (.kicad_sch) ou une carte (.kicad_pcb).')
    if not declared['present']:
        raise ValueError('Ce fichier n’est pas dans le dossier du projet' + (' : il se refait en construisant le projet.' if declared['genere'] else '.'))
    outil, genre = GENRES[suffix]
    label = apps.CAO[outil][0]
    engine, why = moteur(outil)
    if engine is None:
        raise ValueError(why)
    with tempfile.TemporaryDirectory(prefix='aurora-cao-') as folder:
        work = Path(folder)
        try:
            seen = _empreinte(declared['path'])      # avant la copie : un fichier réécrit pendant le contrôle est à revérifier
            copy = _copy(declared['path'], work, outil)
            result = _piece(engine, copy, work) if outil == 'freecad' else _kicad(engine, copy, work, genre)
        except subprocess.TimeoutExpired:
            raise ValueError(f'Le moteur de {label} n’a pas répondu en {TIMEOUT_S:.0f} secondes : il a été arrêté.') from None
        except OSError as exc:
            raise ValueError(f'Le moteur de {label} n’a pas pu être lancé ({exc.strerror or exc}).') from None
    out = {'projet': project_id, 'fichier': chemin, 'outil': outil, 'genre': genre, 'limite': LIMITES[genre], 'date': now_iso(), **result}
    try:
        _garder(project_id, chemin, seen, out)
        out['garde'] = True
    except OSError:
        log.warning('Résultat du contrôle de %s (%s) non gardé', chemin, project_id)
        out['garde'] = False              # le résultat est juste, mais les vérifications du projet ne le verront pas
    return out
