"""東立電子書城 (Tongli) source：公开接口 + 免费试读 token，无 DRM。

方案 A（免注册账号）：浏览页接口（/Book、/Book/BookVol）无需登录；取实际漫画页数据
（/Comic/sas）需要 Firebase Bearer token（免费可取）。图片是 Azure Blob CDN 的 SAS
签名直链（tongli-ebook-cdn），无加密，直接 GET 即可。

已实测验证（2026-08，用登录态 Firebase token）：
- GET /Book?bookID={guid}        书详情：Title/Vol/Page/Authors/CoverURL/BookGroupID/FreeTrialPageLimit
- GET /Book/BookVol/{vol}?bookID={guid}   该系列集列表：[{BookID, Vol, ...}]
- GET /Comic/sas/{单集bookID}?freeTrialToken=free + Authorization   每页 Pages[].ImageURL + IsLTR
- ImageURL 是 Azure SAS 签名直链（se 约 7 分钟有效），即时下载

token 获取优先级（不硬编码）：--token 命令行 > TONG_LI_TOKEN 环境变量 > ~/.mmdl/config.ini。
"""
import os
from pathlib import Path
from configparser import ConfigParser

from mmdl.core.http import HttpClient, HttpConfig, split_url
from mmdl.core.model import Title, Chapter, Page
from .base import BaseSource

API_HOST = "api.tongli.tw"
SITE = "https://ebook.tongli.com.tw"

# 免费试读 token（Comic/sas 索取试读页数据时附带）
FREE_TRIAL = "free"


def _resolve_token(cli_token=None, env="TONG_LI_TOKEN", config_path=None):
    """按优先级解析东立 Bearer token：CLI > 环境变量 > 配置文件。"""
    if cli_token:
        return cli_token.strip()
    env_tok = os.environ.get(env)
    if env_tok:
        return env_tok.strip()
    cfg = config_path or Path.home() / ".mmdl" / "config.ini"
    try:
        p = ConfigParser()
        p.read(cfg, encoding="utf-8")
        tok = p.get("tongli", "token", fallback="").strip()
        if tok:
            return tok
    except Exception:
        pass
    return ""


class Tongli(BaseSource):
    name = "tongli"
    display_name = "東立電子書城"
    lang_choices = None           # 无语言维度（本身是繁体中文）
    quality_choices = None
    capabilities = frozenset({"crawl"})   # 不对外 list（public 检索有限），按 bookID 抓取
    default_output = "manga_million"

    def __init__(self, throttle=0.0, lang="zh-TW", book_group=None, token=None):
        super().__init__(throttle=throttle, lang=lang)
        self.book_group = book_group   # 可选 BookGroupID；缺省从 /Book 返回取
        self.token = token or _resolve_token()

    # ---- HTTP ----
    def http_config(self) -> HttpConfig:
        return HttpConfig(
            origin=SITE,
            referer=SITE + "/",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"),
            content_type="application/json",
            verify_ssl=True,
            extra_headers={"Authorization": f"bearer {self.token}"} if self.token else {},
        )

    def make_client(self, throttle=0.0) -> HttpClient:
        return HttpClient(self.http_config(), throttle=throttle)

    # ---- API ----
    def _get(self, client, path, params=None):
        """GET api.tongli.tw 并解析 JSON；非 200 抛错。"""
        st, body = client.request(API_HOST, "GET", path, params=params)
        if st != 200:
            raise RuntimeError(f"GET {path} HTTP {st}")
        import json
        return json.loads(body.decode("utf-8")) if isinstance(body, bytes) else json.loads(body)

    # ---- BaseSource 实现（crawl 轨）----
    def get_title(self, title_id, *, lang=None, quality=None, **kw):
        """bookID -> Title。title_id 是单集或书组的 GUID。"""
        client = self.ensure_client()
        d = self._get(client, "/Book", params={"bookID": title_id})
        return Title(
            source=self.name,
            id=str(d.get("BookID", title_id)),
            name=d.get("Title") or str(title_id),
            author="、".join(a.get("Name", "") for a in d.get("Authors") or []),
            cover_url=d.get("CoverURL") or "",
            description=d.get("Introduction") or "",
        )

    def get_chapters(self, title_id, *, lang=None, quality=None, **kw):
        """把该系列的各集当作 Chapter（number=Vol，id=单集 BookID）。"""
        client = self.ensure_client()
        d = self._get(client, "/Book", params={"bookID": title_id})
        vol_guid = d.get("BookGroupID")
        if not vol_guid:
            raise RuntimeError(f"no BookGroupID for {title_id}")
        vols = self._get(client, f"/Book/BookVol/{vol_guid}", params={"bookID": title_id})
        chapters = []
        for v in vols or []:
            chapters.append(Chapter(
                id=str(v.get("BookID")),
                number=v.get("Vol") or "",
                name=v.get("Vol") or "",
            ))
        return chapters

    def get_pages(self, chapter: Chapter, *, lang=None, quality=None, **kw):
        """Comic/sas 拿该集每页 ImageURL（Azure SAS 直链），需 token。

        集数若为付费/无免费试读（Comic/sas 返回 404），返回空列表 → driver 跳过该集。
        """
        client = self.ensure_client()
        if not self.token:
            raise RuntimeError("Tongli needs a token for /Comic/sas. "
                               "Set TONG_LI_TOKEN or ~/.mmdl/config.ini, or pass --token.")
        st, body = client.request(API_HOST, "GET", f"/Comic/sas/{chapter.id}",
                                  params={"freeTrialToken": FREE_TRIAL})
        if st != 200:
            return []   # 该集无免费试读/不可访问，跳过（driver 会打印 skip）
        import json
        data = json.loads(body.decode("utf-8")) if isinstance(body, bytes) else json.loads(body)
        pages = []
        for p in data.get("Pages") or []:
            url = p.get("ImageURL")
            if url:
                pages.append(Page(url=url, ext="jpg", mime="image/jpeg"))
        return pages

    def download_page(self, page: Page, chapter: Chapter, *, lang=None, quality=None,
                      client=None, **kw):
        """图片是 Azure SAS 签名直链，直接 GET 原始字节即可。"""
        if client is None:
            client = self.ensure_client()
        host, path = split_url(page.url)
        st, body = client.request(host, "GET", path)
        if st == 200 and body:
            return body
        raise RuntimeError(f"download page HTTP {st}")
