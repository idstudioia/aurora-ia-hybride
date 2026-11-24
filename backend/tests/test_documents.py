"""E4: joined PDF and Word files become text (tools/documents.py, POST /api/attachments/extract).

Files are built in memory: a .docx is a zip of XML, a PDF is written by hand (or by pypdf for the protected one).
"""
import asyncio
import base64
import io
import os
import re
import struct
import sys
import tempfile
import unittest
import zipfile
from unittest import mock
from xml.sax.saxutils import escape

import httpx

from aurora.core.planner import Planner
from aurora.providers.base import ProviderError
from aurora.providers.ollama import OllamaProvider
from aurora.tools import documents, vision
from tests.doubles_core import FakeDecision, FakeOllama, build
from tests.test_api import NO_TOKEN, ApiTestCase
from tests.test_orchestrator import CoreTestCase
from tests.test_vision import FakeOllama as FakeVisionOllama

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def make_docx(paragraphs, rows=None, doctype=False, raw_paragraph='') -> bytes:
    body = ''.join(f'<w:p><w:r><w:t xml:space="preserve">{escape(p)}</w:t></w:r></w:p>' for p in paragraphs)
    body += raw_paragraph
    if rows:
        body += '<w:tbl>' + ''.join('<w:tr>' + ''.join(f'<w:tc><w:p><w:r><w:t>{escape(c)}</w:t></w:r></w:p></w:tc>'
                                                        for c in row) + '</w:tr>' for row in rows) + '</w:tbl>'
    xml = ('<?xml version="1.0" encoding="UTF-8"?>' + ('<!DOCTYPE d [<!ENTITY a "b">]>' if doctype else '')
           + f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
                                          'package/2006/content-types"/>')
        z.writestr('word/document.xml', xml)
    return buf.getvalue()


def make_pdf(pages) -> bytes:
    """A real PDF, one line of Helvetica text per page (xref offsets computed)."""
    n = len(pages)
    page_ids = [4 + 2 * i for i in range(n)]
    objs = [b'<< /Type /Catalog /Pages 2 0 R >>',
            ('<< /Type /Pages /Kids [' + ' '.join(f'{p} 0 R' for p in page_ids) + f'] /Count {n} >>').encode(),
            b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    for i, text in enumerate(pages):
        content = f'BT /F1 12 Tf 72 720 Td ({text}) Tj ET'.encode('latin-1')
        objs.append(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> '
                    f'/Contents {page_ids[i] + 1} 0 R >>'.encode())
        objs.append(b'<< /Length ' + str(len(content)).encode() + b' >>\nstream\n' + content + b'\nendstream')
    out = bytearray(b'%PDF-1.4\n')
    offsets = []
    for number, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f'{number} 0 obj\n'.encode() + body + b'\nendobj\n'
    xref = len(out)
    out += f'xref\n0 {len(objs) + 1}\n0000000000 65535 f \n'.encode()
    for offset in offsets:
        out += f'{offset:010d} 00000 n \n'.encode()
    out += f'trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()
    return bytes(out)


def pypdf_bytes(build_writer) -> bytes:
    from pypdf import PdfWriter
    writer = PdfWriter()
    build_writer(writer)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


class WordTests(unittest.TestCase):
    def test_paragraphs_tabs_and_tables(self):
        tab = '<w:p><w:r><w:t>Total</w:t><w:tab/><w:t>1 100 €</w:t></w:r></w:p>'
        doc = documents.extract('budget.docx', make_docx(['Budget du projet Mars', 'Premier paragraphe : où ça va ?'],
                                                         rows=[['Poste', 'Montant'], ['Loyer', '800 €']],
                                                         raw_paragraph=tab))
        self.assertEqual(doc['kind'], 'docx')
        self.assertEqual(doc['text'], 'Budget du projet Mars\n\nPremier paragraphe : où ça va ?\n\nTotal\t1 100 €\n\n'
                                      'Poste | Montant\n\nLoyer | 800 €')
        self.assertFalse(doc['truncated'])
        self.assertEqual(doc['notes'], [])

    def test_refusals_are_explained(self):
        for name, content, words in (
                ('vieux.doc', b'\xd0\xcf\x11\xe0', 'Ancien format Word'),
                ('faux.docx', b'pas un zip', 'pas un .docx valide'),
                ('piege.docx', make_docx(['x'], doctype=True), 'DOCTYPE'),
                ('image.png', b'png', 'seuls les PDF et les fichiers Word'),
                ('vide.docx', b'', 'Fichier vide'),
                ('blanc.docx', make_docx([]), 'pas de texte')):
            with self.assertRaises(ValueError, msg=name) as caught:
                documents.extract(name, content)
            self.assertIn(words, str(caught.exception), name)
        with mock.patch.object(documents, 'MAX_BYTES', 100), self.assertRaises(ValueError) as caught:
            documents.extract('gros.docx', make_docx(['x' * 500]))
        self.assertIn('trop volumineux', str(caught.exception))

    def test_a_long_text_is_cut_and_the_cut_is_said(self):
        doc = documents.extract('long.docx', make_docx(['mot ' * 5000] * 4))   # 80 000 characters
        self.assertTrue(doc['truncated'])
        self.assertLessEqual(len(doc['text']), 60000)
        self.assertGreater(doc['total_chars'], 60000)
        self.assertIn('Texte coupé à 60', doc['notes'][0])


class PdfTests(unittest.TestCase):
    def test_pages_are_read_and_marked(self):
        doc = documents.extract('rapport.pdf', make_pdf(['Budget du projet Mars : 1200 euros', 'Conclusion du rapport']))
        self.assertEqual((doc['kind'], doc['pages'], doc['pages_read']), ('pdf', 2, 2))
        self.assertIn('[Page 1]\nBudget du projet Mars : 1200 euros', doc['text'])
        self.assertIn('[Page 2]\nConclusion du rapport', doc['text'])

    def test_a_short_pdf_is_read_and_a_mostly_empty_one_warns(self):
        # 29/10/2026: « Essai de lecture PDF » (17 letters) was refused as a scan on the real service
        doc = documents.extract('court.pdf', make_pdf(['Essai de lecture PDF']))
        self.assertEqual((doc['text'], doc['notes']), ('[Page 1]\nEssai de lecture PDF', []))
        sparse = pypdf_bytes(lambda w: [w.add_page(p) for p in __import__('pypdf').PdfReader(
            io.BytesIO(make_pdf(['12']))).pages] + [w.add_blank_page(612, 792) for _ in range(3)])
        doc = documents.extract('scan-partiel.pdf', sparse)
        self.assertIn('peut-être scannées', doc['notes'][0])

    def test_scanned_protected_and_damaged_pdfs_say_why(self):
        blank = pypdf_bytes(lambda w: w.add_blank_page(612, 792))
        with self.assertRaises(documents.NoText) as caught:   # 23/11/2026: its pages can be read as images
            documents.extract('scan.pdf', blank)
        self.assertIn('pas de texte lisible', str(caught.exception))
        self.assertEqual(caught.exception.pages, 1)

        def protect(writer):
            from pypdf import PdfReader
            for page in PdfReader(io.BytesIO(make_pdf(['Document secret du projet']))).pages:
                writer.add_page(page)
            writer.encrypt(user_password='secret', algorithm='RC4-128')
        with self.assertRaises(ValueError) as caught:
            documents.extract('secret.pdf', pypdf_bytes(protect))
        self.assertIn('mot de passe', str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            documents.extract('abime.pdf', b'%PDF-1.4\nrien de lisible ici')
        self.assertIn('illisible', str(caught.exception))

    def test_only_the_first_pages_of_a_huge_pdf(self):
        with mock.patch.object(documents, 'MAX_PAGES', 2):
            doc = documents.extract('gros.pdf', make_pdf(['Page un du rapport', 'Page deux du rapport',
                                                          'Page trois du rapport']))
        self.assertEqual((doc['pages'], doc['pages_read']), (3, 2))
        self.assertNotIn('Page trois', doc['text'])
        self.assertIn('Seules les 2 premières pages sur 3 ont été lues.', doc['notes'])


class ExtractRouteTests(ApiTestCase):
    def test_route(self):
        url = '/api/attachments/extract?name=budget.docx'
        res = self.client.post(url, content=make_docx(['Budget du projet Mars']),
                               headers={'Content-Type': 'application/octet-stream'})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()['text'], 'Budget du projet Mars')
        bad = self.client.post('/api/attachments/extract?name=vieux.doc', content=b'\xd0\xcf\x11\xe0',
                               headers={'Content-Type': 'application/octet-stream'})
        self.assertEqual(bad.status_code, 422)
        self.assertIn('Ancien format Word', bad.json()['detail'])
        with mock.patch.object(documents, 'MAX_BYTES', 10):
            big = self.client.post(url, content=b'x' * 50, headers={'Content-Type': 'application/octet-stream'})
        self.assertEqual(big.status_code, 413)
        self.assertEqual(self.client.post(url, content=b'x', headers=NO_TOKEN).status_code, 401)


# ---------------------------------------------------------------- a PDF made of images, read page by page (23/11/2026)
# ID Studio IA's article printed by « Microsoft: Print To PDF » had no text at all and was refused. No real model is called
# here (Ollama is the fake one of test_vision.py); the pages are really drawn by Windows in PageImageTests only.

BLANK_PDF = pypdf_bytes(lambda w: [w.add_blank_page(612, 792) for _ in range(3)])
JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 32
OCTETS = {'Content-Type': 'application/octet-stream'}


@unittest.skipUnless(sys.platform == 'win32', 'les pages ne sont dessinées que par le lecteur PDF de Windows')
class PageImageTests(unittest.TestCase):
    def test_windows_draws_the_page_asked_and_counts_the_pages(self):
        image, pages = documents.page_image(make_pdf(['Budget du projet Mars', 'Conclusion du rapport']), 2, 400)
        self.assertEqual(pages, 2)
        self.assertEqual(vision.image_kind(image), 'jpeg')
        self.assertGreater(len(image), 1000)                       # a real picture, not an empty file
        # the longest side has the pixels asked for, whatever the display scale of the screen (SOF0: height, width);
        # Windows counts in whole screen points, so a pixel or two may differ
        at = image.index(b'\xff\xc0')
        height, width = struct.unpack('>HH', image[at + 5:at + 9])
        self.assertAlmostEqual(max(width, height), 400, delta=3)
        self.assertLess(width, height)                             # a portrait page stays a portrait

    def test_a_page_that_does_not_exist_and_a_damaged_pdf_say_why(self):
        with self.assertRaises(documents.PageError) as caught:
            documents.page_image(make_pdf(['Une seule page']), 3)
        self.assertIn('Ce PDF a 1 page(s) : pas de page 3', str(caught.exception))
        with self.assertLogs('aurora.tools.documents', level='WARNING'), self.assertRaises(documents.PageError) as caught:
            documents.page_image(b'%PDF-1.4\nrien de lisible ici', 1)
        self.assertIn('protégé par un mot de passe, ou abîmé', str(caught.exception))

    def test_nothing_is_left_in_the_temporary_folder(self):
        real = tempfile.mkdtemp   # documents.tempfile is this same module: keep the real one before replacing it
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(documents.tempfile, 'mkdtemp', side_effect=lambda prefix: real(prefix=prefix, dir=tmp)) as made:
            documents.page_image(make_pdf(['Une page']), 1, 300)
            made.assert_called_once_with(prefix='aurora-pdf-')
            self.assertEqual(os.listdir(tmp), [])


class PageReadingTests(unittest.TestCase):
    def test_nothing_is_drawn_outside_windows_or_for_an_empty_file(self):
        with mock.patch.object(documents.sys, 'platform', 'linux'):
            self.assertIn('que sous Windows', documents.page_reader())
            with self.assertRaises(documents.PageError) as caught:
                documents.page_image(BLANK_PDF, 1)
        self.assertIn('que sous Windows', str(caught.exception))
        with mock.patch.object(documents, 'page_reader', return_value=''), \
                mock.patch.object(documents.subprocess, 'run') as run, self.assertRaises(documents.PageError):
            documents.page_image(b'', 1)
        run.assert_not_called()

    def test_only_checked_numbers_and_aurora_s_own_file_reach_the_script(self):
        done = mock.Mock(returncode=0, stdout=b'{"ok":false,"reason":"page","pages":3}\n', stderr=b'')
        with mock.patch.object(documents, 'page_reader', return_value=''), \
                mock.patch.object(documents.subprocess, 'run', return_value=done) as run, \
                self.assertRaises(documents.PageError):
            documents.page_image(BLANK_PDF, '7', 99999)
        command = run.call_args.args[0]
        self.assertEqual(command[0], str(documents._powershell()))
        self.assertTrue(command[0].lower().endswith(r'system32\windowspowershell\v1.0\powershell.exe'))
        self.assertEqual(command[command.index('-File') + 1], str(documents._PAGE_SCRIPT))
        self.assertTrue(documents._PAGE_SCRIPT.is_file())
        self.assertEqual((command[command.index('-Page') + 1], command[command.index('-Side') + 1]), ('7', '2400'))
        self.assertTrue(command[command.index('-Pdf') + 1].endswith('document.pdf'))
        self.assertIs(run.call_args.kwargs['stdin'], documents.subprocess.DEVNULL)

    def test_the_page_is_copied_by_the_vision_model_in_its_own_language(self):
        fake = FakeVisionOllama(['ornith-1.5:9b'], reply='ARTICLE DE RECHERCHE\n\nCe qui se mesure ne se génère pas')
        with mock.patch('aurora.providers.ollama.ollama', OllamaProvider(transport=httpx.MockTransport(fake))), \
                mock.patch.dict(os.environ, {'AURORA_OLLAMA_URL': 'http://ollama.test'}), \
                mock.patch.object(documents, 'page_image', return_value=(JPEG, 13)) as drawn:
            read = asyncio.run(documents.read_page(BLANK_PDF, 4))
        drawn.assert_called_once_with(BLANK_PDF, 4)
        self.assertEqual((read['page'], read['pages'], read['model'], read['truncated']), (4, 13, 'ornith-1.5:9b', False))
        self.assertEqual(read['text'], 'ARTICLE DE RECHERCHE\n\nCe qui se mesure ne se génère pas')
        body = fake.chats[0]
        asked = body['messages'][-1]['content']
        self.assertIn('Recopie tout le texte de cette page, mot pour mot', asked)
        self.assertIn('ne traduis pas', asked)
        self.assertIn('[illisible]', asked)
        self.assertNotIn('Réponds en français', asked)             # an English page stays in English
        self.assertEqual(base64.b64decode(body['messages'][-1]['images'][0]), JPEG)
        self.assertEqual(body['options']['num_predict'], documents.PAGE_TOKENS)

    def test_an_image_description_keeps_its_rules_and_its_length(self):
        fake = FakeVisionOllama(['ornith-1.5:9b'])
        with mock.patch('aurora.providers.ollama.ollama', OllamaProvider(transport=httpx.MockTransport(fake))), \
                mock.patch.dict(os.environ, {'AURORA_OLLAMA_URL': 'http://ollama.test'}):
            asyncio.run(vision.describe(JPEG))
        self.assertIn('Réponds en français', fake.chats[0]['messages'][-1]['content'])
        self.assertEqual(fake.chats[0]['options']['num_predict'], 1200)


class PageRouteTests(ApiTestCase):
    READY = {'available': True, 'model': 'ornith-1.5:9b', 'reason': ''}
    READ = {'page': 2, 'pages': 3, 'text': 'Texte de la page deux.', 'model': 'ornith-1.5:9b', 'seconds': 24.1,
            'processor': 'carte graphique', 'truncated': False}

    def patch(self, target, name, **kw):
        p = mock.patch.object(target, name, **kw)
        started = p.start()
        self.addCleanup(p.stop)
        return started

    def test_a_pdf_without_text_is_offered_page_by_page(self):
        self.patch(vision, 'status', new=mock.AsyncMock(return_value=self.READY))
        self.patch(documents, 'page_reader', return_value='')
        res = self.client.post('/api/attachments/extract?name=article.pdf', content=BLANK_PDF, headers=OCTETS)
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()
        self.assertEqual((data['text'], data['pages'], data['kind']), ('', 3, 'pdf'))
        self.assertEqual(data['images'], {'model': 'ornith-1.5:9b', 'max_pages': documents.MAX_VISION_PAGES,
                                          'max_chars': 60000})

    def test_a_pdf_with_text_is_never_sent_to_the_vision_model(self):
        status = self.patch(vision, 'status', new=mock.AsyncMock(return_value=self.READY))
        res = self.client.post('/api/attachments/extract?name=rapport.pdf', content=make_pdf(['Budget du projet Mars']),
                               headers=OCTETS)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertNotIn('images', res.json())
        status.assert_not_awaited()

    def test_without_a_vision_model_or_outside_windows_the_refusal_says_why(self):
        self.patch(documents, 'page_reader', return_value='')
        self.patch(vision, 'status', new=mock.AsyncMock(return_value={
            'available': False, 'model': '', 'reason': 'aucun modèle de vision installé dans Ollama'}))
        res = self.client.post('/api/attachments/extract?name=article.pdf', content=BLANK_PDF, headers=OCTETS)
        self.assertEqual(res.status_code, 422)
        self.assertIn('pas de texte lisible', res.json()['detail'])
        self.assertIn('aucun modèle de vision installé', res.json()['detail'])
        self.patch(vision, 'status', new=mock.AsyncMock(return_value=self.READY))
        self.patch(documents, 'page_reader', return_value='les pages d’un PDF ne sont dessinées en images que sous Windows')
        res = self.client.post('/api/attachments/extract?name=article.pdf', content=BLANK_PDF, headers=OCTETS)
        self.assertEqual(res.status_code, 422)
        self.assertIn('que sous Windows', res.json()['detail'])

    def test_one_page_is_read(self):
        read = self.patch(documents, 'read_page', new=mock.AsyncMock(return_value=self.READ))
        res = self.client.post('/api/attachments/page?page=2', content=BLANK_PDF, headers=OCTETS)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json(), self.READ)
        read.assert_awaited_once_with(BLANK_PDF, 2)

    def test_what_cannot_be_drawn_or_read_is_said(self):
        for failure in (documents.PageError('Ce PDF a 3 page(s) : pas de page 9.'),
                        vision.VisionError('Ollama local est arrêté : aucune image ne peut être lue.')):
            self.patch(documents, 'read_page', new=mock.AsyncMock(side_effect=failure))
            res = self.client.post('/api/attachments/page?page=9', content=BLANK_PDF, headers=OCTETS)
            self.assertEqual(res.status_code, 422)
            self.assertEqual(res.json()['detail'], str(failure))

    def test_the_page_number_the_size_and_the_token_are_checked(self):
        read = self.patch(documents, 'read_page', new=mock.AsyncMock(return_value=self.READ))
        for query in ('', '?page=0', '?page=deux'):
            self.assertEqual(self.client.post(f'/api/attachments/page{query}', content=BLANK_PDF, headers=OCTETS).status_code,
                             422, query)
        with mock.patch.object(documents, 'MAX_BYTES', 10):
            big = self.client.post('/api/attachments/page?page=1', content=BLANK_PDF, headers=OCTETS)
        self.assertEqual(big.status_code, 413)
        self.assertEqual(self.client.post('/api/attachments/page?page=1', content=BLANK_PDF,
                                          headers={**OCTETS, **NO_TOKEN}).status_code, 401)
        read.assert_not_awaited()


def long_text(paragraphs: int = 500) -> str:
    """About 32 000 characters in numbered paragraphs: three parts of 12 000 at most."""
    return ''.join(f'Paragraphe {n} du rapport du projet Mars, ligne de budget {n}.\n\n' for n in range(paragraphs))


def reader(fail: tuple[int, ...] = (), seen: list | None = None, final: str = 'Réponse finale sur le budget.'):
    """A local model that takes notes on each part it is given (it fails on the parts of `fail`), then answers
    (`final`, 22/11/2026: tests/test_sources_document.py gives an answer that cites the document)."""
    def reply(messages):
        content = messages[0]['content']
        if seen is not None:
            seen.append(content)
        part = re.search(r'— partie (\d+) sur (\d+)', content)
        if part and int(part.group(1)) in fail:
            raise ProviderError('unavailable', 'Ollama ne répond pas.', provider='ollama')
        return f'Notes de la partie {part.group(1)} : budget relevé.' if part else final
    return reply


class PartsTests(unittest.TestCase):
    def test_nothing_is_lost_and_no_part_is_too_long(self):
        text = long_text()
        pieces = documents.parts(text, 12000)
        self.assertEqual(''.join(pieces), text)
        self.assertEqual(len(pieces), 3)
        self.assertTrue(all(0 < len(p) <= 12000 for p in pieces))
        self.assertTrue(all(p.endswith('\n\n') for p in pieces[:-1]))      # cut at the end of a paragraph
        self.assertEqual(documents.parts('court', 12000), ['court'])
        self.assertEqual(documents.parts('', 12000), [''])

    def test_a_text_without_any_break_is_cut_at_the_size(self):
        pieces = documents.parts('x' * 2500, 1000)
        self.assertEqual([len(p) for p in pieces], [1000, 1000, 500])
        sentences = documents.parts('Une phrase. ' * 200, 1000)             # no line break: the end of a sentence
        self.assertEqual(''.join(sentences), 'Une phrase. ' * 200)
        self.assertTrue(all(p.endswith('. ') and len(p) <= 1000 for p in sentences))


class LongDocumentTests(CoreTestCase):
    async def test_a_long_document_is_read_whole_part_after_part(self):
        seen: list[str] = []
        orch, _, ollama, _ = build(FakeDecision('chat.general', method='default'), ollama=FakeOllama(reply=reader(seen=seen)))
        text = long_text()
        result = await orch.handle_request('Quel est le budget ?', attachments=[{'name': 'rapport.pdf', 'text': text}])
        self.assertEqual(result['decision']['intent'], 'doc.analyze')
        pieces = documents.parts(text, 12000)
        self.assertEqual(len(ollama.calls), len(pieces) + 1)                # one call per part, then the answer
        for rank, (piece, call) in enumerate(zip(pieces, ollama.calls), 1):
            content = call['messages'][0]['content']
            self.assertIn(f'rapport.pdf — partie {rank} sur 3', content)
            self.assertIn('Quel est le budget ?', content)
            first, last = piece.split('\n\n')[0], piece.rstrip().split('\n\n')[-1]
            self.assertIn(first, content)                                   # the part, from its first paragraph…
            self.assertIn(last, content)                                    # … to its last one
            self.assertEqual(call['max_tokens'], 300)
        final = ollama.calls[-1]['messages'][0]['content']
        self.assertIn('lu en 3 parties ; voici les notes prises sur chaque partie', final)
        for rank in (1, 2, 3):
            self.assertIn(f'Partie {rank} sur 3 :\nNotes de la partie {rank}', final)
        self.assertNotIn('premiers caractères', final)
        self.assertNotIn('Paragraphe 5 du rapport', final)                  # the notes, not the text again
        self.assertTrue(result['answer'].startswith('Réponse finale sur le budget.'))
        self.assertIn('l’IA locale l’a lu en entier, en 3 parties.', result['answer'])
        titles = [s['title'] for s in result['steps']]
        self.assertIn('Lecture de « rapport.pdf » : partie 2 sur 3 — IA locale', titles)

    async def test_a_part_that_could_not_be_read_is_said(self):
        orch, _, ollama, _ = build(FakeDecision('chat.general', method='default'), ollama=FakeOllama(reply=reader(fail=(2,))))
        result = await orch.handle_request('', attachments=[{'name': 'rapport.pdf', 'text': long_text()}])
        final = ollama.calls[-1]['messages'][0]['content']
        self.assertIn('les parties 2 n’ont pas pu être lues : dis-le', final)
        self.assertIn('Notes de la partie 1', final)
        self.assertNotIn('Notes de la partie 2', final)
        self.assertIn('### Demande de l’utilisateur\nAnalyse ce document.', ollama.calls[0]['messages'][0]['content'])   # none typed
        self.assertTrue(result['answer'].startswith('Réponse finale'))      # a note is never taken for the answer
        self.assertIn('mais 1 partie sur 3 n’a pas pu être lue.', result['answer'])

    async def test_without_any_note_the_reading_of_before_remains(self):
        orch, _, ollama, _ = build(FakeDecision('chat.general', method='default'), ollama=FakeOllama(reply=reader(fail=(1, 2, 3))))
        result = await orch.handle_request('', attachments=[{'name': 'rapport.pdf', 'text': long_text()}])
        final = ollama.calls[-1]['messages'][0]['content']
        self.assertIn('long : seuls les 12000 premiers caractères', final)
        self.assertIn('l’IA n’en a lu que le début', result['answer'])

    async def test_a_short_document_is_read_at_once(self):
        orch, _, ollama, _ = build(FakeDecision('chat.general', method='default'))
        short = await orch.handle_request('', attachments=[{'name': 'court.txt', 'text': 'Projet Mars : budget.'}])
        self.assertEqual(len(ollama.calls), 1)
        self.assertNotIn('n’en a lu que le début', short['answer'])
        self.assertNotIn('parties', short['answer'])


class ReadingPlanTests(unittest.TestCase):
    def plan(self, engine: str, attachments: list[dict]) -> list[dict]:
        return Planner().build({'intent': 'doc.analyze'}, {'engine': engine, 'needs_consent': engine == 'cloud'},
                               text='résume', settings={}, attachments=attachments)

    def test_with_claude_no_more_of_the_document_leaves_the_pc(self):
        doc = [{'name': 'rapport.pdf', 'text': long_text()}]
        cloud = self.plan('cloud', doc)
        self.assertEqual([s.get('purpose') for s in cloud if s['tool'] == 'llm'], ['doc'])
        local = self.plan('local', doc)
        self.assertEqual([s.get('purpose') for s in local if s['tool'] == 'llm'], ['doc_part'] * 3 + ['doc'])
        self.assertTrue(all(s['stage'] == 'local_ai' and not s['requires_permission'] for s in local if s['tool'] == 'llm'))

    def test_the_parts_of_a_request_have_a_ceiling(self):
        big = long_text(880)                                                # about 56 000 characters: 5 parts
        self.assertEqual(len(documents.parts(big, 12000)), 5)
        steps = self.plan('local', [{'name': f'doc{n}.pdf', 'text': big} for n in range(3)])
        read = [s['params'] for s in steps if s.get('purpose') == 'doc_part']
        self.assertEqual(len(read), 10)                                     # the third document would make 15: over 12
        self.assertEqual({p['doc'] for p in read}, {0, 1})
        self.assertEqual([p['part'] for p in read if p['doc'] == 1], [1, 2, 3, 4, 5])


if __name__ == '__main__':
    unittest.main()
