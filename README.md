# Bonsai

Bonsai trims an Xtream Codes IPTV account's live TV list down to a small `.m3u` playlist for low spec clients, such as a PS Vita. The trimmed playlist still plays through the same account.

Bonsai only **reads** from the provider and writes local files. It never changes anything on the provider side, so other devices can keep using the full list. It handles live TV only: no movies, series or VOD.

> Status: milestone 1 of 7 (API client and command line). The GUI comes in milestone 3.

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

## Command line (milestone 1)

```bash
.venv/bin/python -m bonsai login                      # test login, show status, expiry, connections, formats
.venv/bin/python -m bonsai categories                 # live categories in provider order
.venv/bin/python -m bonsai categories --counts        # plus channel count per category (fetches all streams)
.venv/bin/python -m bonsai streams --category 123     # channels in one category
.venv/bin/python -m bonsai streams --limit 0          # every live channel
```

## Tests

```bash
.venv/bin/python -m pytest
```

The tests don't touch the network or your real config.
