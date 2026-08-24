"""归一化数据模型：所有 source 的输出统一映射到这些结构。"""
from dataclasses import dataclass, field


@dataclass
class Title:
    source: str                 # 来源名，如 "mangamillion"
    id: str                     # source 本地标识；MangaMillion/Tongli 用 id，BW 用 url 哈希
    name: str
    author: str = ""
    cover_url: str = ""
    description: str = ""


@dataclass
class Chapter:
    id: str
    number: str = ""            # 字符串含数字，供 range 过滤（_num）
    name: str = ""
    pages: list["Page"] | None = None   # 预取页（BW capture 时填充）；None = 需现拉


@dataclass
class Page:
    url: str | None = None
    data: bytes | None = None   # BW 拦截拿到的原图字节 / 已解密字节
    ext: str = "webp"           # 落盘扩展名（写入 NNN.ext）
    mime: str = "image/webp"


@dataclass
class CaptureResult:
    """capture 轨（BookWalker）的输出：URL 直接给出一批含字节的章节。"""
    title: Title
    chapters: list[Chapter]     # 每个 Chapter.pages 已填充 data
