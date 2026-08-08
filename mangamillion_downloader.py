#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import gzip
import html
import http.client
import mimetypes
import os
import re
import ssl
import sys
import time
import uuid
import zipfile
from pathlib import Path

from Crypto.Cipher import AES

API_HOST = "api.mangamillion.shueisha.co.jp"
IMG_HOST = "img.mangamillion.shueisha.co.jp"
SITE = "https://mangamillion.shueisha.co.jp"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
MIN_DOWNLOADED_IMAGE_BYTES = 1



def _varint(b, i):
    v = 0
    s = 0
    while True:
        x = b[i]
        i += 1
        v |= (x & 0x7F) << s
        if not x & 0x80:
            return v, i
        s += 7


def pb_fields(b):
    out = {}
    i = 0
    end = len(b)
    while i < end:
        tag, i = _varint(b, i)
        fn, wt = tag >> 3, tag & 7
        if wt == 0:
            v, i = _varint(b, i)
            out.setdefault(fn, []).append(("v", v))
        elif wt == 2:
            ln, i = _varint(b, i)
            out.setdefault(fn, []).append(("b", b[i:i + ln]))
            i += ln
        elif wt == 1:
            out.setdefault(fn, []).append(("b", b[i:i + 8]))
            i += 8
        elif wt == 5:
            out.setdefault(fn, []).append(("b", b[i:i + 4]))
            i += 4
        else: 
            raise ValueError(f"unexpected wire type {wt} at field {fn}")
    return out


def pb_str(v):
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return str(v)


def pb_int(v):
    return v if isinstance(v, int) else int.from_bytes(v, "little")



class MMC:

    def __init__(self, lang="en", throttle=0.3, referer_page=None):
        self.lang = lang
        self.throttle = throttle
        self.token = None
        self._ctx = ssl.create_default_context()
        self._ctx.check_hostname = False
        self._ctx.verify_mode = ssl.CERT_NONE  

    def _headers(self, token=None, extra=None):
        h = {
            "User-Agent": UA,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Origin": SITE,
            "Referer": SITE + "/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
        }
        if token:
            h["Access-Token"] = token
        if extra:
            h.update(extra)
        return h

    def _req(self, host, method, path, params=None, body=None, headers=None):
        url = path
        if params:
            url += "?" + "&".join(f"{k}={urllib_quote(str(v))}" for k, v in params.items() if v is not None)
        last = None
        for attempt in range(5):
            try:
                c = http.client.HTTPSConnection(host, timeout=60, context=self._ctx)
                if body is not None:
                    h = headers or self._headers()
                    h["Content-Type"] = "application/x-protobuf"
                    h["Content-Length"] = str(len(body))
                    c.request(method, url, body=body, headers=h)
                else:
                    h = headers or self._headers()
                    h["Content-Length"] = "0"
                    c.request(method, url, headers=h)
                r = c.getresponse()
                data = r.read()
                c.close()
                if data[:2] == b"\x1f\x8b":
                    data = gzip.decompress(data)
                return r.status, data
            except (ssl.SSLEOFError, ConnectionResetError, http.client.HTTPException, TimeoutError, OSError) as e:
                last = e
                time.sleep(1.2 * (attempt + 1))
        raise RuntimeError(f"request failed after retries: {last}")

    def register(self):
        """POST /api/register -> device token"""
        st, body = self._req(API_HOST, "POST", "/api/register")
        if st != 200:
            raise RuntimeError(f"register failed HTTP {st}")
        resp = pb_fields(body)
        reg = resp.get(170, [("b", b"")])[0][1]
        inner = pb_fields(reg)
        tok = inner.get(1, [("b", b"")])[0][1]
        if not isinstance(tok, bytes):
            raise RuntimeError("no token in register response")
        self.token = tok.decode()
        return self.token

    def _auth(self):
        if not self.token:
            self.register()

    def api(self, path, params=None, method="GET"):
        self._auth()
        for attempt in range(2):
            st, body = self._req(API_HOST, method, path, params, headers=self._headers(self.token))
            if st == 403 and attempt == 0:
                time.sleep(0.5)
                self.register()
                continue
            if st != 200:
                raise RuntimeError(f"{path} HTTP {st}")
            return body

    def _img(self, url):
        u = url
        scheme, rest = u.split("://", 1)
        host, _, path = rest.partition("/")
        path = "/" + path
        st, body = self._req(host, "GET", path, headers=self._headers(self.token, extra={
            "Sec-Fetch-Dest": "image", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "cross-site",
            "Referer": SITE + "/en/title/1/chapter/1",
        }))
        return st, body



def parse_manga_list(body):
    """Response.field22(MangaListViewResponse) -> [{id,name,author}]。
    field22 -> field1(repeated MangaListItem) -> field1(OriginalTitleSummary)
    OriginalTitleSummary: field1 originalTitleId, field3 serviceTitleName, field4 authorName"""
    resp = pb_fields(body)
    ml = resp.get(22, [("b", b"")])[0][1]
    inner = pb_fields(ml)
    out = []
    for kind, val in inner.get(1, []):  # MangaListItem
        if kind != "b":
            continue
        item = pb_fields(val)
        o = pb_fields(item.get(1, [("b", b"")])[0][1])  # OriginalTitleSummary
        oid = o.get(1, [("v", 0)])[0][1]
        name = pb_str(o.get(3, [("b", b"")])[0][1]) if 3 in o else ""
        author = pb_str(o.get(4, [("b", b"")])[0][1]) if 4 in o else ""
        out.append({"id": pb_int(oid), "name": name, "author": author})
    return out


def parse_title_detail(body):
    """Response.field50(TitleDetailViewResponse).field1(ServiceTitle)"""
    resp = pb_fields(body)
    td = resp.get(50, [("b", b"")])[0][1]
    inner = pb_fields(td)
    st_bytes = inner.get(1, [("b", b"")])[0][1]
    st = pb_fields(st_bytes)
    return {
        "coverUrl": pb_str(st.get(1, [("b", b"")])[0][1]) if 1 in st else "",
        "name": pb_str(st.get(2, [("b", b"")])[0][1]) if 2 in st else "",
        "author": pb_str(st.get(3, [("b", b"")])[0][1]) if 3 in st else "",
        "description": pb_str(st.get(7, [("b", b"")])[0][1]) if 7 in st else "",
    }


def parse_chapter_list(body):
    """Response.field60(ChapterListViewResponse) -> [{number,name,id}]。
    (ChapterInfo: field1 number, field2 name, field3 translatedChapterId)。"""
    resp = pb_fields(body)
    cl = resp.get(60, [("b", b"")])[0][1]
    inner = pb_fields(cl)
    chapters = []
    groups = inner.get(2, [])
    for gkind, gval in groups:
        if gkind != "b":
            continue
        g = pb_fields(gval)
        for ckind, cval in g.get(2, []):
            if ckind != "b":
                continue
            c = pb_fields(cval)
            chapters.append({
                "number": pb_str(c.get(1, [("b", "")])[0][1]) if 1 in c else "",
                "name": pb_str(c.get(2, [("b", "")])[0][1]) if 2 in c else "",
                "id": pb_int(c.get(3, [("v", 0)])[0][1]),
            })
        if not g.get(2) and g.get(3):
            chapters.append({
                "number": pb_str(g.get(1, [("b", "")])[0][1]) if 1 in g else "",
                "name": pb_str(g.get(2, [("b", "")])[0][1]) if 2 in g else "",
                "id": pb_int(g.get(3, [("v", 0)])[0][1]),
            })
    if not chapters:
        for ckind, cval in inner.get(2, []):
            c = pb_fields(cval)
            if c.get(3):
                chapters.append({
                    "number": pb_str(c.get(1, [("b", "")])[0][1]) if 1 in c else "",
                    "name": pb_str(c.get(2, [("b", "")])[0][1]) if 2 in c else "",
                    "id": pb_int(c.get(3, [("v", 0)])[0][1]),
                })
    return chapters


def parse_viewer(body):
    """Response.field70(ViewerViewResponse) -> {pages:[url], aesKey, aesIv}"""
    resp = pb_fields(body)
    vw = resp.get(70, [("b", b"")])[0][1]
    v = pb_fields(vw)
    pages = []
    for pkind, pval in v.get(1, []):
        if pkind != "b":
            continue
        p = pb_fields(pval)
        if 1 in p:
            pages.append(pb_str(p[1][0][1]))
    key = pb_str(v.get(7, [("b", b"")])[0][1]) if 7 in v else ""
    iv = pb_str(v.get(8, [("b", b"")])[0][1]) if 8 in v else ""
    return {"pages": pages, "aesKey": key, "aesIv": iv}



def urllib_quote(s):
    import urllib.parse
    return urllib.parse.quote(s, safe="-_.~")


def clean_name(s):
    s = re.sub(r'[\\/:*?"<>|\r\n]', " ", s)
    return re.sub(r"\s+", " ", s).strip() or "unknown"


def aes_decrypt(data, key_hex, iv_hex):
    """AES-256-CBC , PKCS7 """
    key = bytes.fromhex(key_hex)
    iv = bytes.fromhex(iv_hex)
    c = AES.new(key, AES.MODE_CBC, iv)
    pt = c.decrypt(data)
    if pt:
        pad = pt[-1]
        if 1 <= pad <= 16 and pt[-pad:] == bytes([pad]) * pad:
            pt = pt[:-pad]
    return pt


def natural_sort_key(value):
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", str(value))]


def _epub_href(value):
    import urllib.parse
    return urllib.parse.quote(value, safe="/._-")


def _media_type(path):
    if path.suffix.lower() == ".webp":
        return "image/webp"
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _downloaded_chapters(title_dir):
    title_dir = Path(title_dir)
    chapters = []
    for chapter_dir in sorted((p for p in title_dir.iterdir() if p.is_dir()), key=lambda p: natural_sort_key(p.name)):
        pages = sorted(
            (p for p in chapter_dir.iterdir() if p.is_file() and p.suffix.lower() in {".webp", ".jpg", ".jpeg", ".png"}),
            key=lambda p: natural_sort_key(p.name),
        )
        if pages:
            chapters.append((chapter_dir.name, pages))
    return chapters


def _missing_title_dir_message(title_dir):
    title_dir = Path(title_dir)
    message = f"title directory does not exist: {title_dir}"
    parent = title_dir.parent
    if parent.is_dir():
        candidates = sorted((p.name for p in parent.iterdir() if p.is_dir()), key=natural_sort_key)
        if candidates:
            message += "\nAvailable title directories:"
            message += "".join(f"\n  {parent / name}" for name in candidates)
    return message


def page_already_downloaded(path):
    path = Path(path)
    return path.is_file() and path.stat().st_size > MIN_DOWNLOADED_IMAGE_BYTES


def chapter_already_downloaded(chapter_dir, page_count):
    chapter_dir = Path(chapter_dir)
    if page_count <= 0:
        return False
    return all(page_already_downloaded(chapter_dir / f"{page_no:03d}.webp") for page_no in range(1, page_count + 1))


def build_epub(title_dir, epub_path=None, title=None, author=None, language="en"):
    title_dir = Path(title_dir)
    if not title_dir.is_dir():
        raise FileNotFoundError(_missing_title_dir_message(title_dir))

    chapters = _downloaded_chapters(title_dir)
    if not chapters:
        raise RuntimeError(f"no downloaded chapter images found in {title_dir}")

    title = title or title_dir.name
    author = author or "Unknown"
    language = language or "en"
    epub_path = Path(epub_path) if epub_path else title_dir.with_suffix(".epub")
    epub_path.parent.mkdir(parents=True, exist_ok=True)

    identifier = f"urn:uuid:{uuid.uuid4()}"
    manifest_items = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
    ]
    spine_items = []
    nav_items = []
    chapter_docs = []
    image_entries = []

    for chapter_index, (chapter_name, pages) in enumerate(chapters, 1):
        chapter_id = f"chapter_{chapter_index:03d}"
        chapter_href = f"chapters/{chapter_id}.xhtml"
        manifest_items.append(
            f'<item id="{chapter_id}" href="{chapter_href}" media-type="application/xhtml+xml"/>'
        )
        spine_items.append(f'<itemref idref="{chapter_id}"/>')
        nav_items.append(
            f'<li><a href="{_epub_href(chapter_href)}">{html.escape(chapter_name)}</a></li>'
        )

        image_tags = []
        for page_index, page in enumerate(pages, 1):
            image_id = f"img_{chapter_index:03d}_{page_index:03d}"
            image_href = f"images/{chapter_id}/{page.name}"
            manifest_items.append(
                f'<item id="{image_id}" href="{_epub_href(image_href)}" media-type="{_media_type(page)}"/>'
            )
            image_entries.append((page, f"EPUB/{image_href}"))
            image_tags.append(
                f'<img src="../{_epub_href(image_href)}" alt="{html.escape(chapter_name)} page {page_index}"/>'
            )

        chapter_docs.append((
            f"EPUB/{chapter_href}",
            f'''<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{html.escape(language)}" lang="{html.escape(language)}">
<head>
  <title>{html.escape(chapter_name)}</title>
  <style>
    body {{ margin: 0; padding: 0; background: #111; }}
    section {{ margin: 0 auto; max-width: 100%; }}
    img {{ display: block; width: 100%; height: auto; margin: 0 auto; }}
  </style>
</head>
<body>
  <section>
    <h1>{html.escape(chapter_name)}</h1>
    {chr(10).join(image_tags)}
  </section>
</body>
</html>
''',
        ))

    package_doc = f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">{identifier}</dc:identifier>
    <dc:title>{html.escape(title)}</dc:title>
    <dc:creator>{html.escape(author)}</dc:creator>
    <dc:language>{html.escape(language)}</dc:language>
    <meta property="dcterms:modified">{time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}</meta>
  </metadata>
  <manifest>
    {chr(10).join(manifest_items)}
  </manifest>
  <spine>
    {chr(10).join(spine_items)}
  </spine>
</package>
'''
    nav_doc = f'''<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{html.escape(language)}" lang="{html.escape(language)}">
<head>
  <title>{html.escape(title)}</title>
</head>
<body>
  <nav epub:type="toc" id="toc">
    <h1>{html.escape(title)}</h1>
    <ol>
      {chr(10).join(nav_items)}
    </ol>
  </nav>
</body>
</html>
'''
    container_doc = '''<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
'''

    with zipfile.ZipFile(epub_path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container_doc, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("EPUB/package.opf", package_doc, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("EPUB/nav.xhtml", nav_doc, compress_type=zipfile.ZIP_DEFLATED)
        for chapter_name, chapter_doc in chapter_docs:
            archive.writestr(chapter_name, chapter_doc, compress_type=zipfile.ZIP_DEFLATED)
        for source_path, archive_name in image_entries:
            archive.write(source_path, archive_name, compress_type=zipfile.ZIP_DEFLATED)

    return epub_path



def download_title(client, title_id, out_dir, lang, chapter_range=None, quality="middle"):
    os.makedirs(out_dir, exist_ok=True)

    detail = parse_title_detail(client.api("/api/title_detail", {"original_title_id": title_id, "service_language": lang}))
    title_name = clean_name(detail["name"] or f"title_{title_id}")
    title_dir = os.path.join(out_dir, title_name)
    os.makedirs(title_dir, exist_ok=True)
    print(f"[title] {detail['name']} by {detail['author']}")

    chapters = parse_chapter_list(client.api(
        "/api/chapter_list",
        {"original_title_id": title_id, "translated_language": lang, "service_language": lang},
    ))
    if chapter_range:
        lo, hi = chapter_range
        chapters = [c for c in chapters if lo <= _num(c["number"]) <= hi]
    print(f"[chapters] {len(chapters)} to download")

    for idx, ch in enumerate(chapters, 1):
        ch_name = clean_name(f"{ch['number']} {ch['name']}".strip())
        ch_dir = os.path.join(title_dir, ch_name)
        os.makedirs(ch_dir, exist_ok=True)
        view = parse_viewer(client.api(
            "/api/viewer",
            {"translated_chapter_id": ch["id"], "quality": quality, "service_language": lang},
        ))
        if not view["pages"]:
            print(f"  [skip] {ch_name}: no pages")
            continue
        if chapter_already_downloaded(ch_dir, len(view["pages"])):
            print(f"  [skip] {ch_name}: already downloaded")
            continue
        print(f"  [{idx}/{len(chapters)}] {ch['number']} ({len(view['pages'])} pages)")
        for pno, url in enumerate(view["pages"], 1):
            fname = os.path.join(ch_dir, f"{pno:03d}.webp")
            if page_already_downloaded(fname):
                continue
            for _ in range(3):
                st, enc = client._img(url)
                if st == 200 and len(enc) > 32:
                    try:
                        pt = aes_decrypt(enc, view["aesKey"], view["aesIv"])
                    except Exception:
                        st = 403
                    else:
                        if pt[:4] == b"RIFF":
                            with open(fname, "wb") as f:
                                f.write(pt)
                            break
                time.sleep(0.6)
                view = parse_viewer(client.api(
                    "/api/viewer",
                    {"translated_chapter_id": ch["id"], "quality": quality, "service_language": lang},
                ))
                url = view["pages"][pno - 1] if pno - 1 < len(view["pages"]) else url
            if not os.path.exists(fname):
                print(f"    [fail] page {pno}")
            time.sleep(client.throttle)
    return title_dir, detail


def _num(s):
    m = re.search(r"\d+", s)
    return int(m.group()) if m else 0


def main():
    ap = argparse.ArgumentParser(description="MANGA MILLION downloader (personal offline use)")
    ap.add_argument("--lang", default="en", help="language code, e.g. en / ja / zh-CN (default en)")
    ap.add_argument("--list", action="store_true", help="list all titles then exit")
    ap.add_argument("--title", type=int, help="original_title_id to download")
    ap.add_argument("--chapters", help="chapter range e.g. '1-20' (by number in chapter string)")
    ap.add_argument("--output", default="manga_million", help="output directory")
    ap.add_argument("--quality", default="middle", help="image quality: middle (default) / low")
    ap.add_argument("--throttle", type=float, default=0.3, help="seconds between page downloads")
    ap.add_argument("--epub", action="store_true", help="bundle the downloaded title into an EPUB file")
    ap.add_argument("--epub-only", help="bundle an existing downloaded title directory into an EPUB file without downloading")
    args = ap.parse_args()

    if args.epub_only:
        epub_path = build_epub(args.epub_only, language=args.lang)
        print(f"[epub] {epub_path}")
        print("[done]")
        return

    client = MMC(lang=args.lang, throttle=args.throttle)
    print(f"[auth] registering device token...")
    client.register()
    print(f"[auth] token ok ({client.token[:12]}...)")

    if args.list:
        titles = parse_manga_list(client.api("/api/manga_list", {"service_language": args.lang}))
        print(f"[list] {len(titles)} titles:")
        for t in titles:
            print(f"  {t['id']:>5}  {t['name']}  ({t['author']})")
        return

    if args.title:
        chap_range = None
        if args.chapters and "-" in args.chapters:
            a, b = args.chapters.split("-", 1)
            chap_range = (int(a), int(b))
        title_dir, detail = download_title(client, args.title, args.output, args.lang, chap_range, args.quality)
        if args.epub:
            epub_path = build_epub(
                title_dir,
                title=detail.get("name") or Path(title_dir).name,
                author=detail.get("author") or "Unknown",
                language=args.lang,
            )
            print(f"[epub] {epub_path}")
        print("[done]")
        return

    ap.print_help()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
