#!/usr/bin/env python3
"""
Fetch new mixes from artist Mixcloud/SoundCloud pages listed in ARTISTS.md,
diff against what's already tracked under content/, and drop stub files for
anything new under content/uncategorised/, guessing an event/edition folder
structure where possible. Mixes with "demo" in the title/slug are treated as
event-less and filed under content/uncategorised/mixes/ instead, with the
mix's own title and upload date pre-filled.

Usage:
    python3 scripts/fetch_new_mixes.py [--dry-run] [--artist "Name"]
"""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARTISTS_MD = ROOT / "ARTISTS.md"
CONTENT = ROOT / "content"
EVENTS_DIR = CONTENT / "events"
ARTISTS_DIR = CONTENT / "artists"
GENRES_DIR = CONTENT / "genres"
UNCATEGORISED_DIR = CONTENT / "uncategorised"

USER_AGENT = "furrymixes-fetch-new-mixes/1.0 (+https://furrymix.es)"

DEMO_RE = re.compile(r"\bdemo\b", re.IGNORECASE)


def is_demo(mix):
    return bool(DEMO_RE.search(f"{mix['name']} {mix['slug']}"))


def http_get(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read()


def http_get_json(url, headers=None):
    return json.loads(http_get(url, headers))


def slugify(name):
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


# ---------------------------------------------------------------------------
# ARTISTS.md parsing
# ---------------------------------------------------------------------------

def parse_artists_md():
    text = ARTISTS_MD.read_text()
    artists = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        name, platform, profile = cells[0], cells[1], cells[2]
        if name in ("Artist",) or set(name) == {"-"}:
            continue
        if platform not in ("Mixcloud", "SoundCloud"):
            continue
        # profile column may have trailing notes in parens, e.g.
        # "... (posted on Tazzle's account, B2B set)"
        m = re.search(r"https?://\S+", profile)
        if not m:
            continue
        url = m.group(0)
        artists.append({"name": name, "platform": platform, "profile_url": url})
    return artists


# ---------------------------------------------------------------------------
# Already-known embed URLs
# ---------------------------------------------------------------------------

def normalize_url(url):
    url = url.strip().rstrip("/")
    url = re.sub(r"^https?://(www\.)?", "https://", url)
    return url.lower()


def known_embed_urls():
    known = set()
    pattern = re.compile(r'embed:\s*\n\s*url:\s*"([^"]+)"')
    for md in CONTENT.rglob("*.md"):
        try:
            text = md.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for m in pattern.finditer(text):
            known.add(normalize_url(m.group(1)))
    return known


# ---------------------------------------------------------------------------
# Mixcloud
# ---------------------------------------------------------------------------

def fetch_mixcloud_mixes(profile_url):
    m = re.search(r"mixcloud\.com/([^/]+)/?", profile_url)
    if not m:
        raise ValueError(f"Could not parse Mixcloud username from {profile_url}")
    username = m.group(1)
    mixes = []
    url = f"https://api.mixcloud.com/{username}/cloudcasts/?limit=50"
    while url:
        data = http_get_json(url)
        for item in data.get("data", []):
            mixes.append({
                "url": item["url"],
                "name": item.get("name", ""),
                "slug": item.get("slug", ""),
                "tags": [t["name"] for t in item.get("tags", [])],
                "created": item.get("created_time", "")[:10],
                "description": "",
            })
        url = data.get("paging", {}).get("next")
        if url:
            time.sleep(0.3)
    return mixes


# ---------------------------------------------------------------------------
# SoundCloud
# ---------------------------------------------------------------------------

_soundcloud_client_id_cache = None


def get_soundcloud_client_id(profile_url):
    global _soundcloud_client_id_cache
    if _soundcloud_client_id_cache:
        return _soundcloud_client_id_cache

    html = http_get(profile_url).decode("utf-8", errors="ignore")
    script_urls = re.findall(r'https://a-v2\.sndcdn\.com/assets/[^"\']+\.js', html)
    for script_url in script_urls:
        try:
            js = http_get(script_url).decode("utf-8", errors="ignore")
        except urllib.error.URLError:
            continue
        m = re.search(r'client_id:"([a-zA-Z0-9]+)"', js)
        if m:
            _soundcloud_client_id_cache = m.group(1)
            return _soundcloud_client_id_cache
    raise RuntimeError("Could not scrape a SoundCloud client_id")


def fetch_soundcloud_mixes(profile_url):
    client_id = get_soundcloud_client_id(profile_url)
    resolve_url = (
        "https://api-v2.soundcloud.com/resolve"
        f"?url={urllib.request.quote(profile_url, safe='')}&client_id={client_id}"
    )
    user = http_get_json(resolve_url)
    user_id = user["id"]

    mixes = []
    url = (
        f"https://api-v2.soundcloud.com/users/{user_id}/tracks"
        f"?client_id={client_id}&limit=50"
    )
    while url:
        data = http_get_json(url)
        for item in data.get("collection", []):
            tags = []
            if item.get("genre"):
                tags.append(item["genre"])
            if item.get("tag_list"):
                # SoundCloud tag_list is space separated, quoted multi-word tags
                pairs = re.findall(r'"([^"]+)"|(\S+)', item["tag_list"])
                tags += [a or b for a, b in pairs if (a or b)]
            mixes.append({
                "url": item["permalink_url"],
                "name": item.get("title", ""),
                "slug": item.get("permalink", ""),
                "tags": tags,
                "created": (item.get("created_at") or "")[:10],
                "description": (item.get("description") or "").strip(),
            })
        url = data.get("next_href")
        if url:
            url += f"&client_id={client_id}"
            time.sleep(0.3)
    return mixes


# ---------------------------------------------------------------------------
# Artist field resolution
# ---------------------------------------------------------------------------

def known_artist_slugs():
    return {p.name for p in ARTISTS_DIR.iterdir() if p.is_dir()} if ARTISTS_DIR.exists() else set()


def resolve_artist_field(name, mix, artist_slugs):
    # Shared MeowMix / Px Mixcloud account (pixel_p1x3l): split by mix name,
    # keyed off the mix URL rather than the ARTISTS.md name text since that
    # name includes a parenthetical, e.g. "MeowMix (Kittz + Px duo)".
    if "pixel_p1x3l" in mix["url"].lower():
        text = f"{mix['name']} {mix['slug']}".lower()
        target = "meowmix" if "meowmix" in text else "px"
        if target in artist_slugs:
            return target
    slug = slugify(name)
    if slug in artist_slugs:
        return slug
    return name


# ---------------------------------------------------------------------------
# Event / edition guessing
# ---------------------------------------------------------------------------

def event_vocabulary():
    """{event_dir_name: [edition_dir_name, ...]} from content/events/*"""
    vocab = {}
    if not EVENTS_DIR.exists():
        return vocab
    for event_dir in EVENTS_DIR.iterdir():
        if not event_dir.is_dir():
            continue
        editions = [
            p.name for p in event_dir.iterdir()
            if p.is_dir() and (p / "_index.md").exists()
        ]
        vocab[event_dir.name] = editions
    return vocab


def guess_event_edition(mix, vocab):
    text = f"{mix['name']} {mix['slug']}".lower()
    for event_name, editions in vocab.items():
        needle = event_name.replace("-", " ")
        if event_name.lower() in text or needle in text:
            for edition in editions:
                edition_needle = edition.lower().replace("-", " ")
                if edition.lower() in text or edition_needle in text:
                    return event_name, edition
                year_m = re.search(r"(19|20)\d{2}", edition)
                if year_m and year_m.group(0) in text:
                    return event_name, edition
            year_m = re.search(r"(19|20)\d{2}", text)
            if year_m:
                return event_name, year_m.group(0)
            if mix["created"]:
                return event_name, mix["created"][:4]
            return event_name, "unknown-edition"
    return None, None


# ---------------------------------------------------------------------------
# Genre mapping
# ---------------------------------------------------------------------------

def known_genres():
    genres = {}
    if not GENRES_DIR.exists():
        return genres
    for genre_dir in GENRES_DIR.iterdir():
        idx = genre_dir / "_index.md"
        if not idx.exists():
            continue
        m = re.search(r'title:\s*"([^"]+)"', idx.read_text())
        if m:
            genres[m.group(1).lower()] = m.group(1)
    return genres


def map_genres(tags, genre_lookup):
    mapped = []
    seen = set()
    for tag in tags:
        tag = tag.strip()
        if not tag:
            continue
        key = tag.lower()
        value = genre_lookup.get(key, key)
        if value.lower() not in seen:
            seen.add(value.lower())
            mapped.append(value)
    return mapped


# ---------------------------------------------------------------------------
# Stub file writing
# ---------------------------------------------------------------------------

def yaml_list(values):
    return "[" + ", ".join(json.dumps(v) for v in values) + "]"


def build_stub(mix, artist_field, genres, title=""):
    front = (
        "---\n"
        "embed:\n"
        f'  url: "{mix["url"]}"\n'
        f'date: {mix["created"] or ""}\n'
        f'artists: {yaml_list([artist_field])}\n'
        f'genres: {yaml_list(genres)}\n'
        f'title: "{title}"\n'
        "---\n"
    )
    body = f"{mix['description']}\n\n" if mix["description"] else "\n"
    body += "## Tracklist\n1. \n2. \n3. \n"
    return front + body


def target_path(subdir_parts, artist_slug_or_name):
    artist_part = slugify(artist_slug_or_name)
    base = UNCATEGORISED_DIR.joinpath(*subdir_parts)
    path = base / f"{artist_part}.md"
    n = 2
    while path.exists():
        path = base / f"{artist_part}-{n}.md"
        n += 1
    return path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print planned files without writing them")
    parser.add_argument("--artist", help="Only process this artist name (as written in ARTISTS.md)")
    args = parser.parse_args()

    artists = parse_artists_md()
    if args.artist:
        artists = [a for a in artists if a["name"].lower() == args.artist.lower()]
        if not artists:
            print(f"No artist named {args.artist!r} found in ARTISTS.md", file=sys.stderr)
            return 1

    known = known_embed_urls()
    artist_slugs = known_artist_slugs()
    vocab = event_vocabulary()
    genre_lookup = known_genres()

    created = 0
    for artist in artists:
        name, platform, profile_url = artist["name"], artist["platform"], artist["profile_url"]
        print(f"[{platform}] {name}: {profile_url}")
        try:
            if platform == "Mixcloud":
                mixes = fetch_mixcloud_mixes(profile_url)
            else:
                mixes = fetch_soundcloud_mixes(profile_url)
        except (urllib.error.URLError, RuntimeError, ValueError, KeyError) as exc:
            print(f"  ! failed to fetch: {exc}", file=sys.stderr)
            continue

        new_mixes = [m for m in mixes if normalize_url(m["url"]) not in known]
        print(f"  {len(mixes)} mixes found, {len(new_mixes)} new")

        for mix in new_mixes:
            artist_field = resolve_artist_field(name, mix, artist_slugs)
            genres = map_genres(mix["tags"], genre_lookup)

            if is_demo(mix):
                # Demo mixes aren't tied to an event; file them under
                # uncategorised/mixes/ (mirrors content/events/mixes/), with
                # the mix's own title and upload date rather than an event
                # guess.
                path = target_path(["mixes"], artist_field)
                stub = build_stub(mix, artist_field, genres, title=mix["name"])
            else:
                event, edition = guess_event_edition(mix, vocab)
                subdir = [event, edition] if event else ["_unsorted"]
                path = target_path(subdir, artist_field)
                stub = build_stub(mix, artist_field, genres)

            print(f"  -> {path.relative_to(ROOT)}  ({mix['url']})")
            if args.dry_run:
                continue

            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(stub)
            known.add(normalize_url(mix["url"]))
            created += 1

    if not args.dry_run:
        print(f"\nWrote {created} new stub file(s) under content/uncategorised/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
