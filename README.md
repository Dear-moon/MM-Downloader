# MM-Downloader

Download manga from [MANGA MILLION](https://mangamillion.shueisha.co.jp) — Shueisha's free global manga service — for personal offline reading.

## ⚠️ Disclaimer

- For **personal offline reading only**. All content © Shueisha Inc. **Do not redistribute.**
- MANGA MILLION is a limited-time free service (expected to run until ~Dec 2027). The tool may need updates if the service changes.

## Features

- No login required
- List all available titles (~375)
- Download a full series or a chapter range
- Multiple languages (`en`, `ja`, `zh-CN`, ...)
- Pages are AES-decrypted and saved as `.webp`

## Requirements

- Python 3.8+
- [`pycryptodome`](https://pypi.org/project/pycryptodome/): `pip install pycryptodome`

## Usage

```bash
# List all titles (use the ID from here with --title)
python mangamillion_downloader.py --list --lang en

# Download a full series (e.g. One Piece, id=1)
python mangamillion_downloader.py --title 1 --lang en

# Download only chapters 1-20
python mangamillion_downloader.py --title 1 --chapters 1-20 --lang en

# Chinese version, custom output dir
python mangamillion_downloader.py --title 1 --lang zh-CN --output ./manga
```

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

### Output layout

```
manga_million/
  One Piece/
    #001 Chapter 1 Romance Dawn/
      001.webp
      002.webp
      ...
```

## How it works

The site is a Next.js SPA backed by a protobuf API (`api.mangamillion.shueisha.co.jp`). This tool:

1. Registers a device token via `POST /api/register`
2. Fetches manga list / title detail / chapter list through the API
3. Requests each chapter's page URLs plus an AES key from `/api/viewer`
4. Downloads the encrypted pages (`.webp.enc`) and decrypts them (AES-256-CBC)

Requests need a full browser-like header set (a missing `Accept-Encoding` triggers a Varnish 403), and the device token expires after a while — the script re-registers automatically on a 403.

## License

[MIT](LICENSE)
