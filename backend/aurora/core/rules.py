"""Weighted rule router: regexes on normalised French text -> score per intent.

Scoring: positive evidences of an intent combine as a noisy-OR (1 - prod(1 - w)); penalties
multiply the score by (1 - p). Rules are written from linguistic reasoning and the training
examples (aurora/data/router_examples.jsonl), never tuned on the benchmark corpus.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .intents import DEFAULT_INTENT, INTENT_ORDER
from .normalize import (HARDWARE_IDENTITY, UNIT_PATTERN, globe_place_named, normalize, scene_3d_asked, scene_subject,
                        split_clauses)

CHAT_PRIOR = 0.2          # baseline of chat.general: weak cues must beat it
MIN_RULE_SCORE = 0.35     # below this, a non-chat intent is not considered matched
SECONDARY_MIN = 0.6       # a clause must reach this to add a secondary intent


@dataclass(frozen=True)
class Rule:
    intent: str
    weight: float
    pattern: re.Pattern
    label: str


@dataclass
class RuleResult:
    intent: str
    confidence: float
    score: float
    scores: dict[str, float]
    matched: dict[str, list[str]]
    secondary_intents: list[str] = field(default_factory=list)
    alternatives: list[tuple[str, float]] = field(default_factory=list)
    clause_intents: list[str] = field(default_factory=list)
    clauses: list[tuple[str, str]] = field(default_factory=list)   # (original clause, its intent)

    @property
    def matched_any(self) -> bool:
        return self.intent != DEFAULT_INTENT or bool(self.matched.get(DEFAULT_INTENT))


# --------------------------------------------------------------------------- vocabulary fragments

PC = r'(?:pc|ordinateur|ordinateurs|machine|systeme|windows|laptop|portable|unite centrale|bécane|becane)'
CPU = r'(?:cpu|processeur|processeurs)'
GPU = r'(?:gpu|carte graphique|carte video|vram)'
DISK = r'(?:disque|disques|disque dur|ssd|hdd|stockage|partition)'
NET = r'(?:reseau|debit|bande passante|wifi|ping|upload|download)'
BAT = r'(?:batterie|autonomie)'
METRIC = rf'(?:{CPU}|ram|memoire vive|{DISK}|{GPU}|{BAT}|{NET})'
SLOW_STRONG = (r'(?:rame|rament|ramer|ralentit|ralentissent|ralentissement|ralentissements|lag|lague|laggue|'
               r'freeze|freezes|fige|figent|plante|plantent|plantage|plantages|crash|crashe|surchauffe|bug|bugge|'
               r'beugue|saccade|saccades|mouline|lenteur|lenteurs)')
SLOW = (rf'(?:{SLOW_STRONG}|lent|lente|lents|lentes|lentement|chauffe|bloque|bloquee|patine|galere|souffle|'
        r'met (?:du temps|longtemps|des heures|une eternite)|n avance pas|tourne au ralenti|au ralenti|'
        r'fait (?:du|un|beaucoup de|un drole de) bruit|bruit|bruyant)')
TECH = rf'(?:{PC}|internet|connexion|wifi|reseau|jeu|jeux|navigateur|chrome|firefox|demarrage|appli|application|logiciel|windows|ecran|souris|clavier|ventilateur|ventilateurs)'
POSS = r'(?:notre|nos|mon|ma|mes)'
SECRET = (r'(?:mots? de passe|mdp|password|code secret|codes? pin|code d acces|codes? (?:de l |d )alarme|digicode|'
          r'code wifi|cle wifi|cle wpa)')
MEM_OBJ = (r'(?:projet|projets|notes?|idees?|conversation|conversations|discussion|discussions|echanges?|travail|'
           r'travaux|plans?|souvenirs?|preferences?|decisions?|reunion|reunions|session|seance|liste)')
KILL = (r'(?:ferme|fermer|fermes|arrete|arreter|arretes|tue|tuer|termine|terminer|quitte|quitter|kill|stoppe|'
        r'stopper|coupe|couper|force la fermeture d|force la fermeture de|force la fermeture du|'
        r'forcer la fermeture de|mets fin a|met fin a)')
APPS = (r'(?:chrome|google chrome|firefox|edge|microsoft edge|opera|brave|spotify|discord|steam|teams|zoom|skype|'
        r'slack|word|excel|powerpoint|outlook|onenote|notepad|bloc notes|vlc|obs|photoshop|illustrator|blender|'
        r'unity|whatsapp|telegram|vscode|vs code|visual studio|python|java|javaw|node|explorateur|onedrive|'
        r'dropbox|itunes|netflix|minecraft|fortnite|valorant|ollama|anydesk|teamviewer|utorrent|qbittorrent|'
        r'thunderbird|acrobat|paint|calculatrice|epic games|battle net|origin|uplay|signal|notion|figma|'
        r'virtualbox|vmware|docker)')
FILES = (r'(?:fichiers?|dossiers?|documents|telechargements|bureau|images|photos|videos|doublons|'
         r'pieces jointes|corbeille|temporaires|cache)')
# A folder named in a size question (« le dossier Documents », « mes téléchargements », « cheminlocal »).
FOLDER_NAMES = (r'(?:dossiers?|repertoires?|documents|telechargements|bureau|images|photos|videos|musique|'
                r'cheminlocal)')
OLD = (r'(?:vieux|vieilles|anciens|anciennes|gros|grands|volumineux|lourds|inutiles|inutilises|oublies|doublons|'
       r'temporaires|obsoletes|dupliques|en double|plus anciens|plus gros|plus lourds|perimes|inutilisees)')
# Size criterion on files (« de plus de 500 Mo », « d'au moins 2 Go », « 1 Go ou plus »).
SIZE_UNIT = r'(?:ko|mo|go|to|kb|mb|gb|tb|mega|megas|giga|gigas|megaoctets|gigaoctets|teraoctets)'
FILE_SIZE = (r'(?:\b(?:plus de|superieurs? a|superieures? a|au moins|au dela de|depassant|plus gros que|'
             rf'plus lourds? que|plus grands? que|minimum) \d+(?:\.\d+)? ?{SIZE_UNIT}\b|'
             rf'\b\d+(?:\.\d+)? ?{SIZE_UNIT} (?:ou plus|et plus|minimum|au minimum)\b)')
CLEAN = (r'(?:supprime|supprimer|supprimes|supprimez|efface|effacer|effaces|nettoie|nettoyer|nettoies|vide|vider|'
         r'vides|elimine|eliminer|jette|jeter|vire|virer|debarrasse moi|debarrasse|debarrasser|degage|degager|'
         r'purge|purger|libere|liberer|mets en quarantaine|met en quarantaine|mettre en quarantaine|'
         r'deplace en quarantaine|fais le menage|faire le menage|fais du menage|fais du tri|faire du tri|'
         r'fais le tri|range|ranger|trie|trier)')
DOC = (r'(?:document|documents joints?|texte|fichier joint|piece jointe|pdf|rapport|article|code|script|contrat|'
       r'email|mail|courriel|message|lettre|cv|erreur|erreurs|message d erreur|log|logs|trace|stack trace|'
       r'exception|paragraphe|extrait|chapitre|these|compte rendu|presentation|tableau|csv|json|xml|notice|'
       r'manuel|publication|etude|papier|redaction|dissertation|programme|fonction|requete sql|traceback)')
DOC_VERB = (r'(?:analyse|analyser|analyses|resume|resumer|resumes|synthetise|synthetiser|relis|relire|corrige|'
            r'corriger|explique|expliquer|traduis|traduire|lis|lire|examine|examiner|regarde|decortique|'
            r'commente|critique|evalue|reformule|reformuler|simplifie|interprete|debogue|deboguer|debugge|'
            r'verifie|verifier|relire|resous|resoudre|comprends|dis moi ce que)')
NAV = (r'(?:ouvre|ouvrir|ouvres|affiche|afficher|montre|montrer|va|aller|allons|vas|(?<!se )passe|passer|bascule|'
       r'basculer|navigue|naviguer|retourne|retour|reviens|revenir|emmene moi|amene moi|charge|accede|acceder|'
       r'direction|go)')
PAGE = (r'(?:accueil|tableau de bord|dashboard|page d accueil|page principale|chat|conversation|discussion|'
        r'recherche|ordinateur|systeme(?! solaire)|moniteur|surveillance|globe|monde|carte du monde|'
        r'carte(?! graphique| mere| video| sd| des seismes)|planisphere|simulations?|simulateur|'
        r'memoire(?! vive| ram| utilisee| libre| disponible)|souvenirs|graphe|graphe de memoire|taches|activite|'
        r'historique|journal|chronologie|timeline|parametres|reglages|configuration|options)')
TERMINAL = r'(?:terminal|terminaux|lignes? de commande|invite de commandes?|powershell|console|shell|cmd)'
PLANETS = r'(?:mercure|venus|terre|mars|jupiter|saturne|uranus|neptune|pluton|planetes?|planetaires?)'
NUM = (r'(?:\d+(?:\.\d+)?|zero|un|une|deux|trois|quatre|cinq|six|sept|huit|neuf|dix|onze|douze|treize|'
       r'quatorze|quinze|seize|vingt|trente|quarante|cinquante|soixante|cent|mille)')
OP = (r'(?:\+|-|\*|/|x|fois|plus|moins|divise par|divisee par|multiplie par|multipliee par|sur|puissance|'
      r'a la puissance|\^|modulo|%)')
U = rf'(?<![a-z0-9])(?:{UNIT_PATTERN})(?![a-z0-9])'
POLITE = (r'(?:peux tu |tu peux |pourrais tu |tu pourrais |pouvez vous |je veux que tu |j aimerais que tu |'
          r'je voudrais que tu |merci de |il faut que tu |est ce que tu peux |essaie de |tu veux bien )?')
DEFINITIONAL = (r'(?:^c est quoi|^qu est ce que c est|^qu est ce qu (?:un|une)|^qu est ce que (?:le|la|les|l)|'
                r'^que signifie|^que veut dire|^definition|^definis|'
                # "explique (moi) (en une phrase / simplement…) ce qu est …" — a few words allowed in between
                r'^(?:explique|expliquez|explique moi|expliquez moi|explique nous)(?: \w+){0,4}? (?:ce qu est|'
                r'ce que c est|ce que sont|comment fonctionnent?|le principe|la difference)|'
                r'^comment fonctionne|^a quoi sert|'
                r'^comment (?:se forme|se forment|se produit|se produisent|marche)|difference entre|'
                r'^pourquoi (?:les|le|la|l) (?!pc|ordinateur|machine|systeme|ventilateur)|'
                r'^comment (?:on|fait on pour) (?:calcule|mesure))')
NARRATIVE = (r'(?:^j ai (?:fait|vu|lu|entendu|regarde|eu|passe|achete|fini|termine|rate|adore|trouve|mange|'
             r'appris)|^hier j|^ce matin j|^je viens de (?:lire|voir|finir|regarder)|^je me souviens|'
             r'^je pense que|^je crois que|^mon (?:chat|chien|ami|amie|frere|pere|fils)|^ma (?:mere|soeur|fille|'
             r'femme|copine)|^il etait une fois)')
STORY = (r'(?:ecris|ecrire|redige|rediger|compose|composer|invente|inventer|imagine|imaginer|raconte|raconter)'
         r'(?: moi| nous)? (?:une |un |des |le |la |l |quelques )?(?:petite |petit |courte |court |jolie |belle )?'
         r'(?:histoire|histoires|poeme|poemes|poesie|blague|blagues|conte|recit|chanson|haiku|nouvelle|lettre|'
         r'discours|scenario|dialogue|slogan|devinette|anecdote|mail|email|message|texte|article|resume|'
         # 28/10, A4 test: « Invente une phrase courte où tu dis que tu as programmé un rappel » went to Capacités
         r'phrase|phrases|replique|repliques|paragraphe|paragraphes)')
FREE_SPACE = r'(?:de l |de la |du |un peu d |un peu de |plus d |plus de )?(?:espace|place)\b'
# A sentence to repeat, to quote or to put in a character's mouth is a text, not an order (28/10, A4 test:
# « Réponds uniquement par cette phrase, mot pour mot : J'ai supprimé 3 fichiers de ton bureau » asked to move
# files from the real Desktop). It only damps the action intents (_MODIFIERS): a question stays a question.
QUOTED = (r'(?:\bmot pour mot\b|\brepete apres moi\b|'
          r'\b(?:reponds|repondez|repete|repetez|redis|recopie|recopiez)(?: moi| nous)?(?: juste| uniquement| seulement|'
          r' simplement| exactement)? (?:par|avec) (?:cette|la|ce|ces|les) (?:phrase|phrases|texte|replique|formule|'
          r'mots?)\b|'
          r'\breponds(?: moi)? (?:juste|uniquement|seulement|simplement|exactement)\b|'
          r'\b(?:dis|ecris|recopie|repete|prononce)(?: moi| nous)? (?:cette phrase|ce texte|la phrase suivante|'
          r'le texte suivant|ces mots)\b|'
          r'\b(?:termine|terminez|commence|commencez|finis|finissez)(?: ta| ton| votre| la| le)? (?:replique|reponse|'
          r'phrase|message|texte|histoire|poeme|lettre) (?:par|avec)\b|'
          r'\b(?:ou|dans laquelle|dans lequel) (?:tu|il|elle|vous|on) (?:dis|dit|dites|annonces?|annoncez|affirmes?|'
          r'pretends?|racontes?) (?:que|qu)\b|'
          r'\b(?:joue|jouez|incarne|incarnez|imite|imitez) (?:le role d (?:un|une)|un personnage|un role)\b)')

META_INTERNET = (r'(?:^|\b(?:est ce que |dis moi si ))(?:as tu|tu as|t as|avez vous|vous avez|disposes tu|tu disposes|'
                 r'possedes tu|peux tu|tu peux|pouvez vous|sais tu|es tu capable d|arrives tu a|tu arrives a|'
                 r'es tu|tu es)(?: vraiment| bien)? (?:(?:aller|acceder|te connecter|naviguer|surfer|consulter|'
                 r'chercher|acces|une connexion|connecte|connectee)(?: a| au| sur| a l)? )?(?:internet|le web|web|'
                 r'en ligne|le net|reseau)(?: en ce moment| maintenant| actuellement)?\s*$')

# 08/11/2026 (run 28 of the real AURORA): « toi qui fais le travail ou c'est Claude ? » was answered by the local
# model, which claimed Claude's code as its own. Who wrote an answer is read on its run, never guessed by a model.
_MADE = (r'(?:ecrit|ecris|redige|rediges|fait|fais|code|codes|programme|genere|cree|produit|developpe|concu|'
         r'repondu|repond|reponds|travaille|travailles)')
_THIS_STRONG = (r'(?:ca|cela|tout ca|ce code|ce jeu|ce script|ce programme|cette reponse|ce texte|ce travail|ce message|'
                r'ce fichier html|cette page|ta reponse|ta derniere reponse|ton code|ton jeu|ton script|ton programme|'
                r'ton texte|ton travail)')
_THIS_WEAK = (r'(?:le|la) (?:code|jeu|script|programme|reponse|texte|travail|message|derniere reponse|page html)'
              r'(?: (?:precedente?|d avant|de tout a l heure|d il y a \w+ minutes?|du jeu|ci dessus|au dessus|'
              r'que tu (?:m |nous )?as (?:donnee?|ecrite?|faite?|envoyee?|propose|proposee)))?')
_THIS = rf'(?: {_THIS_STRONG}\b| {_THIS_WEAK}$)'
_SELF = r'(?:toi meme|vous meme|tout seul|toute seule|seul|seule|vraiment|toi|ou claude)'
_WRITERS = r'(?:claude|l ia locale|ollama|anthropic|le cloud|une autre ia|un autre modele)'
ANSWER_AUTHOR = re.compile('|'.join((
    rf'\bqui (?:t |m |nous |vous )?(?:a|as) {_MADE}{_THIS}',
    r'^(?:et |mais )?qui (?:m |nous )?(?:a repondu|me repond|nous repond|repond)$',
    rf'\b(?:c est|est ce) (?:bien |vraiment )?(?:toi|vous|{_WRITERS[3:-1]}|gemma\w*|qwen\w*) qui (?:l |m |nous )?'
    rf'(?:a |as |vient de |viens de )?{_MADE}(?: (?:toi meme|tout seul|toute seule|ca|cela))?(?:$|{_THIS})',
    rf'\b(?:quel|quelle) (?:modele|ia|llm|moteur|intelligence artificielle)(?: \w+)? (?:a |as |t a |m a |vient de )?'
    rf'{_MADE}(?:$|{_THIS})',
    rf'\b(?:quel|quelle) (?:modele|ia|llm|moteur)\b.*\b(?:utilise|utilisee|servi)\b.*\b(?:pour|dans){_THIS}',
    rf'\b(?:tu as|as tu|t as|tu l as|l as tu|vous avez|avez vous|vous l avez|l avez vous) (?:vraiment |bien )?'
    rf'{_MADE}(?:{_THIS}(?: {_SELF})?| {_SELF})$',
    rf'\b{_WRITERS} (?:a |l a |t a |m a |vient de )(?:ecrit|redige|fait|code|programme|genere|cree|repondu)'
    rf'(?:$|{_THIS})',
)))
# « toi … ou Claude », « Claude ou toi »: one of the two did it (with a verb of making, never « qui est le plus fort »).
_EITHER = re.compile(rf'\b(?:toi|tu|vous)\b.*\bou (?:c est |bien |alors |plutot )?{_WRITERS}\b|'
                     rf'\b{_WRITERS}\b.*\bou (?:c est |bien |alors |plutot )?(?:toi|vous)\b')
_ORDER_FIRST = re.compile(r'^(?:fais|faites|ecris|ecrivez|code|codez|cree|creez|genere|generez|redige|redigez|'
                          r'programme|programmez|developpe|refais|reecris)\b')


# « Regarde mon écran », « décris la capture d'écran », « ce qu'il y a sur mon écran » (normalised text).
SCREEN_ASKED = re.compile(r'\b(?:regarde|vois|voir|lis|lire|decris|analyse|observe)(?: moi)? (?:mon|l) ecran\b'
                          r'|\bcaptures? d ecran\b|\bce qu il y a (?:sur|a) (?:mon|l) ecran\b|\bvision de l ecran\b')


def asks_answer_author(normalized: str) -> bool:
    """« C'est toi ou Claude qui a écrit ce code ? », « Quel modèle a répondu ? »: who wrote a previous answer of
    this conversation. A request to make something (« fais-moi un jeu, toi ou Claude ») or a long message is not."""
    text = (normalized or '').strip()
    if not text or len(text.split()) > 20 or _ORDER_FIRST.search(text):
        return False
    if ANSWER_AUTHOR.search(text):
        return True
    return bool(_EITHER.search(text) and re.search(rf'\b{_MADE}\b|\btravail\b', text))


MEASURE_LIKE = ('system.status', 'system.processes', 'quakes.recent', 'weather.current', 'sim.solar_system',
                'sim.orbit', 'files.scan', 'files.cleanup', 'calc.evaluate', 'time.now', 'news.latest')
ACTION_LIKE = ('files.cleanup', 'system.kill_process', 'memory.save', 'ui.navigate', 'shell.run', 'pc.control',
               'app.launch', 'youtube.comment', 'reminder.set')


# E5: a past conversation is recalled (« tu te souviens de… », « on avait parlé de… », « retrouve notre conversation »).
RECALL_ASKED = (r'\b(?:tu te souviens|te souviens tu|tu te rappelles|te rappelles tu|tu t en souviens|'
                r'on (?:en )?(?:avait|a) (?:deja )?(?:parle|discute)|retrouve (?:moi )?(?:notre|nos) '
                r'(?:conversation|conversations|discussion|discussions|echanges))\b')
# P1: the request is handed to the agent (« en mode agent », « Agent : … », « lance l'agent pour … »).
AGENT_ASKED = (r'\b(?:en|mode) agent\b|^agent\b(?! (?:immobilier|secret|commercial|double|de |d ))|'
               r'\b(?:lance|lancer|lancez|utilise|utiliser|utilisez|active|activer|activez|demande a|confie a|'
               r'confier a|confiez a|passe par|passer par)(?: moi)? (?:l |ton |votre |le mode )agent\b')


def _r(intent: str, weight: float, regex: str, label: str) -> Rule:
    return Rule(intent, weight, re.compile(regex), label)


class _Test:
    """A rule whose « pattern » is a test on the normalised text (the scene words come from the catalogue of
    tools/lab.py, read when it changes, not from a fixed regex). Only ``search`` is used by ``analyze``."""

    def __init__(self, test) -> None:
        self._test = test

    def search(self, normalized: str) -> bool:
        return bool(self._test(normalized))


RULES: list[Rule] = [
    # ---------------------------------------------------------------- system.status
    _r('system.status', 0.75, rf'\b(?:combien|quel|quelle|quels|quelles|niveau|pourcentage|taux|utilisation|usage|'
       rf'consommation|charge|occupation|etat|statut|mesure|mesurer|affiche|montre|donne|indique|surveille)\b.*\b{METRIC}\b',
       'question de mesure sur une ressource'),
    _r('system.status', 0.75, rf'\b{METRIC}\b.*\b(?:utilise|utilisee|utilises|utilisees|occupe|occupee|libre|disponible|'
       r'charge|reste|restant|restante|consomme|consommee|a combien|en ce moment|actuellement|pourcentage|niveau|'
       r'temperature|frequence|vitesse|pleine?|sature|saturee)\b', 'ressource + état mesurable'),
    _r('system.status', 0.6, r'\bmemoire\b.*\b(?:utilisee|libre|disponible|occupee|consommee|reste|vive|ram)\b',
       'mémoire vive utilisée'),
    _r('system.status', 0.65, r'\b(?:combien|quelle quantite) de (?:memoire|ram|stockage|place|espace)\b',
       'quantité de mémoire / stockage'),
    _r('system.status', 0.7, rf'\b(?:etat|sante|charge|ressources|performances?|statistiques|stats|chiffres|metriques|'
       rf'indicateurs|consommation|usage)\b (?:actuel(?:le)?s? )?(?:du |de mon |de l |de la |de ma |mon |ma )?{PC}\b',
       'état du PC'),
    _r('system.status', 0.7, rf'\bcomment (?:va|se porte|tient) (?:mon |le |l |ma )?{PC}\b', 'comment va le PC'),
    _r('system.status', 0.5, rf'\b(?:{CPU}|ram|{GPU}|{BAT})\b', 'ressource matérielle citée'),
    # 22/11/2026 (banc de conversation, tour 12) : le numéro de série, la date de fabrication, la garantie d'un
    # composant ne sont lus par aucun outil. La question reste celle de l'état du PC, qui le dit (metric « identity »).
    _r('system.status', 0.85, rf'\b{HARDWARE_IDENTITY}\b.*\b(?:{METRIC}|{PC}|carte mere|ecran)\b|'
       rf'\b(?:{METRIC}|{PC}|carte mere|ecran)\b.*\b{HARDWARE_IDENTITY}\b', 'identité du matériel (non lue)'),
    _r('system.status', 0.7, rf'\btemperature\b.*\b(?:{CPU}|{GPU}|{PC}|composants)\b', 'température matérielle'),
    _r('system.status', 0.7, rf'\b(?:{CPU}|{GPU}|{PC})\b.*\b(?:temperature|degres|chauffe a combien)\b',
       'température matérielle'),
    _r('system.status', 0.7, r'\b(?:espace|place) (?:disque|libre|restant|restante|disponible|qu il reste|qui reste)\b',
       'espace disque'),
    _r('system.status', 0.85, r'\b(?:allume depuis|demarre depuis|uptime|temps de fonctionnement)\b|'
       r'\bdepuis (?:combien de temps|quand)\b.*\b(?:allume|demarre|tourne|en marche)\b',
       'durée depuis le démarrage (mesurée)'),
    _r('system.status', 0.6, r'\bcombien (?:d espace|de place|de stockage|de go|de giga|de gigas)\b', 'espace disque'),
    _r('system.status', 0.7, r'\b(?:debit|vitesse) (?:internet|de connexion|de la connexion|du reseau|reseau|wifi|'
       r'de mon internet)\b', 'débit réseau'),
    _r('system.status', 0.65, r'\b(?:niveau|pourcentage|etat|charge) (?:de |de la |du |de ma )?batterie\b',
       'niveau de batterie'),
    _r('system.status', 0.6, rf'\b(?:mesure|mesurer|surveille|monitore|releve)\b.*\b(?:{PC}|ressources|{METRIC})\b',
       'demande de mesure'),
    _r('system.status', -0.6, r'\b(?:achete|acheter|achat|prix|coute|coutent|vaut le coup|recommande|conseille|'
       r'conseilles|choisir|quel modele|quelle marque|meilleur|meilleure|installer|ajouter de la)\b',
       'achat / conseil, pas une mesure'),
    _r('system.status', -0.6, r'\bcheminlocal\b', 'dossier désigné par un chemin (fichiers)'),

    # ---------------------------------------------------------------- system.processes
    _r('system.processes', 0.85, r'\b(?:quels?|quelles?|liste|lister|listes|montre|affiche|donne|top|classement)\b.*'
       r'\b(?:programmes?|processus|applications?|logiciels?|taches de fond|services|applis?)\b.*'
       r'\b(?:consomm\w*|utilis\w*|prennent|prend|mangent|bouffent|gourmand(?:e|s|es)?|ouverts?|actifs?|actives|en cours|'
       r'tournent|tourne|lances?|lancees|ralentissent)\b', 'liste des programmes actifs / gourmands'),
    _r('system.processes', 0.85, r'\b(?:qu est ce qui|qui|quoi|quel programme|quelle application|quel processus|'
       r'quel logiciel)\b.*\b(?:consomme|bouffe|mange|utilise|prend|monopolise|accapare|pompe)\b.*'
       rf'\b(?:{CPU}|ram|memoire|ressources|batterie|le plus|tout|toute|autant)\b', 'qu’est-ce qui consomme'),
    _r('system.processes', 0.8, r'\b(?:processus|programmes?|applications?|logiciels?) (?:actifs?|en cours|ouverts?|'
       r'qui tournent|lances?|en arriere plan|les plus gourmand(?:e|s|es)?|gourmand(?:e|s|es)?|en memoire)\b', 'processus actifs'),
    _r('system.processes', 0.75, r'\b(?:gestionnaire des taches|liste des processus|top des processus|'
       r'classement des processus)\b', 'gestionnaire des tâches'),
    _r('system.processes', 0.6, r'\bplus gourmand(?:e|s|es)?\b', 'les plus gourmands'),

    # ---------------------------------------------------------------- system.diagnose
    _r('system.diagnose', 0.9, rf'\b{TECH}\b.*\b{SLOW}\b', 'appareil + lenteur / plantage'),
    _r('system.diagnose', 0.85, rf'\b{SLOW_STRONG}\b.*\b{TECH}\b', 'lenteur + appareil'),
    _r('system.diagnose', 0.85, rf'\bpourquoi\b.*\b(?:{SLOW}|{PC})\b', 'pourquoi + lenteur / PC'),
    _r('system.diagnose', 0.85, rf'\b(?:{SLOW}|{PC})\b.*\bpourquoi$', 'lenteur / PC + pourquoi'),
    _r('system.diagnose', 0.9, r'\b(?:diagnostic|diagnostique|diagnostiquer|diagnostiques|depanne|depanner|'
       r'depannage|bilan de sante)\b', 'diagnostic'),
    _r('system.diagnose', 0.85, r'\b(?:analyse|analyser|examine|examiner|inspecte|inspecter|verifie|verifier|check|'
       rf'audit|audite|controle|controler|teste|tester|ausculte)\b (?:\w+ ){{0,3}}(?:{PC}|performances|ressources)\b',
       'analyse du PC'),
    _r('system.diagnose', 0.85, r'\b(?:optimise|optimiser|optimisation|accelere|accelerer|acceleration|booste|booster|'
       r'ameliore|ameliorer|rendre plus rapide|plus rapide|plus fluide|accelere|desengorger)\b.*'
       rf'\b(?:{PC}|performances|demarrage|windows)\b', 'optimiser le PC'),
    _r('system.diagnose', 0.85, rf'\b(?:{PC}|performances|demarrage|windows)\b.*\b(?:optimise|optimiser|optimisation|'
       r'accelere|accelerer|plus rapide|booster|ameliorer)\b', 'optimiser le PC'),
    _r('system.diagnose', 0.8, r'\b(?:qu est ce qui ne va pas|ce qui ne va pas|probleme|problemes|souci|soucis|panne|'
       rf'anomalie|dysfonctionnement|ecran bleu|bsod)\b.*\b{PC}\b', 'problème sur le PC'),
    _r('system.diagnose', 0.8, rf'\b{PC}\b.*\b(?:probleme|problemes|souci|soucis|panne|ne va pas|ne marche pas|'
       r'ne fonctionne pas|ecran bleu|redemarre tout seul|s eteint)\b', 'problème sur le PC'),
    _r('system.diagnose', 0.85, rf'\b(?:cause|causes|raison|raisons|origine)\b.*\b(?:{SLOW}|probleme|lenteur)\b',
       'cause de la lenteur'),
    _r('system.diagnose', 0.55, rf'^(?:{SLOW_STRONG}|ca rame|tout rame|ca lague|ca plante|ca freeze|c est lent|'
       r'tout est lent|tout est bloque)\b', 'lenteur signalée'),
    _r('system.diagnose', 0.5, r'\banalyse (?:le|ce|du|les|ces) (?:probleme|problemes|souci|soucis|bug)\b',
       'analyser le problème'),
    _r('system.diagnose', -0.6, r'\b(?:ecris|redige|raconte|code|programme)\b.*\b(?:script|programme|fonction)\b',
       'écriture de code'),

    # ---------------------------------------------------------------- system.kill_process
    _r('system.kill_process', 0.9, rf'^{POLITE}{KILL} (?:moi )?(?:le |la |l |les )?(?:programme |processus |'
       rf'application |appli |logiciel |jeu )?{APPS}\b', 'fermer une application connue'),
    _r('system.kill_process', 0.8, rf'\b{KILL} (?:moi )?(?:le |la |l |les |ce |cette |cet )?(?:programme|processus|'
       r'application|appli|logiciel|jeu)\b', 'fermer un programme'),
    _r('system.kill_process', 0.9, rf'\b{KILL}\b.*\b(?:pid|processus|process) \d+\b', 'arrêt par PID'),
    _r('system.kill_process', 0.75, rf'\b{APPS}\b.*\b(?:ne repond plus|plante|bloque|freeze|fige)\b.*'
       r'\b(?:ferme|tue|arrete|kill|force|quitte)\w*', 'application bloquée à fermer'),
    _r('system.kill_process', 0.55, rf'^{POLITE}{KILL} [a-z0-9]+$', 'fermer un nom de programme'),
    _r('system.kill_process', -0.9, rf'\b{KILL} (?:de|d)\b|\b(?:ferme|ferme moi) (?:la|le|les|cette|ce) '
       r'(?:page|fenetre|onglet|panneau|porte|bouche|conversation|session|discussion|volet|yeux|rideau)\b',
       'pas un processus'),

    # ---------------------------------------------------------------- shell.run (B2, terminal avec autorisation)
    # normalize() strips « : », so the marker is recognised by its keywords; the exact command is pulled from
    # the RAW text by normalize.extract_command (which does read the « : »/« > »).
    _r('shell.run', 0.9, r'^(?:aurora[ ,]+)?(?:execute|executer|excute|lance|lancer) (?:moi )?(?:la |une |cette )?'
       r'(?:ligne de )?commande\b', 'exécuter une commande'),
    _r('shell.run', 0.85, r'^(?:aurora[ ,]+)?(?:dans le )?(?:terminal|shell|cmd|invite de commande|'
       r'ligne de commande)\b', 'terminal : commande'),
    _r('shell.run', -0.9, r'\b(?:execute|executer|lance|lancer)\b.*\b(?:recherche|plan|routine|simulation|requete|'
       r'programme|jeu|video|musique)\b', 'pas une commande de terminal'),

    # ---------------------------------------------------------------- pc.control (9/11/2026, souris et clavier)
    # The gesture itself is read on the RAW text by normalize.extract_gesture; these rules only recognise the verb.
    _r('pc.control', 0.9, rf'^(?:aurora )?{POLITE}(?:fais |faire )?(?:un )?(?:double )?(?:clique|cliquer|cliques|'
       r'cliquez|clic)\b', 'cliquer'),
    _r('pc.control', 0.85, rf'^(?:aurora )?{POLITE}(?:tape|taper|tapez|ecris|ecrire|ecrivez|saisis|saisir)(?: moi)?'
       r'(?: le texte| ce texte| ceci)? dans (?:la fenetre |l application |le programme )?\w', 'taper dans une fenêtre'),
    _r('pc.control', -0.9, r'\bdans (?:ta|ma|la|notre|sa) memoire\b|\bdans (?:mes|tes|nos) notes\b',
       'mémoriser, pas taper'),
    _r('pc.control', 0.9, rf'^(?:aurora )?{POLITE}(?:appuie|appuies|appuyer|appuyez|presse|presser|pressez|enfonce|'
       r'enfoncer) sur\b', 'appuyer sur une touche'),
    _r('pc.control', 0.9, rf'^(?:aurora )?{POLITE}(?:fais|faire|faites) (?:un )?(?:ctrl|control|controle|alt|maj|'
       r'shift)\b', 'raccourci clavier'),
    _r('pc.control', 0.85, rf'^(?:aurora )?{POLITE}(?:fais defiler|faire defiler|defile|defiler|scrolle|scroller|'
       r'scroll)\b', 'faire défiler'),
    _r('pc.control', 0.9, rf'^(?:aurora )?{POLITE}(?:deplace|deplacer|bouge|bouger|mets|mettre|place|placer|amene|'
       r'amener) (?:la souris|le curseur|le pointeur)\b', 'déplacer la souris'),
    _r('pc.control', 0.85, r'\b(?:ou est|ou se trouve|ou en est|position|coordonnees)\b.*\b(?:souris|curseur|'
       r'pointeur)\b', 'position de la souris'),
    # « clic droit en 300 x 500 »: a position on the screen, not a multiplication
    _r('calc.evaluate', -0.9, rf'^(?:aurora )?{POLITE}(?:(?:fais |faire )?(?:un )?(?:double )?(?:clique|cliquer|'
       r'cliquez|clic)\b|(?:deplace|deplacer|bouge|bouger|mets|mettre|place|placer|amene|amener) (?:la souris|'
       r'le curseur|le pointeur)\b)', 'un geste à une position, pas un calcul'),

    # ---------------------------------------------------------------- app.launch (9/11/2026, applications autorisées)
    # A name of the allowed list is matched before the router (orchestrator._decide, tools/apps.match); these rules
    # catch the other requests to open an app, so the answer can say it is not in the list (Paramètres).
    _r('app.launch', 0.9, rf'^(?:aurora )?{POLITE}(?:ouvre|ouvrir|ouvres|ouvrez|lance|lancer|lances|lancez|demarre|'
       r'demarrer|demarres|demarrez)(?: moi)? (?:l |le |la |mon |ma |une |un )?(?:application|appli|app|logiciel|'
       rf'programme)s?\b(?! (?:qui|que|pour)\b)(?! (?:de |du |des |d )?(?:la |le |l |les )?{PAGE}\b)',
       'ouvrir une application'),
    _r('app.launch', 0.8, rf'^(?:aurora )?{POLITE}(?:ouvre|ouvrir|ouvres|ouvrez|lance|lancer|lances|lancez|demarre|'
       rf'demarrer|demarres|demarrez)(?: moi)? (?:l |le |la )?{APPS}$', 'ouvrir une application connue'),

    # ---------------------------------------------------------------- youtube.comment (9/11/2026, chaîne reliée)
    # The text and the video are read on the RAW text by normalize.extract_comment; these rules recognise the request.
    # « écris-moi / rédige un commentaire pour ma vidéo » asks for a text only: it stays a conversation, nothing posted.
    _r('youtube.comment', 0.95, rf'^(?:aurora )?{POLITE}(?:mets|mettre|met|mettez|poste|poster|postes|postez|publie|'
       r'publier|publies|publiez|laisse|laisser|laisses|laissez|ajoute|ajouter|ajoutes|ajoutez|envoie|envoyer|envoies|'
       r'envoyez)(?: moi| nous)? (?:un |une |le |mon |ce |notre )?(?:petit |gentil |nouveau |court |beau )?commentaire\b'
       r'.*\b(?:video|youtube|(?:ma|notre) chaine)\b', 'commentaire sous une vidéo'),
    _r('youtube.comment', 0.9, rf'^(?:aurora )?{POLITE}(?:ecris|ecrire|ecrivez|redige|rediger|redigez|fais|faire|'
       r'faites) (?:un |une |le )?(?:petit |gentil |court )?commentaire (?:sous|sur) (?:ma|la|mon|notre) (?:derniere |'
       r'nouvelle |plus recente )?video\b', 'écrire un commentaire sous la vidéo'),
    _r('youtube.comment', 0.9, rf'^(?:aurora )?{POLITE}(?:commente|commenter|commentes|commentez)(?: moi)? (?:ma|la|mon|'
       r'notre) (?:derniere |nouvelle |plus recente )?video\b', 'commenter la vidéo'),
    _r('youtube.comment', 0.85, r'\b(?:mettre|poster|publier|laisser|ajouter) un commentaire (?:sous|sur|a) (?:ma|la|'
       r'mon|notre) (?:derniere |nouvelle |plus recente )?video\b', 'mettre un commentaire sous la vidéo'),
    _r('youtube.comment', 0.9, rf'^(?:aurora )?{POLITE}(?:retire|retirer|retirez|supprime|supprimer|supprimez|efface|'
       r'effacer|effacez|enleve|enlever|enlevez)(?: moi)? (?:le |ce |mon |ton )(?:dernier )?commentaire\b.*\b(?:publie|'
       r'poste|mis|video|youtube|tu as)\b', 'retirer le commentaire publié'),

    # ---------------------------------------------------------------- memory.save
    _r('memory.save', 0.95, rf'^{POLITE}(?:retiens|retenir|souviens toi|souviens t en|rappelle toi|memorise|'
       r'memoriser|n oublie pas|n oublies pas|garde en memoire|garde en tete|garde a l esprit|retiens bien|'
       r'enregistre dans ta memoire|ajoute a ta memoire)\b', 'demande explicite de mémoriser'),
    _r('memory.save', 0.9, rf'^{POLITE}(?:note|noter|notes|note bien)\b (?:bien |le fait )?(?:que|qu|ceci|ca|cela|'
       r'dans|pour|mon|ma|mes|cette|ce|cet|l|le|la|les|un|une|mon|qu il|qu elle)\b', 'note que…'),
    _r('memory.save', 0.9, r'\b(?:enregistre|sauvegarde|ajoute|inscris|stocke|mets|garde|range)\b.*\b(?:dans|a|en) '
       r'(?:ta|ma|la|notre) memoire\b', 'enregistrer en mémoire'),
    _r('memory.save', 0.85, r'\b(?:ajoute|ajouter|mets|enregistre) (?:ca |ceci |cela |le |la |l )?(?:a|dans) '
       r'(?:tes|mes|nos) (?:notes|souvenirs)\b', 'ajouter aux notes'),
    _r('memory.save', 0.85, r'\b(?:je veux|j aimerais|je voudrais|il faut) que tu (?:retiennes|te souviennes|'
       r'memorises|notes|saches|gardes en memoire)\b', 'je veux que tu retiennes'),
    _r('memory.save', 0.7, r'\b(?:retiens|memorise|souviens toi|n oublie pas|rappelle toi)\b.*\bque\b',
       'retiens … que'),
    _r('memory.save', -0.95, r'\b(?:je me souviens|je m en souviens|tu te souviens|te souviens tu|vous vous souvenez|'
       r'tu te rappelles|te rappelles tu|je me rappelle|tu t en souviens|t en souviens tu|tu te rappelle)\b',
       'souvenir évoqué, pas une demande'),

    # ---------------------------------------------------------------- memory.search
    # « quel est mon mot de passe Wi-Fi ? »: the user's own secret, looked up locally, never a network measure.
    _r('memory.search', 0.9, rf'\b(?:quel|quelle|quels|quelles|c est quoi|donne moi|redonne moi|dis moi)\b.*'
       rf'(?:\b{POSS} (?:\w+ ){{0,2}}?{SECRET}\b|\b{SECRET} (?:de |d )(?:mon|ma|mes|notre|nos)\b|'
       # « le code de mon alarme » (28/10, A2): an access code of one's own object; a closed list, so that
       # « le code de mon programme » (source code) stays a conversation.
       rf'\bcodes? (?:de |d |du )(?:mon|ma|mes|notre|nos) (?:\w+ ){{0,2}}?(?:alarme|porte|portail|garage|coffre|'
       rf'cadenas|immeuble|interphone|boite aux lettres|box|wifi|carte|telephone|portable|velo|antivol|casier|bip)\b)',
       'mon mot de passe'),
    _r('system.status', -0.8, rf'\b{SECRET}\b', 'mot de passe / code : pas une mesure'),
    _r('memory.search', 0.85, rf'\b(?:retrouve|retrouver|retrouves|recupere|recuperer|ressors|ressortir|redonne)\b.*'
       rf'\b{POSS}\b', 'retrouver nos / mes …'),
    # « Récupère de l'espace sur mon disque » went to the memory (« récupère … mon »): space is freed by a cleanup.
    _r('memory.search', -0.8, r'\b(?:recupere|recuperer|recuperez|retrouve|retrouver|gagne|gagner) (?:de l |de la |du |un peu d |'
       r'un peu de |plus d |plus de )?(?:espace|place)\b', 'récupérer de l’espace : un nettoyage'),
    _r('memory.search', 0.9, r'\b(?:tu te souviens|te souviens tu|te rappelles tu|tu te rappelles|tu te rappelle|'
       r'vous souvenez vous|tu t en souviens|t en souviens tu|t en rappelles tu)\b', 'tu te souviens…'),
    _r('memory.search', 0.85, r'\brappelle moi (?:ce que|ce qu|de quoi|le nom|mon|ma|mes|notre|nos|quel|quelle|'
       r'quels|quelles|ou en|la ou)\b', 'rappelle-moi ce que…'),
    _r('memory.search', 0.85, r'\b(?:dans|de) (?:ma|ta|la|notre) memoire\b', 'dans ta mémoire'),
    _r('memory.search', 0.85, r'\b(?:qu est ce que|qu est ce qu|que|ce que|qu) (?:tu sais(?! faire)|tu connais|'
       r'tu as retenu|tu as memorise|tu as note|tu as garde|je t ai dit|je t ai raconte|je t ai appris|on a dit|'
       r'on s est dit|nous avons dit|on avait dit|nous avions dit|on a decide|nous avons decide|on avait decide)\b',
       'ce que tu sais / ce qu’on a dit'),
    _r('memory.search', 0.9, r'\b(?:que|qu est ce que|qu|quoi|ce que) (?:sais tu|connais tu|tu sais|tu connais|'
       r'as tu retenu|tu as retenu|as tu memorise|tu as memorise)(?: \w+){0,2}? (?:de|sur|a propos de|concernant) '
       r'(?:moi|mon|ma|mes|nous|notre|nos)\b|\btu sais quoi (?:de|sur) (?:moi|nous)\b', 'ce que tu sais de moi'),
    _r('memory.search', 0.9, r'\b(?:la ou|ou) (?:nous en etions|on en etait|nous nous etions arretes|'
       r'on s etait arrete|j en etais|nous en sommes|on en est)\b', 'reprendre là où on en était'),
    _r('memory.search', 0.7, r'^(?:reprends|reprenons|reprendre|on reprend|continue|continuons)\b', 'reprendre'),
    _r('memory.search', 0.8, r'\b(?:notre|nos) (?:dernier |derniere |derniers |dernieres )?(?:projet|projets|'
       r'conversation|conversations|discussion|discussions|echanges|idees|travail|travaux|decisions|reunion|session|'
       r'seance|plan|plans)\b', 'notre projet / nos échanges'),
    _r('memory.search', 0.85, r'\b(?:mes|nos) (?:notes|souvenirs|memos)\b', 'mes notes'),
    _r('memory.search', 0.6, r'\bmes (?:preferences|projets|idees)\b', 'mes préférences / projets'),
    _r('memory.search', 0.75, r'\b(?:on avait parle|on a parle|nous avons parle|on a discute|nous avons discute|'
       r'on parlait|tu m avais dit|je t avais dit|je t ai parle|je t avais parle|on en a parle|on en avait parle|'
       r'nous en avons parle)\b', 'on en avait parlé'),
    _r('memory.search', 0.7, r'\b(?:derniere|precedente|ancienne) (?:conversation|discussion|session)\b',
       'conversation précédente'),
    # E5 (29/10/2026): « nos dernières conversations », « les conversations d'hier » list the past ones.
    _r('memory.search', 0.85, r'\b(?:dernieres|precedentes|anciennes|autres) (?:conversations|discussions)\b|'
       r'\bconversations? (?:precedentes?|passees?|d hier|d avant)\b', 'conversations passées'),
    _r('memory.search', 0.8, r'\b(?:ouvre|rouvre|reouvre|charge|recharge) (?:mon|notre|le|ce) projet\b',
       'rouvrir le projet'),
    _r('memory.search', 0.55, r'\b(?:retrouve|retrouver)\b', 'retrouver'),
    _r('memory.search', 0.6, r'\b(?:est ce que je t ai|je t ai deja) (?:dit|parle|raconte)\b', 'déjà dit'),
    _r('memory.search', -0.5, r'\b(?:sur internet|sur le web|en ligne|wikipedia|sur google|dernieres decouvertes|'
       r'articles? scientifiques?|publications?)\b', 'cible publique (web)'),

    # ---------------------------------------------------------------- web.search
    _r('web.search', 0.85, rf'^{POLITE}(?:recherche|rechercher|cherche|chercher|cherches|recherches|trouve moi|'
       r'trouve|cherche moi|recherche moi)\b(?! (?:dans|en) (?:ma|ta|la|notre|mes) memoire)', 'recherche…'),
    _r('web.search', 0.9, r'\b(?:sur|dans|via|avec|par) (?:internet|le web|la toile|google|wikipedia|wiki|le net)\b',
       'sur Internet'),
    _r('web.search', 0.5, r'\ben ligne\b', 'en ligne'),
    _r('web.search', 0.85, r'\b(?:fais|fait|lance|effectue|faire|lancer)(?: moi)? (?:une |des |un peu de )?'
       r'recherches?\b', 'fais une recherche'),
    # « dis-moi une recherche sur… », « que tu me fasses une petite recherche sur… » (28/10, demande n° 8)
    _r('web.search', 0.85, r'\b(?:une |des |un peu de )?(?:petites? |rapides? |breves? )?recherches? '
       r'(?:rapides? )?(?:sur|concernant|(?:a propos|au sujet) (?:de|du|des|d))\b(?! (?:ma|ta|la|notre) memoire)',
       'une recherche sur…'),
    _r('web.search', -0.6, r'\b(?:ma|ta|sa|notre|votre) recherche\b', '« ma recherche » (nom)'),
    _r('web.search', 0.85, r'\b(?:dernieres?|derniers?|recentes?|recents?|nouvelles?|nouveaux) (?:decouvertes?|'
       r'avancees?|recherches|percees?|etudes?|publications?|travaux|resultats|innovations?|missions?|images)\b',
       'dernières découvertes'),
    _r('web.search', 0.8, r'\b(?:trouve|trouver|donne|cherche|fournis|apporte)(?: moi| nous)? (?:des |les |quelques |'
       r'de la |une |un |d )?(?:informations?|infos?|renseignements?|articles?|sources?|documentation|publications?|'
       r'etudes?|references?|liens?|pages?|definitions?|solutions?|explications?)\b', 'trouve des informations'),
    _r('web.search', 0.8, r'\b(?:renseigne toi|informe toi|documente toi|renseigne moi)\b', 'renseigne-toi'),
    _r('web.search', 0.8, r'\b(?:que dit|selon|d apres|qu en dit) (?:wikipedia|wiki|internet|le web)\b|\bwikipedia\b',
       'Wikipédia'),
    _r('web.search', 0.85, r'\b(?:google|googler|googlise|googlise moi|bing)\b', 'Google'),
    _r('web.search', 0.55, r'\b(?:informations?|infos?|renseignements?) (?:sur|concernant|a propos)\b',
       'informations sur…'),
    _r('web.search', 0.6, r'\b(?:qui a gagne|resultats? (?:du|des|de la) (?:match|elections?)|score du match|'
       r'cours (?:du|de l|de la) (?:bitcoin|euro|dollar|action|bourse))\b', 'fait récent'),
    _r('web.search', -0.6, rf'\b{POSS} (?:{MEM_OBJ})\b(?! (?:sur internet|en ligne))', 'objet personnel (mémoire)'),
    _r('web.search', -0.6, r'\b(?:mes|nos|tes) recherches\b', '« mes recherches » (nom)'),
    _r('web.search', -0.8, META_INTERNET, 'question sur l’accès Internet'),

    # ---------------------------------------------------------------- news.latest
    _r('news.latest', 0.9, r'\b(?:actualite|actualites|nouvelles du jour|infos du jour|informations du jour|'
       r'journal televise|journal du jour|flash info|flux rss|rss|gros titres|titres du jour|a la une|revue de presse)\b',
       'actualités'),
    _r('news.latest', 0.85, r'\bquoi de neuf (?:dans|en|cote|du cote|sur|chez|au niveau)\b', 'quoi de neuf dans…'),
    _r('news.latest', 0.45, r'\bquoi de neuf\b', 'quoi de neuf'),
    _r('news.latest', 0.75, r'\b(?:dernieres?|recentes?) (?:nouvelles|infos|informations|actualites|actus|news)\b',
       'dernières nouvelles'),
    _r('news.latest', 0.8, r'\b(?:les|des) (?:nouvelles|infos)\b.*\b(?:scientifiques?|spatiales?|tech|'
       r'technologiques?|du jour|de la semaine|cette semaine|d aujourd hui|recentes|de ce matin|du moment|'
       r'(?:de la |des )?sciences?)\b', 'les infos du jour'),
    _r('news.latest', 0.6, r'\bqu est ce qui se passe\b.*\b(?:monde|science|espace|actualite|aujourd hui)\b',
       'ce qui se passe'),

    # ---------------------------------------------------------------- weather.current
    _r('weather.current', 0.95, r'\bmeteo\b', 'météo'),
    _r('weather.current', 0.95, r'\bquel temps\b', 'quel temps'),
    _r('weather.current', 0.85, r'\b(?:va t il|va t elle|est ce qu il va|il va|va il|risque t il de) (?:pleuvoir|'
       r'neiger|grele|faire (?:beau|chaud|froid|mauvais|frais|doux)|y avoir (?:de la pluie|du vent|de l orage|'
       r'du soleil|de la neige))\b', 'va-t-il pleuvoir'),
    _r('weather.current', 0.6, r'\b(?:pleut|pleuvoir|pluie|neige|neiger|orage|orages|grele|canicule|averses?|'
       r'brouillard|verglas)\b', 'phénomène météo'),
    _r('weather.current', 0.85, r'\b(?:il fait|fait il|fera t il|il fera|fait t il|ca caille) (?:combien|chaud|'
       r'froid|beau|bon|mauvais|gris|doux|frais|quel temps|combien de degres)\b', 'il fait chaud / froid'),
    _r('weather.current', 0.75, r'\btemperature (?:exterieure|dehors|a|au|en|de la ville|actuelle a|qu il fait)\b',
       'température extérieure'),
    _r('weather.current', 0.65, r'\bcombien de degres\b', 'combien de degrés'),
    _r('weather.current', 0.7, r'\b(?:previsions?|bulletin) (?:meteo|meteorologiques?|du temps|pour demain|'
       r'de la semaine)\b', 'prévisions'),
    _r('weather.current', 0.6, r'\b(?:parapluie|manteau|k way|impermeable|creme solaire)\b', 'équipement météo'),
    _r('weather.current', 0.75, r'\b(?:y a t il|y a|il y a|est ce qu il y a|y aura t il|il y aura) (?:du|de la|des|de l) '
       r'(?:vent|pluie|neige|soleil|orage|orages|brouillard|nuages|averses|grele|verglas)\b', 'y a-t-il du vent / de la pluie'),
    _r('weather.current', 0.55, r'\b(?:vent|humidite|ensoleillement|nuageux|nuages)\b.*\b(?:aujourd hui|demain|'
       r'ce soir|maintenant|dehors)\b', 'conditions du jour'),
    _r('weather.current', -0.85, rf'\btemperature\b.*\b(?:{CPU}|{GPU}|{PC}|composants|carte mere|disque)\b|'
       rf'\b(?:{CPU}|{GPU}|{PC})\b.*\btemperature\b', 'température matérielle'),
    _r('weather.current', -0.7, r'\bpluie (?:d etoiles|de meteorites|d etoiles filantes|de meteores)\b',
       'pluie d’étoiles'),
    _r('weather.current', -0.6, r'\b(?:sur|de) (?:mars|venus|jupiter|saturne|la lune|titan|mercure)\b',
       'autre planète'),

    # ---------------------------------------------------------------- quakes.recent
    _r('quakes.recent', 0.95, r'\b(?:seismes?|tremblements? de terre|sismiques?|seismiques?|secousses? (?:sismiques?|'
       r'telluriques?)|activite sismique|richter|usgs|epicentre)\b', 'séisme'),
    _r('quakes.recent', 0.6, r'\b(?:secousses?|magnitude|tsunamis?)\b', 'secousse'),

    # ---------------------------------------------------------------- calc.evaluate
    _r('calc.evaluate', 0.9, rf'(?<![a-z]){NUM} ?{OP} ?{NUM}(?![a-z])', 'opération entre deux nombres'),
    _r('calc.evaluate', 0.85, r'\b(?:racine (?:carree |cubique )?(?:de |d )?|carre de |cube de |factorielle (?:de )?|'
       r'logarithme (?:de )?|log (?:de )?|sinus (?:de )?|cosinus (?:de )?|tangente (?:de )?)\d', 'fonction mathématique'),
    _r('calc.evaluate', 0.85, r'\b\d+(?:\.\d+)? (?:au carre|au cube|factorielle)\b', 'puissance'),
    _r('calc.evaluate', 0.85, r'\b\d+(?:\.\d+)? ?(?:%|pour ?cents?|pourcents?) (?:de|d|du|des) \d', 'pourcentage'),
    _r('calc.evaluate', 0.8, r'\b(?:calcule|calculer|calcul|combien font|combien fait|ca fait combien|'
       r'combien ca fait|resous|resoudre|evalue|evaluer|egal|egale|vaut|equivaut)\b.*\d', 'calcule + nombres'),
    _r('calc.evaluate', -0.85, r'\b\d{4} - \d{1,2} - \d{1,2}\b|\b\d{1,2} / \d{1,2} / \d{2,4}\b', 'une date'),

    # ---------------------------------------------------------------- calc.convert
    _r('calc.convert', 0.95, r'\b(?:convertis|convertir|converti|convertit|convertisse|conversion|convertissez|'
       r'convertie)\b', 'conversion'),
    _r('calc.convert', 0.9, rf'{NUM} ?(?:{U}) (?:en|vers|=|font combien de|ca fait combien de|c est combien en|'
       r'c est combien de|combien en|combien de|fait combien en|font combien en|equivaut a combien de|'
       rf'equivaut en|a combien de) (?:{U})', 'valeur + unité → unité'),
    _r('calc.convert', 0.9, rf'\bcombien (?:de |d )?(?:{U}) (?:dans|pour|font|fait|y a t il dans|equivaut a|valent|'
       rf'vaut|represente|representent|en|d|de|a)\b (?:un |une )?(?:{NUM} ?)?(?:{U})', 'combien de X dans Y'),
    _r('calc.convert', 0.6, rf'(?:{U}) en (?:{U})', 'unité en unité'),
    _r('calc.convert', -0.7, r'\b(?:convertis|convertir|conversion)\b.*\b(?:fichier|fichiers|document|image|images|'
       r'video|videos|pdf|word|mp3|mp4|texte|format|jpg|png)\b', 'conversion de fichier'),

    # ---------------------------------------------------------------- time.now
    _r('time.now', 0.95, r'\b(?:quelle heure|l heure qu il est|il est quelle heure|heure actuelle|l heure actuelle|'
       r'(?:donne|dis) moi (?:juste |seulement |simplement |vite )?l heure|tu as l heure|t as l heure|'
       r'avez vous l heure|c est quelle heure|'
       r'l heure s il|quelle heure est il)\b', 'heure'),
    _r('time.now', 0.9, r'\b(?:quel jour (?:sommes nous|on est|est on|nous sommes|est ce|c est|aujourd hui|'
       r'de la semaine)|on est quel jour|nous sommes quel jour|quelle (?:est la )?date|la date (?:d aujourd hui|'
       r'du jour|actuelle)|date d aujourd hui|on est le combien|nous sommes le combien|quel est le jour)\b',
       'date du jour'),
    _r('time.now', 0.85, r'\bheure (?:a|au|en|aux) [a-z]+', 'heure dans une ville'),
    _r('time.now', -0.85, r'\ba quelle heure\b|\bheure de (?:pointe|coucher|lever)\b', 'à quelle heure (événement)'),
    # 22/11/2026 : « quelle est la date de la réunion avec le fournisseur ? » recevait la date du jour.
    _r('time.now', -0.85, r'\bquelle (?:est la )?date (?:de |des |du (?!jour\b)|d (?!aujourd hui\b))',
       'la date d’un événement, pas celle du jour'),
    # « à l'heure actuelle » / « à l'heure qu'il est » mean « currently » (29/10/2026: « sur quel modèle tu tournes
    # à l'heure actuelle » got the clock), unless the sentence asks the time.
    _r('time.now', -0.9, r'^(?!.*\b(?:quelle heure|il est quelle heure)\b).*\ba l heure (?:actuelle|qu il est)\b',
       'à l’heure actuelle (= actuellement)'),

    # ---------------------------------------------------------------- sim.orbit
    _r('sim.orbit', 0.9, r'\b(?:simule|simuler|simulation|simules|modelise|modeliser|calcule|calculer|trace|tracer|'
       r'dessine|anime)\b.*\b(?:orbite|orbites|orbitale?s?|trajectoire|trajectoires|satellite|satellites|sonde|'
       r'sondes|fusee|vaisseau|station spatiale|iss|mise en orbite|lancement)\b', 'simuler une orbite'),
    _r('sim.orbit', 0.85, r'\btrajectoires? (?:orbitale?s?|spatiale|(?:de la |du |d un |d une |de l |des )?'
       r'(?:station spatiale|satellites?|sondes?|iss|fusees?|capsules?|vaisseaux?|navettes?))\b', 'trajectoire orbitale'),
    _r('sim.orbit', 0.7, r'\borbitale?s?\b', 'orbital'),
    _r('sim.orbit', 0.9, r'\b(?:periode|vitesse|altitude|mecanique) orbitale\b', 'paramètre orbital'),
    _r('sim.orbit', 0.8, r'\b(?:satellite|sonde|station spatiale|iss|vaisseau|capsule)\b.*\b(?:autour|en orbite|'
       r'orbite)\b', 'satellite autour de…'),
    _r('sim.orbit', 0.8, r'\borbite (?:basse|haute|geostationnaire|geosynchrone|polaire|terrestre|lunaire|'
       r'martienne|circulaire|elliptique|de transfert|moyenne)\b|\bgeostationnaire\b', 'type d’orbite'),
    _r('sim.orbit', 0.7, r'\b\d+ ?(?:km|kilometres) d altitude\b', 'altitude'),
    _r('sim.orbit', 0.5, r'\borbite\b', 'orbite'),
    _r('sim.orbit', -0.5, r'\b(?:planetes|systeme solaire|autour du soleil)\b', 'planètes (système solaire)'),

    # ---------------------------------------------------------------- sim.solar_system
    _r('sim.solar_system', 0.9, r'\b(?:position|positions|emplacement|ou (?:est|sont|se trouve|se trouvent|se situe|'
       rf'se situent)|alignement|configuration|disposition)\b.*\b{PLANETS}\b', 'position des planètes'),
    _r('sim.solar_system', 0.9, r'\bsysteme solaire\b', 'système solaire'),
    _r('sim.solar_system', 0.85, r'\b(?:montre|affiche|visualise|represente|dessine|vois|voir)\b.*\b(?:planetes|'
       r'systeme solaire)\b', 'montrer les planètes'),
    _r('sim.solar_system', 0.6, r'\bplanetes\b.*\b(?:aujourd hui|ce soir|maintenant|en ce moment|actuellement|'
       r'le \d+|en \d{4}|demain|dans le ciel|actuelle)\b', 'planètes à une date'),
    _r('sim.solar_system', 0.85, r'\bephemerides?\b', 'éphémérides'),
    _r('sim.solar_system', 0.8, rf'\b{PLANETS}\b.*\bautour du soleil\b', 'planètes autour du Soleil'),
    _r('sim.solar_system', -0.7, r'\bcombien (?:de|d) (?:planetes|lunes|satellites|etoiles)\b', 'question de culture'),

    # ---------------------------------------------------------------- files.scan
    _r('files.scan', 0.85, r'\b(?:quels?|quelles?|liste|lister|listes|montre|montrer|affiche|afficher|scanne|scanner|'
       r'analyse|analyser|examine|examiner|inspecte|regarde|verifie|trouve|trouver|cherche|recherche|repere|reperer|'
       r'identifie|identifier|combien|y a t il|qu y a t il|il y a|que contient|contenu)\b.*'
       rf'\b{OLD}\b.*\b(?:fichiers?|documents|dossiers?|telechargements)\b', 'repérer de vieux fichiers'),
    _r('files.scan', 0.6, rf'\b(?:fichiers?|dossiers?|documents)\b (?:\w+ )?{OLD}\b|\b{OLD} (?:fichiers|dossiers|'
       r'documents)\b', 'fichiers anciens / volumineux'),
    _r('files.scan', 0.8, r'\b(?:quels?|quelles?|liste|lister|montre|montrer|affiche|afficher|scanne|scanner|analyse|'
       r'analyser|examine|examiner|inspecte|regarde|verifie|combien|y a t il|qu y a t il|qu est ce qu il y a|'
       r'que contient|contenu)\b.*\b(?:dans|de|du|des) (?:mes |mon |le |la |les |ma )?(?:telechargements|bureau|'
       r'documents|dossier|dossiers|images|photos|videos)\b', 'regarder dans un dossier'),
    _r('files.scan', 0.85, r'\b(?:scanne|scanner|scan|analyse|analyser|examine|inspecte|audite|parcours|liste|lister)\b '
       r'(?:moi )?(?:mes |mon |le |la |les |un |ce |ces )?(?:fichiers|dossiers?|telechargements|bureau|documents|'
       r'disque)\b', 'scanner les fichiers'),
    _r('files.scan', 0.8, r'\b(?:qu est ce qui|ce qui|quels? fichiers?|quels? dossiers?) (?:prend|prennent|occupe|'
       r'occupent|utilise|bouffe|mange) (?:le plus |autant |tant )?(?:de )?(?:place|espace|d espace|de la place)\b',
       'ce qui prend de la place'),
    _r('files.scan', 0.75, r'\b(?:fichiers?|dossiers?)\b.*\b(?:pourrais|peux|devrais|faudrait|pourrait|puis|peut|'
       r'faut il|a) (?:je |on )?(?:supprimer|effacer|jeter|nettoyer|virer)\b', 'que pourrais-je supprimer'),
    _r('files.scan', 0.8, r'\bquels? (?:fichiers?|dossiers?)\b.*\b(?:supprimer|effacer|nettoyer|jeter)\b',
       'quels fichiers supprimer'),
    _r('files.scan', 0.45, r'\btelechargements\b', 'téléchargements'),
    _r('files.scan', 0.9, rf'\b(?:fichiers?|dossiers?|documents|videos|photos|images)\b.*{FILE_SIZE}',
       'critère de taille de fichiers'),
    # A Windows path is masked as « cheminlocal » by normalize(): never read as words (« windows », « temp »).
    _r('files.scan', 0.85, r'\b(?:quels?|quelles?|liste|lister|montre|montrer|affiche|afficher|scanne|scanner|'
       r'analyse|analyser|examine|examiner|inspecte|regarde|verifie|trouve|trouver|cherche|chercher|recherche|'
       r'rechercher|parcours|combien|contenu|contient|y a t il|qu y a t il)\b.*\bcheminlocal\b',
       'dossier désigné par un chemin'),
    _r('files.scan', 0.6, r'\bcheminlocal\b', 'chemin de dossier'),
    _r('files.scan', 0.85, r'\bcheminlocal\b.*\b(?:prend|prennent|occupe|occupent|pese|taille|combien)\b|'
       r'\b(?:taille|poids|combien de place|combien d espace)\b.*\bcheminlocal\b', 'taille d’un dossier désigné'),
    # Size of a NAMED folder (28/10, A2: « Combien de place prend le dossier Documents ? » gave the whole disk).
    # The slots already count every file there (normalize._FOLDER_SIZE). What is left or free stays the disk.
    _r('files.scan', 0.95, rf'^(?!.*\b(?:reste|restant|restante|libre|disponible)\b)(?:.*\b(?:combien de place|'
       rf'combien d espace|quelle place|quel espace|quelle taille|quel poids|taille totale|pese combien|combien pese)\b'
       rf'.*\b{FOLDER_NAMES}\b|.*\b(?:taille|poids) (?:du|de mon|de mes|des|de ses) {FOLDER_NAMES}\b|'
       rf'.*\b{FOLDER_NAMES} (?:\w+ )?(?:prend|prennent|occupe|occupent|pese|pesent)\b)', 'taille d’un dossier nommé'),
    _r('system.status', -0.7, rf'^(?!.*\b(?:reste|restant|restante|libre|disponible)\b).*\b(?:combien de place|'
       rf'combien d espace|quelle place|quelle taille|quel poids|pese|pesent|prend|occupe)\b.*\b{FOLDER_NAMES}\b',
       'taille d’un dossier : pas le disque entier'),
    # « Recherche des informations sur les fichiers ISO de plus de 4 Go » (28/10, A2): information ABOUT files is a
    # web search; no folder scan on top, unless a local folder is named.
    _r('files.scan', -0.6, r'\b(?:informations?|infos?|renseignements?|documentation|explications?) '
       r'(?:sur|concernant|a propos (?:de|des|du))\b(?!.*\b(?:dans|de|du) (?:mes |mon |le |la |les |ma )?'
       r'(?:telechargements|bureau|documents|dossier|cheminlocal)\b)', 'informations sur des fichiers (web)'),
    _r('files.scan', -0.5, rf'^{POLITE}{CLEAN}\b', 'impératif de nettoyage'),
    _r('files.scan', -0.5, r'\b(?:sur|dans|via) (?:internet|le web|la toile|google|wikipedia|le net)\b|\ben ligne\b',
       'cible publique (web)'),
    # Negative web.search cue (kept here, with the files rules): local files described by size, age or path are
    # a folder scan, not a web search, unless the web or general information is asked for.
    _r('web.search', -0.75, r'^(?!.*\b(?:internet|web|en ligne|wikipedia|google|net|informations?|infos?|'
       r'articles?|logiciels?|applications?|outils?|programmes?|comment|tutoriels?|astuces?)\b)'
       rf'(?=.*\b(?:fichiers?|dossiers?|cheminlocal)\b)(?=.*(?:\b{OLD}\b|{FILE_SIZE}|\bcheminlocal\b))',
       'fichiers locaux (taille, âge ou chemin)'),

    # ---------------------------------------------------------------- files.cleanup
    _r('files.cleanup', 0.9, rf'\b{CLEAN}\b.*\b(?:{FILES}|disque|pc|ordinateur)\b', 'nettoyer des fichiers'),
    _r('files.cleanup', 0.85, r'\b(?:fais|faire|fait) (?:le|du) (?:menage|tri|nettoyage)\b', 'faire le ménage'),
    # « Mets ces fichiers en quarantaine » went to the conversation: CLEAN only knew « mets en quarantaine »
    # with nothing in between (28/10, after A5). « remets » never matches here (restore).
    _r('files.cleanup', 0.85, r'\b(?:mets|met|mettre|mettez|place|placer|placez|deplace|deplacer|deplacez)\b'
       r'(?: \w+){1,8} en quarantaine\b', 'mettre en quarantaine'),
    _r('files.cleanup', 0.8, r'\b(?:libere|liberer|liberes|gagne|gagner|faire|fais) (?:de l |de la |'
       r'du |un peu d |un peu de |plus d |plus de )?(?:espace|place)\b', 'libérer de l’espace'),
    # « Récupère de l'espace », « où je peux récupérer / libérer de la place ? »: show WHERE, read only (ID Studio IA,
    # 28/10/2026: « plutôt : où je peux récupérer de l'espace »). The biggest files are listed, nothing moves.
    _r('files.scan', 0.9, rf'\b(?:recupere|recuperer|recuperez|recuperes)(?: \w+)? {FREE_SPACE}|'
       rf'\b(?:ou|comment|quoi|quels?|quelles?)\b.*\b(?:recuperer|liberer|gagner|faire) {FREE_SPACE}',
       'où récupérer de l’espace (lecture seule)'),
    _r('files.cleanup', -0.8, rf'\b(?:ou|comment|quoi|quels?|quelles?)\b.*\b(?:recuperer|liberer|gagner|faire) '
       rf'{FREE_SPACE}', 'question : où gagner de la place'),
    _r('files.cleanup', 0.8, r'\bnettoyage (?:du|de|des|de mon|de mes) (?:disque|pc|fichiers|telechargements|dossier|'
       r'bureau|ordinateur)\b', 'nettoyage'),
    _r('files.cleanup', 0.55, rf'^{POLITE}(?:supprime|efface|nettoie|vide|purge|elimine)\b', 'impératif de suppression'),
    _r('files.cleanup', -0.7, r'\b(?:pourrais|peux|devrais|faudrait|faut il|puis|pourrait) (?:je |on )?(?:supprimer|'
       r'effacer|nettoyer|jeter)\b|\bquels? (?:fichiers?|dossiers?)\b.*\b(?:supprimer|effacer|nettoyer)\b',
       'question / conseil'),
    _r('files.cleanup', -0.85, r'\b(?:ne supprime rien|sans supprimer|sans rien supprimer|ne touche a rien|'
       r'n efface rien|sans rien effacer|ne rien supprimer)\b', 'aucune suppression demandée'),
    _r('files.cleanup', -0.7, r'\b(?:supprime|efface|enleve)\b.*\b(?:paragraphe|phrase|ligne|mot|mots|passage|'
       r'memoire|souvenir|note|tache|message)\b', 'édition de texte / mémoire'),

    # ---------------------------------------------------------------- routine.run (D1)
    # The exact phrase of a saved routine is matched before the router (orchestrator._decide); these rules
    # catch « lance la routine X » even for an unknown X, so the answer can list the routines that exist.
    _r('routine.run', 0.95, r'\b(?:lance|lancer|lancez|demarre|demarrer|demarrez|execute|executer|executez|joue|'
       r'jouer)(?: moi)? (?:la |ma |une |notre )?routine\b', 'lancer une routine'),
    _r('routine.run', 0.9, r'^(?:la |ma )?routine (?:du |de la |de l |des |de |d )?\w+', 'routine nommée'),
    _r('routine.run', -0.8, r'\b(?:c est quoi|qu est ce qu|comment (?:creer|faire|marche|fonctionne))\b.*\broutine',
       'question sur les routines'),

    # ---------------------------------------------------------------- agent.run (P1)
    # The interface's « Agent » switch sends the intent itself (options.intent); these rules catch it said or typed.
    _r('agent.run', 0.97, AGENT_ASKED, 'demande confiée à l’agent'),
    _r('agent.run', -0.9, r'\b(?:c est quoi|qu est ce qu|comment (?:marche|fonctionne|faire)|a quoi sert|'
       r'explique)\b.*\bagents?\b', 'question sur les agents'),

    # ---------------------------------------------------------------- files.restore (B4, rapport §4.6)
    # « Restaure les fichiers mis en quarantaine » went to the conversation: only the « Restaurer » button did it.
    _r('files.restore', 0.95, r'\b(?:restaure|restaurer|restaures|restaurez|restore|remets|remet|remettre|remettez|'
       r'recupere|recuperer|recuperez|ramene|ramener|sors|sortir|retire|retirer)\b.*\bquarant(?:aine|ene|ine)\b',
       'restaurer la quarantaine'),
    _r('files.restore', 0.9, rf'^{POLITE}(?:restaure|restaurez|remets|remettez)(?: moi)? (?:les|mes|ces) '
       r'fichiers\b', 'remettre des fichiers en place'),
    _r('files.restore', 0.9, r'\bannule(?:r|z)? (?:le|ce|mon|du|ton) (?:dernier )?(?:nettoyage|menage|tri)\b',
       'annuler le nettoyage'),
    _r('files.cleanup', -0.8, r'\b(?:restaure\w*|restore|remets|remet|remettre|remettez|recupere\w*|ramene\w*|sors|'
       r'sortir)\b.*\bquarant(?:aine|ene|ine)\b|\bannule(?:r|z)? (?:le|ce|mon|du|ton) (?:dernier )?'
       r'(?:nettoyage|menage|tri)\b', 'restauration, pas nettoyage'),

    # ---------------------------------------------------------------- data.analyze (P3)
    # A table file named in the request (« depenses.csv » reads « depenses csv » once normalized). A joined table
    # is routed by the orchestrator (tools/data.is_table), whatever the sentence.
    _r('data.analyze', 0.95, r'\b(?:csv|tsv)\b', 'fichier de données CSV nommé'),
    _r('data.analyze', 0.85, r'\b(?:analyse|analyser|etudie|etudier|calcule|calculer|statistiques?|totaux|total|'
       r'moyennes?|tendances?|evolution|compare|comparer|resume|resumer|regarde|chiffres)\b.*\b(?:xlsx|xls|tableur|'
       r'feuille de calcul|fichier excel|classeur excel)\b|\b(?:mon|ma|mes|ce|cette|le) (?:tableur|feuille de calcul|'
       r'fichier excel|classeur excel)\b', 'tableur nommé, à analyser'),
    # « Comment figer une ligne dans un tableur ? », « Comment ouvrir un fichier csv ? »: a question about software
    # (« Comment ont évolué mes dépenses dans budget.csv ? » stays an analysis).
    _r('data.analyze', -0.9, r'^(?:comment (?:on |je |faire pour |puis je |peut on |dois je )?(?:ouvrir|ouvre|figer|'
       r'fige|creer|cree|installer|convertir|convertit|enregistrer|importer|exporter|imprimer|fusionner|proteger|'
       r'utiliser|lire|reparer|recuperer)|qu est ce qu|c est quoi|a quoi sert|quel logiciel|quelle application)\b',
       'question sur un logiciel, pas une analyse'),
    _r('files.scan', -0.6, r'\b(?:csv|tsv|xlsx|xls)\b', 'un fichier de données, pas un dossier à lister'),
    _r('files.scan', 0.7, r'\b(?:liste|lister|listes|montre|affiche|quels|quelles|cherche|trouve|retrouve|ou sont)\b'
       r'.*\bfichiers (?:csv|tsv|excel|xlsx|xls)\b', 'lister des fichiers de données'),
    _r('data.analyze', -0.6, r'\b(?:liste|lister|listes|montre|affiche|quels|quelles|cherche|trouve|retrouve|ou sont)\b'
       r'.*\bfichiers (?:csv|tsv|excel|xlsx|xls)\b', 'lister des fichiers, pas en analyser un'),
    _r('doc.analyze', -0.4, r'\b(?:csv|tsv|xlsx|xls|tableur)\b', 'un tableau, calculé par le code'),

    # ---------------------------------------------------------------- doc.analyze
    _r('doc.analyze', 0.75, rf'\b{DOC_VERB}\b.*\b{DOC}\b', 'analyser un document'),
    # « Explique-moi le code de mon site web » (28/10, A1), « …le code de la route », « …l'article 49.3 de… »: a
    # subject, not a document given to AURORA. Answered in conversation; a joined file still makes it doc.analyze
    # (orchestrator: an attachment turns chat.general into doc.analyze).
    _r('doc.analyze', -0.6, rf'\b{DOC_VERB}\b(?: moi| nous)? (?:le|la|l|les|un|une|des) {DOC} (?:de|du|des|d)\b'
       r'(?!.*\b(?:ci joint|ci jointe|ci dessous|ci dessus|suivant|suivante|joint|jointe|colle|collee)\b)',
       'sujet général, aucun document fourni'),
    _r('doc.analyze', 0.6, rf'\b(?:ce|cet|cette|ces|ci joint|ci jointe|ci dessous|ci dessus|suivant|suivante|joint|'
       rf'jointe|colle|collee|que je t envoie|que je viens de)\b (?:\w+ )?{DOC}\b|\b{DOC}\b (?:\w+ )?(?:ci joint|'
       r'ci dessous|ci dessus|suivant|suivante|joint|jointe|colle|que je t ai (?:envoye|donne|colle)|'
       r'que je viens de (?:coller|t envoyer|partager))\b', 'document fourni'),
    _r('doc.analyze', 0.8, rf'\b(?:fais|fait|donne|ecris|redige|propose)(?: moi)? (?:un |une )?(?:resume|synthese|'
       rf'analyse|critique|relecture|traduction|explication)\b.*\b{DOC}\b', 'résumé / synthèse d’un document'),
    _r('doc.analyze', 0.7, r'\b(?:ci joint|ci dessous|ci dessus|en piece jointe|que je t ai envoye|'
       r'que je viens de (?:coller|t envoyer|partager)|voici (?:le|un|mon|ma|la|ce) (?:document|texte|code|fichier|'
       r'rapport|erreur|log|contrat))\b', 'pièce jointe'),
    _r('doc.analyze', 0.7, r'\b(?:comment (?:la |le |les )?(?:resoudre|corriger|reparer|regler)|resous|resoudre)\b.*'
       r'\b(?:erreur|bug|exception|traceback)\b', 'résoudre une erreur'),
    _r('doc.analyze', -0.6, rf'\b(?:{PC}|performances)\b', 'cible = le PC'),

    # ---------------------------------------------------------------- task.plan
    _r('task.plan', 0.9, r'\b(?:organise|organiser|organises|planifie|planifier|planifies|programme moi|planning|'
       r'plan d action|plan de travail|feuille de route|roadmap|emploi du temps|to do|todo|check list|checklist|'
       r'liste de taches|retroplanning|echeancier|calendrier de travail)\b', 'organiser / planifier'),
    _r('task.plan', 0.85, r'\b(?:prepare|preparer|prepares|etablis|etablir|elabore|elaborer|dresse|dresser|propose|'
       r'proposer|construis|construire)(?: moi)? (?:un |une |le |la |mon |ma |des |les )?(?:plan|planning|programme|'
       r'strategie|liste|calendrier|agenda|etapes|planification|optimisation|organisation|feuille de route|'
       r'emploi du temps|methode|marche a suivre)\b', 'préparer un plan'),
    _r('task.plan', 0.85, r'\b(?:fais|fait|cree|creer|ecris|redige|rediger)(?: moi)? (?:un |une |le |la |ma )?'
       r'(?:plan|planning|liste de taches|emploi du temps|feuille de route|calendrier|checklist|check list|'
       r'liste des choses a faire|to do list)\b', 'faire un planning'),
    _r('task.plan', 0.85, r'\b(?:aide moi a|aide moi pour|aide nous a|comment) (?:organiser|planifier|preparer|'
       r'structurer|prioriser|repartir|m organiser)\b', 'aide-moi à organiser'),
    _r('task.plan', 0.8, r'\b(?:decompose|decomposer|decoupe|decouper|structure|structurer|priorise|prioriser|'
       r'hierarchise|hierarchiser)\b.*\b(?:taches?|projet|travail|etapes|objectifs?|semaine|journee|recherches|'
       r'priorites)\b', 'découper le travail'),
    _r('task.plan', 0.7, r'\b(?:etapes|plan) (?:pour|afin de|a suivre pour|de mise en place)\b|'
       r'\bquelles (?:sont les )?etapes\b', 'étapes pour…'),
    _r('task.plan', 0.6, r'\b(?:objectifs?|priorites) (?:de|pour) (?:la|cette|ma|demain|aujourd) ?(?:semaine|journee|'
       r'mois|hui)?\b', 'objectifs de la semaine'),
    _r('task.plan', -0.6, rf'\b{NAV}\b (?:la |les |l )?(?:page |onglet )?(?:des )?taches\b', 'ouvrir la page des tâches'),
    _r('task.plan', -0.6, r'\b(?:ecris|redige|code)\b.*\b(?:programme|script|fonction)\b', 'écriture de code'),

    # ---------------------------------------------------------------- ui.navigate
    _r('ui.navigate', 0.9, rf'\b{NAV}\b (?:moi )?(?:a |au |aux |sur |dans |vers |en )?(?:la |le |l |les |mon |ma |mes )?'
       r'(?:page |onglet |panneau |vue |ecran |section |module |rubrique |espace |fenetre )?(?:de |du |des |d )?'
       rf'(?:la |le |l |les )?{PAGE}\b', 'ouvrir une page'),
    _r('ui.navigate', 0.8, rf'\b{NAV}\b.*\b(?:sur|dans) (?:le |la |l )(?:globe|carte du monde|planisphere)\b',
       'afficher sur le globe'),
    # 08/11/2026 (globe hologramme) : « zoome sur le Japon dans le globe », « situe Lyon sur la carte du monde ».
    _r('ui.navigate', 0.8, r'\b(?:zoome|zoomer|zoom|centre|centrer|localise|localiser|situe|situer|place|placer|'
       r'tourne|tourner)\b.*\b(?:sur|dans) (?:le |la |l )(?:globe|carte du monde|planisphere)\b',
       'situer sur le globe'),
    # 08/11/2026 (runs 44-45 of the real AURORA, by voice): « où est la Jordanie sur le globe », « montre sur le globe
    # où est la Jordanie », « Petra sur le globe », « zoome sur la Jordanie » (a country or a capital only).
    _r('ui.navigate', 0.8, r'\bou (?:est|sont|se trouve|se trouvent|se situe|se situent)\b.*\b(?:sur|dans) (?:le |la |l )'
       r'(?:globe|carte du monde|planisphere)\b|\b(?:sur|dans) (?:le |la |l )(?:globe|carte du monde|planisphere) '
       r'ou (?:est|sont|se trouve|se trouvent|se situe|se situent)\b', 'où est … sur le globe'),
    Rule('ui.navigate', 0.8, _Test(lambda n: globe_place_named(n, normalized=True)), 'un lieu sur le globe'),
    # « où est l'ISS ? », « montre-moi les avions au-dessus de Lyon », « affiche les catastrophes en cours » : le globe
    # les montre en direct (normalize.extract_globe_order). Les séismes restent à quakes.recent, qui ouvre aussi le globe.
    _r('ui.navigate', 0.85, r'\b(?:ou (?:est|sont|se trouve|se trouvent|passe)|position|positions|suis|suivre|localise|'
       r'localiser|montre|montrer|affiche|afficher)\b.*\b(?:l iss|iss|station spatiale(?: internationale)?|tiangong|'
       r'hubble|landsat)\b', 'où est l’ISS'),
    _r('ui.navigate', 0.8, r'\b(?:montre|montrer|affiche|afficher|voir|ouvre|suis|suivre)\b(?: moi| nous)? (?:les |le )?'
       r'(?:satellites|avions|trafic aerien|vols en cours|catastrophes|cyclones|ouragans|typhons|volcans|tornades)\b',
       'montre les avions, les satellites, les catastrophes'),
    _r('ui.navigate', 0.85, rf'\b(?:page|onglet|panneau|vue|ecran|section) (?:de |du |des |d )?(?:la |le |l |les )?'
       rf'{PAGE}\b', 'page …'),
    _r('ui.navigate', 0.7, rf'^(?:la |le |l |les )?(?:page |onglet )?{PAGE}$', 'nom de page seul'),
    # « le globe où est la Jordanie » is a place to show, not a content of the page (08/11/2026, run 45).
    _r('ui.navigate', -0.6, rf'{PAGE} (?:sur|concernant|a propos|lies? a|qui|que|ou(?! (?:est|sont|se trouve|se trouvent|'
       r'se situe|se situent)\b)|avec|de mon|de notre|du projet)\b', 'contenu précis demandé'),
    _r('ui.navigate', -0.7, r'\b(?:ouvre|ouvrir) (?:mon|notre|le|ce) projet\b', 'projet, pas une page'),
    # 08/11/2026 (laboratoire 3D de l'accueil) : « montre-moi comment fonctionne un bras robotique » ouvre la scène du
    # catalogue (mots de data/scenes) ; « montre-moi un moteur en 3D » sans scène reçoit une réponse honnête.
    Rule('ui.navigate', 0.95, _Test(lambda n: scene_subject(n, normalized=True)), 'ouvrir une scène 3D du laboratoire'),
    Rule('ui.navigate', 0.9, _Test(lambda n: scene_3d_asked(n, normalized=True)), 'objet demandé en 3D'),

    # ---------------------------------------------------------------- meta.capabilities
    _r('meta.capabilities', 0.9, r'\b(?:as tu|tu as|t as|avez vous|vous avez|disposes tu|tu disposes d|possedes tu|'
       r'tu possedes|est ce que tu as) (?:acces (?:a |au |a l )?|une connexion (?:a |au )?|du |de l |le )?(?:internet|'
       r'web|reseau|net|connexion)\b', 'accès Internet ?'),
    _r('meta.capabilities', 0.9, META_INTERNET, 'peux-tu aller sur Internet ?'),
    _r('meta.capabilities', 0.9, r'\b(?:es tu|tu es|est ce que tu es|etes vous) (?:connecte|connectee|en ligne|hors ligne|'
       r'online|offline|branchee?)\b', 'es-tu connectée ?'),
    _r('meta.capabilities', 0.9, r'\b(?:que sais tu faire|qu est ce que tu sais faire|que peux tu faire|'
       r'qu est ce que tu peux faire|tu sais faire quoi|tu peux faire quoi|tu fais quoi|a quoi sers tu|'
       r'a quoi tu sers|de quoi es tu capable|qu es tu capable de faire|comment (?:tu fonctionnes|fonctionnes tu|'
       r'marches tu|tu marches)|tu peux m aider a quoi)\b', 'que sais-tu faire ?'),
    _r('meta.capabilities', 0.85, r'\b(?:tes|vos) (?:capacites|fonctions|fonctionnalites|competences|limites|'
       r'possibilites|outils)\b|\bquelles (?:sont tes )?(?:capacites|fonctionnalites)\b', 'tes capacités'),
    _r('meta.capabilities', 0.85, r'\b(?:quel|quels) (?:modele|modeles|ia|llm|moteur|moteurs|cerveau)\b.*'
       r'\b(?:utilises tu|tu utilises|es tu|tu es|tourne|tournes|tournent|utilise|derriere|as tu|disponibles?|installes?)\b',
       'quel modèle ?'),
    # Voice turns « quel modèle d'IA es-tu » into « modèle dia es-tu » (29/10/2026): no « quel », same question.
    _r('meta.capabilities', 0.85, r'\b(?:modele|modeles) (?:d ia|dia|ia|de langage|llm)\b.*'
       r'\b(?:es tu|tu es|utilises tu|tu utilises|tourne|tournes|as tu)\b', 'modèle d’IA ?'),
    _r('meta.capabilities', 0.85, r'\bsur quel (?:modele|ia|llm|moteur)\b', 'sur quel modèle ?'),
    _r('meta.capabilities', 0.85, r'\b(?:tu utilises|utilises tu|tu tournes|tournes tu|tu fonctionnes|fonctionnes tu) '
       r'(?:sur |avec )?(?:quel|quelle) (?:modele|ia|llm|moteur)\b', 'tu utilises quel modèle ?'),
    _r('meta.capabilities', 0.8, r'\b(?:claude|ollama|le cloud|anthropic|qwen|mode prive|mode equilibre|mode avance|'
       r'mode actuel)\b.*\b(?:configure|configuree|disponible|actif|active|branche|marche|fonctionne|installe|'
       r'connecte)\b', 'Claude / Ollama disponibles ?'),
    _r('meta.capabilities', 0.7, r'\bquel mode\b|\ben quel mode\b', 'quel mode ?'),
    # 08/11/2026 (run 47 of the real AURORA, by voice): « est-ce que tu as le terminal dans tes applications ou pas ? »
    # went to the local AI, which said « je ne peux pas ». The real setting answers (capabilities.terminal_state).
    _r('meta.capabilities', 0.9, rf'\b(?:as tu|tu as|t as|avez vous|vous avez|disposes tu|tu disposes d|possedes tu|'
       rf'tu possedes)(?: vraiment| bien| deja)? (?:un )?(?:acces (?:a |au |a l |aux )?)?(?:un |une |le |la |l |du |des |'
       rf'ton |ta )?{TERMINAL}\b', 'as-tu le terminal ?'),
    _r('meta.capabilities', 0.9, rf'\b(?:peux tu|tu peux|pouvez vous|sais tu|tu sais|es tu capable d|arrives tu a)'
       rf'(?: vraiment| bien)? (?:utiliser|ouvrir|lancer|te servir d(?:u|e|es)?|executer|acceder (?:a|au|aux)) '
       rf'(?:un |une |le |la |l |du |des |les )?(?:{TERMINAL}|commandes?)(?: windows| powershell)?'
       rf'(?: (?:dans tes applications|sur (?:ce|mon|le) pc|ici|maintenant|ou pas|ou non))*$', 'peux-tu utiliser le terminal ?'),
    _r('meta.capabilities', 0.9, rf'\b{TERMINAL}(?: avec autorisation)? (?:est il|est elle|est|il est|c est) '
       r'(?:bien |deja |encore )?(?:active|actif|activee|allume|disponible|branche|ouvert|desactive|autorise|en marche)\b',
       'le terminal est-il activé ?'),
    # 08/11/2026: who wrote an earlier answer (Claude, the local AI or the tools) is read on its run.
    Rule('meta.capabilities', 0.95, _Test(asks_answer_author), 'qui a rédigé la réponse ?'),
    _r('meta.capabilities', 0.75, r'\b(?:fonctionnes tu|tu fonctionnes|tu marches|marches tu|tu peux fonctionner) '
       r'(?:hors ligne|sans internet|en local|localement|sans connexion)\b', 'fonctionnes-tu hors ligne ?'),
    _r('meta.capabilities', 0.6, r'^(?:aide|help|au secours|menu)$', 'aide'),
    # ---------------------------------------------------------------- reminder.set (C3, 16/11/2026)
    # Reminders, timers, alarms: until then the honest answer was « je ne sais pas » (meta.capabilities). The moment
    # is read by the code (tasks/reminders.understand). « Rappelle-moi ce que… » stays a memory search (look-ahead).
    _r('reminder.set', 0.95, rf'^{POLITE}(?:rappelle moi|rappelez moi|reveille moi|reveillez moi|fais moi penser|'
       r'faites moi penser)\b'
       r'(?! (?:ce que|ce qu|de quoi|le nom|mon|ma|mes|notre|nos|quel|quelle|quels|quelles|ou en|la ou|qui|'
       r'comment|pourquoi|ton|tes|votre|vos)\b)', 'rappel à une heure ou après une durée'),
    _r('reminder.set', 0.9, rf'^{POLITE}(?:previens moi|prevenez moi|avertis moi)\b.*\b(?:dans (?:\d|une?\b|deux|trois|'
       r'cinq|dix|quinze|vingt|trente)|(?:a|vers) (?:\d|midi|minuit)|demain|ce soir)', 'préviens-moi à / dans…'),
    _r('reminder.set', 0.9, r'\b(?:programme|programmer|mets|mettre|mettez|cree|creer|regle|regler|ajoute|ajouter|lance|'
       r'lancer|demarre|demarrer)(?: moi)? (?:un|une|des|le|la|mon|ma) (?:rappels?|alarmes?|reveils?|minuteurs?|'
       r'minuteries?|timers?|compte a rebours|chronos?)\b', 'créer un rappel, un minuteur ou un réveil'),
    _r('reminder.set', 0.9, r'^(?:un |une )?(?:minuteur|minuterie|timer|compte a rebours|alarme|reveil|rappel)'
       r'(?: de| d| pour| sur| a| dans)? (?:\d|une? |deux |trois |cinq |dix |quinze |vingt |trente |midi|minuit)',
       'minuteur de N minutes'),
    _r('reminder.set', 0.9, r'^(?:quels sont|liste|montre(?: moi)?|affiche(?: moi)?|donne(?: moi)?|dis moi|ai je|'
       r'est ce que j ai) (?:mes |les |des |un |une |tous mes |tous les )?(?:rappels?|minuteurs?|alarmes?|reveils?)\b|'
       r'^(?:mes|les) (?:rappels?|minuteurs?|alarmes?|reveils?)$|'
       r'\bcombien de temps (?:il )?reste\b.*\b(?:minuteur|compte a rebours)\b|\bou en est (?:le|mon) minuteur\b',
       'lister les rappels et minuteurs'),
    _r('reminder.set', 0.9, r'^(?:annule|annuler|supprime|supprimer|arrete|arreter|enleve|enlever|retire|retirer|efface|'
       r'effacer|stoppe|stopper|coupe|couper|desactive|desactiver) (?:le |la |l |mon |ma |ce |cette |les |mes |'
       r'tous les |tous mes |toutes les |toutes mes )(?:dernier |derniere |prochain |prochaine )?(?:rappels?|'
       r'minuteurs?|minuteries?|timers?|alarmes?|reveils?|compte a rebours)\b', 'annuler un rappel ou un minuteur'),
    # Screen vision is on a click only (E2): an honest capability statement, never an invented description;
    # with a capture (or an image) joined, the router reads the attachment instead (router.py).
    Rule('meta.capabilities', 0.9, SCREEN_ASKED, 'vision de l’écran (sur clic : bouton Capture d’écran)'),

    # ---------------------------------------------------------------- chat.general (explicit cues)
    _r('chat.general', 0.9, r'^(?:bonjour|salut|coucou|hello|bonsoir|hey|yo|merci|merci beaucoup|merci bien|'
       r'bonne nuit|bonne journee|au revoir|a plus|a bientot|ciao|ca va|comment vas tu|comment ca va|tu vas bien|'
       r'quoi de neuf|bien joue|bravo|super|genial|parfait|d accord|ok|oui|non)(?: aurora| astra| toi| a toi| a vous|'
       r' beaucoup| bien| merci)?$', 'salutation / politesse'),
    # Identity / introduction: answered by the language model (its system prompt carries the real capabilities).
    _r('chat.general', 0.9, r'\b(?:qui es tu|tu es qui|qui etes vous|presente toi|presentez vous|'
       r'comment t appelles tu|tu t appelles comment|comment tu t appelles|quel est ton nom|c est quoi ton nom)\b',
       'présentation'),
    _r('chat.general', 0.85, rf'\b{STORY}\b', 'rédaction créative'),
    _r('chat.general', 0.6, DEFINITIONAL, 'question de connaissance'),
    _r('chat.general', 0.6, r'\b(?:que penses tu|qu en penses tu|ton avis|tu preferes|tu aimes|conseille moi|'
       r'recommande moi|donne moi des idees|des idees de|j ai besoin d un conseil|comment dire|aide moi a ecrire|'
       r'tu crois que|selon toi|a ton avis)\b', 'avis / conseil'),
    _r('chat.general', 0.6, NARRATIVE, 'récit ou constat personnel'),
    _r('chat.general', 0.7, r'\b(?:je me souviens|je m en souviens|je me rappelle)\b', 'souvenir personnel'),
    _r('chat.general', 0.5, r'\b(?:traduis|traduire|traduction)\b', 'traduction'),
]

# Global modifiers: (pattern, {intent: factor}) applied after scoring.
_MODIFIERS: list[tuple[re.Pattern, dict[str, float], str]] = [
    (re.compile(DEFINITIONAL), {i: 0.4 for i in MEASURE_LIKE}, 'question de définition'),
    (re.compile(NARRATIVE), {i: 0.45 for i in MEASURE_LIKE + ACTION_LIKE + ('system.diagnose',)}, 'récit'),
    # 21/11/2026: « En mode agent : … puis écris un résumé » is a task given to the agent, which only scores when
    # it is asked by name: a writing verb inside the task does not take it back to the conversation.
    (re.compile(rf'\b{STORY}\b'), {i: 0.3 for i in INTENT_ORDER if i not in (DEFAULT_INTENT, 'agent.run')},
     'rédaction créative'),
    # E5: « tu te souviens de la météo d'hier ? » recalls a past exchange; it is not a weather request.
    (re.compile(RECALL_ASKED), {i: 0.5 for i in INTENT_ORDER if i not in ('memory.search', DEFAULT_INTENT)},
     'rappel d’une conversation passée'),
    # P1: « en mode agent : regarde ma RAM puis… » is one task for the agent, not a RAM measure.
    # 21/11/2026 (essai réel) : une mission longue dite « En mode agent : … donne les différences entre les deux
    # notes jointes, puis écris un résumé… » cumulait assez de règles « document » pour battre l'agent malgré la
    # moitié : elle partait en simple lecture de document, sans agent ni carte d'accord. D'où 0,2 au lieu de 0,5.
    (re.compile(AGENT_ASKED), {i: 0.2 for i in INTENT_ORDER if i not in ('agent.run', DEFAULT_INTENT)},
     'demande confiée à l’agent'),
    (re.compile(QUOTED), {i: 0.3 for i in ACTION_LIKE}, 'phrase citée ou jouée, pas un ordre'),
]
_QUOTED_RE = _MODIFIERS[-1][0]


def score_text(normalized: str) -> tuple[dict[str, float], dict[str, list[str]]]:
    """Raw rule scores for one normalised text (deterministic)."""
    positive: dict[str, float] = {}
    penalty: dict[str, float] = {}
    matched: dict[str, list[str]] = {}
    for rule in RULES:
        if not rule.pattern.search(normalized):
            continue
        if rule.weight > 0:
            positive[rule.intent] = 1.0 - (1.0 - positive.get(rule.intent, 0.0)) * (1.0 - rule.weight)
            matched.setdefault(rule.intent, []).append(rule.label)
        else:
            penalty[rule.intent] = penalty.get(rule.intent, 1.0) * (1.0 + rule.weight)
    for pattern, factors, _label in _MODIFIERS:
        if pattern.search(normalized):
            for intent, factor in factors.items():
                penalty[intent] = penalty.get(intent, 1.0) * factor
    scores = {intent: round(value * penalty.get(intent, 1.0), 4) for intent, value in positive.items()}
    scores[DEFAULT_INTENT] = max(scores.get(DEFAULT_INTENT, 0.0), CHAT_PRIOR)
    return scores, matched


def _ranked(scores: dict[str, float]) -> list[tuple[str, float]]:
    order = {name: i for i, name in enumerate(INTENT_ORDER)}
    return sorted(scores.items(), key=lambda kv: (-kv[1], order.get(kv[0], 999)))


def _clause_intents(text: str) -> tuple[list[str], list[tuple[str, str]]]:
    clauses = split_clauses(text)
    if len(clauses) < 2:
        return [], []
    found: list[str] = []
    mapping: list[tuple[str, str]] = []
    for clause in clauses:
        norm = normalize(clause)
        if not norm:
            continue
        ranked = _ranked(score_text(norm)[0])
        intent, value = ranked[0]
        if intent != DEFAULT_INTENT and value >= SECONDARY_MIN:
            found.append(intent)
            mapping.append((clause, intent))
    return found, mapping


def analyze(text: str, normalized: str | None = None) -> RuleResult:
    """Score every intent for a request; detect secondary intents of compound requests."""
    norm = normalize(text) if normalized is None else normalized
    scores, matched = score_text(norm)
    ranked = _ranked(scores)
    clause_intents, clause_map = _clause_intents(text)
    if _QUOTED_RE.search(norm):
        # The quoted part, read alone, looks like an order: it is not one (A5 check: « Réponds uniquement par
        # cette phrase : J'ai supprimé 3 fichiers de ton bureau » showed « Non traité : Nettoyage de fichiers »).
        clause_intents = [i for i in clause_intents if i not in ACTION_LIKE]
        clause_map = [(clause, i) for clause, i in clause_map if i not in ACTION_LIKE]
    top_intent, top_score = ranked[0]
    # Compound request with near tie: the first clause carries the main intent.
    if clause_intents and len(ranked) > 1 and ranked[0][1] - ranked[1][1] < 0.05:
        tied = {name for name, value in ranked if ranked[0][1] - value < 0.05}
        for intent in clause_intents:
            if intent in tied:
                top_intent, top_score = intent, scores[intent]
                break
    if top_intent != DEFAULT_INTENT and top_score < MIN_RULE_SCORE:
        top_intent, top_score = DEFAULT_INTENT, scores[DEFAULT_INTENT]
    secondary: list[str] = []
    for intent in clause_intents:
        if intent != top_intent and intent not in secondary:
            secondary.append(intent)
    # strong second evidence in a single clause ("cherche des solutions pour mon PC lent")
    for intent, value in ranked:
        if intent in (top_intent, DEFAULT_INTENT) or intent in secondary:
            continue
        if value >= 0.8 and top_score >= 0.8 and len(secondary) < 3 and _distinct_family(top_intent, intent):
            secondary.append(intent)
    alternatives = [(name, round(value, 3)) for name, value in ranked if name != top_intent][:3]
    rival = max((value for name, value in ranked if name != top_intent and name not in secondary
                 and name != DEFAULT_INTENT), default=0.0)
    confidence = max(0.0, top_score - 0.25 * rival) if top_intent != DEFAULT_INTENT else top_score
    return RuleResult(intent=top_intent, confidence=round(confidence, 4), score=round(top_score, 4), scores=scores,
                      matched=matched, secondary_intents=secondary, alternatives=alternatives,
                      clause_intents=clause_intents, clauses=clause_map)


_FAMILY = {'files.scan': 'files', 'files.cleanup': 'files', 'calc.evaluate': 'calc', 'calc.convert': 'calc',
           'system.status': 'system', 'system.processes': 'system', 'system.diagnose': 'system',
           'sim.orbit': 'sim', 'sim.solar_system': 'sim', 'memory.search': 'memory', 'memory.save': 'memory'}


def _distinct_family(a: str, b: str) -> bool:
    return _FAMILY.get(a, a) != _FAMILY.get(b, b)


def explain(result: RuleResult) -> str:
    labels = result.matched.get(result.intent, [])
    if not labels:
        return ''
    unique = list(dict.fromkeys(labels))[:3]
    return ', '.join(unique)
