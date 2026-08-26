"""kepub 抽页：解密后的 epub 容器 → 按 spine→XHTML→img 顺序抽页图。

与平台无关（Kobo / Readmoo 都复用）。页序以 OPF spine itemref 为准（Kobo 图片文件名编号≠页序，
此坑在此规避）；若 spine `page-progression-direction="rtl"` 或含 `primary-writing-mode:horizontal-rl`
则整篇反转。双页 spread（一个 XHTML 含多个 <img>）按单页图逐个收；封面占位（svg/css 等非 raster）被过滤。
"""
import gzip
import io
import posixpath
import zipfile
import xml.etree.ElementTree as ET

from mmdl.core.model import Title, Page

_LN = "http://www.w3.org/1999/xlink"


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _maybe_gunzip(data):
    if data[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(data)
        except OSError:
            return data
    return data


def _join(base, href):
    return posixpath.normpath(posixpath.join(base, href))


def _detect(data):
    """按字节魔数识别 raster 格式，返回 (ext, mime)；非 raster 返回 ("", "")。"""
    if data[:3] == b"\xff\xd8\xff":
        return "jpg", "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png", "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif", "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    return "", ""


def extract_pages(epub_bytes, *, source="kepub", book_id=None):
    """解包已解密 epub，按 spine→XHTML→img 顺序抽页图。

    返回 (Title, list[Page])。Title.source 为调用方传入（kobo/readmoo），id 用 book_id 或书名。
    """
    with zipfile.ZipFile(io.BytesIO(epub_bytes)) as zp:
        names = set(zp.namelist())

        container = ET.fromstring(_read(zp, "META-INF/container.xml"))
        rootfile = None
        for rf in container.iter():
            if _local(rf.tag) == "rootfile":
                rootfile = rf.get("full-path")
                break
        if not rootfile or rootfile not in names:
            raise RuntimeError("kepub: container.xml 无有效 rootfile")

        opf_raw = _read(zp, rootfile)
        opf = ET.fromstring(opf_raw)
        opf_dir = posixpath.dirname(rootfile)

        title_name, author, spine_pd = "", "", "ltr"
        manifest, spine_ids, spine_el = {}, [], None
        for el in opf.iter():
            tag = _local(el.tag)
            if tag == "title" and not title_name:
                title_name = (el.text or "").strip()
            elif tag == "creator" and not author:
                author = (el.text or "").strip()
            elif tag == "item":
                manifest[el.get("id")] = el.get("href", "")
            elif tag == "spine":
                spine_el = el
            elif tag == "itemref":
                spine_ids.append(el.get("idref", ""))
        if spine_el is not None:
            spine_pd = spine_el.get("page-progression-direction") or "ltr"
        if spine_pd not in ("rtl", "r-l"):
            opf_text = opf_raw.decode("utf-8", "ignore")
            if "primary-writing-mode" in opf_text and ("horizontal-rl" in opf_text or "vertical-rl" in opf_text):
                spine_pd = "rtl"
        if spine_pd == "rtl":
            spine_ids = list(reversed(spine_ids))

        pages = []
        for idref in spine_ids:
            href = manifest.get(idref)
            if not href:
                continue
            spine_path = _join(opf_dir, href)
            xhtml_data = _read(zp, spine_path)
            if not xhtml_data:
                continue
            try:
                xroot = ET.fromstring(xhtml_data)
            except ET.ParseError:
                continue
            xhtml_dir = posixpath.dirname(spine_path)
            for img in xroot.iter():
                if _local(img.tag) != "img":
                    continue
                src = img.get("src") or img.get(f"{{{_LN}}}href")
                if not src:
                    continue
                data = _read(zp, _join(xhtml_dir, src))
                if not data:
                    continue
                ext, mime = _detect(data)
                if not ext:
                    continue  # svg/css 占位等非 raster 跳过
                pages.append(Page(data=data, ext=ext, mime=mime))

    if not title_name:
        title_name = book_id or source
    return Title(source=source, id=book_id or title_name, name=title_name, author=author), pages


def _read(zp, name):
    try:
        data = zp.read(name)
    except KeyError:
        return b""
    return _maybe_gunzip(data)
