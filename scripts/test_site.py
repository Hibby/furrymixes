"""Production Hugo checks; skip only if Hugo is not installed."""
import csv
from html.parser import HTMLParser
import io
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET

from catalogue import inspect, read_page
from taxonomy import ROOT, Taxonomy


class HTML(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.links, self.frames = [], []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a' and 'href' in attrs:
            self.links.append(attrs['href'])
        if tag == 'iframe':
            self.frames.append(attrs.get('src'))


@unittest.skipUnless(shutil.which('hugo'), 'Install Hugo to run site integration tests')
class SiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.build = Path(cls.temp.name) / 'site'
        cls.taxonomy = Taxonomy()
        cls.run_hugo('--destination', str(cls.build), '--cacheDir', str(Path(cls.temp.name) / 'cache'), '--minify')
        published = subprocess.run(['hugo', 'list', 'published'], cwd=ROOT, text=True, capture_output=True, check=True)
        cls.pages = list(csv.DictReader(io.StringIO(published.stdout)))

    @classmethod
    def run_hugo(cls, *args, check=True):
        return subprocess.run(['hugo', *args], cwd=ROOT, text=True, capture_output=True, check=check)

    def file_for(self, permalink):
        return self.build / unquote(urlsplit(permalink).path).lstrip('/') / 'index.html'

    def test_exact_canonical_genres_and_predictable_urls(self):
        directories = {p.name for p in (self.build / 'genres').iterdir() if p.is_dir()}
        self.assertEqual(directories, set(self.taxonomy.categories.values()))
        index = (self.build / 'genres/index.html').read_text()
        self.assertIn('class=card-grid', index)
        self.assertNotIn('<div class=page-header', index)
        links = HTML(index).links
        genres = {urlsplit(link).path for link in links if urlsplit(link).path.startswith('/genres/') and urlsplit(link).path != '/genres/'}
        self.assertEqual(genres, {'/genres/' + slug + '/' for slug in directories})
        self.assertFalse((self.build / 'genres/edm/index.html').exists())
        self.assertFalse((self.build / 'genres/tech-house/index.xml').exists())

    def test_catalogue_valid_and_sets_in_each_category_with_embeds(self):
        changes, errors, _ = inspect(ROOT / 'content', self.taxonomy)
        self.assertFalse(errors)
        self.assertFalse(changes)
        changes, errors, _ = inspect(ROOT / 'content', self.taxonomy, True)
        self.assertFalse(changes, 'Metadata update must be idempotent')
        self.assertFalse(errors)
        checked = 0
        for page in self.pages:
            if page['kind'] != 'page' or page['section'] != 'events':
                continue
            _, data, _ = read_page(ROOT / page['path'])
            if not data.get('embed', {}).get('url'):
                continue
            checked += 1
            html = self.file_for(page['permalink']).read_text()
            parser = HTML(html)
            if any(platform in data['embed']['url'] for platform in ('mixcloud.com', 'soundcloud.com')):
                self.assertTrue(parser.frames, page['path'])
            self.taxonomy.check_categories(data['genres'])
            self.assertIsInstance(data['original_genres'], list)
            for category in data['genres']:
                slug = self.taxonomy.categories[category]
                genre = HTML((self.build / 'genres' / slug / 'index.html').read_text())
                self.assertIn(page['permalink'], genre.links, (page['path'], category))
                self.assertIn('/genres/' + slug + '/', parser.links)
        self.assertGreater(checked, 0)

    def test_feeds_events_artists_and_staging_isolation(self):
        self.assertTrue((self.build / 'events/index.html').exists())
        self.assertTrue((self.build / 'artists/index.html').exists())
        self.assertFalse((self.build / 'uncategorised').exists())
        self.assertFalse((self.build / 'covers/uncategorised').exists())
        for path in self.build.rglob('*.xml'):
            root = ET.parse(path).getroot()
            for element in root.iter():
                if element.tag.endswith(('loc', 'link', 'guid')) and element.text:
                    self.assertNotIn('/uncategorised/', element.text)
        for slug in self.taxonomy.categories.values():
            ET.parse(self.build / 'genres' / slug / 'index.xml')
        ET.parse(self.build / 'index.xml')

    def test_ignored_staging_cannot_create_taxonomy_and_public_unknown_fails_build(self):
        content = Path(self.temp.name) / 'fixture'
        shutil.copytree(ROOT / 'content/genres', content / 'genres', dirs_exist_ok=True)
        stage = content / 'uncategorised/fixture.md'
        stage.parent.mkdir(parents=True)
        stage.write_text('---\nembed: {url: "https://example.com/private"}\ngenres: [Forbidden]\n---\nPrivate\n')
        destination = Path(self.temp.name) / 'fixture-site'
        self.run_hugo('--contentDir', str(content), '--destination', str(destination), '--cacheDir', str(Path(self.temp.name) / 'fixture-cache'))
        self.assertFalse((destination / 'uncategorised').exists())
        self.assertFalse((destination / 'genres/forbidden').exists())
        public = content / 'events/fixture.md'
        public.parent.mkdir(parents=True)
        public.write_text(stage.read_text())
        result = self.run_hugo('--contentDir', str(content), '--destination', str(destination), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Unapproved public genre', result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
