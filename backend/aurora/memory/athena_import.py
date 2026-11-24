"""Import of Athéna's memories into AURORA's (29/10/2026, plan of reviews/athena-inventaire.md).

Two separate steps, as the user asked (« aperçu d'abord ») :
  plan()   reads a COPY of Athéna's base (read-only, through db._read_astra) and says what would be imported,
           kept aside or marked sensitive. Nothing is written.
  apply()  writes the planned memories through store.add_memory (FTS, automatic links, memory.saved events),
           source='athena', source_ref='athena:souvenir:<id>': a memory already imported is skipped, never
           merged on its title (an upsert on the title overwrote a real memory in Athéna, cheminMemoire.ts).
The command line makes the copy, the backup of AURORA's base before any write, and the vectors afterwards:
  python -m aurora.memory.athena_import apercu --sortie FICHIER.md
  python -m aurora.memory.athena_import importer --sauvegarde DOSSIER
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .. import db
from . import store

ATHENA_DB = Path.home() / 'Desktop' / 'vita1' / 'athena-ai-local' / 'data' / 'athena-memoire.db'
SETTING = 'athena_import'
SOURCE = 'athena'
# Same French categories as Athéna (memoryService.ts:25); the others become plain facts, as for ASTRA.
KIND_BY_CATEGORY = {**db._ASTRA_KIND_BY_CATEGORY, 'materiel': 'fact', 'probleme': 'fact', 'echeance': 'fact',
                    'personne': 'fact'}
_ABOUT_ATHENA = re.compile(r'\bath[ée]na\b', re.I)
_PATENT = re.compile(r'\bbrevets?\b|\binpi\b|propri[ée]t[ée] intellectuelle', re.I)
_TO_REREAD = re.compile(r'\bsant[ée]\b|m[ée]dic|maladie|\badresse\b|\bhabite\b|domicile|mot de passe', re.I)
_ISO = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z')
_WORD = re.compile(r'[\wÀ-ÿ-]{3,}')


@dataclass
class Candidate:
    athena_id: int
    title: str
    detail: str
    category: str
    kind: str
    family: str
    verification: str
    confidentiality: str
    pinned: bool
    created_at: str | None
    updated_at: str | None
    excluded: str = ''                       # why it is kept aside ('' = imported)
    reread: list[str] = field(default_factory=list)   # what the user should look at before confirming
    sensitive_why: list[str] = field(default_factory=list)

    @property
    def source_ref(self) -> str:
        return f'athena:souvenir:{self.athena_id}'


def family_of(source: str) -> str:
    """The provenance family of an Athéna memory, from its free-text `source` (inventory § 2.3)."""
    text = (source or '').lower()
    if 'inventaire' in text:
        return 'inventaire'
    if 'conversation' in text and 'confirm' not in text:
        return 'conversation'
    if 'confirm' in text or 'restauration' in text:
        return 'confirmation'
    if 'brevet' in text:
        return 'brevet'
    if 'documentation' in text:
        return 'documentation'
    return 'note'


def _date(value: object) -> str | None:
    text = str(value or '').strip()
    return text if _ISO.fullmatch(text) else None


def _fr_date(iso: str | None) -> str:
    return f'{iso[8:10]}/{iso[5:7]}/{iso[:4]}' if iso else 'date inconnue'


def _detail(row: dict) -> str:
    """The content as it was, then the keywords that are not in it (FTS finds them), then its provenance."""
    content = str(row.get('contenu') or '').strip()
    known = {w.lower() for w in _WORD.findall(f"{row.get('sujet') or ''} {content}")}
    extra = [w for w in dict.fromkeys(str(row.get('mots_cles') or '').split()) if w.lower() not in known]
    lines = [content]
    if extra:
        lines.append('Mots-clés : ' + ' '.join(extra))
    lines.append(f"Provenance (Athéna) : {str(row.get('source') or 'inconnue').strip()}, enregistré le "
                 f"{_fr_date(_date(row.get('cree_le')))}, mis à jour le {_fr_date(_date(row.get('maj_le')))}.")
    return '\n\n'.join(lines)


def candidates(rows: list[dict], *, keep_about_athena: bool = False) -> list[Candidate]:
    """Athéna's memories -> what AURORA would store, with the reasons for each choice."""
    try:
        from ..security.redaction import sensitivity_of   # lazy: owned by the security module
    except Exception:
        def sensitivity_of(_text: str) -> str:   # cannot assess: everything is sensitive (fail closed)
            return 'sensitive'
    out = []
    for row in rows:
        title = str(row.get('sujet') or '').strip()
        if row.get('id') is None or not title:
            continue
        category = str(row.get('categorie') or 'general').strip() or 'general'
        family = family_of(str(row.get('source') or ''))
        text = f"{title}\n{row.get('contenu') or ''}"
        why = []
        if category == 'personne':
            why.append('fiche sur une personne')
        if sensitivity_of(text) == 'sensitive':
            why.append('donnée sensible détectée')
        if _PATENT.search(text):
            why.append('brevet / propriété intellectuelle')
        confirmed = family in ('conversation', 'confirmation')
        item = Candidate(
            athena_id=int(row['id']), title=title[:300], detail=_detail(row), category=category[:60],
            kind=KIND_BY_CATEGORY.get(category.lower(), 'fact'), family=family,
            verification='user_confirmed' if confirmed else 'unverified',
            confidentiality='sensitive' if why else 'private',
            pinned=confirmed and int(row.get('importance') or 0) >= 5,
            created_at=_date(row.get('cree_le')), updated_at=_date(row.get('maj_le')), sensitive_why=why)
        if family == 'inventaire':
            item.excluded = 'inventaire des dossiers : des mesures datées, qu’AURORA remesure avec ses outils'
        elif _ABOUT_ATHENA.search(title) and not keep_about_athena:
            item.excluded = 'décrit Athéna elle-même : AURORA pourrait se croire Athéna'
        if not why and _TO_REREAD.search(text):
            item.reread.append('mentionne la santé, un lieu ou un mot de passe : à marquer sensible ?')
        if not item.excluded and _ABOUT_ATHENA.search(str(row.get('contenu') or '')):
            item.reread.append('nomme Athéna dans son contenu')
        out.append(item)
    return out


def plan(rows: list[dict], *, keep_about_athena: bool = False) -> dict:
    """What an import would do now: imported, already present, kept aside. Nothing is written."""
    present = {r['source_ref'] for r in db.rows("SELECT source_ref FROM memories WHERE source = ?", (SOURCE,))}
    removed = set((db.get_setting(SETTING) or {}).get('removed_refs') or [])
    items = candidates(rows, keep_about_athena=keep_about_athena)
    for c in items:
        if not c.excluded and c.source_ref in removed:
            c.excluded = 'retiré à ta demande : jamais réimporté'
    todo = [c for c in items if not c.excluded and c.source_ref not in present]
    return {'read': len(rows), 'import': todo,
            'already': [c for c in items if not c.excluded and c.source_ref in present],
            'excluded': [c for c in items if c.excluded]}


def apply(planned: dict, *, source_path: str = '') -> dict:
    """Write the planned memories (a memory already imported is skipped), then record the import."""
    from ..tasks import events   # lazy: owned by the tasks module
    imported, skipped = [], 0
    for c in planned['import']:
        if db.row('SELECT 1 AS x FROM memories WHERE source_ref = ?', (c.source_ref,)):
            skipped += 1   # imported since the plan was made: never twice
            continue
        memory = store.add_memory(c.title, c.detail, c.category, kind=c.kind, source=SOURCE, source_ref=c.source_ref,
                                  confidentiality=c.confidentiality, verification=c.verification, pinned=c.pinned,
                                  created_at=c.created_at, updated_at=c.updated_at)
        imported.append(memory['id'])
    counts = {'imported': len(imported), 'skipped': skipped + len(planned['already']),
              'excluded': len(planned['excluded']),
              'sensitive': sum(1 for c in planned['import'] if c.confidentiality == 'sensitive')}
    db.set_setting(SETTING, {**(db.get_setting(SETTING) or {}), 'status': 'done', 'source': source_path,
                             'memory_ids': imported, **counts})   # keeps removed_refs
    events.emit('system.imported', f"Import Athéna : {counts['imported']} souvenir(s)", subject_type='system',
                stage='system', payload={'ok': True, 'source': 'athena', **counts})
    return {**counts, 'memory_ids': imported}


def remove(memory_ids: list[int]) -> dict:
    """Delete imported memories at the user's request (29/10/2026: device passwords, early-September project
    states). Their references are kept in the setting, so a new import never brings them back. A memory that does
    not come from Athéna is refused: this command never touches the user's own memories."""
    setting = db.get_setting(SETTING) or {}
    removed = list(setting.get('removed_refs') or [])
    done, refused = [], []
    for memory_id in memory_ids:
        memory = store.get_memory(int(memory_id))
        if not memory or memory.get('source') != SOURCE:
            refused.append(int(memory_id))
            continue
        if store.delete_memory(int(memory_id)):
            done.append({'id': int(memory_id), 'title': memory['title']})
            if memory['source_ref'] not in removed:
                removed.append(memory['source_ref'])
    db.set_setting(SETTING, {**setting, 'removed_refs': removed})
    return {'removed': done, 'refused': refused, 'removed_refs': removed}


# --------------------------------------------------------------------------- the copy and the preview

def copy_base(source: Path, work: Path) -> Path:
    """Copy Athéna's base with its journal (-wal, -shm) into `work`: Athéna's own files are never opened."""
    if not source.is_file():
        raise FileNotFoundError(f'Base d’Athéna introuvable : {source}')
    work.mkdir(parents=True, exist_ok=True)
    for suffix in ('', '-wal', '-shm'):
        part = Path(f'{source}{suffix}')
        if part.is_file():
            shutil.copy2(part, work / f'{source.name}{suffix}')
    return work / source.name


def read_rows(copy: Path) -> list[dict]:
    return db._read_astra(copy, ('souvenirs',))['souvenirs']


def preview_markdown(planned: dict) -> str:
    """The preview shown to the user before any write (a local file, never committed: it holds titles)."""
    todo, aside, already = planned['import'], planned['excluded'], planned['already']
    sensitive = [c for c in todo if c.confidentiality == 'sensitive']
    reread = [c for c in todo if c.reread]
    lines = ['# Aperçu de l’import des souvenirs d’Athéna', '',
             f"Lus dans la copie de la base d’Athéna : {planned['read']}. **Importés : {len(todo)}**. "
             f'Déjà présents : {len(already)}. Écartés : {len(aside)}. Rien n’est encore écrit.', '',
             '## Ce qui serait importé', '',
             '| N° Athéna | Titre | Catégorie | Vérification | Confidentialité | Épinglé | Créé le |',
             '|---|---|---|---|---|---|---|']
    for c in todo:
        lines.append(f'| {c.athena_id} | {c.title} | {c.category} | '
                     f"{'confirmé par vous' if c.verification == 'user_confirmed' else 'non vérifié'} | "
                     f"{'**sensible**' if c.confidentiality == 'sensitive' else 'privé'} | "
                     f"{'oui' if c.pinned else ''} | {_fr_date(c.created_at)} |")
    lines += ['', f'## Marqués sensibles d’office ({len(sensitive)})', '',
              'Ils ne partent jamais vers Claude sans un accord à part, à chaque envoi.', '']
    lines += [f'- n° {c.athena_id} « {c.title} » : {", ".join(c.sensitive_why)}' for c in sensitive] or ['- aucun']
    lines += ['', f'## À relire avant de confirmer ({len(reread)})', '']
    lines += [f'- n° {c.athena_id} « {c.title} » : {" ; ".join(c.reread)}' for c in reread] or ['- aucun']
    lines += ['', f'## Écartés ({len(aside)})', '']
    lines += [f'- n° {c.athena_id} « {c.title} » : {c.excluded}' for c in aside] or ['- aucun']
    lines += ['', '## Comment c’est écrit', '',
              '- Source « import Athéna », référence `athena:souvenir:<n°>` : un souvenir déjà importé n’est jamais '
              'réécrit, et rien n’est fusionné sur le titre.',
              '- Dates d’origine gardées ; le texte d’origine, ses mots-clés manquants et sa provenance dans le détail.',
              '- Présentés au modèle comme des **données**, jamais comme des consignes (comme l’import ASTRA).',
              '- Sauvegarde de la base d’AURORA juste avant ; puis calcul des vecteurs (`bge-m3`).']
    return '\n'.join(lines) + '\n'


# --------------------------------------------------------------------------- the exchanges (2nd part, E5)

# signalementService.ts:52-80 (vientDUnBanc): the test benches replayed the same questions; they are not the user.
BENCH_PREFIXES = ('banc-', 'mesure-', 'evaluation-', 'tirage-', 'rejeu-', 'iso-', 'ph-', 'mem-', 'nom-')
SEPTEMBER_TESTS = frozenset({
    'apres-1', 'apres-2', 'apres-3', 'apres-4', 'apres-5', 'apres-6',
    'audit-vitesse-1789200415786', 'audit-vitesse-1789200428984', 'audit-vitesse-1789200429735',
    'avec-et-sans', 'bascule', 'bascule2', 'capacites', 'chemins', 'chrono', 'conversation-test', 'culture-generale',
    'd1', 'dessin', 'diag', 'diag1', 'dialogue-claude', 'evaluation', 'final', 'fix1', 'fix2',
    'gmem-1', 'gmem-2', 'gmem-3', 'gmem-4', 'gmem-5', 'gmem-6', 'gpu',
    'gsig-1', 'gsig-2', 'gsig-3', 'gsig-4', 'gsig-5', 'gsig-6',
    'lecture-1', 'lecture-2', 'lecture-3', 'lecture-finale', 'm', 'n1', 'n2', 'n3', 'n4',
    'note-1', 'note-2', 'note-3', 'ram32', 'reference', 'revision', 'rv', 'seul',
    'sig-1', 'sig-2', 'sig-3', 'sig-4', 'sig-5', 'sig-6', 'stress', 'sys', 'sys2', 't',
    'test-bug', 'test-retry', 'v', 'verif', 'verif-sigle', 'vision-reelle', 'voix',
})
EXCHANGES_SETTING = 'athena_import_echanges'
PAUSE_S = 30 * 60   # 30 min without a message = another conversation (the ids did not delimit them before 06/10)


def from_bench(conversation_id: object) -> bool:
    key = str(conversation_id or '')
    return key == 'chauffe' or key in SEPTEMBER_TESTS or key.startswith(BENCH_PREFIXES)


def read_exchanges(copy: Path) -> list[dict]:
    return db._read_astra(copy, ('echanges',))['echanges']


def read_reports(copy: Path) -> list[dict]:
    """Athéna's own reports (signalements): a « faute » verdict marks an exchange she got wrong."""
    return db._read_astra(copy, ('signalements',))['signalements']


# Quality of an answer (29/10/2026, reviews/athena-qualite-echanges.md): what must not come back through the recall.
_ERROR_RE = re.compile(r"\berreur\b|\berror\b|exception|timeout|d[ée]lai d['’]attente|econnrefused|une erreur est "
                       r"survenue|je n['’]ai pas pu (?:g[ée]n[ée]rer|r[ée]pondre|traiter|obtenir)|service (?:indisponible|"
                       r"injoignable)|ollama (?:n['’]est|ne r[ée]pond)", re.I)
_NO_MEMORY_RE = re.compile(r"je n['’]ai pas (?:de |d['’])?(?:m[ée]moire|souvenirs?)\b|je ne (?:garde|conserve|retiens) "
                           r"(?:pas|aucun)|je ne (?:peux|pourrai) pas me (?:souvenir|rappeler)|je ne relis pas|"
                           r"pas acc[eè]s (?:à|a|aux) (?:nos|vos|tes|mes|aux) (?:anciennes |pr[ée]c[ée]dentes )?"
                           r"(?:conversations|[ée]changes)|chaque conversation (?:est|repart)", re.I)
_SELF_RE = re.compile(r"\bje suis ath[ée]na\b|\bath[ée]na est mon nom\b|\bmon (?:r[ôo]le|nom) (?:est|c['’]est)\b|"
                      r"\b(?:je fonctionne|je tourne) (?:localement|sur (?:votre|ton|ta|son))|\bmes (?:capacit[ée]s|outils|"
                      r"fonctions|fonctionnalit[ée]s)\b|\bmon (?:syst[èe]me|inventaire|mod[eè]le)\b", re.I)
_NON_ANSWER_RE = re.compile(r"je n['’]ai pas de r[ée]ponse pr[ée]cise|demande-moi \"?que sais-tu faire|"
                            r"essaie l['’]une de ces pistes", re.I)


def answer_problem(question: str, answer: str | None, faults: set[tuple[str, str]]) -> str:
    """Why an Athéna answer is kept aside ('' = a good answer). Deterministic: her own reports, AURORA's false-claim
    detectors (core/verify.py), empty / failed answers, answers about herself, non-answers."""
    from ..core import verify   # lazy: the core module imports the memory one
    if answer is None:
        return 'question sans réponse gardée'
    text = answer.strip()
    if (question.strip(), text) in faults:
        return 'faute confirmée par Athéna (signalement)'
    if len(re.sub(r'\s+', '', text)) < 12:
        return 'réponse vide ou très courte'
    if _ERROR_RE.search(text[:400]):
        return 'erreur technique'
    if verify._claims(verify.NO_INTERNET_RE, text, skip_negated=False):
        return 'nie avoir Internet (faux : Athéna l’avait)'
    if _NO_MEMORY_RE.search(text):
        return 'nie sa mémoire (faux : elle en avait une)'
    if _SELF_RE.search(text):
        return 'parle d’Athéna elle-même'
    if _NON_ANSWER_RE.search(text):
        return 'ne répond pas (menu générique)'
    return ''


def plan_exchanges(rows: list[dict], reports: list[dict] | None = None) -> dict:
    """Athéna's real exchanges grouped into conversations; the benches, the poor answers (with their question) and
    those already imported set aside."""
    from ..tasks.runs import parse_iso   # lazy: owned by the tasks module
    try:
        from ..security.redaction import sensitivity_of   # lazy: owned by the security module
    except Exception:
        def sensitivity_of(_text: str) -> str:   # cannot assess: everything is sensitive (fail closed)
            return 'sensitive'
    present = {int(r['a']) for r in db.rows(
        "SELECT json_extract(meta_json, '$.athena_id') AS a FROM messages "
        "WHERE json_extract(meta_json, '$.imported_from') = 'athena'") if r['a'] is not None}
    bench = [r for r in rows if from_bench(r.get('conversation_id'))]
    real = sorted((r for r in rows if not from_bench(r.get('conversation_id')) and r.get('role') in ('user', 'assistant')
                   and str(r.get('message') or '').strip() and _date(r.get('cree_le'))),
                  key=lambda r: (r['cree_le'], r['id']))
    faults = {(str(r.get('question') or '').strip(), str(r.get('reponse') or '').strip()) for r in reports or []
              if r.get('verdict') == 'faute' and not from_bench(r.get('conversation_id'))}
    poor, aside = [], set()
    for i, row in enumerate(real):
        if row['role'] != 'user':
            continue
        reply = next((x for x in real[i + 1:i + 4] if x['conversation_id'] == row['conversation_id']), None)
        reply = reply if reply and reply['role'] == 'assistant' else None
        why = answer_problem(str(row['message']), str(reply['message']) if reply else None, faults)
        if why:
            poor.append({'id': int(row['id']), 'created_at': row['cree_le'], 'question': str(row['message']),
                         'why': why})
            aside |= {int(row['id'])} | ({int(reply['id'])} if reply else set())
    real = [r for r in real if int(r['id']) not in aside]
    sessions: list[list[dict]] = []
    for row in real:
        gap = (parse_iso(row['cree_le']) - parse_iso(sessions[-1][-1]['cree_le'])).total_seconds() if sessions else None
        if gap is not None and gap < PAUSE_S:
            sessions[-1].append(row)
        else:
            sessions.append([row])
    todo, already = [], 0
    for group in sessions:
        messages = [{'id': int(r['id']), 'role': r['role'], 'content': str(r['message']), 'created_at': r['cree_le'],
                     'athena_conversation': str(r.get('conversation_id') or ''),
                     'sensitive': sensitivity_of(str(r['message'])) == 'sensitive'} for r in group]
        new = [m for m in messages if m['id'] not in present]
        already += len(messages) - len(new)
        if not new:
            continue
        first_question = next((m['content'] for m in messages if m['role'] == 'user'), messages[0]['content'])
        todo.append({'title': '[Athéna] ' + re.sub(r'\s+', ' ', first_question).strip(), 'first': messages[0]['created_at'],
                     'last': messages[-1]['created_at'], 'questions': sum(1 for m in messages if m['role'] == 'user'),
                     'messages': new, 'all_ids': [m['id'] for m in messages]})
    return {'read': len(rows), 'bench': len(bench), 'sessions': todo, 'already': already, 'poor': poor,
            'messages': sum(len(s['messages']) for s in todo),
            'questions': sum(1 for s in todo for m in s['messages'] if m['role'] == 'user'),
            'sensitive': sum(1 for s in todo for m in s['messages'] if m['sensitive'])}


def apply_exchanges(planned: dict, *, source_path: str = '') -> dict:
    """Write the planned conversations and messages, dated; a message already imported is never written again and
    a conversation cut by an interruption is completed rather than duplicated."""
    from ..tasks import events   # lazy: owned by the tasks module
    conversations = messages = 0
    for session in planned['sessions']:
        marks = ','.join('?' * len(session['all_ids']))
        found = db.row(f"SELECT conversation_id FROM messages WHERE json_extract(meta_json, '$.imported_from') = 'athena' "
                       f"AND json_extract(meta_json, '$.athena_id') IN ({marks}) LIMIT 1", session['all_ids'])
        if found:
            conversation_id = int(found['conversation_id'])
        else:
            conversation_id = store.create_conversation(session['title'], created_at=session['first'])['id']
            conversations += 1
        for m in session['messages']:
            if db.row("SELECT 1 AS x FROM messages WHERE json_extract(meta_json, '$.imported_from') = 'athena' "
                      "AND json_extract(meta_json, '$.athena_id') = ?", (m['id'],)):
                continue
            meta = {'imported_from': 'athena', 'athena_id': m['id'], 'athena_conversation': m['athena_conversation']}
            if m['sensitive']:
                meta['sensitive'] = True   # masked from every cloud context (memory/context.py)
            store.add_message(conversation_id, m['role'], m['content'], meta=meta, created_at=m['created_at'])
            messages += 1
    counts = {'conversations': conversations, 'messages': messages, 'bench': planned['bench'],
              'poor': len(planned.get('poor') or []), 'already': planned['already'], 'sensitive': planned['sensitive']}
    db.set_setting(EXCHANGES_SETTING, {'status': 'done', 'source': source_path, **counts})
    events.emit('system.imported', f'Import Athéna : {messages} message(s) en {conversations} conversation(s)',
                subject_type='system', stage='system', payload={'ok': True, 'source': 'athena', 'part': 'echanges',
                                                                **counts})
    return counts


def preview_exchanges_markdown(planned: dict) -> str:
    """The preview of the exchanges, before any write (a local file, never committed: it holds questions)."""
    sessions, poor = planned['sessions'], planned.get('poor') or []
    reasons: dict[str, int] = {}
    for p in poor:
        reasons[p['why']] = reasons.get(p['why'], 0) + 1
    lines = ['# Aperçu de l’import des échanges d’Athéna', '',
             f"Lus dans la copie : {planned['read']} messages. Écartés (bancs de test) : {planned['bench']}. "
             f"Écartés pour la qualité : {len(poor)} question(s) et leur réponse. Déjà importés : {planned['already']}. "
             f"**À importer : {planned['messages']} messages ({planned.get('questions', 0)} questions) en "
             f"{len(sessions)} conversation(s)**, dont {planned['sensitive']} marqués sensibles. Rien n’est encore écrit.",
             '']
    if poor:
        lines += ['## Écartés pour la qualité', '', '| Raison | Questions |', '|---|---|']
        lines += [f'| {why} | {n} |' for why, n in sorted(reasons.items(), key=lambda kv: -kv[1])]
        lines += ['']
        lines += [f"- {_fr_date(p['created_at'])} n° {p['id']} « {re.sub(r'[|]', '/', p['question'][:80])} » : {p['why']}"
                  for p in poor]
        lines += ['']
    if sessions:
        lines += [f"Période : du {_fr_date(sessions[0]['first'])} au {_fr_date(sessions[-1]['last'])}.", '',
                  '| Début | Questions | Sensibles | Titre (première question) |', '|---|---|---|---|']
        for s in sessions:
            title = s['title'] if len(s['title']) <= 90 else s['title'][:89] + '…'
            sensitive = sum(1 for m in s['messages'] if m['sensitive'])
            lines.append(f"| {_fr_date(s['first'])} {s['first'][11:16]} UTC | {s['questions']} | {sensitive or ''} | "
                         f"{title.replace('|', '/')} |")
    lines += ['', '## Comment c’est écrit', '',
              '- Une conversation AURORA par séance (30 min sans message = une autre), titre « [Athéna] » + la première '
              'question (un secret y est masqué), dates d’origine gardées.',
              '- Chaque message garde `imported_from: athena` et son numéro : jamais importé deux fois.',
              '- Un message que le détecteur juge sensible est marqué : il ne part jamais dans un contexte vers Claude.',
              '- Le rappel les cite comme « réponse d’Athéna, non vérifiée par AURORA », jamais comme un fait.',
              '- Sauvegarde de la base d’AURORA juste avant.']
    return '\n'.join(lines) + '\n'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Import des souvenirs et des échanges d’Athéna dans AURORA.')
    parser.add_argument('action', choices=('apercu', 'importer', 'apercu-echanges', 'importer-echanges', 'retirer'))
    parser.add_argument('--ids', help='retirer : numéros AURORA des souvenirs importés à supprimer, séparés par des virgules')
    parser.add_argument('--athena', default=os.getenv('AURORA_ATHENA_DB') or str(ATHENA_DB))
    parser.add_argument('--sortie', help='apercu : fichier Markdown à écrire (titres compris, ne pas commiter)')
    parser.add_argument('--sauvegarde', help='importer : dossier de la copie de la base faite avant d’écrire')
    parser.add_argument('--avec-athena', action='store_true', help='garder aussi les souvenirs qui décrivent Athéna')
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding='utf-8')
    db.initialize()
    if args.action.endswith('-echanges'):
        return _exchanges_command(args)
    if args.action == 'retirer':
        ids = [int(x) for x in re.findall(r'\d+', args.ids or '')]
        if not ids or not args.sauvegarde:
            print('Refusé : --ids et --sauvegarde DOSSIER sont obligatoires (une copie avant toute suppression).')
            return 2
        backup = db.daily_backup(args.sauvegarde, reason='avant-retrait-souvenirs')
        if backup is None:
            print('Refusé : la sauvegarde de la base d’AURORA n’a pas pu être faite, rien n’est supprimé.')
            return 2
        print('Sauvegarde :', backup)
        done = remove(ids)
        for item in done['removed']:
            print(f"Supprimé n° {item['id']} « {item['title']} »")
        if done['refused']:
            print('Refusés (pas des souvenirs importés d’Athéna) :', ', '.join(map(str, done['refused'])))
        return 0
    with tempfile.TemporaryDirectory(prefix='athena-copie-') as work:
        rows = read_rows(copy_base(Path(args.athena), Path(work)))
    planned = plan(rows, keep_about_athena=args.avec_athena)
    summary = (f"lus {planned['read']}, à importer {len(planned['import'])}, déjà présents "
               f"{len(planned['already'])}, écartés {len(planned['excluded'])}, sensibles "
               f"{sum(1 for c in planned['import'] if c.confidentiality == 'sensitive')}")
    if args.action == 'apercu':
        if args.sortie:
            Path(args.sortie).write_text(preview_markdown(planned), encoding='utf-8')
        print('Aperçu (rien n’est écrit) :', summary)
        return 0
    if not args.sauvegarde:
        print('Refusé : --sauvegarde DOSSIER est obligatoire (CLAUDE.md, une copie avant toute écriture).')
        return 2
    if not planned['import']:
        print('Rien à importer :', summary)
        return 0
    backup = db.daily_backup(args.sauvegarde, reason='avant-import-athena')
    if backup is None:
        print('Refusé : la sauvegarde de la base d’AURORA n’a pas pu être faite, rien n’est écrit.')
        return 2
    print('Sauvegarde :', backup)
    done = apply(planned, source_path=args.athena)
    from . import embeddings   # lazy: needs Ollama; the memories are stored even if it does not answer
    vectors = asyncio.run(embeddings.backfill(limit=200))
    print(f"Importés {done['imported']} (sensibles {done['sensitive']}), ignorés {done['skipped']}, "
          f"écartés {done['excluded']}, vecteurs calculés {vectors}.")
    return 0


def _exchanges_command(args: argparse.Namespace) -> int:
    """apercu-echanges / importer-echanges: the same copy, preview and backup as for the memories."""
    with tempfile.TemporaryDirectory(prefix='athena-copie-') as work:
        copy = copy_base(Path(args.athena), Path(work))
        rows, reports = read_exchanges(copy), read_reports(copy)
    planned = plan_exchanges(rows, reports)
    summary = (f"lus {planned['read']}, bancs écartés {planned['bench']}, écartés pour la qualité "
               f"{len(planned['poor'])}, déjà importés {planned['already']}, à importer {planned['messages']} "
               f"({planned['questions']} questions) en {len(planned['sessions'])} conversation(s), "
               f"sensibles {planned['sensitive']}")
    if args.action == 'apercu-echanges':
        if args.sortie:
            Path(args.sortie).write_text(preview_exchanges_markdown(planned), encoding='utf-8')
        print('Aperçu (rien n’est écrit) :', summary)
        return 0
    if not args.sauvegarde:
        print('Refusé : --sauvegarde DOSSIER est obligatoire (CLAUDE.md, une copie avant toute écriture).')
        return 2
    if not planned['sessions']:
        print('Rien à importer :', summary)
        return 0
    backup = db.daily_backup(args.sauvegarde, reason='avant-import-athena-echanges')
    if backup is None:
        print('Refusé : la sauvegarde de la base d’AURORA n’a pas pu être faite, rien n’est écrit.')
        return 2
    print('Sauvegarde :', backup)
    done = apply_exchanges(planned, source_path=args.athena)
    print(f"Importés {done['messages']} message(s) en {done['conversations']} conversation(s), "
          f"sensibles {done['sensitive']}, bancs écartés {done['bench']}, écartés pour la qualité {done['poor']}.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
