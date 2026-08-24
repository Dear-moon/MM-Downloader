"""参数化 HTTP 传输层。端点相关头由 source 提供，此处只管连接/重试/gzip/SSL/JSON 便利。"""
import gzip
import http.client
import ssl
import time
from dataclasses import dataclass, field

from .naming import natural_sort_key

DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


@dataclass
class HttpConfig:
    origin: str = ""
    referer: str = ""
    user_agent: str = DEFAULT_UA
    content_type: str = "application/octet-stream"   # body 存在且未设 Content-Type 时用
    verify_ssl: bool = True
    extra_headers: dict = field(default_factory=dict)     # API 请求头（token 等）
    img_headers: dict = field(default_factory=dict)       # 图像 GET 头（Referer / Sec-Fetch image）
    timeout: int = 60
    retries: int = 5


def split_url(url):
    """把完整 URL 拆成 (host, path_with_query)。"""
    scheme, rest = url.split("://", 1)
    host, _, path = rest.partition("/")
    return host, "/" + path


def _quote(s):
    import urllib.parse
    return urllib.parse.quote(s, safe="-_.~")


class HttpClient:
    def __init__(self, cfg: HttpConfig | None = None, *, origin=None, referer=None,
                 user_agent=None, content_type=None, verify_ssl=None,
                 extra_headers=None, img_headers=None, timeout=60, retries=5, throttle=0.0):
        """两种构造方式：传 HttpConfig 或逐项传。逐项传用于覆盖 HttpConfig 的默认值。"""
        if cfg is not None:
            self.cfg = cfg
        else:
            self.cfg = HttpConfig()
        # 逐项覆盖（向后兼容 MMC 的调用习惯；覆盖未传的用 cfg 值）
        self.origin = origin if origin is not None else self.cfg.origin
        self.referer = referer if referer is not None else self.cfg.referer
        self.user_agent = user_agent if user_agent is not None else self.cfg.user_agent
        self.content_type = content_type if content_type is not None else self.cfg.content_type
        self.verify_ssl = verify_ssl if verify_ssl is not None else self.cfg.verify_ssl
        self.extra_headers = dict(extra_headers) if extra_headers is not None else dict(self.cfg.extra_headers)
        self.img_headers = dict(img_headers) if img_headers is not None else dict(self.cfg.img_headers)
        self.timeout = timeout or self.cfg.timeout
        self.retries = retries or self.cfg.retries
        self.throttle = throttle

        self._ctx = ssl.create_default_context()
        if not self.verify_ssl:
            self._ctx.check_hostname = False
            self._ctx.verify_mode = ssl.CERT_NONE

    # ---- 基础头 ----
    def base_headers(self, *, extra=None):
        h = {
            "User-Agent": self.user_agent,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Sec-Fetch-Mode": "cors",
        }
        if self.origin:
            h["Origin"] = self.origin
        if self.referer:
            h["Referer"] = self.referer
        h.update(self.extra_headers)
        if extra:
            h.update(extra)
        return h

    # ---- 请求 ----
    def request(self, host, method, path, *, params=None, body=None, headers=None,
                content_type=None, img=False):
        """返回 (status, raw_bytes)。gzip 魔数自动解压。SSLEOF 等异常自动退避重试。"""
        url = path
        if params:
            url += "?" + "&".join(f"{k}={_quote(str(v))}" for k, v in params.items() if v is not None)
        h = headers or {}
        if img:
            base = self.base_headers(extra=self.img_headers)
        else:
            base = self.base_headers()
        h = dict(base)
        if headers:
            h.update(headers)
        last = None
        for attempt in range(self.retries):
            try:
                c = http.client.HTTPSConnection(host, timeout=self.timeout, context=self._ctx)
                if body is not None:
                    h["Content-Type"] = content_type or self.content_type
                    h["Content-Length"] = str(len(body))
                    c.request(method, url, body=body, headers=h)
                else:
                    h["Content-Length"] = "0"
                    c.request(method, url, headers=h)
                r = c.getresponse()
                data = r.read()
                c.close()
                if data[:2] == b"\x1f\x8b":
                    data = gzip.decompress(data)
                return r.status, data
            except (ssl.SSLEOFError, ConnectionResetError, http.client.HTTPException,
                    TimeoutError, OSError) as e:
                last = e
                time.sleep(1.2 * (attempt + 1))
        raise RuntimeError(f"request failed after retries: {last}")

    # ---- 便利 ----
    def get_json(self, host, path, *, params=None, headers=None):
        import json
        st, body = self.request(host, "GET", path, params=params, headers=headers, content_type="application/json")
        if st != 200:
            raise RuntimeError(f"GET {path} HTTP {st}")
        if isinstance(body, bytes):
            body = body.decode("utf-8")
        return json.loads(body)

    def get_bytes(self, host, path, *, params=None, headers=None, img=False):
        return self.request(host, "GET", path, params=params, headers=headers, img=img)

    def set_proxy(self, tunnel_host, tunnel_port):
        """设置代理隧道（东立大陆 IP 受限时用）。"""
        self._proxy = (tunnel_host, tunnel_port)
