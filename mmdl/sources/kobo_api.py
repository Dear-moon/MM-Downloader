"""Kobo 激活与下载 API：网页激活流 → device 注册 → token 持久化 → 下载整本 .kepub。

鉴权（网页激活流，非密码直传）：
  1. GET auth.kobobooks.com/ActivateOnWeb —— 浏览器授权（用户登录；仅邮箱）
  2. POST storeapi.kobo.com/v1/auth/device   —— body 含 UserKey → 拿 deviceId/userId/token
  3. POST storeapi.kobo.com/v1/auth/refresh  —— accessToken 续期

留存 accessToken / userKey / deviceId / userId / hashKey 到 ~/.mmdl/kobo.json
（仿 tongli_auth 的 tongli_refresh.json，URL 秘钥不落盘，密码不落盘）。

⚠️ Store API 的 host/path、header 参数与 content_keys 结构均为服务端下发，需真实漫画实测校准。
   经 KOBO_API_BASE / KOBO_API_KEY 环境变量可配置；失败时抛出含状态/响应摘要的可读错误。
"""
import getpass
import json
import os
import re
import time
from pathlib import Path

from mmdl.core.http import HttpClient, HttpConfig, split_url
from mmdl.core.cdp import CdpClient, DEFAULT_CDP_URL

AUTH_PAGE = "https://auth.kobobooks.com/ActivateOnWeb"
STORE_API = os.environ.get("KOBO_API_BASE", "https://storeapi.kobo.com")
API_KEY = os.environ.get("KOBO_API_KEY", "")
CRED_FILE = Path.home() / ".mmdl" / "kobo.json"

_TOKEN_FIELDS = ("accessToken", "refreshToken", "userKey", "deviceId", "userId", "hashKey", "expiresAt")


def _http() -> HttpClient:
    return HttpClient(HttpConfig(content_type="application/json"))


def _store() -> tuple[str, str]:
    return split_url(STORE_API)


# ---- token 持久化（仿 tongli_refresh.json）----
def load_tokens(path=CRED_FILE) -> dict:
    try:
        p = Path(path)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def save_tokens(tokens: dict, path=CRED_FILE):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(tokens), encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


# ---- 设备激活 ----
def _wait_for_userkey(client, timeout=180):
    """在激活页轮询，抓取授权完成后带 userkey 的 URL / 页面文本。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        href = client.eval("location.href") or ""
        for m in re.finditer(r"userkey=([A-Za-z0-9+/=%_-]+)", href, re.I):
            return m.group(1)
        txt = client.eval("(document.body.innerText||'')") or ""
        m = re.search(r"userkey\s*[:=]\s*([A-Za-z0-9+/=%_-]+)", txt, re.I)
        if m:
            return m.group(1)
        time.sleep(2)
    raise RuntimeError("Kobo activate: 超时未捕获到 userkey（需在激活页完成登录/授权）")


def _register_device(userkey, serial=None):
    """device 注册：POST /v1/auth/device。payload/头按 Kobo 逆向默认；经实测校准。"""
    serial = serial or os.environ.get("KOBO_SERIAL", f"MMDL-{int(time.time())}")
    payload = {
        "ApiKey": API_KEY,
        "AppId": "KoboForPC",
        "AppVersion": "4.38.21971",
        "SerialNumber": serial,
        "UserKey": userkey,
    }
    host, base = _store()
    body = json.dumps(payload).encode()
    st, resp = _http().request(host, "POST", base + "/v1/auth/device",
                               body=body, content_type="application/json")
    if st != 200:
        raise RuntimeError(f"Kobo device auth HTTP {st}: {resp.decode('utf-8', 'ignore')[:300]}")
    data = json.loads(resp.decode())
    return data


def setup(cdp_url=DEFAULT_CDP_URL, path=CRED_FILE) -> dict:
    """一次性激活：CDP 打开 ActivateOnWeb → 用户登录 → 抓 userkey → 设备注册 → 存 token。

    需本地 debug 浏览器（含 websocket-client）。返回并持久化 token dict。
    """
    client = CdpClient(cdp_url=cdp_url, target_url_substr="")
    try:
        client.connect()
        client.eval(f"location.href='{AUTH_PAGE}'")
        userkey = _wait_for_userkey(client)
    finally:
        client.close()
    serial = os.environ.get("KOBO_SERIAL", f"MMDL-{int(time.time())}")
    data = _register_device(userkey, serial)
    tokens = {
        "accessToken": data.get("AccessToken") or data.get("accessToken") or "",
        "refreshToken": data.get("RefreshToken") or data.get("refreshToken") or "",
        "userKey": userkey,
        # serial 是 Obok deviceid 派生的输入（SHA256(hash_key+serial)）；服务器返回的 DeviceId 另存
        "serial": serial,
        "deviceId": data.get("DeviceId") or data.get("deviceId") or data.get("deviceID") or "",
        "userId": data.get("UserId") or data.get("userId") or data.get("userID") or "",
        "hashKey": os.environ.get("KOBO_HASH_KEY", "88b3a2e13"),
        "expiresAt": data.get("AccessTokenExpiry")
        or data.get("accessTokenExpiry")
        or data.get("expiresIn", "") or "",
    }
    save_tokens(tokens, path)
    return tokens


# ---- 鉴权 ----
def _headers(tokens, extra=None):
    h = {}
    if tokens.get("accessToken"):
        h["Authorization"] = f"Bearer {tokens['accessToken']}"
    if API_KEY:
        h["x-api-key"] = API_KEY
    if extra:
        h.update(extra)
    return h


def _expired(tokens):
    exp = tokens.get("expiresAt")
    # 仅当字段"缺失"时才视为未知；0 是真实过期，不能走这里
    if exp is None or exp == "":
        return not tokens.get("accessToken")
    try:
        return time.time() > float(exp)
    except (ValueError, TypeError):
        return False


def ensure_tokens(tokens=None, path=CRED_FILE) -> dict:
    """保证有可用 accessToken：读档 → 若过期则 /auth/refresh → 回写。"""
    tokens = tokens or load_tokens(path)
    if tokens.get("accessToken") and not _expired(tokens):
        return tokens
    if not tokens.get("refreshToken"):
        raise RuntimeError("Kobo 未激活：请先 `--source kobo --setup`")
    body = json.dumps({"refresh_token": tokens["refreshToken"]}).encode()
    host, base = _store()
    st, resp = _http().request(host, "POST", base + "/v1/auth/refresh",
                               body=body, content_type="application/json",
                               headers=_headers(tokens))
    if st != 200:
        raise RuntimeError(f"Kobo refresh HTTP {st}: {resp.decode('utf-8', 'ignore')[:300]}")
    data = json.loads(resp.decode())
    tokens["accessToken"] = data.get("AccessToken") or data.get("accessToken") or tokens["accessToken"]
    tokens["refreshToken"] = data.get("RefreshToken") or data.get("refreshToken") or tokens["refreshToken"]
    tokens["expiresAt"] = data.get("AccessTokenExpiry") or data.get("accessTokenExpiry") or ""
    save_tokens(tokens, path)
    return tokens


# ---- 下载 ----
def download_book(book_id: str, tokens=None, path=CRED_FILE):
    """下载整本 .kepub。返回 (kepub_bytes, content_keys: dict) — elementkey 供 kobo_drm 使用。

    端点从 /v1/initialization 的 Resources 下发（library_sync / content_access_book 模板），
    真实路径与 content_keys 结构需实测；此处按初始化解出的模板拼接并做可读报错。
    """
    tokens = ensure_tokens(tokens, path)
    host, _ = _store()
    return _download_from_api(host, book_id, tokens)


def _download_from_api(host, book_id, tokens):
    st, resp = _http().request(host, "GET", "/v1/initialization", headers=_headers(tokens))
    if st != 200:
        raise RuntimeError(f"Kobo initialization HTTP {st}: {resp.decode('utf-8', 'ignore')[:300]}")
    init = json.loads(resp.decode())

    resource = None
    resources = init.get("Resources") or init.get("resources") or {}
    for key in resources:
        if "content_access" in key.lower() or "download" in key.lower():
            resource = resources[key]
            break
    if resource is None:
        raise RuntimeError("Kobo initialization 缺少 content_access_book / download 模板（需实测）")

    template = resource
    if isinstance(resource, dict):
        template = next((v for v in resource.values() if isinstance(v, str)), "")

    book_url = template.format(ContentId=book_id, contentId=book_id) if "{" in template else template
    if book_url.startswith("http"):
        dl_host, url = split_url(book_url)
    else:  # 相对路径 → 拼到 storeapi host
        dl_host, url = host, book_url

    st, body = _http().request(dl_host, "GET", url, headers=_headers(tokens))
    if st != 200:
        raise RuntimeError(f"Kobo download HTTP {st}: {body.decode('utf-8', 'ignore')[:300]}")

    content_keys = _extract_content_keys(init, book_id)
    return body, content_keys


def _extract_content_keys(init, book_id):
    """从初始化响应尝试提取 content_keys（elementkey 集）；结构需实测，取不到返回空 dict。"""
    ck = init.get("contentKeys") or init.get("ContentKeys")
    if isinstance(ck, dict):
        return ck
    if isinstance(ck, list):
        out = {}
        for item in ck:
            if isinstance(item, dict):
                name = item.get("ContentId") or item.get("contentId") or item.get("File") or ""
                key = item.get("ContentKey") or item.get("contentKey") or item.get("elementKey") or ""
                if name and key:
                    out[name] = key
        return out
    return {}
