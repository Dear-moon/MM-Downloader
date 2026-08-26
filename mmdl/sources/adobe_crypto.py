"""Adobe ADEPT 客户端加密/签名层（anonymous 设备激活与 .acsm 兑现共用）。

按 libgourou/DeDRM 公开算法**独立重写**（不复制 GPL 代码）。纯本地 pycryptodome，
sign_node 对 XML 节点的 Adobe 规范化哈希做 PKCS#1 v1.5 textbook RSA 签名。
可本地单测：serial/fingerprint/nonce/hash_node/sign_node。
"""
import base64
import hashlib
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET

from Crypto.Cipher import AES
from Crypto.PublicKey import RSA
from Crypto.Random import get_random_bytes

_ADEPT_NS = "http://ns.adobe.com/adept"

# Adobe 规范化哈希标记（ASN 标签）
_ASN_NS_TAG = 1
_ASN_CHILD = 2
_ASN_END_TAG = 3
_ASN_TEXT = 4
_ASN_ATTRIBUTE = 5


def make_serial() -> str:
    """随机设备序列号：sha1(256 随机字节) hex 小写。"""
    return hashlib.sha1(get_random_bytes(256)).hexdigest().lower()


def make_fingerprint(serial: str, devsalt: bytes) -> str:
    """设备指纹：base64(sha1(serial + devsalt))，<=20B。"""
    digest = hashlib.sha1((serial + devsalt.decode("latin-1")).encode("latin-1")).digest()
    return base64.b64encode(digest)


def encrypt_with_device_key(data: bytes, devsalt: bytes) -> bytes:
    """AES-256-CBC，随机 IV，PKCS7；返回 iv || encrypted。"""
    iv = get_random_bytes(16)
    pad = 16 - len(data) % 16 or 16
    padded = data + bytes([pad]) * pad
    return iv + AES.new(devsalt, AES.MODE_CBC, iv).encrypt(padded)


def decrypt_with_device_key(data: bytes, devsalt: bytes) -> bytes:
    """IV(v16) + AES-CBC(devsalt) 解密，去 PKCS7。"""
    plain = AES.new(devsalt, AES.MODE_CBC, data[:16]).decrypt(data[16:])
    return plain[:-plain[-1]] if plain[-1] <= 16 else plain


def add_nonce(now=None) -> tuple[str, str]:
    """返回 (nonce_b64, expiration)。nonce = base64(8B 小端 unix毫秒+62167219200000 || 4B 小端0)。"""
    dt = now or datetime.now(timezone.utc)
    sec = int((dt - datetime(1970, 1, 1, tzinfo=timezone.utc)).total_seconds() * 1000) + 62167219200000
    payload = sec.to_bytes(8, "little") + b"\x00" * 4
    exp = (dt + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return base64.b64encode(payload).decode("utf-8"), exp


# ---- XML 规范化哈希（对 nodes 递归，顺序：ns, name, attrs, CHILD, text, children, END_TAG）----
def _hash_do_append_string(ctx, s):
    b = s.encode("utf-8")
    ctx.update(bytes([len(b) // 256, len(b) & 0xFF]) + b)


def _hash_do_append_tag(ctx, tag):
    if tag <= 5:
        ctx.update(bytes([tag]))


def _hash_node_ctx(node, ctx):
    ns, local = node.tag.rsplit("}", 1) if "}" in node.tag else ("", node.tag)
    ns = ns.lstrip("{")   # 关键：rsssplit 会留下 Clark 记法前导 `{`，否则 namespace 多 1 字节 → 签名失效
    if local in ("hmac", "signature") and ns == _ADEPT_NS:
        return           # Adobe hmac/signature 不入哈希
    _hash_do_append_tag(ctx, _ASN_NS_TAG)
    _hash_do_append_string(ctx, ns)
    _hash_do_append_string(ctx, local)
    for attr in sorted(node.keys()):
        ans, aloc = attr.rsplit("}", 1) if "}" in attr else ("", attr)
        ans = ans.lstrip("{")
        _hash_do_append_tag(ctx, _ASN_ATTRIBUTE)
        _hash_do_append_string(ctx, ans)
        _hash_do_append_string(ctx, aloc)
        _hash_do_append_string(ctx, node.get(attr))
    _hash_do_append_tag(ctx, _ASN_CHILD)
    if node.text and node.text.strip():
        text = node.text.strip()
        for i in range(0, len(text), 0x7FFF):
            _hash_do_append_tag(ctx, _ASN_TEXT)
            _hash_do_append_string(ctx, text[i:i + 0x7FFF])
    for child in node:
        if isinstance(child, ET.Element):
            _hash_node_ctx(child, ctx)
    _hash_do_append_tag(ctx, _ASN_END_TAG)


def hash_node(node) -> bytes:
    """对 XML 节点做 Adobe 规范化哈希（SHA1），返回 digest bytes。node 为 ElementTree 元素。"""
    ctx = hashlib.sha1()
    _hash_node_ctx(node, ctx)
    return ctx.digest()


# ---- textbook RSA 签名（PKCS#1 v1.5）：00 01 FF.. 00 message，pow(message, d, n) ----
def rsapad_sign(priv_der: bytes, message: bytes) -> bytes:
    key = RSA.importKey(priv_der)
    keylen = (key.n.bit_length() + 7) // 8
    if len(message) > keylen - 11:
        raise ValueError("message too long for RSA sign")
    padlen = keylen - len(message) - 3
    block = b"\x00\x01" + b"\xff" * padlen + b"\x00" + message
    m = int.from_bytes(block, "big")
    if m >= key.n:
        raise ValueError("message block >= modulus")
    return pow(m, key.d, key.n).to_bytes(keylen, "big")


def sign_node(node, priv_der: bytes) -> str:
    """ADEPT 签名：PKCS#1 v1.5 直接包裹 SHA1 摘要（**不套 DigestInfo**）。

    libgourou padWithPKCS1 / DeDRM pad_message 均只做 `00 01 FF..FF 00 <20B哈希>`。套 DigestInfo
    会被 Adobe 判"无法解析" → E_ADEPT_USER_AUTH（此前踩坑）。
    """
    sig = rsapad_sign(priv_der, hash_node(node))
    return base64.b64encode(bytes(sig)).decode("utf-8")


# ---- 自拟设备 RSA 对 ----
def make_device_keypair() -> bytes:
    """生成 2048-bit 设备 RSA 私钥（DER，PKCS#8），供 activateDevice/fulfill 签名用。"""
    return RSA.generate(2048).exportKey("DER")
