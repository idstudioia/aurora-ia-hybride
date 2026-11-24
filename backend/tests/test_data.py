"""P3 « L'analyse de données » (tools/data.py, intent data.analyze, the agent restricted to the analysis tools).

What is checked: French CSV files are read right (separator, decimal comma, dates); every figure is computed by
the code (sums, periods, Tukey rule, comparison) and known by hand here; the table comes from the request (joined
file or allowed folder, never another place) and is never sent whole to a model; the model chooses only among the
analysis tools, without choosing the table; the page and the proposed actions come from the calculations.
Temporary AURORA_HOME (TempEnv): the real base is never opened.
"""
import logging
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from aurora.core import compose
from aurora.tasks import artefact_html, artefacts, runs
from aurora.tools import data
from aurora.tools.base import ToolError
from tests.doubles_core import FakeDecision, FakeOllama, FakeTools, TempEnv, build
from tests.test_agent import scripted, turn
from tests.test_artefacts import result_for
from tests.test_orchestrator import CoreTestCase

SMALL = 'Date;Catégorie;Montant\n01/09/2026;A;10\n15/09/2026;B;20\n30/09/2026;A;30\n01/10/2026;A;40\n31/10/2026;B;50\n'


def expenses() -> str:
    """Four months of expenses, one row every two days, unique labels; one concert far above the others."""
    lines = ['Date;Catégorie;Libellé;Montant (€)']
    cats = ('Courses', 'Restaurant', 'Transport', 'Loisirs')
    for month in (7, 8, 9, 10):
        for day in range(1, 31, 2):
            cat = cats[(day // 2) % 4]
            amount = 20 + (day % 7) * 3 + (12 if month == 10 and cat == 'Restaurant' else 0)
            lines.append(f'{day:02d}/{month:02d}/2026;{cat};Achat {month}-{day};{amount},50')
    lines.append('15/10/2026;Loisirs;Concert;1 250,00 €')
    return '\n'.join(lines) + '\n'


TOOLS = {'data.describe': (data.describe_tool, data.SourceParams), 'data.group': (data.group_tool, data.GroupParams),
         'data.trend': (data.trend_tool, data.TrendParams), 'data.outliers': (data.outliers_tool, data.OutliersParams),
         'data.compare': (data.compare_tool, data.CompareParams)}


def real(name: str):
    """The real calculation behind a FakeTools entry (its parameters validated by its own model)."""
    func, model = TOOLS[name]

    def call(params: dict) -> dict:
        try:
            return func(model.model_validate(params)).to_dict()
        except ToolError as exc:
            return {'ok': False, 'summary': exc.message_fr, 'error': exc.message_fr, 'provenance': 'csv'}
    return call


def setUpModule():
    logging.getLogger('aurora').setLevel(logging.CRITICAL)


class ReadingTests(unittest.TestCase):
    def test_french_numbers_dates_and_separator(self):
        self.assertEqual([data.parse_number(s) for s in ('1 234,50 €', '(12,5)', '15 %', '0612', '1.234,5', '1,234.5',
                                                         'abc', '')],
                         [1234.5, -12.5, 15.0, None, 1234.5, 1234.5, None, None])
        self.assertEqual(str(data.parse_date('14/09/2026')), '2026-09-14 00:00:00')
        self.assertEqual(data.parse_date('2026-09-14T10:00:00Z'),      # put in the PC's local time
                         datetime(2026, 9, 14, 10, tzinfo=timezone.utc).astimezone().replace(tzinfo=None))
        table = data.parse_text(expenses(), 'depenses.csv', 'test')
        self.assertEqual([(c.name, c.kind) for c in table.columns],
                         [('Date', 'date'), ('Catégorie', 'texte'), ('Libellé', 'texte'), ('Montant (€)', 'nombre')])
        self.assertEqual((table.rows, table.delimiter), (61, ';'))
        self.assertEqual(table.columns[3].values[-1], 1250.0)

    def test_bad_lines_are_counted_and_text_is_not_a_table(self):
        table = data.parse_text('a,b\n1,2\n3,4,5\n6,7\n', 'x.csv', 'test')
        self.assertEqual((table.rows, table.skipped), (2, 1))
        self.assertFalse(data.is_table('Bonjour, voici une phrase.\nUne autre.\nEt encore une.'))
        with self.assertRaisesRegex(ToolError, 'ne ressemble pas à un tableau'):
            data.parse_text('une seule colonne\nsans séparateur\nici', 'x.csv', 'test')


class CalculationTests(unittest.TestCase):
    def setUp(self):
        self.env = TempEnv().__enter__()
        self.addCleanup(self.env.__exit__, None, None, None)
        self.table = data.parse_text(SMALL, 'petit.csv', 'test')

    def test_group_trend_and_compare_known_by_hand(self):
        grouped = data.group(self.table, 'catégorie', 'Montant')          # names match without accents or case
        self.assertEqual([(g['group'], g['value'], g['share']) for g in grouped['groups']],
                         [('A', 80.0, 53.33), ('B', 70.0, 46.67)])
        trend = data.trend(self.table, 'Date', 'Montant', 'somme', 'mois')
        self.assertEqual([(p['period'], p['value'], p['partial']) for p in trend['points']],
                         [('2026-09', 60.0, False), ('2026-10', 90.0, False)])
        self.assertEqual(trend['change_pct'], 50.0)
        compared = data.compare(self.table, 'Date', 'Montant', 'somme', 'mois', by='Catégorie')
        self.assertEqual((compared['first']['value'], compared['second']['value'], compared['diff'], compared['diff_pct']),
                         (60.0, 90.0, 30.0, 50.0))
        self.assertEqual({c['group']: (c['first'], c['second'], c['diff']) for c in compared['changes']},
                         {'A': (40.0, 40.0, 0.0), 'B': (20.0, 50.0, 30.0)})

    def test_filters(self):
        f = data.Filter
        self.assertEqual(data.rows_of(self.table, [f(column='Montant', op='>=', value=30)]), [2, 3, 4])
        self.assertEqual(data.rows_of(self.table, [f(column='Date', op='=', value='2026-10')]), [3, 4])
        self.assertEqual(data.rows_of(self.table, [f(column='Montant', op='entre', value=[20, 40])]), [1, 2, 3])
        self.assertEqual(data.rows_of(self.table, [f(column='Catégorie', op='!=', value='a')]), [1, 4])
        with self.assertRaisesRegex(ToolError, 'Colonnes du fichier : Date, Catégorie et Montant'):
            data.group(self.table, 'Client')
        with self.assertRaisesRegex(ToolError, 'non numérique'):
            data.rows_of(self.table, [f(column='Montant', op='>', value='beaucoup')])

    def test_outliers_by_tukey_and_the_last_month_left_out_when_incomplete(self):
        table = data.parse_text(expenses(), 'depenses.csv', 'test')
        found = data.outliers(table, 'Montant (€)')
        self.assertEqual((found['count'], found['outliers'][0]['line'], found['outliers'][0]['level']), (1, 62, 'fort'))
        self.assertEqual(found['outliers'][0]['context']['Libellé'], 'Concert')
        compared = data.compare(table, 'Date', 'Montant (€)')
        self.assertEqual((compared['first']['period'], compared['second']['period']), ('2026-08', '2026-09'))
        self.assertIn('oct. 2026 incomplet(s)', compared['calc'])           # data stop on the 29th
        self.assertEqual([name for name, _p in data.standard_calls(table)],
                         ['data.trend', 'data.group', 'data.outliers', 'data.compare'])


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.env = TempEnv().__enter__()
        self.addCleanup(self.env.__exit__, None, None, None)
        self.root = Path(tempfile.mkdtemp(dir=self.env.dir)).resolve()
        roots = [{'kind': 'custom', 'label': 'Données', 'path': self.root}]
        patcher = mock.patch('aurora.tools.files.allowed_roots', return_value=roots)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_joined_table_is_read_back_from_its_request(self):
        run = runs.create_run('Analyse ce fichier')
        runs.update_run(run['id'], decision_json={'attachments': [{'name': 'note.txt', 'text': 'Juste une note.'},
                                                                  {'name': 'petit.csv', 'text': SMALL}]})
        source, why = data.source_for('Analyse ce fichier', [{'text': 'Juste une note.'}, {'text': SMALL}], run['id'])
        self.assertEqual((source, why), (f"jointe:{run['id']}:1", ''))
        self.assertEqual(data.load(source).rows, 5)
        with self.assertRaisesRegex(ToolError, 'introuvable'):
            data.load(f"jointe:{run['id']}:7")

    def test_a_named_file_of_an_allowed_folder_and_nothing_else(self):
        (self.root / 'banque').mkdir()
        path = self.root / 'banque' / 'dépenses.csv'
        path.write_bytes('Date;Catégorie;Montant\n01/09/2026;Café;2,5\n'.encode('cp1252'))
        source, why = data.find_file('Analyse dépenses.csv stp')
        self.assertEqual((source, why), (f'fichier:{path}', ''))
        table = data.load(source)
        self.assertEqual((table.encoding, table.columns[1].name, table.columns[1].values[0]), ('cp1252', 'Catégorie', 'Café'))
        outside = Path(tempfile.mkdtemp()) / 'ailleurs.csv'
        outside.write_text('a;b\n1;2\n', encoding='utf-8')
        self.addCleanup(outside.unlink)
        self.assertIsNone(data.source_for(f'Analyse {outside}', [], 1)[0])
        self.assertIn('hors des dossiers autorisés', data.source_for(f'Analyse {outside}', [], 1)[1])
        none, why = data.find_file('Analyse absent.csv')
        self.assertIsNone(none)
        self.assertIn('Je n’ai pas trouvé « absent.csv »', why)
        self.assertEqual(data.find_file('Analyse mes dépenses'), (None, ''))


class ReviewTests(unittest.TestCase):
    """The defects found by the review of P3 (29/10/2026), one test each."""

    def setUp(self):
        self.env = TempEnv().__enter__()
        self.addCleanup(self.env.__exit__, None, None, None)

    def test_the_decimal_mark_is_the_column_s(self):
        english = data.parse_text('Item,Price\na,"1,234"\nb,"2,500"\nc,567.89\n', 'en.csv', 'test')
        self.assertAlmostEqual(sum(english.columns[1].values), 4301.89)
        french = data.parse_text('Libellé;Montant\na;1.234\nb;12,5\n', 'fr.csv', 'test')
        self.assertEqual(french.columns[1].values, [1234.0, 12.5])

    def test_zones_utf16_no_titles_and_wide_files(self):
        self.assertEqual(data.parse_date('2026-09-30T22:30:00Z'),
                         datetime(2026, 9, 30, 22, 30, tzinfo=timezone.utc).astimezone().replace(tzinfo=None))
        self.assertEqual(data._decode('a;b\n1;2\n'.encode('utf-16'))[1], 'utf-16')
        bare = data.parse_text('12/09/2026;Pharmacie;45,20\n13/09/2026;Café;2,50\n', 'x.csv', 'test')
        self.assertEqual((bare.headerless, bare.rows, [c.name for c in bare.columns]),
                         (True, 2, ['Colonne 1', 'Colonne 2', 'Colonne 3']))
        years = data.parse_text('Poste;2025;2026\nLoyer;800;820\nCourses;300;310\n', 'x.csv', 'test')
        self.assertEqual((years.headerless, [c.name for c in years.columns]), (False, ['Poste', '2025', '2026']))
        line = ';'.join(str(i) for i in range(70))
        table = data.parse_text(';'.join(f'c{i}' for i in range(70)) + f'\n{line}\n{line}\n', 'x.csv', 'test')
        self.assertEqual((table.rows, len(table.columns), table.dropped), (2, data.MAX_COLUMNS, 10))

    def test_a_stray_old_date_and_incomplete_periods(self):
        stray = data.parse_text(SMALL + '01/01/1900;A;5\n', 'x.csv', 'test')
        trend = data.trend(stray, 'Date', 'Montant', 'somme', 'mois')
        self.assertEqual(trend['points'][-1]['period'], '2026-10')
        self.assertIn('400 dernières périodes', trend['calc'])
        compared = data.compare(stray, 'Date', 'Montant')
        self.assertEqual((compared['first']['period'], compared['second']['period']), ('2026-09', '2026-10'))
        days = [date(2026, 7, 15) + timedelta(days=n) for n in range(98)]     # 15/07 to 20/10
        daily = data.parse_text('\n'.join(['Date;Montant'] + [f'{d:%d/%m/%Y};100' for d in days]), 'x.csv', 'test')
        trend = data.trend(daily, 'Date', 'Montant', 'somme', 'mois')
        self.assertEqual((trend['first_complete'], trend['last_complete'], trend['change_pct']),
                         ('août 2026', 'sept. 2026', round((3000 - 3100) / 3100 * 100, 2)))
        named = data.compare(daily, 'Date', 'Montant', first='2026-09', second='2026-10')
        self.assertEqual(named['incomplete'], ['oct. 2026'])
        self.assertIn('l’écart compare des durées différentes', named['calc'])

    def test_tukey_when_most_values_are_the_same(self):
        text = 'Libellé;Montant\n' + '\n'.join(['Abonnement;9,99'] * 11 + ['Double débit;99,90'])
        found = data.outliers(data.parse_text(text, 'x.csv', 'test'), 'Montant')
        self.assertEqual((found['count'], found['outliers'][0]['line'], found['outliers'][0]['value']), (1, 13, 99.9))

    def test_signs_shares_and_the_watch_action(self):
        bank = data.parse_text('Date;Catégorie;Montant\n01/09/2026;Salaire;2500\n02/09/2026;Restaurant;-600\n'
                               '03/09/2026;Loyer;-1100\n', 'x.csv', 'test')
        self.assertTrue(all('share' not in g for g in data.group(bank, 'Catégorie', 'Montant')['groups']))

        def watch(first: float, second: float) -> list[str]:
            view = compose.data_view([('data.describe', {'ok': True, 'data': {'name': 'releve.csv', 'rows': 4}}),
                                      ('data.compare', {'ok': True, 'data': {
                                          'value': 'Montant', 'calc': 'calcul', 'diff_pct': 1.0, 'incomplete': [],
                                          'first': {'period': '2026-08', 'label': 'août 2026', 'value': 1},
                                          'second': {'period': '2026-09', 'label': 'sept. 2026', 'value': 1},
                                          'changes': [{'group': 'Restaurant', 'first': first, 'second': second,
                                                       'diff': second - first, 'diff_pct': 0.0}]}})], 'jointe:1:0')
            return [a['title'] for a in view['actions'] if a['id'] == 'compare']
        self.assertEqual(watch(-600.0, -155.0), [])                             # the expense went down
        self.assertEqual(watch(-155.0, -600.0), ['Surveiller « Restaurant » : +287,1 % en sept. 2026'])

    def test_file_names_with_spaces_excel_and_read_errors(self):
        root = Path(tempfile.mkdtemp(dir=self.env.dir)).resolve()
        october = "relevé d'octobre.csv"
        for name in ('budget 2026.csv', '2026.csv', october):
            (root / name).write_text('a;b\n1;2\n', encoding='utf-8')
        with mock.patch('aurora.tools.files.allowed_roots', return_value=[{'kind': 'custom', 'label': 'D', 'path': root}]):
            self.assertEqual(data.find_file('Analyse le fichier budget 2026.csv')[0], f"fichier:{root / 'budget 2026.csv'}")
            self.assertEqual(data.find_file(f'Analyse {october}')[0], f'fichier:{root / october}')
            self.assertEqual(data.source_for('Analyse Documents\\budget.xlsx', [], 1), (None, data.EXCEL_NOT_YET))
            with mock.patch.object(Path, 'read_bytes', side_effect=PermissionError(13, 'Accès refusé')):
                with self.assertRaisesRegex(ToolError, 'Lecture de « 2026.csv » impossible : Accès refusé'):
                    data.load(f"fichier:{root / '2026.csv'}")

    def test_a_page_with_dates_before_1970(self):
        run = runs.create_run('Analyse naissances.csv')
        step = {'tool': 'data.trend', 'finished_at': '2026-10-29T08:00:00Z'}
        res = {'ok': True, 'provenance': 'csv', 'sources': [], 'data': {
            'period': 'annee', 'agg': 'nombre', 'value': None, 'calc': 'calcul', 'points': [
                {'period': '1960', 'label': '1960', 'start': '1960-01-01', 'value': 3, 'count': 3, 'partial': False},
                {'period': '1961', 'label': '1961', 'start': '1961-01-01', 'value': 5, 'count': 5, 'partial': False}]}}
        page = artefacts.analysis_page(result_for(run['id'], []), [(step, res)])
        html = artefact_html.render(page.model_dump(mode='json'), page_id='p', version=1, versions=1, created_at='')
        self.assertIn('01/1960', html)


class AnalysisFlowTests(CoreTestCase):
    def setUp(self):
        super().setUp()
        self.tools = FakeTools({name: real(name) for name in TOOLS})
        for name, (_func, model) in TOOLS.items():   # the tools' own parameter models, as in the service
            self.tools.REGISTRY[name].params = model

    async def test_an_imposed_document_analysis_of_a_table_stays_an_analysis(self):
        # « Analyser le fichier » (or a file sent without text) imposes doc.analyze: a table never goes whole.
        orch, tools, ollama, _ = build(FakeDecision('chat.general'), tools=self.tools,
                                       ollama=FakeOllama(reply=scripted(turn('answer', answer='Rien à signaler.'))))
        result = await orch.handle_request('', options={'intent': 'doc.analyze'},
                                           attachments=[{'name': 'depenses.csv', 'text': expenses()}])
        self.assertEqual(result['decision']['intent'], 'data.analyze')
        sent = ' '.join(str(m) for call in ollama.calls for m in call['messages'])
        self.assertNotIn('Achat 9-27', sent)

    async def test_a_sensitive_folder_file_makes_a_sensitive_request(self):
        root = Path(tempfile.mkdtemp(dir=self.env.dir)).resolve()
        (root / 'cartes.csv').write_text('Date;Carte;Montant\n01/09/2026;4111 1111 1111 1111;12\n'
                                         '02/09/2026;4111 1111 1111 1111;15\n', encoding='utf-8')
        with mock.patch('aurora.tools.files.allowed_roots', return_value=[{'kind': 'custom', 'label': 'D', 'path': root}]):
            orch, tools, ollama, _ = build(FakeDecision('data.analyze'), tools=self.tools,
                                           ollama=FakeOllama(reply=scripted(turn('answer', answer='Deux paiements.'))))
            result = await orch.handle_request('Analyse cartes.csv')
        self.assertEqual(runs.decision_of(runs.get_run(result['task_id']))['sensitivity'], 'sensitive')

    async def test_invalid_params_of_the_model_are_refused_and_said(self):
        replies = scripted(turn('data.group', {'by': 'Catégorie', 'agg': 'sum'}), turn('answer', answer='Voilà.'))
        orch, tools, ollama, _ = build(FakeDecision('chat.general'), tools=self.tools, ollama=FakeOllama(reply=replies))
        result = await orch.handle_request('Analyse', attachments=[{'name': 'petit.csv', 'text': SMALL}])
        refused = [h for h in result['agent']['history'] if h['status'] == 'refused']
        self.assertIn('paramètres invalides', refused[0]['summary'])
        self.assertEqual(len(tools.calls), 5)                                  # the base set only: nothing ran for it

    async def test_a_joined_table_is_computed_and_never_sent_whole(self):
        replies = scripted(turn('data.group', {'by': 'Catégorie', 'value': 'Montant (€)',
                                               'filters': [{'column': 'Date', 'op': '=', 'value': '2026-10'}]}),
                           turn('data.outliers', {'column': 'Montant (€)', 'filters': []}),   # a base one again
                           turn('answer', answer='En octobre, les loisirs arrivent en tête, à cause d’un concert.'))
        orch, tools, ollama, _ = build(FakeDecision('chat.general'), tools=self.tools, ollama=FakeOllama(reply=replies))
        result = await orch.handle_request('Analyse ce fichier', attachments=[{'name': 'depenses.csv', 'text': expenses()}])
        run_id = result['task_id']
        self.assertEqual((result['status'], result['decision']['intent']), ('done', 'data.analyze'))
        self.assertEqual(result['answer'], 'En octobre, les loisirs arrivent en tête, à cause d’un concert.')
        names = [name for name, _params in tools.calls]
        self.assertEqual(names, ['data.describe', 'data.trend', 'data.group', 'data.outliers', 'data.compare', 'data.group'])
        self.assertTrue(all(params['source'] == f'jointe:{run_id}:0' for _name, params in tools.calls))
        self.assertEqual(tools.calls[-1][1]['filters'], [{'column': 'Date', 'op': '=', 'value': '2026-10'}])
        refused = [h for h in result['agent']['history'] if h['status'] == 'refused']
        self.assertIn('fait déjà partie des calculs de base', refused[0]['summary'])
        first = ollama.calls[0]
        self.assertEqual(first['format']['properties']['action']['enum'],
                         ['data.group', 'data.trend', 'data.outliers', 'data.compare', 'answer'])
        sent = ' '.join(str(m) for call in ollama.calls for m in call['messages'])
        self.assertIn('Calculs de base faits par AURORA', sent)
        self.assertNotIn('Achat 9-27', sent)                  # a row of the file, never sent whole
        self.assertNotIn('Date;Catégorie', sent)
        self.assertEqual((result['agent']['max_turns'], result['origin']['kinds'], result['origin']['written_by']),
                         (4, ['mesure'], 'local'))
        view = result['ui']['data']
        self.assertEqual((result['ui']['widget'], view['dataset']['rows'], view['dataset']['source']),
                         ('data', 61, 'jointe'))
        self.assertIn('outliers', [a['id'] for a in view['actions']])
        self.assertNotIn('routine', [a['id'] for a in view['actions']])   # a joined file cannot be read again later
        # « Retenir » sends « Retiens que <fact> » as if typed: it must reach the memory, and only it (no « .csv »
        # asking for another analysis), with a first sentence not cut by an abbreviation (« sept. »)
        from aurora.core.router import DecisionEngine
        fact = compose.data_view([('data.describe', {'ok': True, 'data': {'name': 'depenses.csv', 'rows': 4}}),
                                  ('data.compare', {'ok': True, 'data': {
                                      'value': 'Montant', 'calc': 'calcul', 'diff_pct': 10.0,
                                      'first': {'period': '2026-08', 'label': 'août 2026', 'value': 100},
                                      'second': {'period': '2026-09', 'label': 'sept. 2026', 'value': 110},
                                      'changes': [{'group': 'Restaurant', 'first': 136.0, 'second': 184.0,
                                                   'diff': 48.0, 'diff_pct': 35.29}]}})], 'jointe:1:0')
        memory = next(a for a in fact['actions'] if a['kind'] == 'memory')['memory_text']
        self.assertEqual(memory, '« Restaurant » (Montant) est passé de 136 à 184 entre août 2026 et septembre 2026 '
                                 '(+35,3 %), d’après le fichier « depenses »')
        decided = DecisionEngine(use_semantic=False).decide_fast(f'Retiens que {memory}')
        self.assertEqual((decided.intent, decided.secondary_intents), ('memory.save', []))
        made = artefacts.make_from_run(run_id, orch.run_result)
        page = artefacts.read_version(made['artefact']['id'])['page']
        self.assertEqual(page['kind'], 'analyse')
        self.assertTrue({'stats', 'table', 'line', 'bars', 'list', 'note'} <= {b['type'] for b in page['blocks']})
        html = artefact_html.render(page, page_id='p', version=1, versions=1, created_at='')
        self.assertIn('Calculé par AURORA sur le fichier', html)
        self.assertIn('1 250,00', html)                  # French thousands, non-breaking space

    async def test_a_missing_file_is_said_plainly_and_nothing_runs(self):
        root = Path(tempfile.mkdtemp(dir=self.env.dir)).resolve()
        with mock.patch('aurora.tools.files.allowed_roots', return_value=[{'kind': 'custom', 'label': 'Données',
                                                                           'path': root}]):
            orch, tools, ollama, _ = build(FakeDecision('data.analyze'), tools=self.tools)
            result = await orch.handle_request('Analyse introuvable.csv')
        self.assertIn('Je n’ai pas trouvé « introuvable.csv »', result['answer'])
        self.assertEqual((tools.calls, ollama.calls), ([], []))

    async def test_without_a_model_the_code_writes_the_answer(self):
        orch, tools, ollama, _ = build(FakeDecision('chat.general'), tools=self.tools, ollama=FakeOllama(up=False))
        result = await orch.handle_request('Regarde ça', attachments=[{'name': 'petit.csv', 'text': SMALL}])
        self.assertTrue(result['answer'].startswith('Analyse faite par AURORA (calculs du code, sans texte d’IA)'))
        self.assertIn('A 80', result['answer'])
        self.assertIsNone(result['origin']['written_by'])


if __name__ == '__main__':
    unittest.main()
