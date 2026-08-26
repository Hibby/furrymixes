---
# Leave blank to auto-generate "Artist — Live at Event (Date)".
# Only set this if the set needs a custom name (themed set, B2B, etc).
title: ""

# Required. The actual date the set was played/recorded, not upload date.
date: 2026-06-06T21:00:53+01:00

# Required. Must match the slug of an existing content/artists/<slug>/_index.md
# if you want the artist's proper name + bio + socials to resolve correctly.
# Multiple artists for a B2B: artists: ["dj-name", "meowmix"]
artists: ["Tazzle","Mallard"]

# Required. Match existing pinned genre pages where possible (check
# content/genres/ for what's already there) so terms merge rather than
# splinter into near-duplicates (e.g. "Mash Up" vs "mash ups").
genres: ["trance","uk hard house", "bounce house", "donk"]

# Required for the player to render. platform is either "mixcloud" or "soundcloud".
# url is the full page URL either way, e.g.
#   https://www.mixcloud.com/pixel_p1x3l/meowmix-infurno-25/
#   https://soundcloud.com/artist/set-name
# height is optional (defaults: mixcloud 400, soundcloud 166).
embed:
  url: "https://www.mixcloud.com/DamnTazzle/taz-b2b-mallard-trance-bounce-n-hard-house-live-ftr5/"
  height: # for soundcloud only
---

