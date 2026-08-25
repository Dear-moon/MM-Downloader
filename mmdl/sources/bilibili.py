"""B站漫画 source —— 浏览器辅助（capture 轨）。

纯 HTTP 被 ultra_sign 私有 WASM + 指纹拦死；走浏览器辅助：从阅读器 `.view-container`
canvas 提取明文原图（跨 realm 原生 toDataURL 绕过 B站对主 realm toDataURL 的 patch）。
**免费章节无登录可整话抓**；付费需登录态 + 慢速（<1.5s/跨页会触发账号风控）。
依赖：websocket-client + 本机 debug 浏览器(:9222)。进不了 Actions。
"""
from mmdl.core.model import Title, Chapter, Page, CaptureResult
from .base import BaseSource


class Bilibili(BaseSource):
    name = "bilibili"
    display_name = "哔哩哔哩漫画"
    lang_choices = None
    quality_choices = None
    capabilities = frozenset({"capture"})   # 浏览器辅助，非 crawl/list
    default_output = "manga_million"

    def __init__(self, throttle=0.0, lang="zh-CN", cdp_url="http://127.0.0.1:9222",
                 manga_name="", page_coords=None):
        super().__init__(throttle=throttle, lang=lang)
        self.cdp_url = cdp_url
        self.manga_name = manga_name
        self.page_coords = page_coords or {"next": (258, 600), "prev": (773, 600)}   # 点击翻页(左1/3下页, 右1/3上页)

    def http_config(self):
        raise NotImplementedError("B站 is browser-based; no HTTP config.")

    def _cdp(self):
        from mmdl.core.cdp import CdpClient
        return CdpClient(cdp_url=self.cdp_url, target_url_substr="manga.bilibili")

    def capture_from_url(self, url, *, lang=None, quality=None, **kw):
        """读取当前已打开的 B站阅读器页，慢速翻页逐排提取整章。返回 CaptureResult。"""
        client = self._cdn_open()
        client.connect()
        try:
            title_name = self.manga_name or self._guess_name(client)
            total = self._total_pages(client)
            pages = self._gather_pages(client, total=total)
            chapters = []
            chap = Chapter(id=url, number="", name=title_name or "reader", pages=pages)
            chapters.append(chap)
            return CaptureResult(
                title=Title(source=self.name, id=url, name=title_name or "bilibili"),
                chapters=chapters,
            )
        finally:
            client.close()

    def _cdn_open(self):
        return self._cdp()

    def _guess_name(self, client):
        val = client.eval("document.title || ''")
        part = (val or "").split("-")   # 形如 "48 - Unnamed Memory - 哔哩哔哩漫画"
        return part[1].strip() if len(part) > 1 else (val or "bilibili")

    def _extract_all(self, client):
        """提取当前页的全部漫画 canvas。返回 [Page(data=...)]。"""
        import time
        time.sleep(1.5)
        return [Page(data=d, ext="png", mime="image/png") for d, w, h, _ in client.extract_canvas_png()]

    def _pagenum(self, client):
        """当前跨页页码文本（形如 '1 2'），读不到返回 ''。"""
        val = client.eval(r"((document.body.innerText||'').match(/\d+\s*\n\s*\d+\s*\d+P?/)||[''])[0]")
        return (val or "").strip()

    def _total_pages(self, client):
        """从 '…70P' 读总页数；读不到返回 0。"""
        m = client.eval(r"((document.body.innerText||'').match(/(\d+)P\b/)||[])")
        try:
            return int(m[1])
        except (TypeError, ValueError, IndexError):
            return 0

    def _ep_id(self, client):
        """从 location.href 提取当前 ep 编号（如 1226681）；失败返回 ''。"""
        m = client.eval(r"((location.href||'').match(/mc\d+\/(\d+)/)||[])")
        try:
            return m[1]
        except (TypeError, IndexError):
            return ""

    def _gather_pages(self, client, total=0):
        """整话遍历：Home 回开头 → 逐跨页提取 → ArrowDown 慢速翻页 → 去重。

        收集满 total / 页码不前进 / ep 变化（翻进下一话）即停，防越过本话边界。
        """
        import hashlib, time

        def h(data):
            return hashlib.sha256(data).hexdigest()[:16]

        if not total:
            total = self._total_pages(client)
        client.key("Home", "Home", 36)   # 回到本话第 1 跨页(避开记住的阅读位置停在中间/末尾)
        time.sleep(2.0)
        pages, seen = [], set()
        step, guard = 0, max(40, total or 40)
        ep0 = self._ep_id(client)
        while len(pages) < total and step < guard:
            step += 1
            for pg in self._extract_all(client):
                key = h(pg.data)
                if key in seen:
                    continue
                seen.add(key)
                pages.append(pg)
            if len(pages) >= total:      # 收集满即停，不翻越去下一话
                break
            page_str = self._pagenum(client)
            client.key("ArrowDown", "ArrowDown", 40)
            time.sleep(2.0)   # ≥ 风控阈值(1.5s)，慢速逐跨页
            if self._ep_id(client) != ep0:   # 翻进了下一话（跨话）→ 停
                break
            nxt = self._pagenum(client)
            if nxt == page_str:          # 翻不动 => 到底
                break
        return pages
