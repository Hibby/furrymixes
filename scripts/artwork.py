"""Local set covers, provider metadata, bounded HTTP and operational negative cache.

Ordinary covers: assets/covers/<content-directory>/<set-stem>-cover.<format>.
No network requests happen during Hugo builds; no image conversion is performed.
"""
from email.utils import parsedate_to_datetime
import io
import http.client
import json
import os
import struct
from pathlib import Path
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings

from PIL import Image, UnidentifiedImageError

from taxonomy import ROOT, source_key

FORMATS = {'JPEG': '.jpg', 'PNG': '.png', 'WEBP': '.webp', 'GIF': '.gif'}
MAX_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 16_000_000
USER_AGENT = 'furrymixes-artwork/1.0 (+https://furrymix.es)'


class Unavailable(Exception):
    """Known absent/deleted/private source; retry only when explicitly requested."""


class RateLimited(Exception):
    pass


def web_url(url):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ('https', 'http') or not parts.hostname or parts.username or parts.password:
        raise ValueError('Expected an HTTP(S) URL without credentials')
    return url


def retry_after(value, now=None):
    now = time.time() if now is None else now
    try:
        return max(0, int(value))
    except (ValueError, TypeError):
        try:
            return max(0, parsedate_to_datetime(value).timestamp() - now)
        except (ValueError, TypeError, OverflowError):
            return None


class HTTPClient:
    """Per-host pacing, bounded retries and persistent Retry-After cooldown support."""
    def __init__(self, pace=0.5, attempts=3, timeout=15, cooldowns=None):
        self.pace, self.attempts, self.timeout = pace, attempts, timeout
        self.cooldowns = cooldowns if cooldowns is not None else {}
        self.last = {}

    def read(self, url, max_bytes=MAX_BYTES, headers=None):
        web_url(url)
        host = urllib.parse.urlsplit(url).hostname
        for attempt in range(self.attempts):
            remaining = self.cooldowns.get(host, 0) - time.time()
            if remaining > 0:
                raise RateLimited(f'{host}: Retry-After cooldown for {remaining:.0f}s')
            delay = self.pace - (time.monotonic() - self.last.get(host, 0))
            if delay > 0:
                time.sleep(delay)
            self.last[host] = time.monotonic()
            request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, **(headers or {})})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    web_url(response.geturl())
                    length = response.headers.get('Content-Length')
                    if length and int(length) > max_bytes:
                        raise ValueError('Response exceeds size limit')
                    payload = response.read(max_bytes + 1)
                    if len(payload) > max_bytes:
                        raise ValueError('Response exceeds size limit')
                    return payload
            except urllib.error.HTTPError as exc:
                exc.close()
                wait = retry_after(exc.headers.get('Retry-After')) if exc.headers else None
                if exc.code in (429, 503) and wait is not None:
                    self.cooldowns[host] = time.time() + wait
                if exc.code == 429:
                    # Stop this host for the run instead of repeatedly hitting a limited service.
                    self.cooldowns[host] = time.time() + (wait if wait is not None else 60)
                    raise RateLimited(f'{host}: HTTP 429; wait {wait if wait is not None else 60:.0f}s') from exc
                if exc.code not in (500, 502, 503, 504) or attempt + 1 == self.attempts:
                    raise
                if wait is not None:
                    raise RateLimited(f'{host}: HTTP {exc.code}; wait {wait:.0f}s') from exc
            except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
                if attempt + 1 == self.attempts:
                    raise
            time.sleep(2 ** attempt)
        raise RuntimeError('HTTP request exhausted retries')

    def json(self, url):
        value = json.loads(self.read(url, max_bytes=2 * 1024 * 1024))
        if not isinstance(value, dict):
            raise ValueError('Expected a metadata object')
        return value


def mixcloud_api_url(url):
    parts = urllib.parse.urlsplit(web_url(url))
    if parts.hostname.removeprefix('www.') != 'mixcloud.com' or len(parts.path.strip('/').split('/')) != 2:
        raise Unavailable('Not an individual Mixcloud show URL')
    return 'https://api.mixcloud.com/' + parts.path.strip('/') + '/'


def mixcloud_artwork(data):
    pictures = data.get('pictures') or {}
    return next((pictures[key] for key in ('extra_large', 'large', '640wx640h', 'medium', 'thumbnail')
                 if isinstance(pictures.get(key), str) and pictures[key]), None)


def soundcloud_artwork(data, oembed=False):
    # Never use user.avatar_url. oEmbed may return an avatar for a track without art.
    url = data.get('thumbnail_url' if oembed else 'artwork_url')
    if not isinstance(url, str) or not url or 'avatars-' in url or 'default_avatar' in url:
        return None
    return url


def lookup(url, client):
    host = urllib.parse.urlsplit(web_url(url)).hostname.removeprefix('www.')
    try:
        if host == 'mixcloud.com':
            return mixcloud_artwork(client.json(mixcloud_api_url(url)))
        if host == 'soundcloud.com':
            endpoint = 'https://soundcloud.com/oembed?format=json&url=' + urllib.parse.quote(url, safe='')
            return soundcloud_artwork(client.json(endpoint), oembed=True)
        raise Unavailable('Unsupported artwork provider')
    except urllib.error.HTTPError as exc:
        exc.close()
        if exc.code in (401, 403, 404, 410):
            raise Unavailable(f'Metadata HTTP {exc.code}') from exc
        raise


def image_format(payload):
    if not payload or len(payload) > MAX_BYTES:
        raise ValueError('Empty image or image exceeds 8 MiB')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(payload)) as image:
                extension = FORMATS.get(image.format)
                if not extension or image.width * image.height > MAX_PIXELS:
                    raise ValueError('Unsupported image format or excessive dimensions')
                if image.format == 'JPEG' and not payload.rstrip(b'\x00\r\n \t').endswith(b'\xff\xd9'):
                    raise ValueError('Incomplete JPEG')
                if image.format == 'PNG' and not payload.endswith(b'\x00\x00\x00\x00IEND\xaeB`\x82'):
                    raise ValueError('Incomplete PNG')
                if image.format == 'WEBP' and (len(payload) < 12 or struct.unpack('<I', payload[4:8])[0] + 8 != len(payload)):
                    raise ValueError('Incomplete WebP')
                if image.format == 'GIF' and not payload.endswith(b';'):
                    raise ValueError('Incomplete GIF')
                image.verify()
            with Image.open(io.BytesIO(payload)) as image:
                # verify() alone does not decode JPEG scan data or all animated frames.
                frames = getattr(image, 'n_frames', 1)
                if frames > 100 or image.width * image.height * frames > 32_000_000:
                    raise ValueError('Animation exceeds validation budget')
                for frame in range(frames):
                    image.seek(frame)
                    image.load()
            return extension
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError('Invalid or corrupt image') from exc


def valid_image(path):
    try:
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            return False
        image_format(path.read_bytes())
        return True
    except (OSError, ValueError):
        return False


def atomic_write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.' + path.name, suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


class CoverStore:
    def __init__(self, root=ROOT, client=None, cache_path=None):
        self.root = Path(root).resolve()
        self.content = self.root / 'content'
        self.cache_path = Path(cache_path) if cache_path else self.root / '.cache/artwork.json'
        self.state = {'unavailable': {}, 'cooldowns': {}}
        if self.cache_path.exists():
            try:
                candidate = json.loads(self.cache_path.read_text())
                if isinstance(candidate.get('unavailable'), dict) and isinstance(candidate.get('cooldowns'), dict):
                    self.state = candidate
            except (OSError, ValueError, AttributeError):
                warnings.warn('Artwork cache unreadable; using local files as checkpoints')
        self.client = client or HTTPClient()
        self.client.cooldowns.update(self.state.get('cooldowns', {}))
        self.state['cooldowns'] = self.client.cooldowns
        self.metadata = {}  # in-run reuse for duplicate recording files; no metadata dumps on disk
        self.images = {}

    def save(self):
        self.state['cooldowns'] = {host: until for host, until in self.client.cooldowns.items() if until > time.time()}
        try:
            atomic_write(self.cache_path, (json.dumps(self.state, indent=2, sort_keys=True) + '\n').encode())
        except OSError as exc:
            warnings.warn(f'Artwork checkpoint not saved: {exc}')

    def safe_set(self, path):
        path = Path(path).resolve()
        if not path.is_relative_to(self.content.resolve()) or path.suffix.lower() not in ('.md', '.markdown', '.html'):
            raise ValueError('Set path must be inside content')
        return path

    def cover_base(self, path):
        path = self.safe_set(path)
        return self.root / 'assets/covers' / path.relative_to(self.content).parent / (path.stem + '-cover')

    def covers(self, path):
        path = self.safe_set(path)
        parent = self.cover_base(path).parent
        if not parent.exists():
            return []
        return sorted(p for p in parent.iterdir() if p.resolve().is_relative_to((self.root / 'assets/covers').resolve()) and p.name.startswith(path.stem + '-cover.') and p.suffix.lower() in set(FORMATS.values()) | {'.jpeg'})

    def manual(self, path, data):
        # An explicit local override always wins, even in refresh mode or when missing.
        name = next((data.get(k) for k in ('artwork', 'cover', 'image') if isinstance(data.get(k), str) and data[k]), None)
        if not name:
            return None
        if '://' in name or '..' in name:
            raise ValueError('Manual artwork must be a local path')
        candidate = (self.root / 'static' / name.lstrip('/')) if name.startswith('/') else (self.root / 'assets' / name if name.startswith('covers/') else path.parent / name)
        candidate = candidate.resolve()
        if not candidate.is_relative_to(self.root):
            raise ValueError('Manual artwork path escapes repository')
        return candidate

    def ensure(self, path, url, data=None, artwork_url=None, artwork_known=False, dry_run=False, refresh=False, retry_unavailable=False):
        path = self.safe_set(path)
        identity = source_key(url)
        try:
            manual = self.manual(path, data or {})
            if manual is not None:
                return ('manual', str(manual)) if valid_image(manual) else ('manual_missing', 'Manual override missing/invalid; not replaced')
            existing = self.covers(path)
            if not refresh and any(valid_image(p) for p in existing):
                return 'existing', 'Valid local cover'
            if not refresh and not retry_unavailable and identity in self.state['unavailable']:
                return 'unavailable', self.state['unavailable'][identity]
            if dry_run:
                return 'planned', 'Would refresh cover' if refresh else 'Would retrieve missing artwork'
            if refresh or (retry_unavailable and identity in self.state['unavailable']):
                self.metadata.pop(identity, None)
            if not artwork_known:
                if identity not in self.metadata:
                    self.metadata[identity] = lookup(url, self.client)
                artwork_url = self.metadata[identity]
            if not artwork_url:
                raise Unavailable('Recording has no artwork')
            if refresh:
                self.images.pop(artwork_url, None)
            if artwork_url not in self.images:
                payload = self.client.read(web_url(artwork_url))
                extension = image_format(payload)
            else:
                cached_path, extension = self.images[artwork_url]
                if cached_path.is_file():
                    payload = cached_path.read_bytes()
                else:
                    payload = self.client.read(web_url(artwork_url))
                    extension = image_format(payload)
            base = self.cover_base(path)
            destination = base.parent / (base.name + extension)
            atomic_write(destination, payload)
            self.images[artwork_url] = destination, extension
            # Remove obsolete formats only after a successful, validated atomic replacement.
            for old in existing:
                if old != destination:
                    old.unlink()
            self.state['unavailable'].pop(identity, None)
            self.save()
            return 'downloaded', destination.relative_to(self.root).as_posix()
        except Unavailable as exc:
            if not dry_run:
                self.state['unavailable'][identity] = str(exc)
                self.save()
            return 'unavailable', str(exc)
        except (OSError, ValueError, RateLimited, urllib.error.URLError, http.client.HTTPException) as exc:
            if not dry_run:
                self.save()
            return 'temporary_failure', str(exc)
