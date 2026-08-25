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
            pages = self._gather_pages(client, menu)
            chap = Chapter(id=url, number=str(len(pages)), name=title_name, pages=pages)
            return CaptureResult(
                title=Title(source=self.name, id=url, name=title_name or "BookWalker"),
                chapters=[chap],
            )
        finally:
            client.close()

    def _guess_name(self, client):
        val = client.eval("document.title || ''")
        # 形如 "エロいスキルで異世界無双 THE COMIC 1"
        if val:
            for sep in (" - ", " -", " | "):
                if sep in val:
                    return val.split(sep)[0].strip()
        return val or "BookWalker"

    def _gather_pages(self, client, menu):
        """跳页 + 提取每个跨页的 2 个 canvas。返回到当前展示的那些页。"""
        import time
        # 提取当前页（可能2 canvas = 跨页左右）
        vals = client.extract_canvas_png()
        outs = []
        for data, w, h, idx in vals:
            outs.append(Page(data=data, ext="png", mime="image/png"))
        return outs
