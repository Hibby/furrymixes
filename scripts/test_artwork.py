"""Offline artwork validation, retry, preservation and integration tests."""
import contextlib
from email.message import Message
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

from PIL import Image

import artwork
import backfill_artwork
import fetch_new_mixes as fetch


def image_bytes(kind='JPEG'):
    stream = io.BytesIO()
    Image.new('RGB', (4, 4), '#214365').save(stream, format=kind)
    return stream.getvalue()


class Response(io.BytesIO):
    def __init__(self, payload, url='https://api.mixcloud.com/a/b/', headers=None):
        super().__init__(payload)
        self.headers = headers or {}
        self.url = url

    def geturl(self):
        return self.url


def http_error(code, retry=None):
    headers = Message()
    if retry is not None:
        headers['Retry-After'] = retry
    return urllib.error.HTTPError('https://api.mixcloud.com/a/b/', code, 'test', headers, None)


class NetworkTests(unittest.TestCase):
    def test_mixcloud_extraction_and_official_show_endpoint(self):
        data = {'pictures': {'extra_large': 'https://images.mixcloud.com/large', 'large': 'https://images.mixcloud.com/small'},
                'user': {'pictures': {'large': 'avatar'}}}
        self.assertEqual(artwork.mixcloud_artwork(data), data['pictures']['extra_large'])
        self.assertIsNone(artwork.mixcloud_artwork({'user': data['user']}))
        self.assertEqual(artwork.mixcloud_api_url('https://www.mixcloud.com/Artist/show/'), 'https://api.mixcloud.com/Artist/show/')
        client = MagicMock()
        client.json.return_value = data
        self.assertEqual(artwork.lookup('https://mixcloud.com/a/b/', client), data['pictures']['extra_large'])
        client.json.assert_called_once_with('https://api.mixcloud.com/a/b/')

    def test_soundcloud_track_artwork_and_oembed_without_avatar_fallback(self):
        self.assertEqual(artwork.soundcloud_artwork({'artwork_url': 'https://i1.sndcdn.com/artworks-x.jpg'}), 'https://i1.sndcdn.com/artworks-x.jpg')
        self.assertIsNone(artwork.soundcloud_artwork({'artwork_url': None, 'user': {'avatar_url': 'avatar.jpg'}}))
        client = MagicMock()
        client.json.return_value = {'thumbnail_url': 'https://i1.sndcdn.com/artworks-cover.jpg'}
        self.assertEqual(artwork.lookup('https://soundcloud.com/a/b', client), client.json.return_value['thumbnail_url'])
        self.assertTrue(client.json.call_args.args[0].startswith('https://soundcloud.com/oembed?'))
        self.assertIsNone(artwork.soundcloud_artwork({'thumbnail_url': 'https://i1.sndcdn.com/avatars-a.jpg'}, oembed=True))

    def test_retry_after_seconds_dates_and_invalid_values(self):
        self.assertEqual(artwork.retry_after('12', now=0), 12)
        self.assertEqual(artwork.retry_after('Thu, 01 Jan 1970 00:01:00 GMT', now=0), 60)
        self.assertIsNone(artwork.retry_after('nonsense'))

    def test_429_cooldown_stops_requests_without_bypassing_limit(self):
        client = artwork.HTTPClient(pace=0)
        with patch.object(artwork.urllib.request, 'urlopen', side_effect=http_error(429, '120')) as opened:
            with self.assertRaises(artwork.RateLimited):
                client.read('https://api.mixcloud.com/a/b/')
            with self.assertRaises(artwork.RateLimited):
                client.read('https://api.mixcloud.com/a/another/')
            self.assertEqual(opened.call_count, 1)
            self.assertGreater(client.cooldowns['api.mixcloud.com'], time.time())

    def test_transient_retries_are_bounded(self):
        client = artwork.HTTPClient(pace=0, attempts=3)
        with patch.object(artwork.urllib.request, 'urlopen', side_effect=[http_error(503), http_error(503), Response(b'{}')]) as opened, patch.object(artwork.time, 'sleep') as sleep:
            self.assertEqual(client.read('https://api.mixcloud.com/a/b/'), b'{}')
            self.assertEqual(opened.call_count, 3)
            self.assertEqual([c.args[0] for c in sleep.call_args_list], [1, 2])
        with patch.object(artwork.urllib.request, 'urlopen', side_effect=http_error(500)) as opened, patch.object(artwork.time, 'sleep'):
            with self.assertRaises(urllib.error.HTTPError):
                client.read('https://api.mixcloud.com/a/b/')
            self.assertEqual(opened.call_count, 3)

    def test_interrupted_http_response_is_retried_with_a_bound(self):
        import http.client
        client = artwork.HTTPClient(pace=0, attempts=2)
        with patch.object(artwork.urllib.request, 'urlopen', side_effect=http.client.IncompleteRead(b'partial')) as opened, patch.object(artwork.time, 'sleep'):
            with self.assertRaises(http.client.IncompleteRead):
                client.read('https://api.mixcloud.com/a/b/')
            self.assertEqual(opened.call_count, 2)

    def test_response_size_is_bounded(self):
        with patch.object(artwork.urllib.request, 'urlopen', return_value=Response(b'x' * 9)):
            with self.assertRaises(ValueError):
                artwork.HTTPClient(pace=0).read('https://api.mixcloud.com/a/b/', max_bytes=8)

    def test_actual_formats_corrupt_and_html_rejection(self):
        for kind, suffix in artwork.FORMATS.items():
            self.assertEqual(artwork.image_format(image_bytes(kind)), suffix)
        for payload in (b'', b'<html>error</html>', b'\xff\xd8\xff\xe0broken', image_bytes('PNG')[:-12], image_bytes()[:-2]):
            with self.assertRaises(ValueError):
                artwork.image_format(payload)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'content/events/event/2026/set.md'
        self.path.parent.mkdir(parents=True)
        self.path.write_text('---\nembed: {url: "https://mixcloud.com/a/b/"}\ngenres: [House]\noriginal_genres: [EDM]\n---\nUnchanged body\n')
        self.client = MagicMock()
        self.client.cooldowns = {}
        self.client.json.return_value = {'pictures': {'large': 'https://cdn.example/image.png'}}
        self.client.read.return_value = image_bytes('JPEG')
        self.store = artwork.CoverStore(self.root, client=self.client)

    def cover(self, extension):
        base = self.store.cover_base(self.path)
        base.parent.mkdir(parents=True, exist_ok=True)
        return base.parent / (base.name + extension)

    def test_download_actual_format_atomic_resume_and_idempotency(self):
        before = self.path.read_bytes()
        self.assertEqual(self.store.ensure(self.path, 'https://mixcloud.com/a/b/')[0], 'downloaded')
        cover = self.cover('.jpg')
        self.assertEqual(cover.read_bytes(), self.client.read.return_value)
        self.assertFalse(self.cover('.png').exists())
        resumed = artwork.CoverStore(self.root, client=self.client)
        self.assertEqual(resumed.ensure(self.path, 'https://mixcloud.com/a/b/')[0], 'existing')
        self.client.json.assert_called_once()
        self.client.read.assert_called_once()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(list(self.root.rglob('*.tmp')))

    def test_reused_metadata_and_duplicate_sources_avoid_calls(self):
        self.store.ensure(self.path, 'https://mixcloud.com/a/b/', artwork_url='https://cdn.example/show.jpg', artwork_known=True)
        self.client.json.assert_not_called()
        other = self.path.with_name('second.md')
        self.store.ensure(other, 'https://mixcloud.com/a/b/', artwork_url='https://cdn.example/show.jpg', artwork_known=True)
        self.client.read.assert_called_once()

    def test_manual_override_and_sidecar_are_preserved(self):
        manual = self.path.with_name('special.png')
        manual.write_bytes(image_bytes('PNG'))
        for refresh in (False, True):
            self.assertEqual(self.store.ensure(self.path, 'https://mixcloud.com/a/b/', data={'artwork': 'special.png'}, refresh=refresh)[0], 'manual')
        self.client.json.assert_not_called()
        self.client.read.assert_not_called()
        self.assertEqual(manual.read_bytes(), image_bytes('PNG'))
        self.assertEqual(self.store.ensure(self.path, 'https://mixcloud.com/a/b/', data={'artwork': 'missing.png'})[0], 'manual_missing')

    def test_missing_cover_cached_until_explicit_retry(self):
        self.client.json.return_value = {}
        self.assertEqual(self.store.ensure(self.path, 'https://mixcloud.com/a/b/')[0], 'unavailable')
        resumed = artwork.CoverStore(self.root, client=self.client)
        self.assertEqual(resumed.ensure(self.path, 'https://mixcloud.com/a/b/')[0], 'unavailable')
        self.assertEqual(self.client.json.call_count, 1)
        self.client.json.return_value = {'pictures': {'large': 'https://cdn.example/cover.jpg'}}
        self.assertEqual(resumed.ensure(self.path, 'https://mixcloud.com/a/b/', retry_unavailable=True)[0], 'downloaded')
        self.assertNotIn('https://mixcloud.com/a/b', resumed.state['unavailable'])

    def test_private_deleted_metadata_is_unavailable(self):
        for code in (401, 403, 404, 410):
            with self.subTest(code=code):
                self.client.json.side_effect = http_error(code)
                status, _ = self.store.ensure(self.path, f'https://mixcloud.com/a/set-{code}/')
                self.assertEqual(status, 'unavailable')

    def test_refresh_failure_preserves_existing_and_success_replaces_format(self):
        cover = self.cover('.jpg')
        cover.write_bytes(image_bytes())
        self.client.read.return_value = b'<html>unavailable</html>'
        self.assertEqual(self.store.ensure(self.path, 'https://mixcloud.com/a/b/', refresh=True)[0], 'temporary_failure')
        self.assertEqual(cover.read_bytes(), image_bytes())
        self.client.read.return_value = image_bytes('PNG')
        self.assertEqual(self.store.ensure(self.path, 'https://mixcloud.com/a/b/', refresh=True)[0], 'downloaded')
        self.assertFalse(cover.exists())
        self.assertTrue(self.cover('.png').exists())

    def test_dry_run_does_not_request_or_write_anything(self):
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(self.store.ensure(self.path, 'https://mixcloud.com/a/b/', dry_run=True)[0], 'planned')
        self.client.json.assert_not_called()
        self.client.read.assert_not_called()
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_safe_filenames_and_path_traversal(self):
        with self.assertRaises(ValueError):
            self.store.ensure(self.root / 'escape.md', 'https://mixcloud.com/a/b/')
        self.assertEqual(self.store.ensure(self.path, 'https://mixcloud.com/a/b/', data={'artwork': '../../../../../../outside.png'})[0], 'temporary_failure')
        self.store.ensure(self.path, 'https://mixcloud.com/a/b/', artwork_url='https://cdn.example/../../evil.php', artwork_known=True)
        self.assertTrue(self.cover('.jpg').exists())
        self.assertFalse((self.root / 'evil.php').exists())

    def test_filename_dots_and_nested_directories_do_not_collide(self):
        dotted = self.path.with_name('set.v2.md')
        self.store.ensure(dotted, 'https://mixcloud.com/a/b/', artwork_url='https://cdn.example/art', artwork_known=True)
        base = self.store.cover_base(dotted)
        self.assertTrue((base.parent / 'set.v2-cover.jpg').is_file())
        self.assertFalse((base.parent / 'set.jpg').exists())
        other = self.root / 'content/events/another/2026/set.v2.md'
        self.store.ensure(other, 'https://mixcloud.com/a/b/', artwork_url='https://cdn.example/art', artwork_known=True)
        self.assertNotEqual(self.store.cover_base(other), base)
        self.assertTrue(self.store.covers(other))

    def test_backfill_preserves_content_and_skips_dispositions(self):
        deferred = self.path.with_name('deferred.md')
        deferred.write_text('---\nembed: {url: "https://www.mixcloud.com/AwkoreWolf/monday-sesh/"}\ngenres: []\n---\nDeferred body\n')
        before = {p: p.read_bytes() for p in (self.path, deferred)}
        with contextlib.redirect_stdout(io.StringIO()):
            first = backfill_artwork.backfill(root=self.root, store=self.store)
            second = backfill_artwork.backfill(root=self.root, store=self.store)
        self.assertEqual(first['eligible'], 1)
        self.assertEqual(first['deferred'], 1)
        self.assertEqual(first['downloaded'], 1)
        self.assertEqual(second['existing'], 1)
        self.assertEqual(before, {p: p.read_bytes() for p in before})


class IngestionArtworkTests(unittest.TestCase):
    def test_mixcloud_details_reuse_api_response_for_artwork(self):
        payload = {'description': 'Description', 'pictures': {'large': 'https://cdn.example/mix.jpg'}, 'sections': []}
        with patch.object(fetch, 'http_get_page_with_backoff', return_value=json.dumps(payload)) as requested:
            result = fetch.fetch_mixcloud_extras('https://www.mixcloud.com/a/b/')
        requested.assert_called_once_with('https://api.mixcloud.com/a/b/')
        self.assertEqual(result['artwork_url'], 'https://cdn.example/mix.jpg')
        self.assertEqual(result['description'], 'Description')
        self.assertTrue(result['artwork_known'])

    def test_imported_mixcloud_and_soundcloud_objects_keep_cover_metadata(self):
        with patch.object(fetch, 'http_get_json', return_value={'data': [{'url': 'https://mixcloud.com/a/b/', 'pictures': {'large': 'https://cdn.example/art.jpg'}}]}):
            self.assertEqual(fetch.fetch_mixcloud_mixes('https://mixcloud.com/a/')[0]['artwork_url'], 'https://cdn.example/art.jpg')
        with patch.object(fetch, 'get_soundcloud_client_id', return_value='test'), patch.object(fetch, 'http_get_json', side_effect=[{'id': 1}, {'collection': [{'permalink_url': 'https://soundcloud.com/a/b', 'artwork_url': 'https://cdn.example/sc.jpg'}]}]):
            result = fetch.fetch_soundcloud_mixes('https://soundcloud.com/a')[0]
            self.assertEqual(result['artwork_url'], 'https://cdn.example/sc.jpg')
            self.assertTrue(result['artwork_known'])


class ImportFailureTests(unittest.TestCase):
    def test_cover_failure_never_discards_an_imported_set(self):
        mix = dict(url='https://soundcloud.com/a/b', name='Set', slug='set', tags=['EDM'],
                   created='2026-01-01', description='Description', artwork_url='https://cdn.example/art.jpg', artwork_known=True)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = MagicMock()
            store.ensure.side_effect = RuntimeError('optional artwork failed')
            with patch.object(fetch, 'ROOT', root), patch.object(fetch, 'UNCATEGORISED_DIR', root / 'content/uncategorised'), \
                 patch.object(fetch, 'parse_artists_md', return_value=[dict(name='A', platform='SoundCloud', profile_url='https://soundcloud.com/a')]), \
                 patch.object(fetch, 'known_embed_urls', return_value=set()), patch.object(fetch, 'known_artist_slugs', return_value=set()), \
                 patch.object(fetch, 'event_vocabulary', return_value={}), patch.object(fetch, 'fetch_soundcloud_mixes', return_value=[mix]), \
                 patch.object(fetch, 'CoverStore', return_value=store), patch.object(fetch.sys, 'argv', ['fetch_new_mixes.py']), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(fetch.main(), 0)
            files = list(root.rglob('*.md'))
            self.assertEqual(len(files), 1)
            self.assertIn('original_genres: ["EDM"]', files[0].read_text())
            self.assertEqual(store.ensure.call_args.kwargs['artwork_url'], mix['artwork_url'])
            self.assertTrue(store.ensure.call_args.kwargs['artwork_known'])

    def test_mixcloud_import_reuses_description_response_and_downloads_only_image(self):
        mix = dict(url='https://www.mixcloud.com/a/b/', name='New set', slug='new-set', tags=['EDM'],
                   created='2026-01-01', description='')
        metadata = {'description': 'Original description', 'pictures': {'large': 'https://cdn.example/art.jpg'}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = MagicMock()
            client.cooldowns = {}
            client.read.return_value = image_bytes('PNG')
            with patch.object(fetch, 'ROOT', root), patch.object(fetch, 'UNCATEGORISED_DIR', root / 'content/uncategorised'), \
                 patch.object(fetch, 'parse_artists_md', return_value=[dict(name='A', platform='Mixcloud', profile_url='https://mixcloud.com/a')]), \
                 patch.object(fetch, 'known_embed_urls', return_value=set()), patch.object(fetch, 'known_artist_slugs', return_value=set()), \
                 patch.object(fetch, 'event_vocabulary', return_value={}), patch.object(fetch, 'fetch_mixcloud_mixes', return_value=[mix]), \
                 patch.object(fetch, 'http_get_page_with_backoff', return_value=json.dumps(metadata)) as requested, \
                 patch.object(fetch, 'HTTP', client), patch.object(fetch.time, 'sleep'), \
                 patch.object(fetch.sys, 'argv', ['fetch_new_mixes.py']), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(fetch.main(), 0)
            requested.assert_called_once_with('https://api.mixcloud.com/a/b/')
            client.json.assert_not_called()
            client.read.assert_called_once_with('https://cdn.example/art.jpg')
            files = list((root / 'assets').rglob('*.png'))
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].read_bytes(), image_bytes('PNG'))
            self.assertEqual(files[0].relative_to(root).as_posix(), 'assets/covers/uncategorised/_unsorted/a-cover.png')
            text = next((root / 'content').rglob('*.md')).read_text()
            self.assertIn('Original description', text)
            self.assertIn('genres: ["House"]', text)
