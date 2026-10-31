"""Local cover publication and future template helpers; no network or interface changes."""
from html.parser import HTMLParser
import io
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from PIL import Image

from taxonomy import ROOT


class Attributes(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.cover = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get('id') == 'cover':
            self.cover = attrs


@unittest.skipUnless(shutil.which('hugo'), 'Install Hugo for artwork integration tests')
class ArtworkSiteTests(unittest.TestCase):
    def test_nested_sidecars_manual_override_bundle_and_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            content, layouts, destination = root / 'content', root / 'layouts', root / 'public'
            shutil.copytree(ROOT / 'content/genres', content / 'genres')
            shutil.copytree(ROOT / 'layouts', layouts)
            template = '''{{ define "main" }}
{{ $cover := partial "set-artwork.html" . }}
<a id="cover" href="{{ $cover.url }}" data-ready="{{ $cover.available }}" data-initials="{{ $cover.initials }}">Art</a>
{{ if not $cover.available }}{{ partial "set-artwork-fallback.html" $cover }}{{ end }}
{{ partial "player.html" . }}
{{ end }}'''
            (layouts / 'events/single.html').write_text(template)
            parent = content / 'events/test/2026'
            covers = root / 'assets/covers/events/test/2026'
            covers.mkdir(parents=True)
            parent.mkdir(parents=True)
            for name, extra in [('normal', ''), ('manual', 'artwork: covers/events/test/2026/special.png\n'), ('missing', ''), ('missing-manual', 'artwork: absent.jpg\n')]:
                (parent / f'{name}.md').write_text('---\ndate: 2000-01-01\nartists: [Test Artist]\ngenres: [House]\nembed: {url: "https://soundcloud.com/a/b"}\n' + extra + '---\nBody\n')
            stream = io.BytesIO()
            Image.new('RGB', (4, 4), '#234567').save(stream, format='PNG')
            payload = stream.getvalue()
            private = root / 'assets/covers/uncategorised/private-cover.png'
            private.parent.mkdir(parents=True)
            private.write_bytes(payload)
            (covers / 'normal-cover.png').write_bytes(payload)
            (covers / 'special.png').write_bytes(payload)
            # A different automatic image must not override an explicit missing manual choice.
            (covers / 'missing-manual-cover.png').write_bytes(payload)
            bundle = parent / 'bundle'
            bundle.mkdir()
            (bundle / 'index.md').write_text((parent / 'normal.md').read_text())
            (covers / 'bundle').mkdir()
            (covers / 'bundle/index-cover.png').write_bytes(payload)
            config = root / 'artwork.toml'
            config.write_text('assetDir = ' + repr(str(root / 'assets')) + '\n')
            subprocess.run(['hugo', '--config', str(ROOT / 'hugo.toml') + ',' + str(config), '--contentDir', str(content), '--layoutDir', str(layouts),
                            '--destination', str(destination), '--cacheDir', str(root / 'cache'), '--minify'],
                           cwd=ROOT, check=True, capture_output=True, text=True)
            self.assertFalse((destination / 'covers/uncategorised').exists())
            for name, expected in [('normal', 'normal-cover.png'), ('manual', 'special.png'), ('bundle', 'bundle/index-cover.png')]:
                html = (destination / f'events/test/2026/{name}/index.html').read_text()
                values = Attributes(html).cover
                self.assertEqual(values['data-ready'], 'true', html)
                self.assertEqual(values['href'], '/covers/events/test/2026/' + expected)
                self.assertEqual((destination / values['href'].lstrip('/')).read_bytes(), payload)
            for name in ('missing', 'missing-manual'):
                html = (destination / f'events/test/2026/{name}/index.html').read_text()
                values = Attributes(html).cover
                self.assertEqual(values['data-ready'], 'false')
                self.assertEqual(values['data-initials'], 'TA')
                self.assertIn('Cover artwork unavailable', html)
                self.assertIn('aspect-ratio:1', html)
