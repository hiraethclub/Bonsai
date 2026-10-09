# Bonsai

Bonsai trims an Xtream Codes IPTV account's live TV list down to a small `.m3u` playlist for low spec clients, such as a PS Vita. The trimmed playlist still plays through the same account.

Bonsai only **reads** from the provider and writes local files. It never changes anything on the provider side, so other devices can keep using the full list. It handles live TV only: no movies, series or VOD.

> Status: milestones 1–5 of 7 (API client, playlist writer, main window, filters, profiles and caching). Next: the per-channel ffprobe probe.

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

### Content and region filters

These work the same way as the resolution tiers. A ticked box means shown. Hidden channels keep their ticks but are left out of the view and the export. A channel gets a label if its own name or its category's name carries it. The Labels column shows what each channel matched.

| Filter | Matches | Default |
| --- | --- | --- |
| PPV / events | `PPV`, "Team vs Team" event slots, "NO EVENT STREAMING" placeholders | hidden |
| RAW | `RAW`: passed through without re-encoding, often 1080i or high bitrate | shown |
| HEVC / H.265 | `hevc`, `h265`, `x265` (the PS Vita can't decode HEVC) | hidden |
| 50/60 fps | `50fps`, `60fps` | shown |
| 24/7 | `24/7` looping channels | shown |
| Radio | `radio`, `radyo`, `FM` | hidden |
| Adult | the provider's `is_adult` flag, `XXX`, `adult` | hidden |
| VIP / Gold / backup | `VIP`, `GOLD`, `(BK)` alternative or backup feeds | shown |
| No TV guide | channels without an EPG id; untick to keep only guided channels | shown |

**Regions** come from category prefixes such as `US|` or `UK:`. Use the Regions drop-down to untick regions you never watch. Categories without a prefix are grouped as "Other".

Divider rows such as `####### SOCCER PPV #######` are hidden, because they are headings rather than streams.

### Profiles

A profile, such as "Vita", saves your ticks, filters, format, EPG option and playlist path. Use **Save as…** to create one and **Save** to update it. A `*` in the title bar means unsaved changes, and Bonsai asks before discarding them. The last profile you used opens automatically.

- Each channel is stored by stream id, with its channel and category names as a fallback. If the provider renumbers its streams, channels are found again by name. Any that can't be found at all are listed when the profile loads.
- **New channels** in a category you ticked from are highlighted in green until you save the profile again. With **New channels: Tick automatically**, new channels in categories that were *fully* ticked when you saved are ticked for you. In partly ticked categories they're only ever highlighted.
- **Regenerate** downloads the latest list, applies the profile and writes the playlist to the profile's path, all in one step.

Profiles are JSON files in `~/.config/bonsai/profiles/`.

### Saved channel list

The channel list is cached in `~/.cache/bonsai/` (`%LOCALAPPDATA%\bonsai\cache` on Windows), so the app opens without downloading anything. The Account panel shows when the list was fetched. **Refresh from server** fetches it again. The cache holds channel names and ids only, with no credentials or stream URLs.

## Command line

```bash
.venv/bin/python -m bonsai login                      # test login, show status, expiry, connections, formats
.venv/bin/python -m bonsai categories                 # live categories in provider order
.venv/bin/python -m bonsai categories --counts        # plus channel count per category (fetches all streams)
.venv/bin/python -m bonsai streams --category 123     # channels in one category
.venv/bin/python -m bonsai streams --limit 0          # every live channel
.venv/bin/python -m bonsai export -o vita.m3u --category 12 --category 40   # whole categories
.venv/bin/python -m bonsai profiles                   # list saved profiles
.venv/bin/python -m bonsai regenerate Vita            # same as the Regenerate button
```

`regenerate` works well as a scheduled job (cron or a systemd timer) that keeps the Vita playlist up to date.

`export` also takes `--ext m3u8` and `--epg`.

## Tests

```bash
.venv/bin/python -m pytest
```

The tests don't touch the network or your real config.
