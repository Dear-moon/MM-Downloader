# MM-Downloader

Multi-source manga downloader built around [MANGA MILLION](https://mangamillion.shueisha.co.jp). It's a pluggable framework: add a new comic platform as a `source`. Use the `--source` flag to pick one.

> v2.x = multi-source framework. The original single-platform tool lives on the `v1.0` branch.

## ⚠️ Disclaimer

- For **personal offline reading only**. All content © its respective rights holders. **Do not redistribute.**
- MANGA MILLION is a limited-time free service (expected to run until ~Dec 2027). The tool may need updates if the service changes.

## Features

- Pluggable **source** architecture — swap platforms with `--source`
- List all available titles, download a full series or a chapter range
- Resume interrupted downloads (skip already-downloaded pages and complete chapters)
- Bundle downloaded chapters into an `.epub`
- No login required for the default source

## Sources

| Source | Status | Auth | Notes |
|--------|--------|------|-------|
| `mangamillion` | ✅ implemented | none (device token) | Shueisha free service, protobuf + AES decryption |
| `tongli` | ✅ implemented | `TONG_LI_TOKEN` | Taiwan 東立 e-book, JSON API, Azure SAS image links (no DRM) |
| `bookwalker` | ✅ implemented | logged-in browser | Browser-assisted; needs a local debug Chrome (`--remote-debugging-port`), **not** in Actions |
| `bilibili` | ✅ implemented | logged-in browser | Browser-assisted; canvas extraction, risk-controlled, **not** in Actions |

**Browser-assisted sources** (`bookwalker`, `bilibili`) read the manga from the reader's `<canvas>` (cross-realm `toDataURL` to bypass canvas read-back patching) instead of the HTTP API. This requires your locally logged-in browser started with `--remote-debugging-port=9222 --remote-allow-origins=*`. They can't run in GitHub Actions (no login session there) and need `pip install websocket-client`.

**Tongli note**: browse endpoints (`/Book`, `/Book/BookVol`) don't need auth, but `/Comic/sas` (which returns the per-page image URLs) requires a Firebase Bearer token. Free-trial pages are subject to the service's session/time limits, so a given volume may return fewer or zero readable pages over time. Set `TONG_LI_TOKEN` (env) or `~/.mmdl/config.ini`, or pass `--token`.

All sources emit the same normalized `Title → Chapter → Page` model, so downloads, resume, and EPUB export work identically across platforms.

## Requirements

- Python 3.8+
- [`pycryptodome`](https://pypi.org/project/pycryptodome/)

Install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Or install the required dependency directly:

```bash
python -m pip install pycryptodome
```

## GitHub Actions (one-click download)

No local setup needed — download directly on GitHub:

1. Open the **Actions** tab → select the **Download manga** workflow
2. Click **Run workflow**
3. Fill in the inputs:
   - `source`: `mangamillion` (default) / `tongli`
   - `title_ids`: manga IDs, comma-separated
   - `lang`: language (default `en`)
   - `chapters`: chapter range, leave empty for all chapters
   - `quality`: `middle` / `low`
   - `epub`: `no` / `yes` — bundle into an EPUB
4. Run. When it finishes, download the `manga_million` artifact (tar.gz) from the workflow run page.

Note: `tongli` needs a `TONG_LI_TOKEN` repo secret, and the runner IP must be reachable by the service. `bookwalker` is intentionally not offered here (it needs a local browser login session).

## Usage

```bash
# List available titles for the default source
python mangamillion_downloader.py --list --lang en
# or as a module:
python -m mmdl --list --lang en

# Download a full series (e.g. One Piece, id=1)
python -m mmdl --title 1 --lang en

# Download only chapters 1-20
python -m mmdl --title 1 --chapters 1-20 --lang en

# Download and create an EPUB next to the title directory
python -m mmdl --title 1 --chapters 1-20 --lang en --epub

# Create an EPUB from a title that was already downloaded
python -m mmdl --epub-only "manga_million/One Piece" --lang en

# Pick a different source (Tongli — needs its Bearer token)
python -m mmdl --source tongli --title <volume-guid> --lang zh-TW --token "$TONG_LI_TOKEN"

# Browser-assisted: BookWalker reader URL (needs logged-in browser on :9222)
python -m mmdl --source bookwalker --url "https://viewer.bookwalker.jp/03/30/viewer.html?cid=<uuid>&cty=1"

# Browser-assisted: Bilibili manga reader URL
python -m mmdl --source bilibili --url "<manga-bilibili-reader-url>"
```

> The legacy `python mangamillion_downloader.py ...` command still works — it's a thin shim over `mmdl.cli`.

### Options

| Flag | Description |
|------|-------------|
| `--source` | Content source (default `mangamillion`) |
| `--list` | List all titles, then exit |
| `--title <id>` | Title ID for the selected source |
| `--lang <code>` | Language: `en` / `ja` / `zh-CN` / ... |
| `--chapters <a-b>` | Download only this chapter range |
| `--output <dir>` | Output directory (default `manga_million`) |
| `--quality <q>` | Image quality: `middle` / `low` |
| `--throttle <sec>` | Delay between page downloads (default `0.3`) |
| `--epub` | After downloading, bundle the title into an EPUB |
| `--epub-only <title-dir>` | Build an EPUB from an existing downloaded title directory |
| `--token <t>` | Source auth token (e.g. Tongli Bearer value) |
| `--book-group <g>` | Source optional param (e.g. Tongli BookGroupID) |

### Output layout

```
manga_million/
  One Piece/
    #001 Chapter 1 Romance Dawn/
      001.webp
      002.webp
      ...
  One Piece.epub
```

## How it works

The default source hits MANGA MILLION's Next.js SPA, backed by a protobuf API (`api.mangamillion.shueisha.co.jp`):

1. Registers a device token via `POST /api/register`
2. Fetches manga list / title detail / chapter list through the API
3. Requests each chapter's page URLs plus an AES key from `/api/viewer`
4. Downloads the encrypted pages (`.webp.enc`) and decrypts them (AES-256-CBC)
5. Optionally packages images into an EPUB 3 archive

All sources share the same `core/` transport, resume, and EPUB logic. Each source (`sources/*.py`) only implements its own API calls, field mapping, and image handling via the `BaseSource` interface.

## Project structure

```
mmdl/
  core/        # transport, model, naming, resume, epub — platform agnostic
  sources/     # base.BaseSource + one module per platform
cli.py         # --source routing & capability gating
mangamillion_downloader.py  # legacy shim
```

## License

[MIT](LICENSE)
