# MM-Downloader

Download manga from [MANGA MILLION](https://mangamillion.shueisha.co.jp) — Shueisha's free global manga service — for personal offline reading.

## ⚠️ Disclaimer

- For **personal offline reading only**. All content © Shueisha Inc. **Do not redistribute.**
- MANGA MILLION is a limited-time free service (expected to run until ~Dec 2027). The tool may need updates if the service changes.

## Features

- No login required
- List all available titles (~375)
- Download a full series or a chapter range
- Resume interrupted downloads by skipping already-downloaded pages and complete chapters
- Multiple languages (`en`, `ja`, `zh-CN`, ...)
- Pages are AES-decrypted and saved as `.webp`
- Optionally bundle downloaded chapters into an `.epub`
- Build an `.epub` from an existing downloaded title directory without downloading again

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
   - `title_ids`: manga IDs, comma-separated (get them from `--list`)
   - `lang`: language (default `en`)
   - `chapters`: chapter range, leave empty for all chapters
   - `quality`: `middle` / `low`
   - `epub`: `no` / `yes` — bundle the downloaded chapters into an EPUB (default `no`)
4. Run. When it finishes, download the `manga_million` artifact (tar.gz) from the workflow run page.

The workflow runs the same script on `ubuntu-latest` and uploads the result as an artifact (kept 90 days). Note that GitHub Actions runners use US/EU IPs, which works fine for MANGA MILLION's overseas service — but the site may block certain regions, so behavior can vary.

## Usage

```bash
# List all titles (use the ID from here with --title)
python mangamillion_downloader.py --list --lang en

# Download a full series (e.g. One Piece, id=1)
python mangamillion_downloader.py --title 1 --lang en

# Download only chapters 1-20
python mangamillion_downloader.py --title 1 --chapters 1-20 --lang en

# Download and create an EPUB next to the downloaded title directory
python mangamillion_downloader.py --title 1 --chapters 1-20 --lang en --epub

# Create an EPUB from a title that was already downloaded
python mangamillion_downloader.py --epub-only "manga_million/One Piece" --lang en

# Chinese version, custom output dir
python mangamillion_downloader.py --title 1 --lang zh-CN --output ./manga
```

Downloads are resumable. If a chapter directory already contains every expected page as a non-empty image file, the chapter is skipped. If only some pages are present, the downloader fetches the missing or invalid pages.

### Options

| Flag | Description |
|------|-------------|
| `--list` | List all titles, then exit |
| `--title <id>` | `original_title_id` to download (shown by `--list`) |
| `--lang <code>` | Language: `en` / `ja` / `zh-CN` / ... (default `en`) |
| `--chapters <a-b>` | Download only this chapter range |
| `--output <dir>` | Output directory (default `./manga_million`) |
| `--quality <q>` | `middle` (default) / `low` |
| `--throttle <sec>` | Delay between page downloads (default `0.3`) |
| `--epub` | After downloading, bundle the title into an EPUB file |
| `--epub-only <title-dir>` | Build an EPUB from an existing downloaded title directory without downloading |

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

When `--epub` is used, the EPUB is written next to the title directory. With the default output directory, `manga_million/One Piece` becomes `manga_million/One Piece.epub`.

## How it works

The site is a Next.js SPA backed by a protobuf API (`api.mangamillion.shueisha.co.jp`). This tool:

1. Registers a device token via `POST /api/register`
2. Fetches manga list / title detail / chapter list through the API
3. Requests each chapter's page URLs plus an AES key from `/api/viewer`
4. Downloads the encrypted pages (`.webp.enc`) and decrypts them (AES-256-CBC)
5. Optionally packages downloaded page images into an EPUB 3 archive

Requests need a full browser-like header set (a missing `Accept-Encoding` triggers a Varnish 403), and the device token expires after a while — the script re-registers automatically on a 403.

## License

[MIT](LICENSE)
