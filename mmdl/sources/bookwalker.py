"""BookWalker (BW) source —— 浏览器辅助方案（capture 轨）。

**已验证机制**（实测）：
- BW 阅读器用 `.canvas` 渲染（跨页=2 canvas，1289×1398，left=奇数页 right=偶数页）
- BW **未 patch** toDataURL/getImageData，canvas 未污染 → 跨 realm iframe 原生 toDataURL 直接提取
- BW 翻页 API 公开暴露：`window.NFBR.a6G.Initializer.T1V.menu.options.a6l`
  → `moveToPage(n)`（精确跳页）/ `moveToNext()` / `moveToPrevious()`（对象名 T1V 会随版本变,
  可扫 `NFBR.a6G.Initializer.*.menu` 找非 undefined 的那个）

**参考**：xuzhengyi1995/Manga_downloader 用定制 Chromium 去 taint（绕过安全机制，未采用）；
本方案用跨 realm 提取，无需定制浏览器。登录态用用户已登录浏览器（CDP 9222）。

**依赖**：websocket-client + 已登录 BW 的浏览器页。进不了 GitHub Actions。
"""
from mmdl.core.model import Title, Chapter, Page, CaptureResult
from .base import BaseSource


class BookWalker(BaseSource):
    name = "bookwalker"
    display_name = "BookWalker"
    lang_choices = None
    quality_choices = None
    capabilities = frozenset({"capture"})
    default_output = "manga_million"

    def __init__(self, throttle=0.0, lang="ja", cdp_url="http://127.0.0.1:9222"):
        super().__init__(throttle=throttle, lang=lang)
        self.cdp_url = cdp_url

    def http_config(self):
        raise NotImplementedError("BookWalker is browser-based; no HTTP config.")

    def _cdp(self):
        from mmdl.core.cdp import CdpClient
        return CdpClient(cdp_url=self.cdp_url, target_url_substr="bookwalker")

    # ---- 翻页 API 定位（对象名随版本变化，扫 NFBR 找可用菜单位） ----
    def _menu(self, client):
        """返回可用的 BW 菜单位（含 options.a6l 翻页函数）。"""
        val = client.eval(r"""(()=>{
          const V=window.NFBR&&window.NFBR.a6G&&window.NFBR.a6G.Initializer;
          if(!V) return null;
          for(const k of Object.keys(V)){
            const m=V[k]&&V[k].menu;
            if(m && m.options && m.options.a6l && typeof m.options.a6l.moveToNext==='function')
              return 'NFBR.a6G.Initializer.'+k+'.menu';
          }
          return null;
        })()""")
        return val

    def _move_to(self, client, menu_path, page_n):
        """跳转到第 page_n 页（1-based）。menu_path 形如 'NFBR.a6G.Initializer.T1V.menu'。"""
        client.eval(f"(()=>{{const m={menu_path}; if(m&&m.options&&m.options.a6l&&m.options.a6l.moveToPage) m.options.a6l.moveToPage({page_n}); return 1}})()")

    # ---- capture 轨 ----
    def capture_from_url(self, url, *, lang=None, quality=None, **kw):
        client = self._cdp()
        try:
            client.connect()
            menu = self._menu(client)
            if not menu:
                raise RuntimeError("BookWalker reader API not found (NFBR.a6G.Initializer.*.menu)")
            title_name = self._guess_name(client)
            total = self._total_pages(client)
            pages = self._gather_pages(client, menu, total)
            # Chapter 名不设书名(避免目录嵌套为 书/书/); 让 _write_capture fallback 到 chapter_001
            chap = Chapter(id=url, number=str(len(pages)), name="", pages=pages)
            return CaptureResult(
                title=Title(source=self.name, id=url, name=title_name or "BookWalker"),
                chapters=[chap],
            )
        finally:
            client.close()

    def _guess_name(self, client):
        val = client.eval("document.title || ''")
        if val:
            for sep in (" - ", " -", " | "):
                if sep in val:
                    return val.split(sep)[0].strip()
        return val or "BookWalker"

    def _total_pages(self, client):
        """从页面 `N/169` 读总页数。"""
        import re
        val = client.eval("(document.body.innerText||'').match(/\\d+\\s*\\/\\s*(\\d+)/)?document.body.innerText.match(/\\d+\\s*\\/\\s*(\\d+)/)[1]:''")
        try:
            return int(val)
        except (TypeError, ValueError):
            return 0

    def _gather_pages(self, client, menu, total=0):
        """整章遍历: moveToPage(k) 逐跨页提取正文 canvas, 去重相邻重叠。

        BW 页码 0-based(显示 N/169 时 moveToPage(N-1) 在当前页), 每页有 2 个 1289×1398
        正文 canvas + 1 个封面占位(大而内容平). 翻页时相邻跨页共享边界页, 按 hash 去重.
        若 total 未知, 则循环直到页码不再前进(到最后一页).
        """
        import hashlib
        import time

        def h(data):
            return hashlib.sha256(data).hexdigest()[:16]

        pages = []
        seen = set()
        last_page = client.eval("document.body.innerText.match(/\\d+\\s*\\/\\s*(\\d+)/)?document.body.innerText.match(/\\d+\\s*\\/\\s*(\\d+)/)[1]:''")
        try:
            last_page = int(last_page)
        except (TypeError, ValueError):
            last_page = 0
        if last_page:
            total = last_page  # 兜底: 用页面读到的总页数

        # 从第 0 页开始(0-based)
        for idx in range(total):
            self._move_to(client, menu, idx)
            time.sleep(1.2)   # 等 canvas 绘制
            for data, w, hh, i in client.extract_canvas_png():
                # 只收正文页 canvas(近似等宽的漫画页)。BW 正文多是非 1:1 的竖版(如 1289×1398),
                # 封面/占位通常是不同尺寸(如 1350×1920)或超小。用宽高落在漫画页尺寸带过滤。
                if w < 500 or hh < 500:
                    continue   # 过小图标
                if w > 1350 or hh > 1500:
                    continue   # 封面/大占位(约 1350×1920)
                key = h(data)
                if key in seen:
                    continue
                seen.add(key)
                pages.append(Page(data=data, ext="png", mime="image/png"))
        return pages
