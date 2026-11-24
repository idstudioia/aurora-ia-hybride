"""Intent taxonomy of the decision engine (contract §5).

kind:
  measure    -> answered by a tool with a deterministic template, never by an LLM
  action     -> performed by a tool (permission when sensitive)
  lookup     -> a tool fetches data, an LLM may summarise it
  generative -> an LLM produces the answer
"""
from __future__ import annotations

INTENTS: dict[str, dict] = {
    'system.status': {
        'label': 'État du système', 'kind': 'measure', 'slots': ['metric'],
        'description': "mesure directe d'une ressource du PC (CPU, RAM, disque, réseau, GPU, batterie)",
    },
    'system.processes': {
        'label': 'Processus actifs', 'kind': 'measure', 'slots': ['sort'],
        'description': 'liste des programmes / processus qui consomment le plus',
    },
    'system.diagnose': {
        'label': 'Diagnostic du PC', 'kind': 'measure', 'slots': [],
        'description': "comprendre pourquoi le PC est lent, chauffe, plante ; analyser ou optimiser l'ordinateur",
    },
    'system.kill_process': {
        'label': 'Fermer un programme', 'kind': 'action', 'slots': ['name', 'pid'],
        'description': 'fermer, arrêter ou tuer un programme ou un processus nommé',
    },
    'memory.search': {
        'label': 'Recherche en mémoire', 'kind': 'lookup', 'slots': ['query'],
        'description': "retrouver une information personnelle déjà connue : nos projets, mes notes, nos conversations, reprendre là où on en était",
    },
    'memory.save': {
        'label': 'Mémoriser', 'kind': 'action', 'slots': ['content', 'category'],
        'description': "demande explicite de retenir une information (« retiens que », « note que », « souviens-toi que »)",
    },
    'web.search': {
        'label': 'Recherche web', 'kind': 'lookup', 'slots': ['query'],
        'description': 'chercher des informations publiques sur Internet / Wikipédia, dernières découvertes',
    },
    'news.latest': {
        'label': 'Actualités', 'kind': 'lookup', 'slots': ['topic', 'monde'],
        'description': 'actualités, nouvelles, infos du jour, flux RSS (du monde, d’un pays, ou scientifiques)',
    },
    'weather.current': {
        'label': 'Météo', 'kind': 'measure', 'slots': ['city'],
        'description': 'météo, température extérieure, pluie, vent dans une ville',
    },
    'quakes.recent': {
        'label': 'Séismes récents', 'kind': 'measure', 'slots': [],
        'description': 'séismes et tremblements de terre récents',
    },
    'calc.evaluate': {
        'label': 'Calcul', 'kind': 'measure', 'slots': ['expression'],
        'description': 'calcul arithmétique avec des nombres',
    },
    'calc.convert': {
        'label': "Conversion d'unités", 'kind': 'measure', 'slots': ['value', 'from_unit', 'to_unit'],
        'description': "conversion d'une valeur d'une unité vers une autre",
    },
    'time.now': {
        'label': 'Date et heure', 'kind': 'measure', 'slots': [],
        'description': "heure actuelle, date du jour, jour de la semaine",
    },
    'sim.orbit': {
        'label': 'Simulation orbitale', 'kind': 'measure', 'slots': ['body', 'altitude_km'],
        'description': "simuler l'orbite ou la trajectoire d'un satellite ou d'une sonde autour d'un astre",
    },
    'sim.solar_system': {
        'label': 'Système solaire', 'kind': 'measure', 'slots': ['date'],
        'description': 'position actuelle des planètes, vue du système solaire',
    },
    'files.scan': {
        'label': 'Analyse de fichiers', 'kind': 'lookup', 'slots': ['folder', 'older_than_days'],
        'description': 'lister / examiner des fichiers ou dossiers sans rien modifier',
    },
    'files.cleanup': {
        'label': 'Nettoyage de fichiers', 'kind': 'action', 'slots': ['folder', 'older_than_days'],
        'description': 'supprimer, nettoyer, faire le ménage dans des fichiers (quarantaine réversible)',
    },
    'files.restore': {   # B4 (rapport §4.6): until then only the « Restaurer » button did it
        'label': 'Restauration de fichiers', 'kind': 'action', 'slots': [],
        'description': 'remettre à leur place les fichiers mis en quarantaine par un nettoyage d’AURORA',
    },
    'doc.analyze': {
        'label': 'Analyse de document', 'kind': 'generative', 'slots': [],
        'description': 'analyser, résumer, expliquer un document, un texte, un code ou une erreur fournis',
    },
    'task.plan': {
        'label': 'Planification', 'kind': 'generative', 'slots': ['goal'],
        'description': 'organiser, planifier, préparer un plan ou une liste de tâches',
    },
    'shell.run': {   # B2 (04/11/2026): terminal avec autorisation, désactivé par défaut (tools/shell.py)
        'label': 'Commande de terminal', 'kind': 'action', 'slots': ['command', 'cwd'],
        'description': 'exécuter une commande explicite de terminal, demandée mot pour mot (« exécute : … », '
                       '« lance la commande … », « terminal : … »)',
    },
    'pc.control': {   # 9/11/2026: souris et clavier, un geste à la fois après accord (tools/pc_control.py)
        'label': 'Souris et clavier', 'kind': 'action', 'slots': ['gesture'],
        'description': 'agir sur le PC avec la souris ou le clavier : cliquer à une position, taper un texte dans '
                       'une fenêtre, appuyer sur une touche, faire défiler ; ou demander où est la souris',
    },
    'app.launch': {   # 9/11/2026: une application de la liste autorisée (Paramètres), tools/apps.py
        'label': 'Ouvrir une application', 'kind': 'action', 'slots': ['app'],
        'description': 'ouvrir, lancer ou démarrer une application ou un logiciel du PC par son nom (« ouvre '
                       'l’application YouTube Manager », « lance Spotify »)',
    },
    'youtube.comment': {   # 9/11/2026: un commentaire sous la dernière vidéo de la chaîne reliée (tools/youtube_comments.py)
        'label': 'Commentaire YouTube', 'kind': 'action', 'slots': ['text', 'video', 'remove'],
        'description': 'publier un commentaire sous la dernière vidéo de ma chaîne YouTube (« mets un commentaire sous '
                       'ma dernière vidéo », « commente ma dernière vidéo : … »), ou retirer celui qu’AURORA a publié',
    },
    'ui.navigate': {
        'label': 'Navigation', 'kind': 'action', 'slots': ['page', 'globe', 'scene', 'sujet_3d'],
        'description': "ouvrir ou afficher une page de l'interface (accueil, mémoire, globe, tâches, paramètres…), "
                       "ou une scène 3D du laboratoire de l'accueil (« montre-moi un bras robotique ») et ses "
                       "commandes (« démonte-le », « explique cette pièce »)",
    },
    'meta.capabilities': {
        'label': 'Capacités', 'kind': 'lookup', 'slots': [],
        'description': "questions sur AURORA elle-même : accès Internet, ce qu'elle sait faire, modèles utilisés",
    },
    'routine.run': {     # D1: a saved list of requests, launched by its phrase (tasks/routines.py)
        'label': 'Routine', 'kind': 'action', 'slots': [],
        'description': 'lancer une routine enregistrée (une suite de demandes), par son nom ou sa phrase',
    },
    'reminder.set': {    # C3 (16/11/2026): the moment is read by the code, the service says it when it comes (tasks/reminders.py)
        'label': 'Rappel ou minuteur', 'kind': 'action', 'slots': ['due', 'text', 'kind'],
        'description': 'programmer un rappel, un minuteur ou un réveil pour une heure ou après une durée (« rappelle-moi '
                       'à 18 h d’appeler Paul », « minuteur de 10 minutes », « réveille-moi à 7 h »), lister ceux à '
                       'venir ou les annuler',
    },
    'agent.run': {       # P1: the model chooses the tools turn after turn (core/agent.py)
        'label': 'Agent en plusieurs étapes', 'kind': 'generative', 'slots': [],
        'description': "confier une tâche à l'agent, qui enchaîne lui-même les outils autorisés, tour après tour",
    },
    'data.analyze': {    # P3: a table (CSV) computed by the code; the model sees its structure and the results only
        'label': 'Analyse de données', 'kind': 'lookup', 'slots': ['source'],
        'description': 'analyser un tableau de données (fichier CSV joint ou dans un dossier) : totaux, tendances, '
                       'valeurs anormales, comparaison de périodes',
    },
    'chat.general': {
        'label': 'Conversation', 'kind': 'generative', 'slots': [],
        'description': 'conversation, explication, rédaction, question de culture générale, tout le reste',
    },
}

# Stable order used for deterministic tie-breaking (contract order).
INTENT_ORDER: tuple[str, ...] = tuple(INTENTS)

PAGES: tuple[str, ...] = ('home', 'chat', 'search', 'computer', 'world', 'simulations', 'memory', 'tasks', 'settings')

DEFAULT_INTENT = 'chat.general'


def intent_kind(intent: str) -> str:
    return INTENTS.get(intent, INTENTS[DEFAULT_INTENT])['kind']


def intent_label(intent: str) -> str:
    return INTENTS.get(intent, INTENTS[DEFAULT_INTENT])['label']


def is_intent(name: str | None) -> bool:
    return bool(name) and name in INTENTS
