# Furrymix

The bundled Hugo theme for furrymix.es. It provides responsive light/dark layouts,
recording cards, expandable Mixcloud and SoundCloud players, event editions,
artist and genre directories, RSS feeds, and progressive loading on `/sets/`.
Requires Hugo 0.162 or newer; no Node, Sass, or remote build requests are needed.

## Use and development

Set `theme = "furrymix"` in the site's Hugo configuration. This directory is
tracked directly in the site repository, rather than as a Git submodule.
Run `hugo server` from the repository root to preview changes.

- `layouts/` contains page templates, partials, feeds, and robots.txt.
- `assets/css/site.css` contains styles and colour variables.
- `assets/js/players.js` manages expandable players.
- `assets/js/infinite-sets.js` enhances the paginated set catalogue.
- `archetypes/` contains default page and event recording stubs.

The site retains ownership of content, cover images under `assets/covers/`,
taxonomy data, importer scripts, and editorial validation. Site layouts override
theme layouts using Hugo's normal lookup order. In this repository,
`layouts/partials/custom_head.html` adds site analytics and fediverse metadata;
`layouts/genres/terms.html` checks the approved genre policy before rendering.
The theme's own genre directory does not require that policy file.

## Content contract

Configure `artist = "artists"`, `genre = "genres"`, and `year = "year"` under
`[taxonomies]`. Configure the main menu, title, description, copyright, and author
in the site configuration. The theme expects:

- Recordings at `content/events/<event>/<edition>/<recording>.md`, with a date,
  artist and genre arrays, and `embed.url` linking to Mixcloud or SoundCloud.
  Optional `embed.height` sets the player height.
- Event and edition descriptions in `_index.md` files in their respective folders.
- Artist descriptions at `content/artists/<slug>/_index.md`, with optional
  `cover.*`, `imageCredit`, and `socials` entries containing `platform` and `url`.
- Genre descriptions at `content/genres/<genre>/_index.md` and a catalogue page
  at `content/sets/_index.md`.
- Optional recording artwork under `assets/covers/`, mirroring its content path
  with a `-cover` suffix; `artwork`, `cover`, or `image` can override discovery.

See the repository README and existing content for complete examples and the
site's classification rules. The homepage copy, footer links, and recording
terminology currently retain Furrymix branding. Override `layouts/index.html`
and `layouts/_default/baseof.html` in another site to customise those defaults.

## Validation and provenance

From the repository root:

```sh
python3 scripts/catalogue.py check
python3 -m unittest discover -s scripts -p 'test_*.py'
hugo --gc --minify
```

Furrymix grew from Hugo Bear Blog by Jan Raasch. Its upstream MIT notice is
preserved in `LICENSE.bearblog` for inherited material. That notice does not
declare a new license for the site's original code, artwork, or recordings.
