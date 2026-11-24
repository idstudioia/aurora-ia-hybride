"""Les projets d'AURORA (13/11/2026). ID Studio IA : « chaque projet conserve ses fichiers techniques modifiables, pas
uniquement son modèle 3D […] avant toute fabrication, les calculs et les circuits devront être vérifiés ».

Un projet est un dossier qui porte un fichier « projet.json » (format aurora.projet/1) : son cahier des charges, ses
rubriques (dossier, calculs, pièces, électronique, modèles 3D, fabrication, programme) avec leurs fichiers, et ses
vérifications. Ce module lit ce fichier, contrôle sa forme, regarde lui-même si chaque fichier nommé est bien là, et
dit si le projet est prêt à fabriquer. Le chemin d'un fichier ne sort jamais du dossier du projet. Une seule chose
s'écrit ici, depuis le 23/11/2026 : le dossier neuf d'un projet que la personne crée par le bouton de la page
(creer, plus bas), dans ses propres projets et jamais par-dessus un dossier qui existe. Une seule chose s'exécute :
le programme de calcul d'un projet rangé dans le dossier d'AURORA (dimensionner, en bas de ce fichier), pour
confronter un cahier des charges à ses chiffres.

Deux endroits : les projets rangés dans le dossier d'AURORA (docs/<dossier>/projet.json), suivis par Git, et ceux
de la personne (<AURORA_HOME>/projets/<dossier>/projet.json).

Honnêteté : l'état d'une exigence ou d'une vérification est celui que le fichier déclare ; la présence des fichiers
est mesurée ici. Depuis le 15/11/2026, une vérification qui nomme des fichiers à faire contrôler (« controle ») n'a
plus l'état qu'elle déclare : il est calculé d'après ce que le moteur de FreeCAD ou de KiCad a dit de chaque fichier
tel qu'il est aujourd'hui (tools/moteurs_cao.py). Les autres restent déclarées, et l'interface dit lesquelles.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import re
import unicodedata
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ..config import aurora_home
from .base import Params, Source, ToolError, ToolResult, now_iso, tool

log = logging.getLogger('aurora.tools.projets')

FORMAT = 'aurora.projet/1'
MANIFEST = 'projet.json'
PROJECT_ID = r'^[a-z0-9][a-z0-9-]{1,39}$'
MAX_BYTES = 262_144                      # un projet.json plus gros n'est pas lu
MAX_PROJECTS = 100
AURORA_FOLDER = Path(__file__).resolve().parents[3]
RUBRIQUES = {'dossier': 'Dossier', 'calculs': 'Calculs', 'pieces': 'Pièces', 'electronique': 'Électronique',
             'modeles': 'Modèles 3D', 'fabrication': 'Fabrication', 'programme': 'Programme'}
EXIGENCES = {'tenue': 'tenue', 'a_verifier': 'à vérifier', 'a_etudier': 'à étudier', 'non_tenue': 'non tenue'}
VERIFICATIONS = {'faite': 'faite', 'a_faire': 'à faire', 'bloquante': 'bloquante'}
# Les projets dont AURORA appelle le programme de calcul sans rien demander : ceux qui sont rangés dans son propre
# dossier, suivis par Git. Le programme d'un projet personnel ne s'exécute pas ici (il lui faudra un accord).
TRUSTED_ORIGINS = ('AURORA',)


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


def _stays_inside(value: str) -> str:
    """Le chemin d'un fichier tel qu'un projet l'écrit : relatif, sans « .. », sans lettre de disque. Sinon ValueError."""
    path = PurePosixPath(value)
    if '\\' in value or ':' in value or path.is_absolute() or any(part in ('..', '.', '') for part in path.parts):
        raise ValueError('le chemin d’un fichier reste dans le dossier du projet, sans « .. » ni lettre de disque')
    return value


class Fichier(_Strict):
    nom: str = Field(min_length=1, max_length=80)
    chemin: str = Field(min_length=1, max_length=200)
    role: str = Field('', max_length=300)
    genere: bool = False                 # fabriqué par un script du projet : absent, il se refait ; ce n'est pas un manque

    @field_validator('chemin')
    @classmethod
    def _inside(cls, value: str) -> str:
        return _stays_inside(value)


class Exigence(_Strict):
    texte: str = Field(min_length=1, max_length=200)
    etat: Literal['tenue', 'a_verifier', 'a_etudier', 'non_tenue']
    note: str = Field('', max_length=400)


class Rubrique(_Strict):
    id: Literal['dossier', 'calculs', 'pieces', 'electronique', 'modeles', 'fabrication', 'programme']
    note: str = Field('', max_length=400)
    fichiers: list[Fichier] = Field(default_factory=list, max_length=60)


class Verification(_Strict):
    quoi: str = Field(min_length=1, max_length=200)
    etat: Literal['faite', 'a_faire', 'bloquante']
    detail: str = Field('', max_length=400)
    # 15/11/2026 (ID Studio IA : « Oui, fais-le », calculer les vérifications par les moteurs) : les fichiers déclarés du projet
    # qu'un moteur (FreeCAD, KiCad) contrôle pour cette vérification. Quand il y en a, l'état écrit ici ne vaut plus
    # « faite » : elle n'est faite que si le moteur a dit « conforme » de chaque fichier tel qu'il est aujourd'hui
    # (tools/moteurs_cao.py). « bloquante » reste bloquante tant que ce n'est pas le cas.
    controle: list[str] = Field(default_factory=list, max_length=20)

    @field_validator('controle')
    @classmethod
    def _inside_each(cls, value: list[str]) -> list[str]:
        return [_stays_inside(v) for v in value]


class Projet(_Strict):
    format: Literal['aurora.projet/1']
    id: str = Field(pattern=PROJECT_ID)
    titre: str = Field(min_length=1, max_length=100)
    resume: str = Field('', max_length=600)
    stade: str = Field('', max_length=60)
    maj: str = Field('', max_length=20)
    cahier: list[Exigence] = Field(default_factory=list, max_length=40)
    rubriques: list[Rubrique] = Field(default_factory=list, max_length=len(RUBRIQUES))
    verifications: list[Verification] = Field(default_factory=list, max_length=40)

    @model_validator(mode='after')
    def _one_of_each(self) -> 'Projet':
        ids = [r.id for r in self.rubriques]
        if len(ids) != len(set(ids)):
            raise ValueError('une rubrique est écrite deux fois')
        declared = {f.chemin for r in self.rubriques for f in r.fichiers}
        if any(c not in declared for v in self.verifications for c in v.controle):
            raise ValueError('une vérification fait contrôler un fichier que le projet ne déclare pas')
        return self


# ── La fiche technique interactive d'un projet (15/11/2026). ID Studio IA : « fait la fiche technique interactive avec les model
# 3D et les animation quant on clique sur le cadre […] si je veux crée un nouveaux projet et que les cadre change
# suivent le model du projet ». Le projet décrit lui-même ses cadres dans un fichier « fiche-technique.json », que son
# programme écrit ; la page les dessine. Ce sont des textes et des nombres : bornés, jamais exécutés.
class RepereFiche(_Strict):
    n: int = Field(ge=1, le=99)                              # le numéro montré sur la vue
    piece: str = Field(min_length=1, max_length=80)          # le début du nom de la pièce du modèle qu'il désigne


class CadreFiche(_Strict):
    type: Literal['vue', 'tableau', 'liste', 'etat']         # etat : l'état du projet, que la page tire de son dossier
    titre: str = Field(min_length=1, max_length=80)
    accent: Literal['cyan', 'orange'] = 'cyan'
    place: list[int] = Field(default_factory=list)           # [colonne, ligne, largeur, hauteur] sur 24 colonnes ; vide : la page range
    azimut: float = Field(0, ge=-360, le=360)                # une vue : d'où le modèle est regardé (0 : de face)
    hauteur: float = Field(0, ge=-89, le=89)
    eclate: bool = False
    cote_largeur: str = Field('', max_length=40)             # les cotes écrites sous et à côté de la vue (« 350 mm »)
    cote_hauteur: str = Field('', max_length=40)
    reperes: list[RepereFiche] = Field(default_factory=list, max_length=24)
    lignes: list[list[str]] = Field(default_factory=list, max_length=40)     # un tableau, ou une liste (une case par ligne)
    numerotee: bool = False
    note: str = Field('', max_length=400)

    @field_validator('place')
    @classmethod
    def _on_the_grid(cls, value: list[int]) -> list[int]:
        if value and (len(value) != 4 or not (1 <= value[0] <= 24 and 1 <= value[1] <= 200 and 1 <= value[2] <= 24
                                               and 1 <= value[3] <= 60 and value[0] + value[2] <= 25)):
            raise ValueError('la place d’un cadre est [colonne, ligne, largeur, hauteur] sur une grille de 24 colonnes')
        return value

    @field_validator('lignes')
    @classmethod
    def _short_rows(cls, value: list[list[str]]) -> list[list[str]]:
        if any(not 1 <= len(row) <= 4 or any(len(cell) > 160 for cell in row) for row in value):
            raise ValueError('une ligne a de 1 à 4 cases, de 160 caractères au plus')
        return value


class FicheDonnees(_Strict):
    format: Literal['aurora.fiche/1']
    titre: str = Field(min_length=1, max_length=100)
    sous_titre: str = Field('', max_length=100)
    modele: str = Field('', max_length=200)                  # le modèle 3D (GLB) des vues : un fichier déclaré du projet
    cadres: list[CadreFiche] = Field(min_length=1, max_length=16)
    cartouche: list[list[str]] = Field(default_factory=list, max_length=6)   # des paires (nom, valeur)
    note: str = Field('', max_length=400)

    @field_validator('modele')
    @classmethod
    def _model_inside(cls, value: str) -> str:
        return _stays_inside(value) if value else value

    @field_validator('cartouche')
    @classmethod
    def _pairs(cls, value: list[list[str]]) -> list[list[str]]:
        if any(len(pair) != 2 or any(len(cell) > 60 for cell in pair) for pair in value):
            raise ValueError('le cartouche est fait de paires (nom, valeur) de 60 caractères au plus')
        return value


DONNEES_FICHE = 'fiche-technique.json'
MAX_MODELE_FICHE = 120_000_000           # octets : un modèle 3D plus gros n'est pas donné à la page


def _calc_program(project: Projet, section: str = 'calculs') -> str:
    """Le programme de calcul d'un projet, par convention : le premier fichier .py de sa rubrique « Calculs » ('' s'il
    n'y en a pas). C'est sa fonction « dimensionner » qui répond à un cahier des charges (en bas de ce fichier).
    Même convention pour la rubrique « Fabrication » : son premier .py construit tout le projet (tools/atelier.py)."""
    for rubrique in project.rubriques:
        if rubrique.id == section:
            return next((f.chemin for f in rubrique.fichiers if f.chemin.endswith('.py')), '')
    return ''


FICHES = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp'}
MAX_FICHE = 8_000_000                    # octets : une fiche technique plus grosse n'est pas montrée


def _sheet(project: Projet) -> str:
    """La fiche technique d'un projet, par convention (15/11/2026, ID Studio IA : « ajoute un visuel de fiche technique […] tout
    en haut ») : son fichier déclaré qui s'appelle « fiche-technique », en image (JPEG, PNG ou WebP). '' sinon."""
    for rubrique in project.rubriques:
        for f in rubrique.fichiers:
            name = PurePosixPath(f.chemin)
            if name.stem == 'fiche-technique' and name.suffix.lower() in FICHES:
                return f.chemin
    return ''


def roots() -> list[tuple[str, Path]]:
    """Où les projets sont cherchés : (origine dite à la personne, dossier). Lu à chaque appel."""
    return [('AURORA', AURORA_FOLDER / 'docs'), ('personnel', Path(aurora_home()) / 'projets')]


def _read(folder: Path) -> tuple[Projet | None, str | None]:
    """Le projet d'un dossier : (projet, None) ou (None, la raison en français). Rien n'est exécuté."""
    path = folder / MANIFEST
    try:
        if path.stat().st_size > MAX_BYTES:
            return None, 'projet.json trop gros pour être lu'
        data = json.loads(path.read_text(encoding='utf-8'))
    except OSError:
        return None, 'projet.json illisible'
    except (ValueError, UnicodeDecodeError):
        return None, 'projet.json n’est pas un fichier JSON lisible'
    try:
        return Projet.model_validate(data), None
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ' › '.join(str(p) for p in first.get('loc', ()))
        return None, f'projet.json refusé ({where} : {first.get("msg", "forme invalide")})'[:300]


def _folders() -> list[tuple[str, Path]]:
    found = []
    for origin, root in roots():
        try:
            found += [(origin, p.parent) for p in sorted(root.glob(f'*/{MANIFEST}')) if p.is_file()]
        except OSError:
            log.warning('Dossier de projets illisible : %s', root)
    return found[:MAX_PROJECTS]


def _state(project: Projet, folder: Path, origin: str, *, detail: bool) -> dict:
    """Ce que le service dit d'un projet : ses comptes, ce qui manque, et s'il est prêt à fabriquer. La présence de
    chaque fichier est regardée ici ; son contenu n'est pas ouvert."""
    base = folder.resolve()
    rubriques, rubriques_lues, missing, to_make, present = [], [], [], 0, 0
    for rubrique in project.rubriques:
        files = []
        rubriques_lues.append(files)
        for f in rubrique.fichiers:
            target = (base / f.chemin).resolve()
            inside = base == target or base in target.parents
            there = inside and target.is_file()
            size = target.stat().st_size if there else None
            present += there
            if not there and f.genere:
                to_make += 1
            elif not there:
                missing.append(f.nom)
            files.append({**f.model_dump(), 'present': bool(there), 'taille': size})
        rubriques.append({'id': rubrique.id, 'titre': RUBRIQUES[rubrique.id], 'note': rubrique.note,
                          'fichiers': files if detail else len(files)})
    # Une vérification qui nomme des fichiers à faire contrôler n'a pas l'état qu'elle déclare : il est calculé d'après
    # ce que le moteur a dit de chaque fichier tel qu'il est aujourd'hui (15/11/2026).
    checks, kept = [], None
    for v in project.verifications:
        line = v.model_dump()
        if v.controle:
            from . import moteurs_cao    # ici, pas en tête : ce module-là importe celui-ci
            kept = moteurs_cao.gardes(project.id) if kept is None else kept
            line.update(moteurs_cao.etat_calcule(v.etat, [(c, base / c) for c in v.controle], kept))
        checks.append(line)
    blocking = [v['quoi'] for v in checks if v['etat'] == 'bloquante']
    todo = [v['quoi'] for v in checks if v['etat'] == 'a_faire']
    unmet = [e.texte for e in project.cahier if e.etat == 'non_tenue']
    why = ([f'{len(blocking)} vérification{"s" if len(blocking) > 1 else ""} bloquante{"s" if len(blocking) > 1 else ""}'] if blocking else []) \
        + ([f'{len(todo)} vérification{"s" if len(todo) > 1 else ""} à faire'] if todo else []) \
        + ([f'{len(unmet)} exigence{"s" if len(unmet) > 1 else ""} non tenue{"s" if len(unmet) > 1 else ""}'] if unmet else []) \
        + ([f'{len(missing)} fichier{"s" if len(missing) > 1 else ""} manquant{"s" if len(missing) > 1 else ""}'] if missing else []) \
        + (['aucune vérification écrite'] if not project.verifications else [])
    out = {'id': project.id, 'titre': project.titre, 'resume': project.resume, 'stade': project.stade, 'maj': project.maj,
           'origine': origin, 'dossier': str(folder),
           'fichiers': {'presents': present, 'a_refaire': to_make, 'manquants': missing},
           'verifications': {'faites': sum(v['etat'] == 'faite' for v in checks), 'a_faire': len(todo),
                             'bloquantes': len(blocking), 'total': len(checks)},
           'exigences': {etat: sum(e.etat == etat for e in project.cahier) for etat in EXIGENCES},
           'pret_a_fabriquer': not why, 'pourquoi_pas': why, 'rubriques': rubriques,
           # Le programme de calcul, quand AURORA peut l'appeler (dimensionner) ; None sinon.
           'calcul': (_calc_program(project) or None) if origin in TRUSTED_ORIGINS else None,
           # Le programme qui construit tout le projet, quand AURORA peut le lancer (tools/atelier.py) ; None sinon.
           'fabrication': (_calc_program(project, 'fabrication') or None) if origin in TRUSTED_ORIGINS else None,
           # L'image de sa fiche technique, quand le projet en déclare une et qu'elle est là ; None sinon.
           'fiche': next((f['chemin'] for r in rubriques_lues for f in r if f['chemin'] == _sheet(project) and f['present']), None),
           # Les données de sa fiche technique interactive (son fichier déclaré « fiche-technique.json »), quand elles sont là.
           'fiche_donnees': next((f['chemin'] for r in rubriques_lues for f in r
                                  if PurePosixPath(f['chemin']).name == DONNEES_FICHE and f['present']), None)}
    if detail:
        out.update(cahier=[e.model_dump() for e in project.cahier],
                   liste_verifications=checks)
    return out


def catalogue() -> dict:
    """Tous les projets lisibles, et les dossiers dont le projet.json est refusé (avec la raison) : rien n'est caché."""
    projects, refused, seen = [], [], set()
    for origin, folder in _folders():
        project, why = _read(folder)
        if project is None:
            refused.append({'dossier': str(folder), 'raison': why})
        elif project.id in seen:
            refused.append({'dossier': str(folder), 'raison': f'un autre projet porte déjà l’identifiant « {project.id} »'})
        else:
            seen.add(project.id)
            projects.append(_state(project, folder, origin, detail=False))
    return {'projets': projects, 'refuses': refused}


def fichiers() -> list[dict]:
    """Chaque fichier déclaré par un projet lisible, à plat : son projet, son nom, son chemin dans le projet, son
    chemin sur le disque ('path') et s'il y est. Pour qui doit retrouver UN fichier de projet (tools/apps.py : le
    donner à FreeCAD ou à KiCad). Rien n'est ouvert, et un chemin ne sort jamais du dossier de son projet."""
    out, seen = [], set()
    for _origin, folder in _folders():
        project, _why = _read(folder)
        if project is None or project.id in seen:
            continue
        seen.add(project.id)
        base = folder.resolve()
        for rubrique in project.rubriques:
            for f in rubrique.fichiers:
                target = (base / f.chemin).resolve()
                out.append({'projet': project.id, 'titre': project.titre, 'nom': f.nom, 'chemin': f.chemin,
                            'genere': f.genere, 'path': target, 'present': base in target.parents and target.is_file()})
    return out


def fichier(project_id: str, chemin: str) -> dict | None:
    """UN fichier, par l'identifiant de son projet et son chemin tel que le projet l'écrit ; None s'il n'est pas déclaré."""
    return next((f for f in fichiers() if f['projet'] == project_id and f['chemin'] == chemin), None)


def fiche_technique(project_id: str) -> dict | None:
    """L'image de la fiche technique d'un projet : {'path', 'type'}. None si le projet n'existe pas, n'en déclare pas,
    si le fichier manque ou s'il dépasse MAX_FICHE. Seul un fichier déclaré, resté dans le dossier du projet, est rendu."""
    state = load(project_id)
    found = fichier(project_id, state['fiche']) if state and state.get('fiche') else None
    if not found or not found['present'] or found['path'].stat().st_size > MAX_FICHE:
        return None
    return {'path': found['path'], 'type': FICHES[found['path'].suffix.lower()]}


def fiche_donnees(project_id: str) -> dict | None:
    """La fiche technique interactive d'un projet : {'fiche': ses cadres, validés, 'modele': {'chemin', 'taille'} ou
    None}. None : pas de projet, ou pas de données de fiche. ValueError, avec un message pour la personne : le fichier
    est illisible ou refusé. Le modèle n'est annoncé que s'il est un fichier GLB déclaré par le projet, présent, et
    pas trop gros. Rien n'est exécuté."""
    state = load(project_id)
    found = fichier(project_id, state['fiche_donnees']) if state and state.get('fiche_donnees') else None
    if not found or not found['present']:
        return None
    try:
        if found['path'].stat().st_size > MAX_BYTES:
            raise ValueError('les données de la fiche technique sont trop grosses pour être lues')
        sheet = FicheDonnees.model_validate(json.loads(found['path'].read_text(encoding='utf-8')))
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ' › '.join(str(p) for p in first.get('loc', ()))
        raise ValueError(f'Fiche technique refusée ({where} : {first.get("msg", "forme invalide")})'[:300]) from None
    except OSError:
        raise ValueError('Les données de la fiche technique sont illisibles.') from None
    except UnicodeDecodeError:
        raise ValueError('Les données de la fiche technique ne sont pas un fichier JSON lisible.') from None
    except json.JSONDecodeError:
        raise ValueError('Les données de la fiche technique ne sont pas un fichier JSON lisible.') from None
    model = fichier(project_id, sheet.modele) if sheet.modele else None
    kept = None
    if model and model['present'] and model['path'].suffix.lower() == '.glb':
        size = model['path'].stat().st_size
        kept = {'chemin': sheet.modele, 'taille': size, 'path': model['path']} if size <= MAX_MODELE_FICHE else None
    return {'fiche': sheet.model_dump(), 'modele': kept}


def load(project_id: str) -> dict | None:
    """Un projet en entier (son cahier, ses fichiers un par un, ses vérifications), ou None s'il n'existe pas."""
    if not isinstance(project_id, str) or not re.match(PROJECT_ID, project_id):
        return None
    for origin, folder in _folders():
        project, _why = _read(folder)
        if project is not None and project.id == project_id:
            return _state(project, folder, origin, detail=True)
    return None


# --------------------------------------------------------------------------- créer un projet (23/11/2026)
# ID Studio IA, depuis l'AURORA tout juste installée sur le PC de son garage, qui montrait son projet de robot : « il faut
# supprimer dans la version à distribuer mon projet de robot ou un exemple avec une icône création de projet ; on
# laisse la fiche technique vide sans les modèles 3D ». Une AURORA neuve n'a donc aucun projet, et la page en crée
# un : un dossier neuf dans les projets de la personne, avec son projet.json, ses sept rubriques vides, aucune
# vérification (il n'est donc pas « prêt à fabriquer », et le dit). Ni fiche technique ni modèle 3D : la page les
# montre quand le dossier du projet les déclare. Rien n'est écrit dans le dossier d'AURORA, ni par-dessus un dossier.
class NouveauProjet(_Strict):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    titre: str = Field(min_length=2, max_length=100)
    resume: str = Field('', max_length=600)


def _identifiant(titre: str) -> str:
    """L'identifiant qu'un titre donne à son projet : minuscules sans accents, des tirets, 40 caractères au plus
    (« Lampe de bureau » : « lampe-de-bureau »). « projet » quand le titre n'a ni lettre ni chiffre latins."""
    simple = unicodedata.normalize('NFKD', titre).encode('ascii', 'ignore').decode('ascii').lower()
    found = re.sub(r'[^a-z0-9]+', '-', simple).strip('-')[:40].strip('-')
    return found if re.match(PROJECT_ID, found) else 'projet'


def creer(titre: str, resume: str = '') -> dict:
    """Crée un projet vide dans les projets de la personne (<AURORA_HOME>/projets/<identifiant>/projet.json) et rend
    son état, comme load. ValueError, avec un message pour la personne : titre trop court ou trop long, trop de
    projets, dossier impossible à écrire. Un identifiant déjà pris reçoit un numéro (« lampe-2 »)."""
    try:
        asked = NouveauProjet(titre=titre, resume=resume)
    except ValidationError:
        raise ValueError('Donnez un titre au projet, de 2 à 100 caractères ; son résumé en a 600 au plus.') from None
    if len(_folders()) >= MAX_PROJECTS:
        raise ValueError(f'AURORA lit {MAX_PROJECTS} projets au plus : retirez-en un avant d’en créer un autre.')
    root = Path(aurora_home()) / 'projets'
    taken = {p['id'] for p in catalogue()['projets']}
    base = _identifiant(asked.titre)
    found = base
    for n in range(2, 1000):
        if found not in taken and not (root / found).exists():
            break
        found = f'{base[:40 - len(str(n)) - 1].rstrip("-")}-{n}'
    else:
        raise ValueError('Trop de projets portent déjà ce nom : donnez un autre titre.')
    project = Projet(format=FORMAT, id=found, titre=asked.titre, resume=asked.resume, stade='idée',
                     maj=date.today().strftime('%d/%m/%Y'), rubriques=[Rubrique(id=r) for r in RUBRIQUES])
    folder = root / found
    try:
        folder.mkdir(parents=True, exist_ok=False)
        (folder / MANIFEST).write_text(json.dumps(project.model_dump(), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    except OSError:
        log.warning('Projet non créé : dossier %s impossible à écrire', folder)
        raise ValueError('Le dossier du projet n’a pas pu être créé dans vos données d’AURORA.') from None
    return load(found)


# --------------------------------------------------------------------------- les calculs en premier (13/11/2026)
# ID Studio IA : « conçois-moi un robot quadrupède de cinq kilos, avec douze articulations et deux heures d'autonomie ». Avant
# tout dessin, AURORA confronte la demande aux calculs du projet. Les chiffres viennent du programme du projet (sa
# fonction « dimensionner »), jamais d'une IA ; ce module ne fait que l'appeler et rendre ce qu'il rend.
_CALC_MODULES: dict[str, tuple[float, object]] = {}     # chemin → (date du fichier, module chargé)


def _calc_module(path: Path):
    """Le programme de calcul d'un projet, chargé une fois et rechargé quand son fichier change."""
    stamp = path.stat().st_mtime
    kept = _CALC_MODULES.get(str(path))
    if kept and kept[0] == stamp:
        return kept[1]
    spec = importlib.util.spec_from_file_location(f'aurora_calcul_{len(_CALC_MODULES)}', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _CALC_MODULES[str(path)] = (stamp, module)
    return module


def programme(project_id: str, section: str) -> dict | None:
    """Le programme (.py) qu'AURORA peut lancer pour une rubrique d'un projet (« calculs » : celui qui répond à un
    cahier des charges ; « fabrication » : celui qui construit tout le projet) : {'projet', 'titre', 'programme',
    'path', 'dossier'}. None : projet inconnu. ValueError, avec un message pour la personne : pas de programme,
    projet personnel, ou fichier absent. Rien n'est lancé ici."""
    if not isinstance(project_id, str) or not re.match(PROJECT_ID, project_id):
        return None
    what = 'calcul' if section == 'calculs' else 'fabrication'
    for origin, folder in _folders():
        project, _why = _read(folder)
        if project is None or project.id != project_id:
            continue
        program = _calc_program(project, section)
        if not program:
            raise ValueError(f'Le projet « {project.titre} » n’a pas de programme de {what} (un fichier .py dans sa '
                             f'rubrique « {RUBRIQUES.get(section, section)} »).')
        if origin not in TRUSTED_ORIGINS:
            raise ValueError(f'Le programme de {what} d’un projet personnel ne s’exécute pas encore dans AURORA : il '
                             'lui faudra votre accord.')
        base = folder.resolve()
        path = (base / program).resolve()
        if base not in path.parents or not path.is_file():
            raise ValueError(f'Le programme de {what} « {program} » est introuvable dans le projet.')
        return {'projet': project.id, 'titre': project.titre, 'programme': program, 'path': path, 'dossier': base}
    return None


def dimensionner(project_id: str, masse_kg: float, autonomie_min: float, articulations: int = 12) -> dict | None:
    """Un cahier des charges (masse, autonomie, articulations) confronté aux calculs d'UN projet : ce que rend la
    fonction « dimensionner » de son programme, avec le nom du projet et du programme. None : projet inconnu.
    ValueError, avec un message pour la personne : pas de programme déclaré, projet personnel (son programme ne
    s'exécute pas ici), demande refusée par le programme, ou programme en panne. Aucun chiffre n'est inventé."""
    found = programme(project_id, 'calculs')     # le même chemin que pour la fabrication : un seul endroit le retrouve
    if found is None:
        return None
    program, path = found['programme'], found['path']
    try:
        answer = getattr(_calc_module(path), 'dimensionner', None)
        if answer is None:
            raise ValueError(f'Le programme « {program} » ne sait pas répondre à un cahier des charges (il lui '
                             'manque la fonction « dimensionner »).')
        result = answer(masse_kg, autonomie_min, articulations)
    except ValueError as exc:            # le programme refuse la demande : c'est son message
        raise ValueError(str(exc)[:300]) from None
    except Exception as exc:
        log.exception('Programme de calcul en panne : %s', path)
        raise ValueError(f'Le programme de calcul « {program} » a échoué ({type(exc).__name__}) : aucun chiffre '
                         'n’est donné.') from None
    if not isinstance(result, dict) or not isinstance(result.get('lignes'), list) or 'verdict' not in result:
        raise ValueError(f'Le programme de calcul « {program} » n’a pas rendu un résultat lisible : aucun chiffre '
                         'n’est donné.')
    return {'projet': found['projet'], 'titre': found['titre'], 'programme': program, **result}


# La même demande, dite ou écrite dans le chat : « conçois-moi un robot quadrupède de cinq kilos, avec douze
# articulations et deux heures d'autonomie ». Les nombres sont lus dans la phrase par ces expressions, jamais par un
# modèle ; il faut un robot, une masse et un mot qui demande une étude, sinon la phrase suit son chemin habituel.
_ASKS = re.compile(r'\b(?:concois|concevoir|concevez|dimensionn\w+|calcul\w+|etudi\w+|faisable|faisabilite|possible)\b')
_SUBJECT = re.compile(r'\b(?:robots?|quadrupedes?)\b')
_MASS = re.compile(r'(\d+(?:\.\d+)?) ?(?:kg|kilos?|kilogrammes?)\b( et demi\b)?')
_HOURS = re.compile(r'(\d+(?:\.\d+)?) ?(?:h|heures?)\b(?: et (demie?)\b| ?(\d{1,2})\b(?! ?(?:kg|kilo|articulation|servo|moteur)))?')
_MINUTES = re.compile(r'(\d+(?:\.\d+)?) ?(?:min|mn|minutes?)\b')
_JOINTS = re.compile(r'(\d+) ?(?:articulations?|servomoteurs?|servos?|moteurs?)\b')


def demande_de_calcul(text: str) -> dict | None:
    """Les nombres d'un cahier des charges dit en une phrase : {'projet', 'masse_kg', 'autonomie_min' (None si elle
    n'est pas dite), 'articulations' (12 si elles ne sont pas dites)}. None quand la phrase ne demande pas l'étude
    d'un robot d'une masse donnée, ou qu'aucun projet n'a de programme de calcul qu'AURORA peut appeler."""
    from ..core.normalize import normalize, words_to_numbers   # lazy: the core modules import the tools
    said = words_to_numbers(normalize(text or ''))
    mass = _MASS.search(said)
    if not (mass and _SUBJECT.search(said) and _ASKS.search(said)):
        return None
    able = [p for p in catalogue()['projets'] if p.get('calcul')]
    if not able:
        return None
    words = set(said.split())
    named = lambda p: len(words & set(re.sub(r'[^a-z0-9]+', ' ', normalize(f"{p['titre']} {p['id']}")).split()))   # noqa: E731
    hours, minutes, joints = _HOURS.search(said), _MINUTES.search(said), _JOINTS.search(said)
    autonomy = None
    if hours:
        autonomy = float(hours.group(1)) * 60 + (30 if hours.group(2) else float(hours.group(3) or 0))
    elif minutes:
        autonomy = float(minutes.group(1))
    return {'projet': max(able, key=named)['id'], 'masse_kg': float(mass.group(1)) + (0.5 if mass.group(2) else 0),
            'autonomie_min': autonomy, 'articulations': int(joints.group(1)) if joints else 12}


class SizingParams(Params):
    projet: str = Field(pattern=PROJECT_ID)
    masse_kg: float
    autonomie_min: float
    articulations: int = 12


@tool('calc.projet', description='Confronter un cahier des charges (masse, autonomie, articulations) au '
      'programme de calcul d’un projet : couples, énergie, masse, ce qui est tenu ou non. Sans réseau ; les chiffres '
      'viennent du programme du projet, jamais d’une IA.', action='system.read', params=SizingParams,
      provenance='projet')
def sizing_tool(p: SizingParams) -> ToolResult:
    try:
        found = dimensionner(p.projet, p.masse_kg, p.autonomie_min, p.articulations)
    except ValueError as exc:
        raise ToolError(f'{exc} Aucun calcul n’a été fait.') from None
    if found is None:
        raise ToolError(f'Le projet « {p.projet} » est introuvable. Aucun calcul n’a été fait.')
    source = Source(title=f"Programme de calcul du projet « {found['titre']} » ({found['programme']})", kind='local',
                    retrieved_at=now_iso())
    return ToolResult(ok=True, data=found, summary=str(found.get('resume') or found['verdict']),
                      provenance=f"projet:{found['programme']}", sources=[source])
