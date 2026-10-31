"""Shared operational genre policy. Exact labels only; source tags are never edited."""
import json
from pathlib import Path
import unicodedata
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
POLICY = ROOT / 'data/genre_taxonomy.json'


def normalise(label):
    return ' '.join(unicodedata.normalize('NFKC', label).casefold().split())


def source_key(url):
    parts = urlsplit(url.strip())
    if parts.scheme not in ('https', 'http') or not parts.hostname:
        raise ValueError(f'Invalid recording URL: {url}')
    return 'https://' + parts.hostname.lower().removeprefix('www.') + parts.path.rstrip('/')


class Taxonomy:
    def __init__(self, path=POLICY):
        self.config = json.loads(Path(path).read_text(encoding='utf-8'))
        self.version = self.config['version']
        self.categories = self.config['categories']
        self.lookup = {}
        if len(self.categories) != 12 or set(self.categories) != set(self.config['mappings']):
            raise ValueError('Policy must define exactly 12 categories and their mappings')
        if len(set(self.categories.values())) != 12:
            raise ValueError('Canonical genre slugs must be unique')
        for category, labels in self.config['mappings'].items():
            for label in labels:
                self.lookup.setdefault(normalise(label), set()).add(category)
        alias_keys = set()
        for label, target in self.config['aliases'].items():
            key = normalise(label)
            if key in self.lookup or key in alias_keys or normalise(target) not in self.lookup:
                raise ValueError(f'Invalid or duplicate alias: {label}')
            alias_keys.add(key)
        for label, target in self.config['aliases'].items():
            self.lookup[normalise(label)] = set(self.lookup[normalise(target)])
        self.metadata = {normalise(g) for g in self.config['metadata']}
        self.contextual = {normalise(g) for g in self.config['contextual']}
        if set(self.lookup) & (self.metadata | self.contextual) or self.metadata & self.contextual:
            raise ValueError('Mapped, descriptive and contextual label states must be disjoint')
        self.overrides = self._sources('overrides')
        self.recordings = self._sources('recordings')
        if self.overrides.keys() & self.recordings.keys():
            raise ValueError('A recording cannot be both overridden and deferred/excluded')
        for override in self.overrides.values():
            if override['mode'] not in ('add', 'replace') or not override.get('reason'):
                raise ValueError('Override requires add/replace mode and rationale')
            self.check_categories(override['categories'])
        for recording in self.recordings.values():
            if recording['status'] not in ('deferred', 'excluded') or not recording.get('note'):
                raise ValueError('Recording disposition requires deferred/excluded status and notes')

    def _sources(self, key):
        result = {}
        for url, value in self.config[key].items():
            identity = source_key(url)
            if identity in result:
                raise ValueError(f'Duplicate recording URL in {key}: {url}')
            result[identity] = value
        return result

    def check_categories(self, genres, allow_empty=False):
        if not isinstance(genres, list) or any(not isinstance(g, str) or g not in self.categories for g in genres):
            raise ValueError(f'Non-canonical genre array: {genres!r}')
        if len(genres) != len(set(genres)) or (not genres and not allow_empty):
            raise ValueError(f'Empty or duplicate genre array: {genres!r}')

    def classify(self, original, url):
        if not isinstance(original, list) or not all(isinstance(g, str) for g in original):
            raise ValueError('original_genres must be an array of strings')
        assigned = {c for label in original for c in self.lookup.get(normalise(label), ())}
        identity = source_key(url)
        override = self.overrides.get(identity)
        if override:
            assigned = assigned | set(override['categories']) if override['mode'] == 'add' else set(override['categories'])
        return dict(genres=[c for c in self.categories if c in assigned],
                    unresolved=[g for g in original if normalise(g) not in self.lookup and normalise(g) not in self.metadata],
                    status=self.recordings.get(identity, {}).get('status', 'eligible'))
