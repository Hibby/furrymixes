#!/usr/bin/env python3
"""Download missing per-set covers without editing content or publication state."""
import argparse
from collections import Counter
from pathlib import Path

from artwork import CoverStore
from catalogue import recordings, original_labels
from taxonomy import ROOT, Taxonomy


def backfill(root=ROOT, dry_run=False, refresh=False, retry_unavailable=False, scope='all', limit=None, store=None):
    taxonomy = Taxonomy()
    store = store or CoverStore(root)
    counts = Counter()
    selected = []
    for path, _, data, _ in recordings(Path(root) / 'content'):
        stage = path.relative_to(Path(root) / 'content').parts[0] == 'uncategorised'
        if scope != 'all' and ('staging' if stage else 'catalogue') != scope:
            continue
        result = taxonomy.classify(original_labels(data), data['embed']['url'])
        if result['status'] != 'eligible':
            counts[result['status']] += 1
            continue
        selected.append((path, data, stage))
    if limit is not None:
        selected = selected[:limit]
    counts['eligible'] = len(selected)
    for index, (path, data, stage) in enumerate(selected, 1):
        status, note = store.ensure(path, data['embed']['url'], data=data, dry_run=dry_run,
                                    refresh=refresh, retry_unavailable=retry_unavailable)
        counts[status] += 1
        if status in ('existing', 'downloaded', 'manual'):
            counts['staging_with_artwork' if stage else 'catalogue_with_artwork'] += 1
        print(f'{index}/{len(selected)} {status}: {path.relative_to(root)} — {note}', flush=True)
    print('Artwork summary: ' + str(dict(counts)), flush=True)
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--refresh', action='store_true', help='Replace automatic sidecars; preserve explicit manual overrides')
    parser.add_argument('--retry-unavailable', action='store_true', help='Retry cached missing/deleted/private sources')
    parser.add_argument('--scope', choices=('all', 'catalogue', 'staging'), default='all')
    parser.add_argument('--limit', type=int, help='Process at most this many eligible files')
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error('--limit must be positive')
    counts = backfill(**vars(args))
    return 1 if counts['temporary_failure'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
