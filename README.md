# Furrymix.es

Furrymix.es is a website holding links to sets from internet animal people for
you to listen to and enjoy whenever. 

# Artists included

See [ARTISTS.md](ARTISTS.md) for the list of artists and their mix/soundcloud.

# Contribute

Have I missed your set? Want to add some stuff? Need something in your artist
profile? Want to correct a genre?

## Submitting On Easy Mode

If you wish to contribute a set, artist page, genre description etc [Open an issue](https://github.com/hibby/furrymixes/issues/new).

### Sets
Helpful information for adding a set:

1. [Open an issue](https://github.com/hibby/furrymixes/issues/new) or send a telegram message / mastodon message with the link and details.
2. Add the set link on Mixcloud or SoundCloud.
3. Note the event, edition/date, artist, and genre(s).

### Events
Helpful information for adding an event:

1. [Open an issue](https://github.com/hibby/furrymixes/issues/new) or send a telegram message / mastodon message with the link and details.
2. Give the event a description, date, location etc
3. If there are multiple editions, note the name and date of the specific event, line up, etc

### Artists
Helpful information for adding an artist page:

1. [Open an issue](https://github.com/hibby/furrymixes/issues/new) or send a telegram message / mastodon message with the link and details.
2. Give me a description/overview, social links, websites etc


## Pull Request Submissions

If you're comfortable with github, pull requests are welcome!
### Sets
The template for a set is contained in [archetypes/events.md](archetypes/events.md).

Sets go in content/{event}/{edition}/{artist}.md

### Events
Events are a folder under [content/events](content/events).
Event description lives in content/events/{event}/_index.md

An instance of an event, say Confuzzled 2024 and it's description, would be content/events/confuzzled/2024/_index.md

### Artists
Artists are contained in [content/artists](content/artists).
Each artists has a folder containing an _index.md stub which contains socials, links and a description. Cover images are supported and will be added in due course.

### Genres
The 12 canonical genre pages are contained in [content/genres](content/genres).
Each genre has a folder containing an _index.md stub which contains a description and links.

Inspired by https://www.c3sets.de/

## Genre classification

Visitor-facing genres are House; Techno; Trance; Drum & Bass; UK Garage & Bass;
Dubstep & Bass; Hard Dance; Disco & Funk; Breaks & Electronic; Pop & Open Format;
Chill & Ambient; and Alternative & Other.

The approved policy is [data/genre_taxonomy.json](data/genre_taxonomy.json), version
1.2.0. It is shared by the importer and catalogue tooling. Edit its `mappings` to
assign exact source labels, `aliases` for equivalent labels, and `metadata` or
`contextual` for labels that should not independently create categories. Matching
ignores case and whitespace differences; it never matches substrings or guesses
unknown styles. EDM intentionally maps to House; Dance & EDM does not.

Each eligible set has two independent arrays:

```yaml
genres: [House, Disco & Funk]
original_genres: [EDM, Funkin House, NuDisco]
```

`original_genres` preserves labels exactly as received, including case, order and
repetitions. `genres` contains only deduplicated canonical categories. Several
categories may apply; supported classifications are never truncated to three.

Use `overrides` for recording-specific decisions, keyed by stable source URL.
An `add` override augments supported categories; `replace` explicitly replaces
them. Include a short rationale and increment the policy version when approving
new decisions. A reference recording can be specified to check classification
consistency. URL matching ignores scheme, `www.`, query strings and trailing slash.

The `recordings` entries retain seven temporarily deferred recordings and four
excluded individual tracks. Deferred entries preserve the owner's event-setup
notes. Keep them in local staging until ready; remove or update the disposition
and approve classification before bringing them into the archive.

### Import and validate

Use Python 3.11+ and Hugo 0.162 or newer, with the theme submodule checked out:

```sh
git submodule update --init --recursive
python3 -m pip install -r scripts/requirements.txt
python3 scripts/fetch_new_mixes.py --dry-run
python3 scripts/fetch_new_mixes.py
python3 scripts/catalogue.py check
python3 -m unittest discover -s scripts -p 'test_*.py'
hugo --gc --minify
```

The importer retains Mixcloud/SoundCloud tags before classification and writes
new stubs only under `content/uncategorised/`. Dry-run displays canonical genres
and unresolved labels. A useful classification is kept even if extra labels are
unknown. If none can be established, the stub has `genres: []` and
`genre_review_required: true`; it stays in staging for editorial review. The
catalogue check reports these pending imports and rejects unclassified public
sets or arbitrary tags in `genres`. Deferred/excluded sources are skipped by
the importer.

`content/uncategorised/` is deliberately Git-ignored **and** excluded by Hugo.
Local staging updates are never force-added to commits. A fresh checkout will
not contain another maintainer's staging files; local counts may therefore differ.

For manually added legacy metadata, a partially classified set, or an approved
policy change, preview and then apply the shared classifier:

```sh
python3 scripts/catalogue.py migrate
python3 scripts/catalogue.py migrate --apply
python3 scripts/catalogue.py check
```

The update command changes only genre fields, preserving other front matter and
Markdown. Existing `original_genres` always wins, including an empty array.
Updates are idempotent; deferred/excluded files remain untouched. Review changes
before committing. Only publish a set after review, moving it from local staging
to the appropriate `content/events/` directory and validating again.

### Genre pages and feeds

Canonical URLs are explicitly defined by each term page's `url` field and checked
against the policy's category slugs. Term directories with spaces or ampersands
match their literal taxonomy names; do not rename them to guessed slugs. The
index lists exactly 12 categories and multi-category sets appear in each family.
Original labels are provenance, not a Hugo taxonomy. Keep genre descriptions in
the term pages, separate from classification rules.

The site retains its overall, event, artist and new canonical genre RSS feeds.
Retired genre URLs and their RSS endpoints are intentionally not redirected;
old genre subscribers must choose a new canonical feed. Set URLs are unchanged.

GitHub Actions runs catalogue validation and the production tests before building
for GitHub Pages. Git history preserves the pre-migration policy and catalogue;
normal operation has no dependency on audit reports or source snapshots.
