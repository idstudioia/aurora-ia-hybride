"""Which wording of the engine line stops a small model from signing its answers? A quick trial, straight to Ollama,
with the system prompt AURORA really builds (HEAD) and the line swapped by text replacement: no code of AURORA is
changed. The questions are NOT those of the conversation bench (the bench stays the measure).
The wordings tried are « avant », A, B and C by default; the variable VARIANTES picks others (« E,F » on 22/11/2026).
Results of that day: reviews/banc-modeles/tournures-1.* (avant, A, B, C on four models) and tournures-2.* (E and F
on the two gemma4; granite4.2:8b was stopped before its end). What came out of it is in core/prompts.py (unsigned).
Usage: python -X utf8 essai_tournures.py <sortie.json> <modèle> [<modèle> ...]"""
import json
import os
import re
import sys
import tempfile
import time
import urllib.request

essai = os.path.join(tempfile.gettempdir(), 'aurora-tests')
os.environ.setdefault('AURORA_HOME', essai)
os.environ.setdefault('AURORA_DB_PATH', os.path.join(essai, 'aurora.db'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'backend'))
from aurora.core import prompts  # noqa: E402

LIGNE = '- Cette réponse est produite par : {label}.'
REGLE = 'Tu es le modèle nommé dans « Cette réponse est produite par » : '
NOUVELLE = '- Moteur qui rédige en ce moment, c’est-à-dire toi : {label}.'
RENVOI = 'Tu es le moteur nommé à la ligne « Moteur qui rédige en ce moment » : '
FIN = 'et si l’auteur n’est pas indiqué, dis que tu ne le sais pas.'
SANS_SIGNER = (' Ne signe pas ta réponse et n’y nomme pas ton modèle, sauf si l’utilisateur demande qui répond ou quel '
               'modèle tu es : l’interface l’affiche déjà sous chaque réponse.')
ENTRE = (' (l’interface l’affiche déjà sous ta réponse : ne l’écris pas toi-même, sauf si l’utilisateur demande quel '
         'modèle tu es)')


def variante(nom, consigne, label):
    """The system prompt of HEAD with the engine line and rule 8 rewritten as the variant says."""
    avant = LIGNE.format(label=label)
    assert avant in consigne and REGLE in consigne and FIN in consigne
    if nom == 'avant':
        return consigne
    if nom == 'A':   # the line speaks to the model, and rule 8 says not to sign
        return consigne.replace(avant, NOUVELLE.format(label=label)).replace(REGLE, RENVOI).replace(FIN, FIN + SANS_SIGNER)
    if nom == 'B':   # the line speaks to the model, nothing else
        return consigne.replace(avant, NOUVELLE.format(label=label)).replace(REGLE, RENVOI)
    if nom == 'C':   # the reason is said on the line itself
        return consigne.replace(avant, NOUVELLE.format(label=label)[:-1] + ENTRE + '.').replace(REGLE, RENVOI)
    modele = label.rsplit(' ', 1)[-1]
    locale = f'- IA locale (Ollama) : active, modèle de conversation {modele}.'
    assert locale in consigne
    if nom == 'E':   # no line of its own: said inside the line of the local AI, in the middle of the state
        return (consigne.replace('\n' + avant, '').replace(locale, locale[:-1] + ' : c’est toi, tu rédiges en ce moment.')
                .replace(REGLE, 'Tu es le moteur dont la ligne, dans l’état mesuré, dit « c’est toi » : '))
    if nom == 'F':   # the line of B, but not the last one of the state
        return (consigne.replace('\n' + avant, '').replace(locale, locale + '\n' + NOUVELLE.format(label=label))
                .replace(REGLE, RENVOI))
    raise ValueError(nom)


SANS_SOUVENIR = ('### Contexte (mémoire)\nAucun souvenir sur l’utilisateur n’a été retrouvé pour cette demande : ne lui '
                 'attribue aucune préférence ni information personnelle.')
SIMPLES = ['Pourquoi le ciel est-il bleu ?', 'Explique-moi comment fonctionne une éolienne.',
           'Donne-moi trois conseils pour mieux dormir.', 'Quelle est la différence entre un virus et une bactérie ?',
           'À quoi sert un onduleur ?', 'Comment se forme un arc-en-ciel ?']
SUITES = [('Explique-moi comment fonctionne une éolienne.',
           'Le vent fait tourner les pales, qui entraînent un générateur : il transforme ce mouvement en électricité.',
           'Et quand il n’y a pas de vent ?'),
          ('Comment se forme un arc-en-ciel ?',
           'La lumière du soleil est déviée et décomposée par les gouttes de pluie, qui renvoient chaque couleur sous '
           'un angle un peu différent.', 'Pourquoi il y en a parfois deux ?')]
IDENTITE = ['Quel modèle d’IA es-tu ?', 'C’est quel modèle qui me répond en ce moment ?']
SIGNE = re.compile(r"(produite?|rédigée?|générée?|écrite?) par\s*:?\s*(l['’]\s*)?IA locale|\[IA locale|"
                   r"modèle (qwen|gemma|granite)[\w.:-]+|moteur qui rédige|selon (ma|la) consigne|l['’]interface l['’]affiche|"
                   r"(tu|je) rédiges? en ce moment|c['’]est (toi|moi)\b|IA locale \(?Ollama", re.I)


def demandes(modele):
    for question in SIMPLES:
        yield 'simple', question, f'{SANS_SOUVENIR}\n\n### Demande de l’utilisateur\n{question}'
    for avant, reponse, question in SUITES:
        contexte = ('### Contexte (mémoire)\nÉchanges récents de cette conversation :\n'
                    f'- Utilisateur : {avant}\n- AURORA (rédigé par l’IA locale, {modele}) : {reponse}')
        yield 'suite', question, f'{contexte}\n\n### Demande de l’utilisateur\n{question}'
    for question in IDENTITE:
        yield 'identité', question, f'{SANS_SOUVENIR}\n\n### Demande de l’utilisateur\n{question}'


def ollama(modele, consigne, message):
    corps = json.dumps({'model': modele, 'stream': False, 'think': False, 'keep_alive': '5m',
                        'messages': [{'role': 'system', 'content': consigne}, {'role': 'user', 'content': message}],
                        'options': {'num_predict': 500}}).encode('utf-8')
    requete = urllib.request.Request('http://127.0.0.1:11434/api/chat', corps, {'Content-Type': 'application/json'})
    with urllib.request.urlopen(requete, timeout=300) as reponse:
        return json.load(reponse)['message']['content'].strip()


if __name__ == '__main__':
    sortie, lignes = sys.argv[1], []
    caps = {'internet': True, 'mode': 'equilibre', 'shell': False, 'pc_control': False,
            'cloud': {'configured': False, 'provider': 'anthropic', 'tiers': {}}}
    for modele in sys.argv[2:]:
        label = f'IA locale Ollama, modèle {modele}'
        etat = {**caps, 'ollama': {'up': True, 'models': [modele, 'bge-m3'], 'chat_model': modele,
                                   'embed_model': 'bge-m3', 'embed_ready': True}}
        consigne = prompts.system_prompt(etat, purpose='chat', engine_label=label)
        for nom in os.environ.get('VARIANTES', 'avant,A,B,C').split(','):
            texte, debut = variante(nom, consigne, label), time.time()
            signees, nommees = 0, 0
            for genre, question, message in demandes(modele):
                reponse = ollama(modele, texte, message)
                signe = bool(SIGNE.search(reponse))
                if genre == 'identité':
                    nommees += modele.split(':')[0].lower() in reponse.lower()
                else:
                    signees += signe
                lignes.append({'modele': modele, 'variante': nom, 'genre': genre, 'question': question,
                               'signe': signe, 'reponse': reponse})
            print(f'{modele:16s} {nom:6s} signées {signees} sur {len(SIMPLES) + len(SUITES)} · '
                  f'modèle nommé quand on le demande {nommees} sur {len(IDENTITE)} · {time.time() - debut:.0f} s',
                  flush=True)
            with open(sortie, 'w', encoding='utf-8') as fichier:
                json.dump(lignes, fichier, ensure_ascii=False, indent=1)
