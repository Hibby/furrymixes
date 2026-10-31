#!/usr/bin/env python3
"""Validate the catalogue, or preview/apply canonical metadata updates.

python3 scripts/catalogue.py check
python3 scripts/catalogue.py migrate [--apply]
"""
import argparse
from collections import Counter
import difflib
import json
from pathlib import Path
import re
import sys

import yaml
from yaml.nodes import MappingNode

from taxonomy import ROOT, Taxonomy, source_key


def read_page(path):
    # Keep source bytes/newlines for surgical edits. Do not reserialize YAML or bodies.
    text = path.read_bytes().decode('utf-8')
    match = re.match(r'\A(?:\ufeff)?---\r?\n(.*?)^---[^\S\r\n]*\r?\n?', text, re.S | re.M)
    if not match:
        if re.match(r'\A(?:\ufeff)?(?:---|\+\+\+|\{)', text):
            raise ValueError(f'{path}: only complete YAML front matter is supported')
        return text, None, None
    raw = match.group(1)
    data = yaml.safe_load(raw) or {}
    if not isinstance(data, dict):
        raise ValueError(f'{path}: front matter must be a mapping')
    return text, data, match


def recordings(content):
    for path in sorted(content.rglob('*')):
        if not path.is_file() or path.suffix.lower() not in ('.md', '.markdown', '.html'):
            continue
        text, data, match = read_page(path)
        if data and isinstance(data.get('embed'), dict) and data['embed'].get('url'):
            yield path, text, data, match


def original_labels(data):
    # Existing provenance always wins, including an intentionally empty array.
    genres = data.get('original_genres', data.get('genres', []))
    if isinstance(genres, str) and 'original_genres' not in data:
        genres = [genres]
    if genres is None and 'original_genres' not in data:
        genres = []
    if not isinstance(genres, list) or not all(isinstance(g, str) for g in genres):
        raise ValueError('Genre provenance must be an array of strings')
    return genres


def rewrite(text, data, match, genres, original):
    raw = match.group(1)
    tree = yaml.compose(raw)
    if not isinstance(tree, MappingNode):
        raise ValueError('Front matter must be a YAML mapping')
    edits = []
    found = set()
    newline = '\r\n' if '\r\n' in text else '\n'
    for key, value in tree.value:
        if key.value not in ('genres', 'original_genres'):
            continue
        if key.value in found:
            raise ValueError(f'Duplicate front matter key: {key.value}')
        found.add(key.value)
        if key.value == 'original_genres' or data.get('genres') == genres:
            continue
        start, end = value.start_mark.index, value.end_mark.index
        if start < key.end_mark.index or raw[start:end].lstrip().startswith('&'):
            raise ValueError('Anchored taxonomy fields must be expanded before migration')
        trailing = re.search(r'\s*$', raw[start:end]).group(0)
        replacement = json.dumps(genres, ensure_ascii=False) + trailing
        if start == end:
            replacement = ' ' + replacement
        edits.append((start, end, replacement))
    insert = ''
    if 'genres' not in found:
        insert += 'genres: ' + json.dumps(genres, ensure_ascii=False) + newline
    if 'original_genres' not in found:
        insert += 'original_genres: ' + json.dumps(original, ensure_ascii=False) + newline
    for start, end, value in sorted(edits, reverse=True):
        raw = raw[:start] + value + raw[end:]
    return text[:match.start(1)] + raw + insert + text[match.end(1):]


def inspect(content, taxonomy, migrate=False):
    changes, errors, counts = [], [], Counter()
    all_sources = {}
    for path, text, data, match in recordings(content):
        try:
            original = original_labels(data)
            classification = taxonomy.classify(original, data['embed']['url'])
            status = classification['status']
            scope = 'staging' if path.relative_to(content).parts[0] == 'uncategorised' else 'catalogue'
            counts[scope] += 1
            counts[status] += 1
            all_sources.setdefault(source_key(data['embed']['url']), []).append(classification['genres'])
            if status != 'eligible':
                if scope != 'staging':
                    errors.append(f'{path}: {status} recording must stay in staging')
                continue
            if not migrate:
                if 'original_genres' not in data:
                    errors.append(f'{path}: missing original_genres (run migrate)')
                taxonomy.check_categories(data.get('genres'), allow_empty=scope == 'staging')
            if not classification['genres']:
                if migrate or scope != 'staging':
                    errors.append(f'{path}: no canonical classification; review source tags before publication')
                else:
                    counts['needs_review'] += 1
                continue
            if migrate:
                updated = rewrite(text, data, match, classification['genres'], original)
                if updated != text:
                    changes.append((path, text, updated))
            else:
                if data['genres'] != classification['genres']:
                    errors.append(f'{path}: canonical genres disagree with policy {taxonomy.version}')
        except (ValueError, TypeError, yaml.YAMLError) as exc:
            errors.append(f'{path}: {exc}')
    for url, override in taxonomy.overrides.items():
        if url in all_sources and override.get('reference_source_url'):
            reference = all_sources.get(source_key(override['reference_source_url']), [])
            if not reference or any(set(categories) != set(override['categories']) for categories in reference):
                errors.append('Reference recording disagrees with override: ' + override['reference_source_url'])
    return changes, errors, counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('check', 'migrate'))
    parser.add_argument('--apply', action='store_true', help='Apply previewed canonical metadata changes')
    parser.add_argument('--content', type=Path, default=ROOT / 'content')
    args = parser.parse_args()
    if args.apply and args.command != 'migrate':
        parser.error('--apply requires migrate')
    try:
        if not args.content.is_dir():
            raise ValueError('Content directory does not exist')
        taxonomy = Taxonomy()
        changes, errors, counts = inspect(args.content, taxonomy, args.command == 'migrate')
        if errors:
            print('\n'.join(errors), file=sys.stderr)
            return 1
        for path, before, after in changes:
            if args.apply:
                path.write_bytes(after.encode('utf-8'))
            else:
                print(''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile=str(path), tofile=str(path))), end='')
        print(f'Policy {taxonomy.version}: {dict(counts)}; {len(changes)} changed files' + (' (applied)' if args.apply else ' (read-only)'))
        return 0
    except (ValueError, OSError, yaml.YAMLError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
