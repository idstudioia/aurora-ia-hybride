"""Decision engine: normalisation, slots, key sentences, determinism, engine selection.

Run: cd backend && python -m unittest tests.test_router   (no network: the LLM is a test double)
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from aurora.core import router, rules, semantic
from aurora.core.engine_select import EngineChoice, detect_engine_preference, estimate_complexity, select_engine
from aurora.core.intents import INTENTS, PAGES
from aurora.core.normalize import (extract_city, extract_conversion, extract_expression, extract_folder,
                                   extract_globe_order, extract_memory_content, extract_metric, extract_world_news,
                                   extract_older_than_days, extract_orbit, extract_page, extract_process, extract_query, extract_slots, normalize,
                                   split_clauses)
from aurora.core.router import Decision, DecisionEngine

ENGINE = DecisionEngine(use_semantic=False)

_SANDBOX: tempfile.TemporaryDirectory | None = None
_ENV = None


def setUpModule():
    """get_engine() reads settings: never let it open the user's real ~/.aurora database."""
    global _SANDBOX, _ENV
    _SANDBOX = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    sandbox = Path(_SANDBOX.name)
    _ENV = mock.patch.dict(os.environ, {'AURORA_DB_PATH': str(sandbox / 'router_test.db'),
                                        'AURORA_HOME': str(sandbox / 'home'),
                                        'ASTRA_DB_PATH': str(sandbox / 'no_astra.db')})
    _ENV.start()


def tearDownModule():
    _ENV.stop()
    _SANDBOX.cleanup()


def intent_of(text: str) -> str:
    return ENGINE.decide_fast(text).intent


class NormalizeTests(unittest.TestCase):
    def test_case_accents_punctuation(self):
        self.assertEqual(normalize('Quelle quantité de RAM est utilisée ?'), 'quelle quantite de ram est utilisee')
        self.assertEqual(normalize("Qu'est-ce que c'est ?"), 'qu est ce que c est')

    def test_wake_words(self):
        self.assertEqual(normalize('Aurora, quelle heure est-il ?'), 'quelle heure est il')
        self.assertEqual(normalize('OK Astra : quelle heure est-il'), 'quelle heure est il')
        self.assertEqual(normalize('dis Aurora quelle heure est-il'), 'quelle heure est il')
        self.assertEqual(normalize('o rora quelle heure est il'), 'quelle heure est il')
        self.assertEqual(normalize('quelle heure est-il aurora'), 'quelle heure est il')
        self.assertEqual(normalize('Aurora'), '')

    def test_typos_and_voice(self):
        self.assertIn('ram', normalize('combien de rame est utilisée').split())
        self.assertEqual(normalize('mon pc rame'), 'mon pc rame')          # verb "ramer" = it lags
        self.assertEqual(normalize('quel temp fait-il'), 'quel temps fait il')
        self.assertEqual(normalize('mon ordi'), 'mon ordinateur')
        self.assertEqual(normalize('la metéo'), 'la meteo')
        self.assertEqual(normalize('telechargment'), 'telechargements')
        self.assertEqual(normalize('supprimme les fichiers'), 'supprime les fichiers')
        self.assertEqual(normalize('euh quelle heure est il'), 'quelle heure est il')
        self.assertEqual(normalize('une question simple'), 'une question simple')  # no false correction
        self.assertEqual(normalize('les chercheurs'), 'les chercheurs')

    def test_numbers_kept(self):
        self.assertEqual(normalize('Combien font 17 × 23 ?'), 'combien font 17 x 23')
        self.assertIn('3.5', normalize('3,5 litres'))
        self.assertIn('-5', normalize('il fait -5 degrés'))
        self.assertEqual(normalize('17-5'), '17 - 5')

    def test_split_clauses(self):
        self.assertEqual(split_clauses('mon PC est lent, analyse le problème, recherche des solutions et prépare '
                                       'une optimisation'),
                         ['mon PC est lent', 'analyse le problème', 'recherche des solutions',
                          'prépare une optimisation'])

    def test_deterministic(self):
        text = 'Aurora, peux-tu supprimer mes vieux fichiers du bureau ?'
        self.assertEqual(normalize(text), normalize(text))


class SlotTests(unittest.TestCase):
    def test_city(self):
        self.assertEqual(extract_city('Quel temps fait-il à Lyon ?'), 'Lyon')
        self.assertEqual(extract_city('Météo à Saint-Étienne demain'), 'Saint-Étienne')
        self.assertEqual(extract_city('Météo de demain à New York'), 'New York')
        self.assertEqual(extract_city('Il fait combien au Caire ?'), 'Le Caire')
        self.assertEqual(extract_city('meteo marseille'), 'Marseille')
        self.assertEqual(extract_city('quel temps fait il a villeurbanne'), 'Villeurbanne')
        self.assertIsNone(extract_city('Quel temps fait-il dehors ?'))
        self.assertIsNone(extract_city('Quel temps fait-il ?'))

    def test_expression(self):
        self.assertEqual(extract_expression('Combien font 17 × 23 ?'), '17 * 23')
        self.assertEqual(extract_expression('Quelle est la racine carrée de 144 ?'), 'sqrt(144)')
        self.assertEqual(extract_expression('Calcule 2 puissance 10'), '2 ** 10')
        self.assertEqual(extract_expression('12 au carré'), '12 ** 2')
        self.assertEqual(extract_expression('combien font douze fois trois'), '12 * 3')
        self.assertEqual(extract_expression('combien fait 15 % de 80'), '15 / 100 * 80')
        self.assertEqual(extract_expression('combien fait 3,5 x 2'), '3.5 * 2')
        self.assertEqual(extract_expression('calcule (3+4)*2'), '(3 + 4) * 2')
        self.assertEqual(extract_expression('1 000 divisé par 8'), '1000 / 8')
        self.assertIsNone(extract_expression("J'ai 3 enfants"))

    def test_conversion(self):
        expected = {'value': 30.0, 'from_unit': 'mi', 'to_unit': 'km'}
        for text in ('Convertis 30 miles en km', 'Combien de km dans 30 miles ?'):
            conv = extract_conversion(text)
            self.assertEqual({k: conv[k] for k in expected}, expected, text)
        conv = extract_conversion('100 degrés Fahrenheit en Celsius')
        self.assertEqual((conv['value'], conv['from_unit'], conv['to_unit']), (100.0, 'fahrenheit', 'celsius'))
        conv = extract_conversion('combien de grammes dans une livre')
        self.assertEqual((conv['value'], conv['from_unit'], conv['to_unit']), (1.0, 'lb', 'g'))
        self.assertIsNone(extract_conversion('Combien de calories dans une pomme ?'))

    def test_files(self):
        self.assertEqual(extract_folder('Quels vieux fichiers dans Téléchargements ?'), 'downloads')
        self.assertEqual(extract_folder('Fais le ménage sur mon bureau'), 'desktop')
        self.assertEqual(extract_folder('Scanne C:\\Projets\\Photos'), 'C:\\Projets\\Photos')
        self.assertEqual(extract_older_than_days('fichiers de plus de 6 mois'), 180)
        self.assertEqual(extract_older_than_days("plus d'un an"), 365)
        self.assertIsNone(extract_older_than_days('mes anciens fichiers'))
        self.assertEqual(extract_slots('files.cleanup', 'Supprime mes anciens fichiers'),
                         {'folder': None, 'older_than_days': None})

    def test_queries(self):
        self.assertEqual(extract_query('Recherche les dernières découvertes spatiales'),
                         'dernières découvertes spatiales')
        self.assertEqual(extract_query('Cherche sur Internet comment fonctionne un laser'),
                         'comment fonctionne un laser')
        self.assertEqual(extract_query('Retrouve notre projet sur Mars', kind='memory'), 'projet sur Mars')
        self.assertEqual(extract_query("Tu te souviens de ce que je t'ai dit sur mon chat ?", kind='memory'), 'chat')
        # B6 (rapport §4.6): the question words are not part of what is looked up
        self.assertEqual(extract_query('Quel est mon mot de passe Wi-Fi ?', kind='memory'), 'mot de passe Wi-Fi')
        self.assertEqual(extract_query('Quels sont mes projets en cours ?', kind='memory'), 'projets en cours')
        self.assertEqual(extract_query('Quelle est la couleur préférée de ma fille ?', kind='memory'),
                         'couleur préférée de ma fille')

    def test_system_and_simulation(self):
        self.assertEqual(extract_metric('Quelle quantité de RAM est utilisée ?'), 'ram')
        self.assertEqual(extract_metric("Combien d'espace libre sur le disque ?"), 'disk')
        self.assertEqual(extract_metric('Le CPU est à combien ?'), 'cpu')
        self.assertEqual(extract_metric('état du PC'), 'all')
        self.assertEqual(extract_orbit("Simule la trajectoire d'une sonde autour de Mars")['body'], 'mars')
        self.assertEqual(extract_orbit("satellite à 400 km d'altitude")['altitude_km'], 400.0)
        self.assertEqual(extract_orbit('orbite géostationnaire'), {'body': 'terre', 'altitude_km': 35786.0})
        self.assertEqual(extract_process('Ferme Chrome')['name'], 'chrome')
        self.assertEqual(extract_process('Tue le processus 8812')['pid'], 8812)

    def test_page(self):
        self.assertEqual(extract_page('Ouvre la page mémoire'), 'memory')
        self.assertEqual(extract_page('Affiche le globe'), 'world')
        self.assertEqual(extract_page('Va dans les paramètres'), 'settings')
        self.assertEqual(extract_page("Retourne à l'accueil"), 'home')
        self.assertIn(extract_page('Ouvre les tâches'), PAGES)

    def test_globe_order(self):
        # 08/11/2026: what the globe of Monde 3D shows when the user asks for it (lib/types.ts GlobeOrder)
        self.assertEqual(extract_globe_order('Montre-moi Paris sur le globe'), {'lieu': 'paris'})
        self.assertEqual(extract_globe_order('Aurora, affiche les cyclones actifs sur le globe'),
                         {'couche': 'evenements'})
        self.assertEqual(extract_globe_order('Affiche les avions au-dessus de Lyon sur le globe'),
                         {'couche': 'avions', 'lieu': 'lyon'})
        self.assertEqual(extract_globe_order("Montre l'ISS sur le globe"), {'couche': 'satellites', 'satellite': 25544})
        self.assertEqual(extract_globe_order('Affiche le globe'), {})
        slots = extract_slots('ui.navigate', 'Montre-moi Paris sur le globe')
        self.assertEqual(slots, {'page': 'world', 'globe': {'lieu': 'paris'}})
        self.assertEqual(extract_slots('ui.navigate', 'Ouvre la mémoire'), {'page': 'memory'})
        # world news (news.world) unless the request is about science (news.latest's 5 feeds)
        self.assertEqual(extract_slots('news.latest', 'Quelles sont les actualités en Ukraine ?'),
                         {'topic': None, 'monde': {'pays': 'Ukraine'}})
        self.assertEqual(extract_world_news('Actualités économiques au Japon'), {'theme': 'economie', 'pays': 'Japon'})
        self.assertEqual(extract_world_news('Donne-moi les infos du jour'), {})
        self.assertIsNone(extract_world_news("Quoi de neuf dans l'actualité scientifique ?"))
        self.assertEqual(extract_slots('news.latest', "Les dernières nouvelles de l'espace"), {'topic': 'espace'})
        # without « globe », a live layer still opens Monde 3D
        self.assertEqual(extract_slots('ui.navigate', "Où est l'ISS ?"),
                         {'page': 'world', 'globe': {'couche': 'satellites', 'satellite': 25544}})
        self.assertEqual(extract_slots('ui.navigate', 'Montre les avions au-dessus de Lyon'),
                         {'page': 'world', 'globe': {'couche': 'avions', 'lieu': 'lyon'}})

    def test_globe_place_said_freely(self):
        """08/11/2026 (runs 44-45 of the real AURORA, by voice): « Petra sur le globe » and « où est la Jordanie sur
        le globe » opened the globe without going to the place; « montre-moi où est la Jordanie sur le globe » read
        the place « ou est la jordanie »."""
        cases = {'Petra sur le globe': 'petra',
                 'Où est la Jordanie sur le globe ?': 'jordanie',
                 'Aurora, montre-moi où est la Jordanie sur le globe ?': 'jordanie',
                 'Montrer sur le globe où est la Jordanie': 'jordanie',
                 'Montre sur le globe la Jordanie, s’il te plaît': 'jordanie',
                 'rentrer sur le globe ou la Jordanie ou pas': 'jordanie',   # as heard by voice (run 45)
                 'Où se trouve Pétra sur la carte du monde ?': 'petra',
                 'Zoome sur la Jordanie': 'jordanie', 'Situe Amman': 'amman', 'Va en Jordanie': 'jordanie',
                 'Montre-moi le Japon': 'japon', "Montre-moi la Côte d'Ivoire": 'cote d ivoire',
                 # Runs 48-49 (after the first fix): a World Heritage site is a place too, without « globe ».
                 'Situe Pétra': 'petra', 'Où est Pétra ?': 'petra', 'mondial où est Petra': 'petra',
                 'Où se trouve Venise ?': 'venise', 'Où est la Jordanie ?': 'jordanie'}
        for text, place in cases.items():
            with self.subTest(text):
                self.assertEqual(extract_slots('ui.navigate', text), {'page': 'world', 'globe': {'lieu': place}})
                if not text.startswith('rentrer'):   # a sentence heard wrong is routed by meaning (semantic)
                    self.assertEqual(intent_of(text), 'ui.navigate')
        # Without « globe », only a place the globe knows (country, capital, World Heritage site) is a place.
        for text in ("Zoome sur l'image", 'Situe la Révolution française', 'Où est mon fichier ?', 'Où est le problème ?',
                     'Où est Charlie ?', 'Où est la gare ?', 'Montre-moi les actualités du Japon', 'Va dormir',
                     'Montre-moi mes fichiers', 'tu Pétra'):
            with self.subTest(text):
                self.assertNotEqual(ENGINE.decide_fast(text).slots.get('page'), 'world')
        # A layer, a question, a text to write or one's own things are not places.
        self.assertEqual(intent_of('Écris un poème sur le globe'), 'chat.general')
        self.assertEqual(intent_of('Quoi de neuf sur le globe ?'), 'news.latest')
        self.assertEqual(intent_of('Les séismes sur le globe'), 'quakes.recent')
        # The answer names a World Heritage site and its country from the globe's list, never from a model.
        from aurora.core import compose
        self.assertEqual(compose.navigate('world', {'lieu': 'petra'}),
                         'J’ouvre le globe, centré sur Pétra (Jordanie), un lieu du patrimoine mondial de l’UNESCO.')
        self.assertEqual(compose.navigate('world', {'lieu': 'jordanie'}),
                         'J’ouvre le globe, centré sur « Jordanie » s’il est dans ses pays, ses villes ou ses lieux du '
                         'patrimoine mondial.')
        for text in ('Ma maison sur le globe', 'Montre-moi où est l’ISS', 'Affiche les cyclones sur le globe',
                     'Va sur le globe', 'Montre-moi le globe', 'Affiche la carte du monde'):
            with self.subTest(text):
                self.assertNotIn('lieu', extract_globe_order(text))

    def test_place_photos(self):
        """08/11/2026 (the owner: « histoire du Maroc », des images du sujet): the photographed World Heritage sites of
        the place a question names, from the globe's list; nothing for a sentence that names no place."""
        from aurora.core.normalize import PHOTOS_MAX, place_photos
        maroc = place_photos("Raconte-moi l'histoire du Maroc")
        self.assertEqual((maroc['titre'], maroc['pays']), ('Maroc', 'Maroc'))
        self.assertTrue(1 <= len(maroc['photos']) <= PHOTOS_MAX)
        for photo in maroc['photos']:
            self.assertEqual(photo['pays'], 'Maroc')
            self.assertTrue(photo['img'] and isinstance(photo['la'], (int, float)) and isinstance(photo['lo'], (int, float)))
        # A site named by its name comes first, then the best known of its country.
        petra = place_photos('Qui a construit Pétra ?')
        self.assertEqual((petra['titre'], petra['pays'], petra['photos'][0]['nom']), ('Pétra', 'Jordanie', 'Pétra'))
        self.assertEqual(place_photos('Le climat au Chili')['pays'], 'Chili')
        self.assertEqual(place_photos("Je pars à l'île Maurice")['pays'], 'Maurice')
        # Capitals and site names that are common words, first names, or no place at all.
        for text in ('Combien de sucre dans un gâteau ?', 'Recette du chili con carne', "La médina, c'est quoi ?",
                     'Parle-moi de Dominique, mon collègue', 'Explique-moi la photosynthèse', 'Comment va mon PC ?',
                     'Mesure un acre de terrain', ''):
            with self.subTest(text):
                self.assertIsNone(place_photos(text))

    def test_place_photos_by_city_or_nickname(self):
        """11/11/2026 (ID Studio IA : « les images des sujets ne fonctionnent pas tout le temps »): a site is found by the city
        or the monument in its name, by a few nicknames, and a capital gives the sites of its country; a word that is
        also a thing, a verb or a first name finds nothing."""
        from aurora.core.normalize import place_photos
        for text, titre, first in (
                ('Parle-moi de Marrakech', 'Marrakech', 'Médina de Marrakech'),
                ('parle moi de paris', 'Paris', 'rives de la Seine à Paris'),
                ("C'est quoi la tour Eiffel ?", 'Paris', 'rives de la Seine à Paris'),
                ("Parle-moi d'Avignon", 'Avignon', 'Centre historique d’Avignon'),
                ('Parle-moi du palais des papes', 'Avignon', 'Centre historique d’Avignon'),
                ('Parle-moi du Mont-Saint-Michel', 'Mont-Saint-Michel', 'Mont-Saint-Michel et sa baie'),
                ('Parle-moi du Machu Picchu', 'Machu Picchu', 'sanctuaire historique de Machu Picchu'),
                ('Parle-moi des pyramides de Gizeh', 'Guizeh', None), ('Parle-moi du Colisée', 'Rome', None),
                ('parle moi de fes', 'Fès', 'médina de Fès'), ("Parle-moi d'Alger", 'Alger', "casbah d'Alger"),
                ('Parle-moi de Londres', 'Londres', 'tour de Londres'),
                ('parle moi de rio de janeiro', 'Rio de Janeiro', None), ('parle moi de bordeaux', 'Bordeaux', None),
                ('parle moi du kremlin', 'Moscou', None), ('je veux visiter lyon', 'Lyon', 'Vieux Lyon'),
                ('parle moi de vienne', 'Vienne', 'Centre historique de Vienne'),
                ("le théâtre antique d'orange", 'Orange', None)):
            with self.subTest(text):
                found = place_photos(text)
                self.assertIsNotNone(found)
                self.assertEqual(found['titre'], titre)
                if first:
                    self.assertEqual(found['photos'][0]['nom'], first)
        # A capital, or a country under its short name: the sites of the country.
        for text, pays in (('Parle-moi de Tokyo', 'Japon'), ('parle moi de la chine', 'république populaire de Chine'),
                           ("c'est comment l'angleterre", 'Royaume-Uni')):
            with self.subTest(text):
                found = place_photos(text)
                self.assertEqual((found['titre'], found['pays']), (pays, pays))
        # The whole name of a site still wins, and so does a country named by its name.
        self.assertEqual(place_photos('Parle-moi du château de Versailles')['titre'], 'château de Versailles')
        self.assertEqual(place_photos('Marrakech ou le reste du Maroc ?')['titre'], 'Marrakech')
        for text in ('il faut que je vienne demain', "un jus d'orange", "j'ai un plan pour demain",
                     'la lune est belle ce soir', 'à ma place tu ferais quoi', 'trois pommes et deux poires',
                     'des outils tels que le marteau', 'on split la facture', 'Parle-moi de Marie',
                     "le noyau d'AURORA", "c'est au nord de la ville", 'un mâle et une femelle', 'my son is here',
                     'la victoire de hier', 'Parle-moi des lions', 'Parle-moi de la fusée Ariane', 'Et sa capitale ?'):
            with self.subTest(text):
                self.assertIsNone(place_photos(text))

    def test_memory_content(self):
        slots = extract_memory_content('Retiens que je préfère le mode sombre')
        self.assertEqual(slots['content'], 'je préfère le mode sombre')
        self.assertEqual(slots['category'], 'preference')
        slots = extract_memory_content('Aurora, souviens-toi que le projet Orion commence en janvier.')
        self.assertEqual(slots['content'], 'le projet Orion commence en janvier')
        self.assertEqual(slots['category'], 'projet')


class LabSceneRoutingTests(unittest.TestCase):
    """08/11/2026 (reviews/accueil-holographique.md § 3): a scene of the catalogue opens on the home page, an object
    asked in 3D without a scene gets an honest answer, and the usual requests keep their routing."""

    OPEN = {'scene': 'bras_robotique', 'action': 'ouvrir'}

    def test_opening_a_scene_of_the_catalogue(self):
        for text in ('Montre-moi comment fonctionne un bras robotique', 'Affiche un bras robotique en 3D',
                     'Aurora, montre-moi un bras robot', 'Je veux voir un bras articulé', 'Visualise les bras robotiques',
                     'Construis un bras manipulateur', 'fais voir un bras mécanique'):
            decision = ENGINE.decide_fast(text)
            self.assertEqual(decision.intent, 'ui.navigate', text)
            self.assertEqual(decision.slots, {'page': 'home', 'scene': self.OPEN}, text)

    def test_object_in_3d_without_scene(self):
        cases = {'Montre-moi un moteur électrique en 3D': 'un moteur électrique',
                 'Affiche un volcan en trois dimensions': 'un volcan',
                 'Montre un cœur humain en hologramme': 'un cœur humain',
                 "Fais-moi un hologramme d'une cellule": 'une cellule',
                 'Montre-moi en 3D comment fonctionne un moteur': 'un moteur'}
        for text, subject in cases.items():
            decision = ENGINE.decide_fast(text)
            self.assertEqual(decision.intent, 'ui.navigate', text)
            self.assertEqual(decision.slots, {'page': None, 'sujet_3d': subject}, text)

    def test_a_3d_model_named_as_such(self):
        """11/11/2026 (ID Studio IA, by voice, in the real AURORA): « … modèle 3D d'une voiture » reached AURORA without its
        verb (the dictation heard « on rentre chez moi » for « Aurora, fais-moi ») and got a chat answer that claimed
        things AURORA cannot do. The thing asked is also named as such, with a wish or with no verb at all."""
        cases = {"Fais-moi un modèle 3D d'une voiture": 'une voiture',
                 'Génère un modèle 3D de fusée': 'fusée',
                 "Je veux une maquette 3D d'un vélo": 'un vélo',
                 "Il me faut le modèle 3D d'un pont suspendu": 'un pont suspendu',
                 "on rentre chez moi modèle 3D d'une voiture": 'une voiture',          # the sentence as it was received
                 "Modèle 3D d'un château fort s'il te plaît": 'un château fort',
                 "Montre-moi un modèle en 3D d'une voiture": 'une voiture',            # not « un modèle »
                 'Montre-moi un moteur électrique en 3D, merci': 'un moteur électrique'}
        for text, subject in cases.items():
            decision = ENGINE.decide_fast(text)
            self.assertEqual(decision.intent, 'ui.navigate', text)
            self.assertEqual(decision.slots, {'page': None, 'sujet_3d': subject}, text)
        # A model the person already has, a question about models, a photo (« Photo vers 3D »), the Earth: not built.
        for text in ("J'ai un modèle 3D d'une voiture", 'Le modèle 3D de la voiture est-il précis ?',
                     "C'est quoi un modèle 3D ?", "Voici une maquette 3D d'un vélo",
                     'Fais un modèle 3D à partir de cette photo', 'Un modèle 3D de la Terre'):
            self.assertNotIn('sujet_3d', ENGINE.decide_fast(text).slots, text)

    def test_usual_requests_keep_their_routing(self):
        self.assertEqual(intent_of('Explique-moi comment fonctionne un moteur'), 'chat.general')
        self.assertEqual(intent_of('Comment fonctionne un bras robotique ?'), 'chat.general')   # no display verb
        self.assertEqual(ENGINE.decide_fast('Ouvre la mémoire').slots, {'page': 'memory'})
        self.assertEqual(ENGINE.decide_fast('Montre-moi le globe').slots, {'page': 'world'})
        self.assertEqual(intent_of('Affiche le système solaire en 3D'), 'sim.solar_system')
        self.assertEqual(intent_of('Cherche des vidéos de bras robotiques'), 'web.search')
        for text in ('Montre-moi les articles sur les bras robotiques', 'Montre-moi des images en 3D d’un volcan',
                     'Ouvre la page des paramètres', 'Montre la Terre en 3D'):
            self.assertNotIn('scene', ENGINE.decide_fast(text).slots, text)
            self.assertNotIn('sujet_3d', ENGINE.decide_fast(text).slots, text)

    def test_who_wrote_an_answer_is_a_capability_question(self):
        """08/11/2026 (run 28 of the real AURORA): the local model answered « toi qui le fait de travail ou c'est
        Claude » and claimed Claude's code. Who wrote an answer is now read on its run (meta.capabilities)."""
        for text in ("toi qui le fait de travail ou c'est Claude", "C'est toi ou Claude qui a écrit ce code ?",
                     'Qui a écrit ce code ?', 'Quel modèle a répondu ?', "C'est Claude qui a répondu ?",
                     "c'est toi qui as fait le jeu ?", 'Qui a rédigé cette réponse ?',
                     "est-ce que c'est l'IA locale qui a répondu", "c'est toi qui l'as codé ou Claude ?",
                     'Qui a fait ce jeu, toi ou Claude ?', 'tu as écrit ça toi-même ?', 'Quelle IA a écrit ça ?',
                     'Quel modèle as-tu utilisé pour ce code ?', "Tu l'as écrit toi-même ?",
                     'Est-ce que Claude a répondu ?', 'Qui a répondu ?'):
            with self.subTest(text=text):
                self.assertEqual(intent_of(text), 'meta.capabilities')
                self.assertTrue(rules.asks_answer_author(normalize(text)))
        for text, expected in [('Qui a écrit Les Misérables ?', 'chat.general'),
                               ('Qui a écrit le code de Linux ?', 'chat.general'),
                               ('Qui a fait la tour Eiffel ?', 'chat.general'),
                               ('Fais-moi un jeu, toi ou Claude', 'chat.general'),
                               ('Écris-moi un code Python qui trie une liste', 'chat.general'),
                               ('Qui est le plus fort, toi ou Claude ?', 'chat.general'),
                               ("C'est toi qui décides ?", 'chat.general'), ("Qui l'a écrit ?", 'chat.general'),
                               ('Quel modèle utilises-tu ?', 'meta.capabilities')]:
            with self.subTest(text=text):
                self.assertEqual(intent_of(text), expected)
                self.assertFalse(rules.asks_answer_author(normalize(text)))
        # About an attached file, the same question is an analysis of that file.
        decision = asyncio.run(DecisionEngine(use_semantic=False).decide(
            'Qui a écrit ce code ?', context={'attachments': [{'name': 'a.py', 'text': 'print(1)'}]}))
        self.assertEqual(decision.intent, 'doc.analyze')
        # E2: with a screen capture joined (its description), a question on the screen reads that capture; without
        # one, it stays the honest capability answer (« clique sur Capture d'écran », test_review_fixes.py).
        capture = [{'name': 'Capture d’écran 05:58', 'text': 'Capture de l’écran principal (3840×2160) : un éditeur.'}]
        for text in ('Regarde mon écran et dis-moi ce qui ne va pas', 'Décris la capture d’écran',
                     'Qu’est-ce qu’il y a sur mon écran ?', 'Analyse mon écran'):
            with self.subTest(text=text):
                self.assertEqual(intent_of(text), 'meta.capabilities')
                self.assertTrue(rules.SCREEN_ASKED.search(normalize(text)))
                decision = asyncio.run(DecisionEngine(use_semantic=False).decide(text, context={'attachments': capture}))
                self.assertEqual(decision.intent, 'doc.analyze')


KEY_SENTENCES = {
    # 29/10/2026 (voice): « à l'heure actuelle » means « currently »; « modèle dia » is « modèle d'IA »
    "sur quel modèle tu tournes à l'heure actuelle": 'meta.capabilities',
    'modèle dia es-tu': 'meta.capabilities',
    "Tu utilises quel modèle d'IA ?": 'meta.capabilities',
    'tu tournes sur quel modèle ?': 'meta.capabilities',
    "Donne-moi l'heure actuelle": 'time.now',
    "Quelle heure est-il à l'heure actuelle ?": 'time.now',
    # user's document
    'Quelle quantité de RAM est utilisée ?': 'system.status',
    'Retrouve notre projet sur Mars': 'memory.search',
    'Restaure les fichiers mis en quarantaine': 'files.restore',   # B4 (rapport §4.6)
    'Analyse le fichier Documents\\ventes.csv': 'data.analyze',     # P3
    'Lance ma routine du soir': 'routine.run',                     # D1
    'Rappelle-moi dans 20 minutes de sortir le plat': 'reminder.set',   # C3 (16/11/2026)
    'En mode agent : regarde ma RAM puis cherche pourquoi elle est haute': 'agent.run',   # P1
    'Explique-moi pourquoi mon PC ralentit': 'system.diagnose',
    'Recherche les dernières découvertes spatiales': 'web.search',
    'Analyse ce document technique compliqué': 'doc.analyze',
    'Simule une trajectoire orbitale': 'sim.orbit',
    'Supprime mes anciens fichiers': 'files.cleanup',
    'Aurora, organise mes recherches scientifiques de la semaine': 'task.plan',
    'Aurora, affiche les cyclones actifs sur le globe': 'ui.navigate',
    'Aurora, regarde cette erreur et explique-moi comment la résoudre': 'doc.analyze',
    'Aurora, ouvre mon projet et reprends là où nous en étions': 'memory.search',
    # contract table
    'Quels programmes consomment le plus ?': 'system.processes',
    'Pourquoi mon PC ralentit ?': 'system.diagnose',
    'Ferme Chrome': 'system.kill_process',
    'Retiens que je préfère le mode sombre': 'memory.save',
    "Quoi de neuf dans l'actualité scientifique ?": 'news.latest',
    'Quel temps fait-il à Lyon ?': 'weather.current',
    "Y a-t-il des séismes aujourd'hui ?": 'quakes.recent',
    'Combien font 17 × 23 ?': 'calc.evaluate',
    'Convertis 30 miles en km': 'calc.convert',
    'Quelle heure est-il ?': 'time.now',
    "Simule la trajectoire d'une sonde autour de Mars": 'sim.orbit',
    'Montre la position des planètes': 'sim.solar_system',
    'Quels vieux fichiers dans Téléchargements ?': 'files.scan',
    'Analyse ce document': 'doc.analyze',
    'Organise mes recherches de la semaine': 'task.plan',
    'Ouvre la mémoire': 'ui.navigate',
    'Zoome sur le Japon dans le globe': 'ui.navigate',     # 08/11/2026: situer sur le globe
    "Où est l'ISS ?": 'ui.navigate',
    'Montre les avions au-dessus de Lyon': 'ui.navigate',
    "Simule l'orbite de l'ISS à 400 km": 'sim.orbit',
    "Explique-moi ce qu'est l'ISS": 'chat.general',
    'Où est mon fichier rapport ?': 'chat.general',
    'As-tu accès à Internet ?': 'meta.capabilities',
    'Exécute la commande git status': 'shell.run',
    'Clique en 640, 400 dans Bloc-notes': 'pc.control',    # 9/11/2026: souris et clavier
    'Ouvre l’application YouTube Manager': 'app.launch',   # 9/11/2026: applications autorisées
    'Mets un commentaire sous ma dernière vidéo': 'youtube.comment',   # 9/11/2026: chaîne reliée
    'Raconte-moi une blague': 'chat.general',
}


class RoutingTests(unittest.TestCase):
    def test_key_sentences(self):
        for text, expected in KEY_SENTENCES.items():
            with self.subTest(text=text):
                decision = ENGINE.decide_fast(text)
                self.assertEqual(decision.intent, expected)
                self.assertIn(decision.method, ('rule', 'semantic', 'default'))

    def test_every_intent_reachable(self):
        self.assertEqual(set(KEY_SENTENCES.values()), set(INTENTS))

    def test_ambiguities(self):
        cases = {
            # memory vs web
            'Retrouve notre projet sur Mars': 'memory.search',
            'Les dernières découvertes sur Mars': 'web.search',
            # explicit save vs a memory mentioned in conversation
            'Souviens-toi que mon train part à 8 h': 'memory.save',
            'Note que la réunion est jeudi': 'memory.save',
            'Je me souviens de notre voyage à Lyon': 'chat.general',
            'Tu te souviens de notre voyage à Lyon ?': 'memory.search',
            # measure vs diagnosis
            'Le CPU est à combien ?': 'system.status',
            'Mon ordinateur ralentit, analyse ça': 'system.diagnose',
            # read vs delete
            'Liste les gros fichiers du bureau': 'files.scan',
            'Quels fichiers puis-je effacer sans risque ?': 'files.scan',
            'Fais le ménage dans mes téléchargements': 'files.cleanup',
            # calc vs chat
            'Combien font 12 fois 8 ?': 'calc.evaluate',
            'Combien de temps faut-il pour aller sur Mars ?': 'chat.general',
            # capabilities vs web
            'Que sais-tu faire ?': 'meta.capabilities',
            'Peux-tu chercher sur Internet le prix du pétrole ?': 'web.search',
            # navigation vs measures
            'Affiche la page mémoire': 'ui.navigate',
            'Montre le globe': 'ui.navigate',
            "Affiche l'utilisation du processeur": 'system.status',
            # counter-examples
            'Quelle est la température de mon processeur ?': 'system.status',
            'Le temps passe vite': 'chat.general',
            'Mon chat est très lent ce matin': 'chat.general',
            'Écris une histoire sur un satellite perdu': 'chat.general',
            "Qu'est-ce qu'un séisme ?": 'chat.general',
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(intent_of(text), expected)

    def test_compound_requests(self):
        decision = ENGINE.decide_fast('mon PC est lent, analyse le problème, recherche des solutions et prépare '
                                      'une optimisation')
        self.assertEqual(decision.intent, 'system.diagnose')
        self.assertIn('web.search', decision.secondary_intents)
        decision = ENGINE.decide_fast('Quel temps fait-il à Paris et quelle heure est-il ?')
        self.assertEqual((decision.intent, decision.secondary_intents), ('weather.current', ['time.now']))
        self.assertEqual(ENGINE.decide_fast('Quelle heure est-il ?').secondary_intents, [])

    def test_compound_slots_come_from_their_clause(self):
        decision = ENGINE.decide_fast('Retiens que la réunion est à 10 h et dis-moi quel temps il fera à Nantes')
        self.assertEqual((decision.intent, decision.secondary_intents), ('memory.save', ['weather.current']))
        self.assertEqual((decision.slots['content'], decision.slots['city']), ('la réunion est à 10 h', 'Nantes'))
        decision = ENGINE.decide_fast("Mesure la RAM puis cherche sur internet comment l'optimiser")
        self.assertEqual(decision.slots['metric'], 'ram')
        self.assertIn('optimiser', decision.slots['query'])
        # a single fact containing "et" stays whole
        decision = ENGINE.decide_fast("Retiens que j'aime le thé et le café")
        self.assertEqual(decision.slots['content'], "j'aime le thé et le café")

    def test_slots_in_decision(self):
        self.assertEqual(ENGINE.decide_fast('Quel temps fait-il à Lyon ?').slots, {'city': 'Lyon'})
        self.assertEqual(ENGINE.decide_fast('Combien font 17 × 23 ?').slots, {'expression': '17 * 23'})
        self.assertEqual(ENGINE.decide_fast('Quelle quantité de RAM est utilisée ?').slots, {'metric': 'ram'})

    def test_explicit_hint(self):
        decision = ENGINE.decide_fast('Paris', intent_hint='weather.current')
        self.assertEqual((decision.intent, decision.method, decision.confidence), ('weather.current', 'explicit', 1.0))
        self.assertEqual(decision.slots['city'], 'Paris')
        decision = ENGINE.decide_fast('Quelle heure est-il ?', intent_hint='nimporte.quoi')
        self.assertEqual((decision.intent, decision.method), ('time.now', 'rule'))
        self.assertIn('inconnue', decision.explanation)

    def test_empty_and_default(self):
        decision = ENGINE.decide_fast('')
        self.assertEqual((decision.intent, decision.method), ('chat.general', 'default'))
        decision = ENGINE.decide_fast('zorglub flimp')
        self.assertEqual((decision.intent, decision.method), ('chat.general', 'default'))
        self.assertTrue(decision.explanation)

    def test_determinism(self):
        other = DecisionEngine(use_semantic=False)
        sem = DecisionEngine(use_semantic=True)
        for text in list(KEY_SENTENCES) + ['zorglub', 'mon pc rame grave', 'tu a internet']:
            first = ENGINE.decide_fast(text).to_dict()
            for engine in (ENGINE, other):
                again = engine.decide_fast(text).to_dict()
                first.pop('duration_ms', None)
                again.pop('duration_ms', None)
                self.assertEqual(first, again, text)
            self.assertEqual(sem.decide_fast(text).intent, sem.decide_fast(text).intent)

    def test_to_dict_contract(self):
        data = ENGINE.decide_fast('Quel temps fait-il à Lyon ?').to_dict()
        self.assertEqual(set(data), {'intent', 'confidence', 'method', 'slots', 'alternatives', 'secondary_intents',
                                     'normalized', 'duration_ms', 'explanation'})
        json.dumps(data)
        rebuilt = Decision(**{**data, 'alternatives': [tuple(a) for a in data['alternatives']]})
        self.assertEqual(rebuilt.intent, 'weather.current')

    def test_latency(self):
        texts = list(KEY_SENTENCES)
        ENGINE.decide_fast('initialisation')
        samples = []
        for text in texts:
            start = time.perf_counter()
            DecisionEngine(use_semantic=False).decide_fast(text + ' ')
            samples.append((time.perf_counter() - start) * 1000)
        self.assertLess(statistics.median(samples), 30.0)

    def test_get_engine_shared(self):
        router.reset_engine()
        try:
            self.assertIs(router.get_engine(), router.get_engine())
        finally:
            router.reset_engine()
        self.assertIsInstance(router.DEFAULT_SEMANTIC_ROUTER, bool)
        self.assertEqual(router.SEMANTIC_DEFAULT, router.DEFAULT_SEMANTIC_ROUTER)


class SemanticTests(unittest.TestCase):
    def test_training_example_matches_itself(self):
        rows = semantic.load_examples()
        self.assertGreaterEqual(len(rows), 12 * len(INTENTS))
        match = semantic.classify(normalize(rows[0]['text']))
        self.assertEqual(match.intent, rows[0]['intent'])
        self.assertTrue(match.confident)

    def test_semantic_stays_behind_rules(self):
        sem = DecisionEngine(use_semantic=True)
        for text, expected in KEY_SENTENCES.items():
            self.assertEqual(sem.decide_fast(text).intent, expected, text)

    def test_unrelated_text_not_confident(self):
        match = semantic.classify(normalize('zorglub flimp wazaa'))
        self.assertTrue(match is None or not match.confident)


class FakeClassifier:
    def __init__(self, label='meta.capabilities', confidence=0.9, fail=False):
        self.calls = 0
        self.label, self.confidence, self.fail = label, confidence, fail

    async def classify(self, text, labels, *, instructions, timeout=8):
        self.calls += 1
        if self.fail:
            raise RuntimeError('ollama down')
        assert self.label in labels and 'Intentions' in instructions
        return self.label, self.confidence


class LlmFallbackTests(unittest.TestCase):
    def test_not_called_when_rules_confident(self):
        fake = FakeClassifier()
        decision = asyncio.run(DecisionEngine(llm_classifier=fake, use_semantic=False).decide('Quelle heure est-il ?'))
        self.assertEqual((decision.intent, fake.calls), ('time.now', 0))

    def test_called_when_uncertain_and_cached(self):
        fake = FakeClassifier('meta.capabilities', 0.9)
        engine = DecisionEngine(llm_classifier=fake, use_semantic=False)
        # a sentence no rule recognises (« Tu utilises quel modèle d'IA ? » is a rule since 29/10/2026)
        first = asyncio.run(engine.decide('Quelle version de toi me parle ?'))
        second = asyncio.run(engine.decide('Quelle version de toi me parle ?'))
        self.assertEqual((first.intent, first.method), ('meta.capabilities', 'llm'))
        self.assertEqual(first.intent, second.intent)
        self.assertEqual(fake.calls, 1)

    def test_low_llm_confidence_ignored(self):
        fake = FakeClassifier('web.search', 0.3)
        decision = asyncio.run(DecisionEngine(llm_classifier=fake, use_semantic=False).decide('zorglub flimp'))
        self.assertEqual((decision.intent, decision.method), ('chat.general', 'default'))

    def test_failure_falls_back(self):
        fake = FakeClassifier(fail=True)
        decision = asyncio.run(DecisionEngine(llm_classifier=fake, use_semantic=False).decide('zorglub flimp'))
        self.assertEqual(decision.intent, 'chat.general')

    def test_plain_function_classifier(self):
        calls = []

        async def classify(text, labels, *, instructions, timeout=8, model=None):
            calls.append(text)
            return 'meta.capabilities', 0.8
        decision = asyncio.run(DecisionEngine(llm_classifier=classify, use_semantic=False).decide('tu a internet'))
        self.assertEqual((decision.intent, decision.method, len(calls)), ('meta.capabilities', 'llm', 1))

    def test_explicit_hint_skips_llm(self):
        fake = FakeClassifier()
        decision = asyncio.run(DecisionEngine(llm_classifier=fake).decide('zorglub', intent_hint='task.plan'))
        self.assertEqual((decision.intent, decision.method, fake.calls), ('task.plan', 'explicit', 0))

    def test_attachment_context(self):
        decision = asyncio.run(DecisionEngine(use_semantic=False).decide(
            'Voilà', context={'attachments': [{'name': 'a.txt', 'text': 'contenu'}]}))
        self.assertEqual(decision.intent, 'doc.analyze')


CAPS_FULL = {'internet': True,
             'ollama': {'up': True, 'models': ['qwen3.5:9b'], 'chat_model': 'qwen3.5:9b'},
             'cloud': {'configured': True, 'provider': 'anthropic',
                       'tiers': {'rapide': {'model': 'claude-haiku-5-5', 'effort': 'low'},
                                 'standard': {'model': 'claude-sonnet-5-5', 'effort': 'medium'},
                                 'expert': {'model': 'claude-opus-5-5', 'effort': 'high'}}}}
CAPS_NO_CLOUD = {**CAPS_FULL, 'cloud': {'configured': False, 'provider': 'anthropic', 'tiers': {}}}
CAPS_NOTHING = {'internet': False, 'ollama': {'up': False, 'models': []}, 'cloud': {'configured': False}}


def choose(text: str, mode: str, caps=CAPS_FULL, **kw) -> EngineChoice:
    decision = ENGINE.decide_fast(text)
    kw.setdefault('budget_remaining_usd', 1.0)
    return select_engine(decision, mode=mode, text=text, caps=caps, **kw)


class EngineSelectTests(unittest.TestCase):
    MODES = ('prive', 'equilibre', 'avance')

    def test_measures_are_tools(self):
        for mode in self.MODES:
            for text in ('Quelle quantité de RAM est utilisée ?', 'Combien font 17 × 23 ?', 'Quelle heure est-il ?',
                         'Quel temps fait-il à Lyon ? Utilise Claude'):
                choice = choose(text, mode)
                self.assertEqual((choice.engine, choice.cloud_tier, choice.needs_consent), ('tool', None, False))
                self.assertIn('sans IA', choice.reason)

    def test_diagnose(self):
        self.assertEqual(choose('Pourquoi mon PC ralentit ?', 'equilibre').engine, 'tool+local')
        self.assertEqual(choose('Pourquoi mon PC ralentit ?', 'equilibre', caps=CAPS_NOTHING).engine, 'tool')

    def test_actions_and_navigation(self):
        self.assertEqual(choose('Ferme Chrome', 'avance').engine, 'tool')
        self.assertEqual(choose('Retiens que je préfère le thé', 'avance').engine, 'tool')
        self.assertEqual(choose('Ouvre la mémoire', 'avance').engine, 'none')

    def test_prive_never_cloud(self):
        for text in ('Utilise Claude pour analyser ce document technique compliqué',
                     'Mode expert : écris une stratégie détaillée et approfondie'):
            choice = choose(text, 'prive')
            self.assertEqual(choice.engine, 'local')
            self.assertIsNone(choice.cloud_tier)
            self.assertIn('privé', choice.reason)
        self.assertEqual(choose('Utilise Claude pour raconter une blague', 'prive', caps={
            **CAPS_FULL, 'ollama': {'up': False}}).engine, 'none')

    def test_equilibre_needs_consent(self):
        choice = choose('Utilise Claude pour me raconter une blague', 'equilibre')
        self.assertEqual((choice.engine, choice.cloud_tier, choice.needs_consent), ('cloud', 'standard', True))
        self.assertEqual(choice.model, 'claude-sonnet-5-5')
        # 04/11/2026: a complex text no longer goes to Claude first; the local model tries, Claude is its fallback.
        choice = choose('Analyse ce document technique compliqué', 'equilibre')
        self.assertEqual((choice.engine, choice.cloud_tier), ('local', None))
        self.assertIn('Claude en secours', choice.reason)
        # Opus is reserved to the Avancé mode (CLAUDE.md): Équilibré lowers the request to Sonnet and says so.
        for asked in ('Utilise Claude en mode expert pour analyser ce document', 'Demande à Claude Opus une analyse'):
            choice = choose(asked, 'avance')
            self.assertEqual((choice.cloud_tier, choice.model), ('expert', 'claude-opus-5-5'), asked)
            choice = choose(asked, 'equilibre')
            self.assertEqual((choice.cloud_tier, choice.model), ('standard', 'claude-sonnet-5-5'), asked)
            self.assertIn('réservé au mode Avancé', choice.reason)
        self.assertEqual(choose('Utilise Claude Haiku pour me raconter une blague', 'equilibre').model,
                         'claude-haiku-5-5')
        self.assertEqual(choose('Raconte-moi une blague', 'equilibre').engine, 'local')
        self.assertEqual(choose('Recherche les dernières découvertes spatiales', 'equilibre').engine, 'tool+local')

    def test_avance(self):
        choice = choose('Utilise Claude pour me raconter une blague', 'avance')
        self.assertEqual((choice.engine, choice.needs_consent), ('cloud', True))   # every cloud send is previewed, in every mode
        choice = choose('Utilise Claude pour me raconter une blague', 'avance', sensitivity='sensitive')
        self.assertEqual((choice.engine, choice.needs_consent), ('cloud', True))
        choice = choose('Utilise Claude et cherche sur internet les dernières découvertes', 'avance')
        self.assertEqual(choice.engine, 'tool+cloud')

    def test_cloud_blocked(self):
        choice = choose('Utilise Claude pour me raconter une blague', 'avance', caps=CAPS_NO_CLOUD)
        self.assertEqual(choice.engine, 'local')
        self.assertIn('pas configuré', choice.reason)
        choice = choose('Utilise Claude pour me raconter une blague', 'avance', budget_remaining_usd=0.0)
        self.assertEqual(choice.engine, 'local')
        self.assertIn('budget', choice.reason)
        choice = choose('Utilise Claude pour me raconter une blague', 'avance', user_pref='local')
        self.assertEqual(choice.engine, 'local')
        self.assertEqual(choose('Raconte une blague', 'equilibre', caps=CAPS_NOTHING).engine, 'none')

    def test_ollama_down_uses_cloud_when_allowed(self):
        caps = {**CAPS_FULL, 'ollama': {'up': False}}
        choice = choose('Raconte-moi une blague', 'equilibre', caps=caps)
        self.assertEqual((choice.engine, choice.needs_consent), ('cloud', True))

    def test_tier_preference(self):
        choice = choose('Utilise Claude pour raconter une blague', 'avance', cloud_tier_pref='rapide')
        self.assertEqual((choice.cloud_tier, choice.model), ('rapide', 'claude-haiku-5-5'))
        self.assertEqual(choose('Mode expert : raconte une blague', 'avance').cloud_tier, 'expert')

    def test_a_tier_chosen_in_the_bar_does_not_put_claude_first(self):
        # 04/11/2026, ID Studio IA: « c'est le modèle local en premier que je veux ». Runs 23 to 35 (29-31/10) went to
        # Claude because « Standard (Sonnet) » was chosen in the bar with the engine on Auto (rule of 29/10).
        choice = choose('Explique-moi la photosynthèse', 'equilibre', cloud_tier_pref='standard')
        self.assertEqual((choice.engine, choice.needs_consent), ('local', False))
        self.assertIn('Claude en secours', choice.reason)
        explicit = choose('Explique-moi la photosynthèse', 'equilibre', user_pref='cloud', cloud_tier_pref='rapide')
        self.assertEqual((explicit.engine, explicit.model), ('cloud', 'claude-haiku-5-5'))   # « Claude » chosen: direct
        private = choose('Raconte une blague', 'prive', cloud_tier_pref='standard')
        self.assertEqual(private.engine, 'local')
        self.assertNotIn('Claude en secours', private.reason)      # never Claude in private mode

    def test_local_cannot_answer(self):
        from aurora.core.engine_select import local_cannot_answer
        refusals = (   # observed (comment above _CANNOT_RE)
            'Je suis AURORA, votre assistant personnel.  Je vous informe que je ne peux pas effectuer de recherche '
            "directe sur votre site web, car je n'ai pas la capacité d'accéder à des URLs spécifiques",
            'Je suis AURORA, votre assistant personnel.  Je vous rappelle que je ne peux pas naviguer directement sur '
            'votre chaîne YouTube ni effectuer d’analyse en temps réel de votre site.',
            "Je ne peux pas voir votre carte mémoire. En tant qu'assistant logiciel, je n'ai pas la capacité de voir",
            "Je n'ai pas d'information actualisée en temps réel sur les nouveautés récentes concernant les modules",
            'Je n’ai pas accès en temps réel aux données financières actuelles (prix de l’action Tesla en ce moment',
        )
        for text in refusals:
            self.assertTrue(local_cannot_answer(text), text)
        answers = (
            "Pour pouvoir vous proposer un commentaire pertinent pour votre dernière vidéo YouTube, j'aurais besoin "
            'de savoir de quelle vidéo il s’agit.',
            'Je comprends que vous souhaitez que j’effectue une recherche sur Internet.  Veuillez noter que, '
            'conformément à mes capacités, je ne navigue pas de manière libre ; j’utilise des outils spécifiques',
            'Le sigle VITA désigne le projet de veste intelligente de thermoregulation adaptative, appelé aussi VITA '
            '(Veste Intellectuelle de Thermorégulation Adaptative).   Je ne dispose pas de la définition explicite',
            'Bonjour. Comment puis-je vous assister aujourd’hui ?',
            'Je suis actuellement basé sur le modèle de conversation **gemma4:e4b**, exécuté par l’IA locale Ollama.',
        )
        for text in answers:
            self.assertEqual(local_cannot_answer(text), '', text)

    def test_preference_detection(self):
        self.assertEqual(detect_engine_preference('Utilise Claude pour ça')['engine'], 'cloud')
        self.assertEqual(detect_engine_preference('avec Claude stp')['engine'], 'cloud')
        self.assertEqual(detect_engine_preference('fais-le en local')['engine'], 'local')
        self.assertEqual(detect_engine_preference('sans cloud, merci')['engine'], 'local')
        self.assertEqual(detect_engine_preference("n'utilise pas Claude")['engine'], 'local')
        self.assertEqual(detect_engine_preference('mode expert')['tier'], 'expert')
        self.assertEqual(detect_engine_preference('Quelle heure est-il ?')['engine'], 'auto')

    def test_complexity(self):
        self.assertEqual(estimate_complexity('Raconte une blague')['tier'], 'rapide')
        decision = ENGINE.decide_fast('Analyse ce document technique compliqué')
        self.assertEqual(estimate_complexity('Analyse ce document technique compliqué', decision)['tier'], 'expert')

    def test_reason_and_dict(self):
        for mode in self.MODES:
            choice = choose('Raconte-moi une blague', mode)
            self.assertTrue(choice.reason and choice.reason[0].isupper())
            self.assertEqual(set(choice.to_dict()), {'engine', 'cloud_tier', 'model', 'needs_consent', 'reason'})


class BenchCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests import bench_routing
        cls.bench = bench_routing
        cls.rows = bench_routing.load_bench()

    def test_corpus_shape(self):
        self.assertGreaterEqual(len(self.rows), 180)
        self.assertEqual({r['intent'] for r in self.rows}, set(INTENTS))
        self.assertTrue({r['tag'] for r in self.rows} <= set(self.bench.TAGS))
        self.assertEqual({r['tag'] for r in self.rows}, set(self.bench.TAGS))
        texts = {r['text'] for r in self.rows}
        for sentence in ('Quelle quantité de RAM est utilisée ?', 'Retrouve notre projet sur Mars',
                         'Aurora, ouvre mon projet et reprends là où nous en étions'):
            self.assertIn(sentence, texts)

    def test_no_overlap_with_training(self):
        train = {normalize(r['text']) for r in semantic.load_examples()}
        overlap = [r['text'] for r in self.rows if normalize(r['text']) in train]
        self.assertEqual(overlap, [])
        per_intent = {}
        for r in semantic.load_examples():
            per_intent[r['intent']] = per_intent.get(r['intent'], 0) + 1
        self.assertTrue(all(per_intent.get(i, 0) >= 12 for i in INTENTS), per_intent)

    def test_run_bench(self):
        report = self.bench.run_bench(use_semantic=True, use_llm=False, write=False)
        self.assertEqual(report['determinism']['mismatches'], 0)
        self.assertGreaterEqual(report['rules']['accuracy'], 0.9)   # regression guard (measured 190/205)
        self.assertLess(report['latency_rules']['p95_ms'], 30.0)
        self.assertIn('semantic_router', report['recommendation'])
        self.assertEqual(report['recommendation']['semantic_router'], router.DEFAULT_SEMANTIC_ROUTER)
        json.dumps(report)


if __name__ == '__main__':
    unittest.main()
