# Bonsai

Bonsai trims an Xtream Codes IPTV account's live TV list down to a small `.m3u` playlist for low spec clients, such as a PS Vita. The trimmed playlist still plays through the same account.

Bonsai only **reads** from the provider and writes local files. It never changes anything on the provider side, so other devices can keep using the full list. It handles live TV only: no movies, series or VOD.

> Status: milestones 1–4 of 7 (API client, playlist writer, main window, resolution tags). Next: profiles and caching.

## Security warning

**A generated playlist contains your username and password in every stream URL.** That is how Xtream live URLs work (`/live/{user}/{pass}/{id}.ts`), so it can't be avoided. Treat the `.m3u` file like a password:

- Don't commit it, post it or share it. `.gitignore` already excludes `*.m3u` and `*.m3u8`.
- Be careful where you copy it, for example to shared folders or cloud sync.

Your server, username and password are stored in a local config file, readable only by your user (mode 0600):

| Platform | Location |
| --- | --- |
| Linux | `~/.config/bonsai/config.json` (or `$XDG_CONFIG_HOME/bonsai/`) |
| Windows | `%APPDATA%\bonsai\config.json` |

Credentials are never hard coded and never stored in the project folder.

Bonsai makes no network calls except to your IPTV server, and sends no telemetry.

## Setup (Debian 13)

```bash
sudo apt install python3-venv python3-pip ffmpeg git
cd ~/bonsai
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
```

On Windows, use `py -m venv .venv` and `.venv\Scripts\pip install -r requirements-dev.txt`.

## Configure

```bash
.venv/bin/python -m bonsai init-config
```

Then edit the file it prints the path of:

```json
{
  "server": "http://your.provider.example:8080",
  "username": "your-username",
  "password": "your-password",
  "user_agent": ""
}
```

- `server` takes the address in any of these forms: `host:port`, `http://host:port`, or a pasted `player_api.php` / `get.php` link.
- `user_agent` is optional. Leave it blank unless your provider blocks unknown clients.

## Running the app

```bash
.venv/bin/python -m bonsai
```

1. Fill in the server, username and password, then click **Test login**. A successful login saves them to the config file and loads the channel list.
2. Tick categories or individual channels. Ticking a category ticks only the channels currently shown, so you can filter first (for example "bbc") and then tick a category to take just those channels.
3. Right-click (or press Ctrl+T / Ctrl+U) to tick or untick every selected row at once.
4. Choose the format (`ts`, or `m3u8` if the account allows it) and whether to include the EPG URL, then click **Export playlist…**.

### Resolution filter

The provider doesn't report resolutions, so Bonsai reads quality tags from channel names. A tag only counts as a whole word, so `HD` never matches inside `FHD`, `UHD` or `HDTV`. The tiers, most specific first:

| Tier | Tags |
| --- | --- |
| 4K / UHD | `4K`, `UHD`, `2160p`, `3840p`, `3840` |
| FHD / 1080 | `FHD`, `Full HD`, `1080p`, `1080i`, `1080` |
| HD / 720 | `HD`, `720p`, `720` |
| SD | `SD`, `576p`, `576i`, `480p` |
| Untagged | none of the above |

Tags written in superscript, like `ᴴᴰ` or `ᵁᴴᴰ ³⁸⁴⁰ᴾ`, are read as plain text, and the search box matches them too: typing `raw` finds `ᴿᴬᵂ`. A channel with no tag of its own takes the tier from its category's name. These inherited tiers appear in *italics* in the Tier column.

`8K` is deliberately not treated as a resolution, because providers use it as a brand ("8K EXCLUSIVE").

UHD and FHD are hidden by default, which gives a 720p maximum. Hidden channels are left out of the export even if they're ticked. Their ticks are remembered, so showing the tier again brings them back. Bonsai remembers which tiers you've hidden.

"HD" is only the provider's label, so it doesn't guarantee 720p; some HD channels are really 1080i. A per-channel probe that measures the actual resolution arrives in milestone 6.

Divider rows such as `####### SOCCER PPV #######` are hidden, because they are headings rather than streams.

Ticks are not saved between runs yet. Profiles arrive in milestone 5.

## Command line

```bash
.venv/bin/python -m bonsai login                      # test login, show status, expiry, connections, formats
.venv/bin/python -m bonsai categories                 # live categories in provider order
.venv/bin/python -m bonsai categories --counts        # plus channel count per category (fetches all streams)
.venv/bin/python -m bonsai streams --category 123     # channels in one category
.venv/bin/python -m bonsai streams --limit 0          # every live channel
.venv/bin/python -m bonsai export -o vita.m3u --category 12 --category 40   # whole categories
```

`export` also takes `--ext m3u8` and `--epg`.

## Tests

```bash
.venv/bin/python -m pytest
```

The tests don't touch the network or your real config.
