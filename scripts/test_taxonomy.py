"""Operational classifier, importer and content-model tests. No network access."""
import contextlib
import copy
import io
import json
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

import catalogue
import fetch_new_mixes as fetch
from taxonomy import ROOT, Taxonomy, source_key


class TaxonomyTests(unittest.TestCase):
    def setUp(self):
        self.taxonomy = Taxonomy()

    def classify(self, labels, url='https://example.com/set'):
        return self.taxonomy.classify(labels, url)

    def test_approved_categories_rules_and_aliases(self):
        self.assertEqual(set(self.taxonomy.categories), {'House', 'Techno', 'Trance', 'Drum & Bass',
                         'UK Garage & Bass', 'Dubstep & Bass', 'Hard Dance', 'Disco & Funk',
                         'Breaks & Electronic', 'Pop & Open Format', 'Chill & Ambient', 'Alternative & Other'})
        for label in ('EDM', 'edm', 'EdM', 'Electric House', 'electro house'):
            self.assertEqual(self.classify([label])['genres'], ['House'])
        self.assertEqual(self.classify(['EDM', 'dubstep', 'techno', 'EDM'])['genres'], ['House', 'Techno', 'Dubstep & Bass'])
        self.assertEqual(self.classify(['Drumstep'])['genres'], ['Drum & Bass', 'Dubstep & Bass'])
        self.assertEqual(self.classify(['breakbeat hardcore', 'hardcore breaks'])['genres'], ['Hard Dance'])
        self.assertEqual(self.classify(['bass funk', 'Indie Disco'])['genres'], ['Disco & Funk'])

    def test_unknown_contextual_and_metadata_labels(self):
        labels = ['Dance', 'Dance & EDM', 'Mainstage EDM', 'Festival EDM', 'Bass', 'Bass Music',
                  'Bass & Club Music', 'Trap', 'Garage', 'Hardcore', 'unknown new style']
        result = self.classify(labels)
        self.assertEqual(result['genres'], [])
        self.assertEqual(result['unresolved'], labels)
        self.assertEqual(self.classify(['Dariacore', 'Chilled'])['genres'], [])
        self.assertEqual(self.classify(['chillout', 'chillwave', 'ambient house'])['genres'], ['Chill & Ambient'])
        self.assertEqual(self.classify(['house', 'unknown new style'])['genres'], ['House'])

    def test_approved_recording_overrides(self):
        cases = {
            'https://www.mixcloud.com/TeoTheHyena/yfsp-final-mix/': ['House'],
            'https://soundcloud.com/threadfiend/core-element-2026-ritual': ['Pop & Open Format'],
            'https://www.mixcloud.com/DJVally/dream-conquer-pt-ii-live-at-fc-2017/': ['House'],
            'https://www.mixcloud.com/DJVally/recovery-live-at-fc-2023/': ['House', 'Trance'],
            'https://www.mixcloud.com/Phozon/chill-selection-cfz-2026/': ['Chill & Ambient'],
        }
        for url, expected in cases.items():
            self.assertEqual(self.classify([], url)['genres'], expected)
        self.assertEqual(self.classify(['EDM'], 'https://www.mixcloud.com/djswolfuk/club-animalz-live-set-november-2021/')['genres'], ['House'])
        core = self.classify(['genrefluid', 'drum & bass,funk,dubstep,dance & edm'], 'https://soundcloud.com/threadfiend/core-element-2026-ritual')
        self.assertEqual(core['genres'], self.classify(['genrefluid'], 'https://soundcloud.com/threadfiend/infurno-2026')['genres'])
        self.assertEqual(self.classify(['edm', 'future house'], 'https://www.mixcloud.com/Spirou333/spirous-dj-livestream-23052023/')['genres'],
                         ['House', 'Drum & Bass', 'Dubstep & Bass', 'Hard Dance'])
        self.assertEqual(self.classify(['dubstep', 'darkstep'], 'https://www.mixcloud.com/sylvix/burrow-beats-a-dark-140midtempo-minimix/')['genres'],
                         ['Drum & Bass', 'Dubstep & Bass'])
        self.assertEqual(source_key('http://www.mixcloud.com/Artist/Set/?utm=test'), 'https://mixcloud.com/Artist/Set')

    def test_dispositions_and_canonical_validation(self):
        statuses = [r['status'] for r in self.taxonomy.recordings.values()]
        self.assertEqual(statuses.count('deferred'), 7)
        self.assertEqual(statuses.count('excluded'), 4)
        for url, recording in self.taxonomy.recordings.items():
            self.assertEqual(self.classify([], url)['status'], recording['status'])
            self.assertTrue(recording['note'])
        for values in (['EDM'], ['House', 'House'], [], 'House', [None], [{}]):
            with self.assertRaises(ValueError):
                self.taxonomy.check_categories(values)


class ContentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.content = Path(self.temp.name)
        self.taxonomy = Taxonomy()

    def page(self, source, name='events/test/set.md'):
        path = self.content / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(source.encode())
        return path

    def test_migration_preserves_provenance_front_matter_body_and_is_idempotent(self):
        original = '---\r\n# comment\r\nembed: {url: "https://example.com/set"}\r\ntitle: "Unchanged"\r\ndate: 2026-01-01\r\nartists: ["A"]\r\ngenres:\r\n  - EDM\r\n  - "NuDisco"\r\nother: "untouched"\r\n---\r\n\r\n## Tracklist\r\nOriginal body: --- stays.\r\n'
        path = self.page(original)
        changes, errors, _ = catalogue.inspect(self.content, self.taxonomy, True)
        self.assertFalse(errors)
        self.assertEqual(len(changes), 1)
        after = changes[0][2]
        self.assertIn('# comment\r\n', after)
        path.write_bytes(after.encode())
        _, data, match = catalogue.read_page(path)
        self.assertEqual(data['original_genres'], ['EDM', 'NuDisco'])
        self.assertEqual(data['genres'], ['House', 'Disco & Funk'])
        self.assertEqual(after[match.end():], original.split('---\r\n', 2)[2])
        self.assertIn('other: "untouched"\r\n', after)
        self.assertEqual(catalogue.inspect(self.content, self.taxonomy, True)[0], [])
        self.assertEqual(catalogue.inspect(self.content, self.taxonomy)[1], [])

    def test_partial_migration_uses_existing_originals_even_empty(self):
        path = self.page('---\nembed: {url: "https://example.com/set"}\ngenres: [Techno]\noriginal_genres: [EDM, Disco]\n---\nBody\n')
        changes, errors, _ = catalogue.inspect(self.content, self.taxonomy, True)
        self.assertFalse(errors)
        self.assertIn('original_genres: [EDM, Disco]', changes[0][2])
        self.assertIn('genres: ["House", "Disco & Funk"]', changes[0][2])
        self.assertEqual(catalogue.original_labels({'original_genres': [], 'genres': ['House']}), [])

    def test_deferred_and_excluded_content_is_not_modified(self):
        for index, (url, recording) in enumerate(self.taxonomy.recordings.items()):
            self.page('---\nembed: {url: ' + json.dumps(url) + '}\ngenres: [unknown]\n---\nBody\n', f'uncategorised/set-{index}.md')
        changes, errors, counts = catalogue.inspect(self.content, self.taxonomy, True)
        self.assertFalse(errors)
        self.assertFalse(changes)
        self.assertEqual(counts['deferred'], 7)
        self.assertEqual(counts['excluded'], 4)

    def test_catalogue_check_works_without_git_ignored_local_staging(self):
        checkout = self.content / 'checkout'
        shutil.copytree(ROOT / 'content', checkout, ignore=shutil.ignore_patterns('uncategorised'))
        _, errors, counts = catalogue.inspect(checkout, self.taxonomy)
        self.assertFalse(errors)
        self.assertGreater(counts['catalogue'], 0)
        self.assertEqual(counts['staging'], 0)

    def test_unknown_import_is_allowed_only_as_empty_canonical_staging(self):
        self.page('---\nembed: {url: "https://example.com/set"}\ngenres: []\noriginal_genres: [unknown]\ngenre_review_required: true\n---\n', 'uncategorised/set.md')
        _, errors, counts = catalogue.inspect(self.content, self.taxonomy)
        self.assertFalse(errors)
        self.assertEqual(counts['needs_review'], 1)
        self.page('---\nembed: {url: "https://example.com/other"}\ngenres: [unknown]\noriginal_genres: [unknown]\n---\n', 'uncategorised/bad.md')
        self.assertTrue(catalogue.inspect(self.content, self.taxonomy)[1])

    def test_cli_dry_run_is_read_only_and_apply_is_idempotent(self):
        path = self.page('---\nembed: {url: "https://example.com/set"}\ngenres: [EDM]\n---\nBody\n')
        before = path.read_bytes()
        command = [sys.executable, str(ROOT / 'scripts/catalogue.py'), 'migrate', '--content', str(self.content)]
        dry = subprocess.run(command, text=True, capture_output=True, check=True)
        self.assertIn('1 changed files', dry.stdout)
        self.assertEqual(before, path.read_bytes())
        subprocess.run(command + ['--apply'], capture_output=True, check=True)
        again = subprocess.run(command, text=True, capture_output=True, check=True)
        self.assertIn('0 changed files', again.stdout)


class ImporterTests(unittest.TestCase):
    def setUp(self):
        self.mix = dict(url='https://example.com/set', name='A "demo"', slug='demo', tags=['EDM', 'NuDisco', 'unknown tag', 'EDM'],
                        created='2026-01-01', description='Original description\n\nTracklist\nA — B')

    def test_stub_preserves_raw_tags_and_classifies_independently(self):
        raw = copy.deepcopy(self.mix)
        result = Taxonomy().classify(self.mix['tags'], self.mix['url'])
        text = fetch.build_stub(self.mix, 'A', result, title=self.mix['name'])
        data = yaml.safe_load(text.split('---', 2)[1])
        self.assertEqual(data['original_genres'], raw['tags'])
        self.assertEqual(data['genres'], ['House', 'Disco & Funk'])
        self.assertEqual(data['title'], 'A "demo"')
        self.assertIn('Original description', text)
        self.assertIn('A — B', text)
        self.assertEqual(self.mix, raw)

    def test_importer_dry_run_unknown_and_deferred_behaviour(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mixed = [self.mix, dict(self.mix, url='https://example.com/unknown', tags=['Trap']),
                     dict(self.mix, url='https://soundcloud.com/sablexox/no-stoppy', tags=['house'])]
            output = io.StringIO()
            with patch.object(fetch, 'ROOT', root), patch.object(fetch, 'UNCATEGORISED_DIR', root / 'content/uncategorised'), \
                 patch.object(fetch, 'parse_artists_md', return_value=[dict(name='A', platform='SoundCloud', profile_url='https://soundcloud.com/a')]), \
                 patch.object(fetch, 'known_embed_urls', return_value=set()), patch.object(fetch, 'known_artist_slugs', return_value=set()), \
                 patch.object(fetch, 'event_vocabulary', return_value={}), patch.object(fetch, 'fetch_soundcloud_mixes', return_value=mixed), \
                 patch.object(sys, 'argv', ['fetch_new_mixes.py', '--dry-run']), contextlib.redirect_stdout(output):
                self.assertEqual(fetch.main(), 0)
            self.assertIn("genres: ['House', 'Disco & Funk']", output.getvalue())
            self.assertIn("unresolved: ['Trap']", output.getvalue())
            self.assertIn('genre review required', output.getvalue())
            self.assertIn('excluded by policy', output.getvalue())
            self.assertFalse(list(root.rglob('*.md')))

    def test_importer_apply_writes_only_staging_with_review_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mixes = [self.mix, dict(self.mix, url='https://example.com/unknown', tags=['Trap'])]
            with patch.object(fetch, 'ROOT', root), patch.object(fetch, 'UNCATEGORISED_DIR', root / 'content/uncategorised'), \
                 patch.object(fetch, 'parse_artists_md', return_value=[dict(name='A', platform='SoundCloud', profile_url='https://soundcloud.com/a')]), \
                 patch.object(fetch, 'known_embed_urls', return_value=set()), patch.object(fetch, 'known_artist_slugs', return_value=set()), \
                 patch.object(fetch, 'event_vocabulary', return_value={}), patch.object(fetch, 'fetch_soundcloud_mixes', return_value=mixes), \
                 patch.object(sys, 'argv', ['fetch_new_mixes.py']), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(fetch.main(), 0)
            files = list(root.rglob('*.md'))
            self.assertEqual(len(files), 2)
            self.assertTrue(all(p.is_relative_to(root / 'content/uncategorised') for p in files))
            pages = {catalogue.read_page(p)[1]['embed']['url']: catalogue.read_page(p)[1] for p in files}
            known = pages[self.mix['url']]
            unknown = pages['https://example.com/unknown']
            self.assertEqual(known['original_genres'], self.mix['tags'])
            self.assertEqual(known['genres'], ['House', 'Disco & Funk'])
            self.assertEqual(unknown['genres'], [])
            self.assertTrue(unknown['genre_review_required'])

    def test_unclassified_stub_flags_review(self):
        self.mix['tags'] = ['Trap', 'unrecognised']
        data = yaml.safe_load(fetch.build_stub(self.mix, 'A', Taxonomy().classify(self.mix['tags'], self.mix['url'])).split('---', 2)[1])
        self.assertEqual(data['genres'], [])
        self.assertEqual(data['original_genres'], ['Trap', 'unrecognised'])
        self.assertTrue(data['genre_review_required'])

    def test_mixcloud_and_soundcloud_tags_retain_case_and_order(self):
        with patch.object(fetch, 'http_get_json', return_value={'data': [{'url': 'https://mixcloud.com/a/b/', 'tags': [{'name': 'EDM'}, {'name': 'NuDisco'}]}]}):
            self.assertEqual(fetch.fetch_mixcloud_mixes('https://mixcloud.com/a/')[0]['tags'], ['EDM', 'NuDisco'])
        responses = [{'id': 1}, {'collection': [{'permalink_url': 'https://soundcloud.com/a/b', 'genre': 'EDM', 'tag_list': '"Funkin House" NuDisco'}]}]
        with patch.object(fetch, 'get_soundcloud_client_id', return_value='test'), patch.object(fetch, 'http_get_json', side_effect=responses):
            self.assertEqual(fetch.fetch_soundcloud_mixes('https://soundcloud.com/a')[0]['tags'], ['EDM', 'Funkin House', 'NuDisco'])


if __name__ == '__main__':
    unittest.main()
