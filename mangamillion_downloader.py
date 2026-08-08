#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import gzip
import http.client
import os
import re
import ssl
import sys
import time

from Crypto.Cipher import AES

API_HOST = "api.mangamillion.shueisha.co.jp"
IMG_HOST = "img.mangamillion.shueisha.co.jp"
SITE = "https://mangamillion.shueisha.co.jp"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")



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
        print(f"  [{idx}/{len(chapters)}] {ch['number']} ({len(view['pages'])} pages)")
        for pno, url in enumerate(view["pages"], 1):
            fname = os.path.join(ch_dir, f"{pno:03d}.webp")
            if os.path.exists(fname) and os.path.getsize(fname) > 0:
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
    return title_dir


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
    args = ap.parse_args()

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
        download_title(client, args.title, args.output, args.lang, chap_range, args.quality)
        print("[done]")
        return

    ap.print_help()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
