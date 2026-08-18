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
Artists are contained in [content/genres](content/genres).
Each genre has a folder containing an _index.md stub which contains a description and links.

Inspired by https://www.c3sets.de/
