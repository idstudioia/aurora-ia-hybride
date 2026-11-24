"""Reading the first pages a search found (28/10/2026), with Athéna's safe reader and its tests.

Ported cases: tests/agent-web.test.ts (no disk, no private network, no script in the text) and
tests/searxng.test.ts (relevant passage, prices cut into pieces, scrambled pages). Added: the connection
pinned to the checked address, every redirect checked again, and the text SearXNG already brings
(answers, Wikipedia infobox), since Wikipedia refuses AURORA's Python client (403, measured on 28/10).
No network: HTTP through httpx.MockTransport, DNS through a patched ``webpage.resolve``.
"""
import asyncio
import os
import time
import unittest
from unittest import mock

import httpx

from aurora.tools import web, webpage
from aurora.tools.base import ToolError
from tests.test_tools import MockHttp, run, setUpModule, tearDownModule  # noqa: F401  (temporary database)

PUBLIC = '93.184.216.34'


def dns(table: dict[str, list[str]]):
    async def resolve(host: str) -> list[str]:
        if host not in table:
            raise OSError('nom inconnu')
        return table[host]
    return mock.patch.object(webpage, 'resolve', resolve)


def html_page(body: str, status: int = 200, **headers) -> httpx.Response:
    return httpx.Response(status, text=body, headers={'content-type': 'text/html; charset=utf-8', **headers})


class AddressTests(unittest.TestCase):
    """Athéna: « les pages ne peuvent pas ouvrir le disque ni le réseau privé »."""

    def test_disk_private_network_and_odd_addresses_are_refused(self):
        for url in ['http://example.com', 'file:///C:/test', 'https://127.0.0.1', 'https://192.168.1.1',
                    'https://2130706433', 'https://[::1]', 'https://user:pass@example.com', 'https://example.com:3005',
                    'https://localhost/x', 'https://imprimante.local/']:
            with self.subTest(url):
                with self.assertRaises(ToolError):
                    webpage.public_url(url)
        self.assertEqual(webpage.public_url('https://example.com/page#avis'), 'https://example.com/page')

    def test_only_public_addresses(self):
        for ip in ['10.0.0.1', '169.254.169.254', '100.64.0.1', '172.16.0.1', '224.0.0.1', '::ffff:127.0.0.1',
                   '127.0.0.1', '0.0.0.0', '2001:db8::1', 'pas une adresse']:
            with self.subTest(ip):
                self.assertFalse(webpage.public_ip(ip))
        self.assertTrue(webpage.public_ip(PUBLIC))


class TextTests(unittest.TestCase):
    def test_scripts_are_not_in_the_text_given_to_the_model(self):
        self.assertEqual(webpage.page_text('<script>voler()</script><h1>Bonjour &amp; bienvenue</h1>'), 'Bonjour & bienvenue')
        self.assertEqual(webpage.page_text('<span data-x="<ref>privé</ref>">l&#039;article</span>'), "l'article")
        self.assertEqual(webpage.page_text('<style>p{}</style><noscript>x</noscript><p>Texte</p>'), 'Texte')

    def test_a_price_cut_in_tags_stays_a_price(self):
        # Athéna, 05/10/2026: 4 tries out of 6 quoted « 899 € » for a module at 8,99 €. Real amazon.fr HTML:
        amazon = ('<span class="a-price-whole">8<span class="a-price-decimal">,</span></span>'
                  '<span class="a-price-fraction">99</span><span class="a-price-symbol">€</span>')
        self.assertEqual(webpage.page_text(amazon), '8,99 €')
        self.assertEqual(webpage.page_text('<span class="a-price-whole">8</span><span class="a-price-fraction">99</span> €'),
                         '8,99 €')
        self.assertEqual(webpage.page_text('<b>8</b> , <i>99</i> €'), '8,99 €')
        self.assertEqual(webpage.page_text('<p>1 299 €</p>'), '1 299 €')          # thousands: untouched
        self.assertEqual(webpage.page_text('<p>Prix : 6,99 € TTC</p>'), 'Prix : 6,99 € TTC')

    def test_a_scrambled_page_is_refused(self):
        scrambled = 'Marque : TOOGOO ' + '\ufffd\ufffdt\x03j\ufffd@ E\x10\ufffd e6\x11 ' * 200
        self.assertTrue(webpage.unreadable(scrambled))
        self.assertFalse(webpage.unreadable("Module Peltier 12 Vcc TEC1-12706 : refroidissement jusqu'à 67 °C."))
        self.assertTrue(webpage.unreadable(''))

    def test_the_passage_kept_is_the_one_about_the_question(self):
        # Page CONSTRUCTED for the test (Athéna's): shop menus repeated, then the product sheet.
        page = 'Accueil Boutique Compte Panier Livraison offerte dès 50 € ' * 40 + \
               'Module Peltier TEC1-12706 : 12 V, 6 A, 60 W. Prix : 4,90 € TTC. En stock.'
        passage = webpage.relevant_passage(page, 'prix module Peltier TEC1-12706')
        self.assertIn('4,90 €', passage)
        self.assertLessEqual(len(passage), 900)

    def test_main_part_first_and_comments_hidden(self):
        self.assertEqual(webpage.page_text('<nav>menu</nav><main><p>Le fond</p></main>'), 'Le fond')
        self.assertEqual(webpage.page_text('<p>Sans main</p>'), 'Sans main')
        self.assertEqual(webpage.page_text('<!-- caché --><p>vu</p><!-- pas fermé <p>caché</p>'), 'vu')


MENU = '<nav>' + '<a href="/x">Sport Football Coupe du monde 2026 Ligue des champions</a>' * 40 + '</nav>'
FOOT = '<div class="pied">' + '<a href="/y">autre titre</a>' * 120 + '</div>'
BODY_20_MINUTES = [
    'Soyons honnête, quand on a vu son nom couché sur la feuille de match pour palier le forfait de Nuno Mendes, '
    'on n’a pu s’empêcher d’esquisser une moue dubitative.',
    'Le champion du monde 2018 n’est plus qu’une roue de secours au PSG, que ce soit au poste de latéral ou dans l’axe.',
    'L’impact de Lucas sur ce match s’est également vu à sa sortie, sous l’ovation du Parc, à la 78e minute.',
]


def para(text: str, attributes: str = '') -> str:
    return f'<p{attributes}>{text}</p>'


class ArticleTextTests(unittest.TestCase):
    """11/11/2026 (ID Studio IA : « sur Athéna la version payante on a un système de lecture des articles plus abouti »):
    webpage.article_text, Athéna's rule of 21/10/2026 with its cases (tests/actualites.test.ts). Each page takes the
    structure and the sentences of a page read that day, cut down to what matters."""

    def test_the_longest_run_of_paragraphs_without_menus_nor_footer_titles(self):
        page = (MENU + '<h1>PSG - FC Barcelone</h1>' + '\n'.join(para(t) for t in BODY_20_MINUTES) + FOOT
                + para('Retrouvez tous les jours à 17h30 notre sélection de contenus “société”, dans votre boîte mail.'))
        self.assertEqual(webpage.article_text(page), ('\n'.join(BODY_20_MINUTES), False))

    def test_captions_ad_blocker_messages_and_author_cards_are_left_out(self):
        caption = para("Image de la une des médias de la RDC, réalisé avec des captures d'écran de la chaîne YouTube de ACP",
                       ' id="caption-attachment-304220" class="wp-caption-text"')
        text, _ = webpage.article_text(caption + para(
            'En septembre 2026, une enquête a mis en lumière un réseau de faux sites d’information ciblant la RDC.'))
        self.assertTrue(text.startswith('En septembre 2026'))
        body = 'Dans la salle de comparution immédiate, le premier prévenu fait son entrée : Victor B., silhouette longiligne.'
        page = (para(body) + para('Une extension de votre navigateur semble bloquer le chargement du lecteur vidéo. Pour '
                                  'pouvoir regarder ce contenu, vous devez la désactiver.',
                                  ' class="o-em-adblock__content__text"')
                + para('Eusebio Val', ' class=author_name')
                + para("Corresponsal de 'La Vanguardia' en París desde el 2018.", ' class=author_role'))
        self.assertEqual(webpage.article_text(page)[0], body)

    def test_an_article_for_subscribers_is_partial_by_its_class_not_by_a_sentence(self):
        figaro = (para('Le débat tombe à point nommé. Avancé par Sébastien Lecornu après les dégradations commises lors '
                       'des manifestations lycéennes.')
                  + '<div is="fig-paragraph-with-paywall">'
                  + para('Cet article est réservé aux abonnés. Il vous reste 90% à découvrir.',
                         ' class="fig-premium-paywall__infos"') + '</div>')
        text, partial = webpage.article_text(figaro)
        self.assertTrue(partial)
        self.assertNotIn('abonnés', text)
        vanguardia = ('<div class="freemium relative my-3" role=presentation aria-hidden=true><span class=sr-only>'
                      'Contenido solo para suscriptores</span></div>'
                      + para('El imponente monumento “a la gloria de la República” está manchado por múltiples pintadas.'))
        self.assertFalse(webpage.article_text(vanguardia)[1])   # the whole text is in the page

    def test_an_anti_robot_page_gives_no_text(self):
        page = ('<html><head><title>Client Challenge</title></head><body><div>A required part of this site couldn’t '
                'load. This may be due to a browser extension, network issues, or browser settings.</div></body></html>')
        self.assertEqual(webpage.article_text(page), ('', False))
        self.assertEqual(webpage.article_text(''), ('', False))

    def test_cut_by_whole_paragraphs(self):
        one = ('Une phrase de quarante caractères bien comptés. ' * 30).strip()
        text, _ = webpage.article_text('\n'.join(para(one) for _ in range(10)), 6000)
        self.assertLessEqual(len(text), 6000)
        self.assertEqual(len(text.split('\n')), 6001 // (len(one) + 1))
        self.assertTrue(all(line == one for line in text.split('\n')))   # no paragraph cut in its middle

    def test_entities_inline_tags_and_scripts_inside_a_paragraph(self):
        text, _ = webpage.article_text(para('Dans le cadre de son programme gouvernemental, l&rsquo;exécutif entend '
                                            'placer la famille au rang de priorités.'))
        self.assertIn('l’exécutif', text)
        # A letter in its own tag stays in its word; a line break is a space; a script inside is never text.
        text, _ = webpage.article_text(para('<span class="lettrine">L</span>e gouvernement dévoile de nouvelles '
                                            'mesures<br>pour les familles<script>alert(1)</script> dès janvier.'))
        self.assertEqual(text, 'Le gouvernement dévoile de nouvelles mesures pour les familles dès janvier.')
        # A <p> never closed is closed by the next one, as in a browser.
        text, _ = webpage.article_text('<p>Premier paragraphe assez long pour être gardé par la règle.'
                                       '<p>Second paragraphe assez long pour être gardé lui aussi.</p>')
        self.assertEqual(text.split('\n'), ['Premier paragraphe assez long pour être gardé par la règle.',
                                            'Second paragraphe assez long pour être gardé lui aussi.'])

    def test_trapped_pages_stay_fast(self):
        for unit in ('<', '<p ', '<p>', '<p class="', '</p', '<p class=paywall>', '<p><span ', '<br', '<!--',
                     '<p class="' + 'x' * 990 + '">'):
            page = (unit * (webpage.MAX_HTML_CHARS // len(unit) + 1))[:webpage.MAX_HTML_CHARS]
            with self.subTest(unit[:20]):
                started = time.perf_counter()
                webpage.article_text(page)
                self.assertLess(time.perf_counter() - started, 1.0)


class HostilePageTests(unittest.TestCase):
    """Independent security review of 28/10/2026: each proven fault, kept proven-fixed."""

    def test_trapped_pages_stay_fast(self):
        # The first version took ~7,7 h on « < » repeated over 1,5 Mo; Python 3.11.7's html.parser took x8 time
        # per doubled page on « <span » repeated. Each trap here is the full parsed size (300 KB).
        for unit in ('<', '<span ', '<main>', '<!--', '</', '<!', '<a b="', '<?', '<<<<>', '<a "' + 'x' * 998 + '"',
                     '<a b="' + '<' * 990 + '"', '<span class="a-price-fraction">'):
            page = (unit * (webpage.MAX_HTML_CHARS // len(unit) + 1))[:webpage.MAX_HTML_CHARS]
            with self.subTest(unit[:20]):
                started = time.perf_counter()
                webpage.page_text(page)
                self.assertLess(time.perf_counter() - started, 1.0)

    def test_odd_charsets_and_names_end_in_a_tool_error_or_a_text(self):
        for charset in ('pas-un-codec', 'undefined'):
            with self.subTest(charset), dns({'exemple.fr': [PUBLIC]}), \
                    MockHttp(lambda req, c=charset: httpx.Response(200, content='Été'.encode(), headers={
                        'content-type': f'text/html; charset={c}'})):
                self.assertIn('t', run(webpage.read_public('https://exemple.fr/')).body)
        for location in ('https://a..b/', 'https://' + 'x' * 64 + '.fr/'):
            with self.subTest(location), dns({'exemple.fr': [PUBLIC]}), \
                    MockHttp(lambda req, l=location: httpx.Response(302, headers={'location': l})):
                with self.assertRaises(ToolError):
                    run(webpage.read_public('https://exemple.fr/'))

    def test_an_international_name_is_sent_in_ascii(self):
        with dns({'xn--exmple-cua.fr': [PUBLIC]}), MockHttp(lambda req: html_page('ok')) as http:
            run(webpage.read_public('https://exämple.fr/'))
        self.assertEqual(http.calls[0].headers['host'], 'xn--exmple-cua.fr')

    def test_no_compressed_page(self):
        bomb = httpx.Response(200, content=b'\x1f\x8b', headers={'content-type': 'text/html', 'content-encoding': 'gzip'})
        with dns({'exemple.fr': [PUBLIC]}), MockHttp(lambda req: bomb) as http:
            with self.assertRaisesRegex(ToolError, 'compressée'):
                run(webpage.read_public('https://exemple.fr/'))
        self.assertEqual(http.calls[0].headers['accept-encoding'], 'identity')

    def test_the_users_contact_never_goes_to_the_sites_read(self):
        with mock.patch.dict(os.environ, {'AURORA_CONTACT': 'prenom.nom@exemple.fr'}), dns({'exemple.fr': [PUBLIC]}), \
                MockHttp(lambda req: html_page('ok')) as http:
            run(webpage.read_public('https://exemple.fr/'))
        self.assertNotIn('prenom.nom', http.calls[0].headers['user-agent'])

    def test_content_type_is_compared_exactly(self):
        for kind in ('application/octet-stream; x=text/html', 'application/jsonx', 'image/svg+xml'):
            with self.subTest(kind), dns({'exemple.fr': [PUBLIC]}), \
                    MockHttp(lambda req, k=kind: httpx.Response(200, text='x', headers={'content-type': k})):
                with self.assertRaises(ToolError):
                    run(webpage.read_public('https://exemple.fr/'))

    def test_python_3_11_7_ietf_addresses_are_not_public(self):
        for ip in ('192.0.0.8', '192.0.0.100'):   # CVE-2024-4032: is_global said True
            self.assertFalse(webpage.public_ip(ip), ip)

    def test_the_whole_reading_has_a_deadline(self):
        async def slow(host):
            await asyncio.sleep(5)
            return [PUBLIC]
        with mock.patch.object(webpage, 'resolve', slow), MockHttp(lambda req: html_page('ok')):
            started = time.perf_counter()
            with self.assertRaisesRegex(ToolError, 'Délai'):
                run(webpage.read_public('https://exemple.fr/', timeout=0.3))
            self.assertLess(time.perf_counter() - started, 2)


class ReaderTests(unittest.TestCase):
    def test_pinned_to_the_checked_address_with_the_site_name_kept(self):
        with dns({'exemple.fr': [PUBLIC]}), MockHttp(lambda req: html_page('<main>Bonjour</main>')) as http:
            page = run(webpage.read_public('https://exemple.fr/article'))
        call = http.calls[0]
        self.assertEqual((call.url.host, call.headers['host'], call.extensions.get('sni_hostname')),
                         (PUBLIC, 'exemple.fr', 'exemple.fr'))
        self.assertEqual(page.url, 'https://exemple.fr/article')

    def test_a_name_pointing_to_the_local_network_sends_nothing(self):
        for addresses in (['192.168.1.20'], [PUBLIC, '127.0.0.1'], []):
            with self.subTest(addresses), dns({'piege.example': addresses}), \
                    MockHttp(lambda req: html_page('x')) as http:
                with self.assertRaises(ToolError):
                    run(webpage.read_public('https://piege.example/'))
                self.assertEqual(http.calls, [])

    def test_every_redirect_is_checked_again(self):
        def to(location):
            return lambda req: httpx.Response(302, headers={'location': location})
        for location in ('http://exemple.fr/clair', 'https://127.0.0.1/admin', 'https://interne.example/'):
            with self.subTest(location), dns({'exemple.fr': [PUBLIC], 'interne.example': ['10.0.0.5']}), \
                    MockHttp(to(location)) as http:
                with self.assertRaises(ToolError):
                    run(webpage.read_public('https://exemple.fr/'))
                self.assertEqual(len(http.calls), 1)
        with dns({'exemple.fr': [PUBLIC]}), MockHttp(to('/encore')) as http:
            with self.assertRaisesRegex(ToolError, 'redirections'):
                run(webpage.read_public('https://exemple.fr/'))
            self.assertEqual(len(http.calls), webpage.MAX_REDIRECTS + 1)

    def test_only_text_and_not_too_big(self):
        cases = [httpx.Response(200, content=b'\x89PNG', headers={'content-type': 'image/png'}),
                 html_page('x' * (webpage.MAX_BYTES + 10)), html_page('interdit', status=403)]
        for response in cases:
            with self.subTest(response.headers.get('content-type')), dns({'exemple.fr': [PUBLIC]}), \
                    MockHttp(lambda req, r=response: r):
                with self.assertRaises(ToolError):
                    run(webpage.read_public('https://exemple.fr/'))


# Real excerpt of SearXNG's JSON on 28/10/2026 (« Le Comte de Monte-Cristo », 127.0.0.1:8888), shortened.
MONTE_CRISTO = {
    'results': [{'title': 'Le Comte de Monte-Cristo - Wikipédia', 'url': 'https://fr.wikipedia.org/wiki/Le_Comte_de_Monte-Cristo',
                 'content': 'Le Comte de Monte-Cristo est un roman d\'Alexandre Dumas…', 'engines': ['google cse', 'duckduckgo']},
                {'title': 'Le Comte de Monte-Cristo - Film 2024 - AlloCiné', 'url': 'https://www.allocine.fr/film/fichefilm_gen_cfilm=288404.html',
                 'content': 'Victime d\'un complot, le jeune Edmond Dantès…', 'engines': ['duckduckgo']}],
    'answers': [{'url': 'https://fr.wikipedia.org/wiki/Le_Comte_de_Monte-Cristo', 'engine': 'duckduckgo',
                 'answer': "Le Comte de Monte-Cristo est un roman d'Alexandre Dumas, écrit avec la collaboration d'Auguste "
                           "Maquet et dont la publication commence pendant l'été 1844. More at Wikipedia (FR)"}],
    'infoboxes': [],
}


class SearxngTextTests(unittest.TestCase):
    def test_the_text_searxng_brings_fills_the_snippet(self):
        results = web.searxng_results(MONTE_CRISTO['results'], 3, web.searxng_extracts(MONTE_CRISTO))
        self.assertIn('été 1844', results[0]['extract'])
        self.assertNotIn('More at Wikipedia', results[0]['extract'])
        self.assertEqual(results[1]['extract'], 'Victime d\'un complot, le jeune Edmond Dantès…')

    def test_an_infobox_page_missing_from_the_results_comes_first(self):
        payload = {'results': MONTE_CRISTO['results'][1:], 'infoboxes': [
            {'infobox': 'Marie Curie', 'id': 'https://fr.wikipedia.org/wiki/Marie_Curie', 'engine': 'wikipedia',
             'content': 'Marie Curie, née le 7 novembre 1867 à Varsovie et morte le 4 juillet 1934 à Passy.'}]}
        results = web.searxng_results(payload['results'], 3, web.searxng_extracts(payload))
        self.assertEqual((results[0]['title'], results[0]['domain']), ('Marie Curie', 'fr.wikipedia.org'))
        self.assertIn('1867', results[0]['extract'])


class FirstPagesTests(unittest.TestCase):
    ON = {'AURORA_READ_PAGES': 'on'}
    PAGE = ('<nav>Accueil Menu</nav><main><h1>Trous noirs</h1><p>' + 'Introduction générale. ' * 60 +
            'Un trou noir de masse stellaire naît de l’effondrement d’une étoile de plus de 20 masses solaires.</p></main>')

    def items(self):
        return [{'title': 'Trou noir - Wikipédia', 'url': 'https://fr.wikipedia.org/wiki/Trou_noir', 'source': 'searxng',
                 'snippet': 'Un trou noir…', 'extract': 'Un trou noir…'},
                {'title': 'Trous noirs - Imep CNRS', 'url': 'https://www.imep-cnrs.com/trous-noirs/', 'source': 'searxng',
                 'snippet': 'Qu’est-ce qu’un trou noir ?', 'extract': 'Qu’est-ce qu’un trou noir ?'}]

    def test_the_page_is_read_and_its_useful_passage_kept(self):
        items = self.items()
        with mock.patch.dict(os.environ, self.ON), dns({'www.imep-cnrs.com': [PUBLIC]}), \
                MockHttp(lambda req: html_page(self.PAGE)) as http:
            run(web.read_first_pages(items, 'masse étoile effondrement trou noir'))
        self.assertEqual([c.headers['host'] for c in http.calls], ['www.imep-cnrs.com'])   # Wikipedia not contacted
        self.assertTrue(items[1]['read'])
        self.assertIn('20 masses solaires', items[1]['extract'])
        self.assertNotIn('Menu', items[1]['extract'])
        self.assertNotIn('read', items[0])

    def test_a_page_that_fails_keeps_its_snippet(self):
        for handler in (lambda req: html_page('refusé', status=403),
                        lambda req: html_page('\ufffd\x03' * 500)):
            items = self.items()
            with self.subTest(handler), mock.patch.dict(os.environ, self.ON), dns({'www.imep-cnrs.com': [PUBLIC]}), \
                    MockHttp(handler):
                run(web.read_first_pages(items, 'trou noir'))
            self.assertEqual(items[1]['extract'], 'Qu’est-ce qu’un trou noir ?')
            self.assertNotIn('read', items[1])

    def test_off_reads_nothing(self):
        for value in ('off', '0', 'false', 'non'):
            items = self.items()
            with self.subTest(value), mock.patch.dict(os.environ, {'AURORA_READ_PAGES': value}), \
                    MockHttp(lambda req: html_page(self.PAGE)) as http:
                run(web.read_first_pages(items, 'trou noir'))
            self.assertEqual(http.calls, [])

    def test_wikipedia_with_a_final_dot_is_not_read_either(self):
        items = [{'title': 'x', 'url': 'https://fr.wikipedia.org./wiki/x', 'source': 'searxng', 'extract': 'x'}]
        with mock.patch.dict(os.environ, self.ON), dns({'fr.wikipedia.org': [PUBLIC]}), \
                MockHttp(lambda req: html_page(self.PAGE)) as http:
            run(web.read_first_pages(items, 'x'))
        self.assertEqual(http.calls, [])

    def test_an_unexpected_failure_keeps_the_snippet_and_the_search(self):
        def boom(markup, query):
            raise RuntimeError('imprévu')
        items = self.items()
        with mock.patch.dict(os.environ, self.ON), dns({'www.imep-cnrs.com': [PUBLIC]}), \
                mock.patch.object(webpage, 'passage_of', boom), MockHttp(lambda req: html_page(self.PAGE)):
            run(web.read_first_pages(items, 'trou noir'))                   # does not raise
        self.assertEqual(items[1]['extract'], 'Qu’est-ce qu’un trou noir ?')


if __name__ == '__main__':
    unittest.main()
