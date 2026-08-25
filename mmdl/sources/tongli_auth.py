"""東立 Firebase 认证：邮箱登录 + refreshToken 自动刷新（纯脚本，无浏览器）。

東立前端用 `firebase.auth().signInWithEmailAndPassword(email, password)` 登录
（ebook.tongli.com.tw/js/firebase_token.js 的 loginNormal），我们直接调它对应的
Firebase Auth REST API 拿 idToken / refreshToken：

- `idToken`（JWT，约 1 小时过期）是 `/Comic/sas` 需要的 `Authorization: bearer` 值
- `refreshToken`（Google 长期凭证）可反复换取新 idToken → 实现免手动的自动刷新

主入口 `resolve_access_token()`：静态 token（CLI/env/config.ini）优先 → 缓存
refreshToken 刷新 → 否则交互登录（首次）。**密码永不落盘**；落盘的只有 refreshToken。

已实测确认（2026-08）：identitytoolkit 的 signInWithPassword 在此项目（projectId=tongli-book,
apiKey=REDACTED）开放且无 reCAPTCHA 拦截非浏览器请求
（假凭据返回 EMAIL_NOT_FOUND 而非 operation-not-allowed / 验证码错误）。
"""
import getpass
import json
import os
from pathlib import Path
from configparser import ConfigParser

from mmdl.core.http import HttpConfig, HttpClient, split_url

FIREBASE_API_KEY = "REDACTED"
SIGNIN_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
REFRESH_URL = "https://securetoken.googleapis.com/v1/token"

# refreshToken 缓存路径（测试可覆盖）
CRED_FILE = Path.home() / ".mmdl" / "tongli_refresh.json"
TOKEN_KEY = "refresh_token"


def _http():
    return HttpClient(HttpConfig(content_type="application/json"))


def _raise_error(prefix, data):
    """把 Firebase 错误结构（data["error"].message 或 data.status）转成 RuntimeError。"""
    err = data.get("error") if isinstance(data, dict) else data
    msg = ""
    if isinstance(err, dict):
        msg = err.get("message") or ""
    elif isinstance(err, str):
        msg = err
    if not msg and isinstance(data, dict):
        msg = data.get("status") or data.get("message") or ""
    raise RuntimeError(f"{prefix}: {msg}" if msg else prefix)


def signin_password(email, password):
    """邮箱密码登录，返回 (idToken, refreshToken)。"""
    host, path = split_url(SIGNIN_URL)
    body = json.dumps({"email": email, "password": password, "returnSecureToken": True}).encode()
    st, resp = _http().request(host, "POST", path, body=body, content_type="application/json")
    if st != 200:
        _raise_error(f"Firebase signin HTTP {st}", json.loads(resp.decode()))
    data = json.loads(resp.decode())
    if "error" in data:
        _raise_error("Firebase signin failed", data)
    return data["idToken"], data["refreshToken"]


def refresh_access_token(refresh_token):
    """用 refreshToken 换新 idToken，返回 (idToken, 新 refreshToken)。"""
    host, path = split_url(REFRESH_URL)   # path 自带 ?key=...
    body = json.dumps({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": FIREBASE_API_KEY,
    }).encode()
    st, resp = _http().request(host, "POST", path, body=body, content_type="application/json")
    if st != 200:
        _raise_error(f"Firebase refresh HTTP {st}", json.loads(resp.decode()))
    data = json.loads(resp.decode())
    if "error" in data:
        _raise_error("Firebase refresh failed", data)
    return data["id_token"], data.get("refresh_token", refresh_token)


def load_refresh_token(path=CRED_FILE):
    """读取缓存的 refreshToken；不存在/为空返回 ''。"""
    try:
        p = Path(path)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8")).get(TOKEN_KEY, "") or ""
    except Exception:
        pass
    return ""


def save_refresh_token(refresh_token, path=CRED_FILE):
    """把 refreshToken 写缓存（0600）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({TOKEN_KEY: refresh_token}), encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass   # Windows 上 chmod 语义有限，忽略失败（不影响功能）


def static_token():
    """读静态 token（向后兼容 TONG_LI_TOKEN 或 ~/.mmdl/config.ini 的 [tongli] token）。"""
    env = os.environ.get("TONG_LI_TOKEN", "").strip()
    if env:
        return env
    try:
        cfg = Path.home() / ".mmdl" / "config.ini"
        if cfg.exists():
            cp = ConfigParser()
            cp.read(cfg, encoding="utf-8")
            return cp.get("tongli", "token", fallback="").strip()
    except Exception:
        pass
    return ""


def _login(email=None, password=None):
    """用环境变量或交互输入邮箱密码，返回 (idToken, refreshToken)。密码不落盘。"""
    email = (email or os.environ.get("TONG_LI_EMAIL", "")).strip()
    password = password or os.environ.get("TONG_LI_PASSWORD", "")
    if not email:
        email = input("東立 email: ").strip()
    if not password:
        password = getpass.getpass("東立 password: ")
    if not email or not password:
        raise RuntimeError("東立登录需要邮箱/密码：设 TONG_LI_EMAIL / TONG_LI_PASSWORD，或交互输入")
    return signin_password(email, password)


def resolve_access_token(cli_token=None, email=None, password=None, cred_file=CRED_FILE):
    """返回当前可用的 idToken。

    优先级：
    1. cli_token / 静态来源（TONG_LI_TOKEN、~/.mmdl/config.ini）—— 显式静态 idToken，直接用；
    2. 缓存的 refreshToken —— 自动刷新拿新 idToken；
    3. 邮箱密码登录（TONG_LI_EMAIL/TONG_LI_PASSWORD 环境变量，或交互输入）—— 首次。
    """
    static = cli_token or static_token()
    if static:
        return static.strip()
    rt = load_refresh_token(cred_file)
    if rt:
        try:
            idt, new_rt = refresh_access_token(rt)
            if new_rt != rt:
                save_refresh_token(new_rt, cred_file)
            return idt
        except Exception:
            pass   # refresh 失效，降级为交互登录
    idt, rt = _login(email=email, password=password)
    save_refresh_token(rt, cred_file)
    return idt
