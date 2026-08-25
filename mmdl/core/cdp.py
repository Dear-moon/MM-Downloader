"""CDP(Chrome DevTools Protocol) 客户端 — 用于浏览器辅助 source(B站/BW)。

连接已调试启动的浏览器(127.0.0.1:9222),在目标标签页上下文执行 JS。
核心用途:B站漫画从 canvas 提取明文原图(跨 realm 绕过 B站 JS patch)。
"""
import base64
import json
import time
import urllib.request
from pathlib import Path

try:
    import websocket
except ImportError:  # pragma: no cover
    websocket = None

DEFAULT_CDP_URL = "http://127.0.0.1:9222"

# 提取 B站 reader 漫画 canvas 为 PNG 的 JS(跨 realm iframe 取原生 toDataURL)。
# 用 `.view-container canvas`(任意尺寸),避免窄页(如 826w)被宽度>1000 过滤漏掉。
EXTRACT_CANVAS_JS = r"""
(async () => {
  const iframeWin = await new Promise(resolve => {
    const fr = document.createElement('iframe'); fr.style.display='none';
    fr.onload = () => resolve(fr.contentWindow);
    fr.src = 'about:blank'; document.body.appendChild(fr);
  });
  const nativeTDR = iframeWin.HTMLCanvasElement.prototype.toDataURL;
  let cvs = [...document.querySelectorAll('.view-container canvas')];
  if (!cvs.length) cvs = [...document.querySelectorAll('canvas')].filter(c=>c.width>500 && c.height>500);
  if (!cvs.length) return {error:'no comic canvas'};
  const pages = [];
  for (let i=0;i<cvs.length;i++){
    const cv = cvs[i];
    const out = iframeWin.document.createElement('canvas'); out.width=cv.width; out.height=cv.height;
    out.getContext('2d').drawImage(cv,0,0);
    let png=null, err=null;
    try { png = nativeTDR.call(out,'image/png'); } catch(e){ err=e.name+':'+e.message; }
    if (!png || !png.startsWith('data:image/png')) { pages.push({i, error:'toDataURL failed', err}); continue; }
    pages.push({i, w:cv.width, h:cv.height, b64: png.split(',')[1]});
  }
  return pages;
})()
"""


class CdpClient:
    """简易 CDP 客户端:连接浏览器,在指定标签页执行 JS。"""

    def __init__(self, cdp_url=DEFAULT_CDP_URL, target_url_substr=""):
        if websocket is None:
            raise RuntimeError("websocket-client not installed (pip install websocket-client)")
        self.cdp_url = cdp_url
        self.target_url_substr = target_url_substr
        self._ws = None

    def _list_targets(self):
        with urllib.request.urlopen(self.cdp_url + "/json", timeout=10) as r:
            return json.load(r)

    def _pick_target(self):
        targets = self._list_targets()
        # 先按子串精确找;找不到则任选一个 page
        for t in targets:
            if t.get("type") == "page" and self.target_url_substr and self.target_url_substr in t.get("url", ""):
                return t
        for t in targets:
            if t.get("type") == "page":
                return t
        raise RuntimeError("no page target in browser")

    def connect(self):
        target = self._pick_target()
        self._ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=90, suppress_origin=True)
        self._send("Runtime.enable")
        return target

    def _send(self, method, params=None, msg_id=None):
        msg_id = msg_id or int(time.time() * 1000) % 100000
        self._ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        while True:
            m = json.loads(self._ws.recv())
            if m.get("id") == msg_id:
                return m

    def eval(self, js, *, await_promise=False):
        """在页面上下文执行 JS,返回 value(dict 或 None)。"""
        req = {"expression": js, "returnByValue": True, "awaitPromise": await_promise}
        res = self._send("Runtime.evaluate", req)
        if "exceptionDetails" in res.get("result", {}):
            raise RuntimeError("JS exception: " + json.dumps(res["result"]["exceptionDetails"])[:500])
        return res.get("result", {}).get("result", {}).get("value")

    def extract_canvas_png(self, out_path=None):
        """从页面上所有漫画 canvas 提取 PNG 字节。返回 [(bytes, w, h, idx)]。"""
        val = self.eval(EXTRACT_CANVAS_JS, await_promise=True)
        if not isinstance(val, list) or not val:
            raise RuntimeError("canvas extract failed: " + json.dumps(val)[:300])
        results = []
        for p in val:
            if "b64" not in p:
                continue
            data = base64.b64decode(p["b64"])
            w, h = p.get("w", 0), p.get("h", 0)
            if out_path and len(results) == 0:
                Path(out_path).parent.mkdir(parents=True, exist_ok=True)
                Path(out_path).write_bytes(data)
            results.append((data, w, h, p.get("i", 0)))
        return results

    # ---- 翻页驱动(坐标点击 + 键盘;已验证 B站/BW 阅读器) ----
    def click(self, x, y):
        """在视口坐标 (x,y) 鼠标左键点击一次。B站读者: 左1/3 下页, 右1/3 上页。"""
        for t in ("mousePressed", "mouseReleased"):
            self._send("Input.dispatchMouseEvent",
                       {"type": t, "x": x, "y": y, "button": "left", "clickCount": 1, "pointerType": "mouse"})
        import time
        time.sleep(1.0)

    def key(self, key, code, vk):
        """派发键盘 keyDown/keyUp。B站: ArrowDown/Left/PageDown 下页, ArrowUp/PageUp 上页, Home 回开头。"""
        self._send("Input.dispatchKeyEvent", {"type": "keyDown", "key": key, "code": code, "windowsVirtualKeyCode": vk})
        self._send("Input.dispatchKeyEvent", {"type": "keyUp", "key": key, "code": code, "windowsVirtualKeyCode": vk})
        import time
        time.sleep(1.0)

    def close(self):
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
