"""Apply the « sources » check as it is now (core/verify.check, which also takes the joined document) to the answers
already measured by the bench, off line: which answers it reported at the time, which it still reports.
The bench kept the verdict of the check at the time of the measure (« controles_rates ») and the composed answer;
the text of the model is what stands before AURORA's own notes (the same cut as rejouer_retrait.py).
An answer written without any tool is recomputed exactly, without the sheet then with it when the turn joined it.
The verdict of an answer that searched the web is kept as measured: its sources were not kept, and nothing is joined
there. The files whose name starts with a prefix given at the end were measured with the corrected check: for them
the measure must give the verdict computed WITH the sheet, for the others the one computed WITHOUT it — an answer
where it does not is listed. Read only.
Usage: python -X utf8 rejouer_sources.py <dossier> <sortie.json ou -> [<préfixe mesuré après la correction> ...]"""
import glob
import json
import os
import re
import sys
import tempfile

essai = os.path.join(tempfile.gettempdir(), 'aurora-tests')
os.environ.setdefault('AURORA_HOME', essai)
os.environ.setdefault('AURORA_DB_PATH', os.path.join(essai, 'aurora.db'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'backend'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from aurora.core import verify  # noqa: E402
from conversation_modeles import FICHE, TOURS  # noqa: E402

NOTE = re.compile(r'\n\n(?=Source consultée avant de répondre|Sources? :|Vérification AURORA)')
APRES = tuple(sys.argv[3:])
CLES = ('rédigées', 'sur le document', 'sur le document avec [n]', 'ailleurs avec [n]',
        'signalées sans la correction, document', 'signalées avec la correction, document',
        'signalées sans la correction, ailleurs', 'signalées avec la correction, ailleurs')


def signale(texte, documents=()):
    """True when the « sources » check reports this text of a model, written with no tool and these documents."""
    bilan = verify.check(answer=texte, steps=[], caps={'internet': True}, mode='equilibre', permissions={},
                         llm_text_used=True, documents=documents)
    return not next(c for c in bilan['checks'] if c['name'] == 'sources')['ok']


groupes = {nom: dict.fromkeys(CLES, 0) for nom in ('mesurées avant la correction', 'mesurées après la correction')}
levees, restent, ecarts = [], [], []
for chemin in sorted(glob.glob(os.path.join(sys.argv[1], '*.json'))):
    try:
        donnees = json.load(open(chemin, encoding='utf-8'))
    except ValueError:
        continue
    if not isinstance(donnees, dict) or not isinstance(donnees.get('tirages'), list):
        continue
    nom = os.path.basename(chemin)[:-5]
    corrige = bool(APRES) and nom.startswith(APRES)
    total = groupes['mesurées après la correction' if corrige else 'mesurées avant la correction']
    for rang, tirage in enumerate(donnees['tirages'], 1):
        for ligne in tirage:
            if not isinstance(ligne, dict) or not ligne.get('temps'):
                continue
            joint = bool(TOURS[ligne['tour'] - 1].get('joint')) and ligne.get('route') == 'doc.analyze'
            du_modele = NOTE.split(ligne.get('reponse') or '', 1)[0]
            cite = bool(verify.REFERENCE_RE.search(du_modele))
            mesure = 'sources' in (ligne.get('controles_rates') or [])
            sans = avec = mesure
            if not (ligne.get('origine') or {}).get('tools'):
                sans = signale(du_modele)
                avec = signale(du_modele, [FICHE]) if joint else sans
                if mesure != (avec if corrige else sans):     # the replay does not give the verdict of the measure
                    ecarts.append(f"{nom} t{rang}/{ligne['tour']}")
            lieu = 'document' if joint else 'ailleurs'
            total['rédigées'] += 1
            total['sur le document'] += joint
            total['sur le document avec [n]' if joint else 'ailleurs avec [n]'] += cite
            total[f'signalées sans la correction, {lieu}'] += sans
            total[f'signalées avec la correction, {lieu}'] += avec
            if sans or avec:
                refs = ', '.join(f'[{n}]' for n in sorted({int(n) for n in verify.REFERENCE_RE.findall(du_modele)}))
                extrait = ' ⏎ '.join(m.strip() for m in re.split(r'\n+', du_modele) if verify.REFERENCE_RE.search(m))
                (restent if avec else levees).append({'tirage': f"{nom} t{rang}/{ligne['tour']}", 'modele': donnees.get('modele'),
                                                      'mesure_apres_la_correction': corrige, 'lieu': lieu,
                                                      'question': ligne.get('texte'), 'references': refs,
                                                      'sans': sans, 'avec': avec, 'extrait': extrait[:400]})
for groupe, total in groupes.items():
    print(f'=== {groupe}')
    for cle, valeur in total.items():
        print(f'   {cle:40s} {valeur}')
print('\n--- la mesure ne donne pas le verdict recalculé ---')
print('   ', ', '.join(ecarts) or 'aucun écart')
for titre, liste in (('alertes levées', levees), ('alertes qui restent', restent)):
    print(f'\n--- {titre} : {len(liste)} ---')
    for cas in liste:
        print(f"   {cas['tirage']} · {cas['lieu']} · {cas['references']}\n      {cas['extrait'][:230]}")
if len(sys.argv) > 2 and sys.argv[2] != '-':
    with open(sys.argv[2], 'w', encoding='utf-8') as fichier:
        json.dump({'groupes': groupes, 'ecarts': ecarts, 'levees': levees, 'restent': restent}, fichier,
                  ensure_ascii=False, indent=1)
