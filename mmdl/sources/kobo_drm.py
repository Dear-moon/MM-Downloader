"""内联 Kobo Obok 解密（AES-128-ECB 双层 + PKCS7 + gzip 展开）。

只依赖 pycryptodome，不引入 DeDRM_tools。密钥派生与每文件解密公式按
docs/kobo_source_design.md：

  deviceid = SHA256_hex(hash_key + serial)
  userkey  = unhex(SHA256_hex(deviceid + userid)[32:])        # 后 16B = AES-128 密钥
  page_key = AES_ECB_decrypt(base64(elementkey), userkey)
  plain    = AES_ECB_decrypt(文件字节, page_key)，去 PKCS#7，若 gzip 魔数再 gunzip

elementkey 优先从 .kepub 的 META-INF/encryption.xml（每文件 CipherReference@EncryptionKey）
读取；缺失时用调用方给的 content_keys（下载端点返回，dict: zip名->base64 elementkey）兜底。
"""
import base64
import gzip
import io
import zipfile
import xml.etree.ElementTree as ET
from hashlib import sha256

from Crypto.Cipher import AES

HASH_KEYS = ("88b3a2e13", "XzUhGYdFp", "NoCanLook", "QJhwzAtXL")

# elementkey 通常为 base64 的 16B 密文；AES-128-ECB 每块 16B
_BLOCK = 16


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _unpad_pkcs7(data):
    pad = data[-1]
    if pad and pad <= _BLOCK and data[-pad:] == bytes([pad]) * pad:
        return data[:-pad]
    return data


def device_id(serial: str, hash_key: str = HASH_KEYS[0]) -> str:
    """由设备序列号派生 Kobo deviceid：sha256(hash_key + serial) 的 hex。"""
    return sha256((hash_key + serial).encode("utf-8")).hexdigest()


def user_key(deviceid: str, userid: str) -> bytes:
    """由 deviceid + userid 派生 16B AES 密钥（SHA256 hex 的后 16B）。"""
    h = sha256((deviceid + userid).encode("utf-8")).hexdigest()
    return bytes.fromhex(h[32:])


def _load_encryption_keymap(src: zipfile.ZipFile, content_keys=None) -> dict:
    """返回 {zip条目名: elementkey_bytes}。优先 encryption.xml，缺失用 content_keys 兜底。"""
    keymap = {}
    try:
        raw = src.read("META-INF/encryption.xml")
    except KeyError:
        raw = b""
    if raw:
        root = ET.fromstring(raw)
        for cd in root.iter():
            if _local(cd.tag) != "CipherReference":
                continue
            uri, ek = None, None
            for key, val in cd.attrib.items():
                k = _local(key)
                if k == "URI":
                    uri = val
                elif k == "EncryptionKey":
                    ek = val
            if uri and ek:
                try:
                    keymap[uri] = base64.b64decode(ek)
                except (ValueError, TypeError):
                    pass
    if content_keys:
        for k, v in content_keys.items():
            keymap.setdefault(k, base64.b64decode(v) if isinstance(v, str) else v)
    return keymap


def _decrypt_entry(data: bytes, elementkey: bytes, userkey: bytes) -> bytes:
    """单文件：elementkey → page_key → AES-ECB 解密 → 去 PKCS7 → 必要时 gunzip。"""
    page_key = AES.new(userkey, AES.MODE_ECB).decrypt(elementkey)
    if len(data) % _BLOCK:
        # Kobo 块加密文件应为 16B 的整数倍
        data = data[: len(data) - len(data) % _BLOCK]
    plain = AES.new(page_key, AES.MODE_ECB).decrypt(data)
    plain = _unpad_pkcs7(plain)
    if plain[:2] == b"\x1f\x8b":
        try:
            plain = gzip.decompress(plain)
        except OSError:
            pass
    return plain


def decrypt_kepub(kepub_bytes: bytes, deviceid: str, userid: str, content_keys=None) -> bytes:
    """解密整本 .kepub，返回重建后的明文 zip（epub）字节。

    加密条目替换为明文，其余条目原样保留；密钥派生自 deviceid+userid。
    """
    userkey = user_key(deviceid, userid)
    src = zipfile.ZipFile(io.BytesIO(kepub_bytes))
    keymap = _load_encryption_keymap(src, content_keys)
    buf = io.BytesIO()
    out = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)
    for item in src.infolist():
        data = src.read(item.filename)
        ek = keymap.get(item.filename)
        if ek:
            data = _decrypt_entry(data, ek, userkey)
        out.writestr(item, data)
    out.close()
    return buf.getvalue()
