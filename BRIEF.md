# Project brief: Xtream Playlist Trimmer

## Goal

A native desktop app that logs into an Xtream Codes IPTV account, lists the live TV channels by category, lets me tick and untick what I want, and writes a trimmed .m3u playlist that still plays through the same account.

The full playlist is too heavy for low spec clients (mainly a PS Vita). My other devices (an Nvidia Shield) keep using the full list, so this app must never change anything on the provider side. It only reads from the provider and writes a local file.

Live TV only. No movies, no series, no VOD of any kind.

## Platform and stack

- Development and main use: Debian 13 (Trixie) on a Dell OptiPlex 7050.
- Python 3 in a virtual environment (Debian blocks system wide pip installs, so use a venv in the project folder).
- GUI: PySide6 (Qt Widgets). No web UI, no embedded browser, no Electron, no Tauri, no local web server for the interface.
- HTTP: `requests` (or `httpx`), with sensible timeouts and a clear error if the server is unreachable.
- ffprobe (from the Debian `ffmpeg` package) for the optional resolution probe.
- Should also run on Windows from source. Packaging into a standalone binary with PyInstaller comes last; note that a Windows .exe has to be built on Windows.

## Credentials and security

- Never hard code credentials. Never commit them.
- Store server URL, username and password in a local config file under `~/.config/xtream-trimmer/` (or the platform equivalent via Qt's QStandardPaths).
- Add `.gitignore` entries for the config file, caches, generated playlists, the venv and build output from the very first commit.
- The output .m3u necessarily contains the username and password in every URL. Warn about this in the README.

## Xtream API (Player API)

All calls are GET requests to `{server}/player_api.php?username={user}&password={pass}` plus an `action` parameter.

- No action: returns `user_info` and `server_info`. Use it to test the login, show account status/expiry/max connections, and read `allowed_output_formats`.
- `action=get_live_categories`: list of live categories (`category_id`, `category_name`).
- `action=get_live_streams`: all live channels. Optional `category_id` to fetch one category. Fields include `num`, `name`, `stream_id`, `stream_icon`, `epg_channel_id`, `added`, `category_id`.

The provider does not supply resolution data, see the filtering section.

Live stream URL format:

```
{server}/live/{username}/{password}/{stream_id}.{ext}
```

`ext` is `ts` by default. Offer `m3u8` as an option if the account's `allowed_output_formats` includes it.

The server's existing full playlist is at `{server}/get.php?username=...&password=...&type=m3u_plus&output=ts`. Do not download or parse that; use the API instead because it is far lighter.

## Output format

Standard m3u_plus:

```
#EXTM3U
#EXTINF:-1 tvg-id="{epg_channel_id}" tvg-name="{name}" tvg-logo="{stream_icon}" group-title="{category_name}",{name}
{server}/live/{username}/{password}/{stream_id}.ts
```

- Keep categories in the provider's order, channels in the provider's order within each category.
- Escape or strip double quotes inside names so attributes do not break.
- Option to also write an EPG URL in the header line: `#EXTM3U x-tvg-url="{server}/xmltv.php?username=...&password=..."`.
- Write UTF-8 without BOM, LF line endings by default.

## The main window

- Login panel: server URL, username, password, "Test login" button, account status line.
- A tree view: categories as top level items with tri-state tick boxes, channels as children with tick boxes. Ticking a category ticks all its visible channels.
- A search/filter box that filters the tree by channel or category name.
- Resolution filter controls (below).
- A live count: "X channels selected in Y categories".
- "Export playlist" button with a save dialog, remembering the last path.
- Load the tree lazily or efficiently; the full list may be several thousand channels and the UI must stay responsive. Do network calls off the UI thread (QThread or QThreadPool) with a progress indicator.

## Resolution filtering (target: 720p max)

The API has no resolution field, so use two methods.

### 1. Name tag detection (default, fast)

Parse each channel name for quality tags, case insensitive, matched as whole tokens so that "HD" does not match inside "FHD" or "UHD". Check most specific first:

- UHD tier: `4K`, `UHD`, `2160p`
- FHD tier: `FHD`, `Full HD`, `1080p`, `1080i`, `1080`
- HD tier: `HD`, `720p`, `720`
- SD tier: `SD`, `576p`, `576i`, `480p`
- Untagged: none of the above

Also check common decorations like `UK: Channel FHD`, `|UK| Channel HD`, `Channel (FHD)`, `Channel [4K]`.

Default filter state:

- Hide UHD and FHD tiers.
- Show HD, SD tiers.
- Untagged channels: a toggle, shown by default.

Show the detected tier as a column in the tree. Hidden channels are excluded from export even if their category is ticked. Each tier can be toggled on or off by the user.

Note in the UI that "HD" is a provider label and is not guaranteed to be 720p; some HD channels are 1080i.

### 2. Probe (optional, per channel, accurate)

- A "Probe" action on a selected channel (right click menu or button) runs ffprobe against the stream URL with a timeout of about 10 seconds and reads the video stream's width, height and field order.
- Show the result in the tree and cache it to disk, keyed by stream_id.
- Never probe in bulk automatically. Each probe uses one of the account's connections, and mass probing risks tripping the provider's connection limit. If a multi select probe is added, run them one at a time with a short delay, and show a warning with the account's max connections from `user_info`.
- A probed result overrides the name tag result for filtering.

## Profiles

- Save the current selection as a named profile (for example "Vita"), stored as JSON in the config folder.
- Store selected channels by stream_id, plus channel name and category name as a fallback in case the provider renumbers streams.
- On load, report any saved channels that can no longer be matched.
- Option per profile: when new channels appear in a ticked category, either include them automatically or leave them unticked and highlight them as new.
- "Regenerate" button: refresh from the API, apply the profile, export to the profile's last path in one step.

## Caching

- Cache the category and channel lists to disk so reopening the app is instant.
- Show when the cache was last refreshed, with a "Refresh from server" button.

## Project layout (suggested)

```
xtream-trimmer/
  BRIEF.md
  README.md
  requirements.txt
  .gitignore
  xtream_trimmer/
    __main__.py
    api.py          (Xtream API client)
    models.py       (Category, Channel, Profile)
    quality.py      (tag detection and probe)
    m3u.py          (playlist writer)
    storage.py      (config, cache, profiles)
    ui/
      main_window.py
      channel_tree.py
  tests/
```

## Milestones

1. Project skeleton, venv, .gitignore, requirements, README. API client with login test, categories and live streams, tested from the command line against my real account (credentials read from the config file).
2. M3U writer plus unit tests (including names with quotes, commas, non Latin characters). Command line export of a hard coded selection, which I will test on the Vita.
3. PySide6 main window: login panel, tree with tick boxes, search, counts, export.
4. Resolution tag detection with tier column and filter toggles, with unit tests covering the tag patterns.
5. Profiles, caching, regenerate.
6. Per channel ffprobe probe.
7. PyInstaller packaging for Linux.

Stop after each milestone so I can test it before moving on.

## Not wanted

- No movies, series, VOD, catch up or timeshift.
- No web UI of any kind.
- No changes to the provider account or its playlists.
- No bulk automatic stream probing.
- No telemetry or network calls to anywhere except the IPTV server.
