"""Action catalogue and the permission rules of each mode (CONTRACT.md §5 « Modes », §6).

Design choice for forbidden actions: `requires_permission` RAISES `PolicyDenied` instead of
returning a boolean. A bare `False` means « no permission needed, go ahead », so returning
it for a forbidden action (cloud.send in private mode) would be an easy way to leak data.
Callers that prefer a non-raising answer use `evaluate()`.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass

log = logging.getLogger(__name__)

ACTIONS: dict[str, dict[str, str]] = {
    'system.read': {'risk': 'low', 'label': "Lire l'état du système (CPU, mémoire, disques, processus)"},
    'web.fetch': {'risk': 'low', 'label': 'Consulter une source publique sur Internet'},
    'files.scan': {'risk': 'low', 'label': 'Lister des fichiers (lecture seule)'},
    'files.quarantine': {'risk': 'medium', 'label': 'Déplacer des fichiers en quarantaine (réversible)'},
    'process.terminate': {'risk': 'high', 'label': 'Arrêter un programme en cours'},
    'cloud.send': {'risk': 'medium', 'label': 'Envoyer un texte à Claude (Anthropic, cloud)'},
    'memory.write': {'risk': 'low', 'label': 'Enregistrer un souvenir dans la mémoire locale'},
    # B2 (04/11/2026) : terminal avec autorisation, désactivé par défaut (tools/shell.py).
    'shell.run': {'risk': 'high', 'label': 'Exécuter une commande dans un terminal'},
    # 9/11/2026 : souris et clavier, un geste à la fois, désactivés par défaut (tools/pc_control.py).
    'pc.control': {'risk': 'high', 'label': 'Agir sur le PC avec la souris et le clavier'},
    # 9/11/2026 : seulement une application de la liste tenue par la personne (tools/apps.py) ; la liste vaut accord.
    'app.launch': {'risk': 'medium', 'label': 'Ouvrir une application de votre liste autorisée'},
    # 9/11/2026 : public, en votre nom (tools/youtube_comments.py) ; « Publier sans redemander » (Paramètres) vaut accord.
    'youtube.comment': {'risk': 'medium', 'label': 'Publier un commentaire YouTube en votre nom'},
    # 16/11/2026 : des mots gardés dans AURORA et montrés à un moment (tools/reminders.py) ; rien ne sort, rien n'est touché.
    'reminder.set': {'risk': 'low', 'label': 'Noter, lister ou annuler un rappel ou un minuteur dans AURORA'},
}
MODES = ('prive', 'equilibre', 'avance')
DEFAULT_MODE = 'equilibre'
_ALWAYS_ASK = frozenset({'files.quarantine', 'process.terminate', 'shell.run', 'pc.control'})
_MODE_ALIASES = {'privé': 'prive', 'private': 'prive', 'équilibré': 'equilibre', 'equilibré': 'equilibre',
                 'balanced': 'equilibre', 'avancé': 'avance', 'advanced': 'avance'}


class PolicyDenied(PermissionError):
    """The action is forbidden in the current mode; no consent can unlock it."""

    def __init__(self, action: str, mode: str, message_fr: str) -> None:
        super().__init__(message_fr)
        self.action = action
        self.mode = mode
        self.message_fr = message_fr


@dataclass
class PolicyDecision:
    action: str
    mode: str
    sensitivity: str
    allowed: bool
    requires_permission: bool
    risk: str
    reason: str          # French, shown in the interface

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_mode(mode: str | None) -> str:
    """Unknown or empty modes fall back to the most restrictive one (fail closed)."""
    value = (mode or '').strip().lower()
    value = _MODE_ALIASES.get(value, value)
    if value not in MODES:
        if value:
            log.warning('Unknown mode %r treated as private', mode)
        return 'prive'
    return value


def risk_of(action: str, sensitivity: str = 'normal') -> str:
    base = ACTIONS.get(action, {}).get('risk', 'high')
    if action == 'cloud.send' and sensitivity == 'sensitive':
        return 'high'
    return base


def evaluate(action: str, *, mode: str, sensitivity: str = 'normal') -> PolicyDecision:
    mode = normalize_mode(mode)
    sensitive = sensitivity == 'sensitive'
    risk = risk_of(action, sensitivity)

    def decide(allowed: bool, ask: bool, reason: str) -> PolicyDecision:
        return PolicyDecision(action, mode, 'sensitive' if sensitive else 'normal', allowed, ask, risk, reason)

    if action not in ACTIONS:
        log.warning('Unknown action %r: permission required', action)
        return decide(True, True, 'Action non répertoriée : confirmation requise.')
    if action in _ALWAYS_ASK:
        return decide(True, True, 'Action modifiant votre système : confirmation requise à chaque fois.')
    if action == 'cloud.send':
        if mode == 'prive':
            return decide(False, False, "Mode privé : aucun envoi vers le cloud n'est autorisé.")
        # Owner's rule (CLAUDE.md): what leaves for the cloud is always shown first, in every mode.
        if sensitive:
            return decide(True, True, 'Contenu sensible : le texte masqué vous est montré et votre accord est requis.')
        return decide(True, True, 'Chaque envoi à Claude vous est montré avant et demande votre accord.')
    if action == 'web.fetch':
        if mode == 'prive':
            return decide(True, True, 'Mode privé : chaque accès à Internet demande votre accord.')
        return decide(True, False, 'Accès en lecture à une source publique.')
    if action == 'app.launch':
        return decide(True, False, 'Application de votre liste autorisée (Paramètres) : votre liste vaut accord.')
    if action == 'youtube.comment':
        if mode == 'prive':
            return decide(False, False, 'Mode privé : AURORA ne publie rien sur Internet.')
        # The card is always made (exact text, video, channel); « Publier sans redemander » grants it at once.
        return decide(True, True, 'Publication publique en votre nom : le commentaire vous est montré et attend votre '
                                  'accord, sauf si « Publier sans redemander » est activé (Paramètres › Studio).')
    return decide(True, False, 'Action locale en lecture ou sans risque.')


def requires_permission(action: str, *, mode: str, sensitivity: str = 'normal') -> bool:
    """True if the user must approve; raises PolicyDenied when the action is forbidden."""
    decision = evaluate(action, mode=mode, sensitivity=sensitivity)
    if not decision.allowed:
        raise PolicyDenied(action, decision.mode, decision.reason)
    return decision.requires_permission


def is_allowed(action: str, *, mode: str, sensitivity: str = 'normal') -> bool:
    return evaluate(action, mode=mode, sensitivity=sensitivity).allowed
