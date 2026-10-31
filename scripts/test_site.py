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
        self.links, self.frames, self.buttons, self.images, self.regions = [], [], [], [], []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a' and 'href' in attrs:
            self.links.append(attrs['href'])
        if tag == 'iframe':
            self.frames.append(attrs.get('src'))
        if tag == 'button':
            self.buttons.append(attrs)
        if tag == 'img':
            self.images.append(attrs)
        if attrs.get('class') == 'player-region':
            self.regions.append(attrs)


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
                genre_pages = [self.build / 'genres' / slug / 'index.html']
                genre_pages += list((self.build / 'genres' / slug / 'page').glob('*/index.html'))
                links = [link for path in genre_pages for link in HTML(path.read_text()).links]
                self.assertIn(page['permalink'], links, (page['path'], category))
                self.assertIn('/genres/' + slug + '/', parser.links)
        self.assertGreater(checked, 0)

    def test_infinite_sets_preserves_static_fallback_and_is_catalogue_only(self):
        sets = (self.build / 'sets/index.html').read_text()
        self.assertIn('data-infinite-sets', sets)
        self.assertIn('infinite-sets.min.', sets)
        self.assertIn('/sets/page/2/', HTML(sets).links)
        self.assertIn('rel=next', sets)
        self.assertEqual(len(HTML(sets).buttons), 24)
        for path in ['index.html', 'genres/house/index.html', 'artists/kittz/index.html']:
            html = (self.build / path).read_text()
            self.assertNotIn('infinite-sets.min.', html)
            self.assertNotIn('data-infinite-sets', html)

    def test_existing_artist_images_and_credits_are_preserved(self):
        directory = HTML((self.build / 'artists/index.html').read_text())
        for cover in (ROOT / 'content/artists').glob('*/cover.*'):
            artist = cover.parent.name
            html = (self.build / 'artists' / artist / 'index.html').read_text()
            image_url = f'/artists/{artist}/{cover.name}'
            self.assertIn(image_url, [image['src'] for image in HTML(html).images])
            self.assertIn(image_url, [image['src'] for image in directory.images])
            self.assertEqual((self.build / image_url.lstrip('/')).read_bytes(), cover.read_bytes())
            _, data, _ = read_page(cover.parent / '_index.md')
            if data.get('imageCredit'):
                self.assertIn('Image from:', html)
                self.assertIn(data['imageCredit']['text'], html)
                if data['imageCredit'].get('url'):
                    self.assertIn(data['imageCredit']['url'], HTML(html).links)
        kittz = (self.build / 'artists/kittz/index.html').read_text()
        self.assertNotIn('class=artist-image', kittz)
        self.assertNotIn('artist-intro--with-image', kittz)

    def test_lazy_cards_platforms_metadata_and_pagination(self):
        home = HTML((self.build / 'index.html').read_text())
        self.assertEqual(len(home.buttons), 6)
        self.assertFalse(home.frames)
        self.assertTrue(home.images)
        for image in home.images:
            self.assertTrue(image['src'].startswith(('/covers/', '/artists/')))
            self.assertEqual(image['width'], image['height'])
            self.assertEqual(image['loading'], 'lazy')
        platforms = set()
        for path in [self.build / 'sets/index.html', *sorted((self.build / 'sets/page').glob('*/index.html'))]:
            if path.parent.name == '1':  # Hugo's page-one redirect
                continue
            parser = HTML(path.read_text())
            self.assertFalse(parser.frames, str(path))
            self.assertLessEqual(len(parser.buttons), 24)
            region_ids = {region['id'] for region in parser.regions}
            for button in parser.buttons:
                self.assertEqual(button['aria-expanded'], 'false')
                self.assertIn(button['aria-controls'], region_ids)
                self.assertIn('hidden', button)
                src = button['data-player-src']
                if 'mixcloud' in src:
                    platforms.add('Mixcloud')
                    self.assertIn('feed=%2F', src)
                else:
                    platforms.add('SoundCloud')
                    self.assertIn('url=https%3A%2F%2F', src)
                    self.assertIn('auto_play=false', src)
            self.assertTrue(any('/artists/' in link for link in parser.links))
            self.assertTrue(any('/genres/' in link for link in parser.links))
        self.assertEqual(platforms, {'Mixcloud', 'SoundCloud'})

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
