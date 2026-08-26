"""Adobe .acsm 兑现（ADEPT）—— 把 .acsm 兑换成带 DRM 的 epub，再交 adept_drm 解密。

从 DeDRM_tools（GPL）libadobeFulfill/fulfill 协议**算法独立重写**为 MIT，复用 adobe_crypto（签名）、
adept_drm（内容解密）、adobe_auth 的激活态。解用户凭证 pkcs12 需 `cryptography`（requirements 已加）。

流程：parse_acsm → 签 fulfill 请求(用户私钥) → operatorAuth(Auth) → POST /Fulfill
→ fulfillmentResult(src/licenseToken) → fetchLicenseService → buildRights → download(src)
→ 带 rights.xml 的 epub → adept_drm.decrypt_epub。

⚠️ 报文构造可本地单测（build_auth/build_fulfill/build_rights）；**完整链路需连 operator/Adobe 实测**。
"""
import base64
import io
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12 as _pkcs12

from .adobe_crypto import sign_node, add_nonce
from mmdl.core.http import HttpClient, HttpConfig, split_url

_ADE = "http://ns.adobe.com/adept"


def _ad(tag):
    return "{%s}%s" % (_ADE, tag)


def _http():
    return HttpClient(HttpConfig(user_agent="book2png",
                                content_type="application/vnd.adobe.adept+xml"))


def _read_text(account_dir, name):
    p = Path(account_dir) / name
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _text(node, *tags):
    cur = node
    for tag in tags:
        cur = cur.find(_ad(tag))
        if cur is None:
            return ""
    return (cur.text or "").strip()


def parse_acsm(acsm_bytes) -> dict:
    """解析 .acsm → dict（operatorURL/transaction/resource/书名）。"""
    root = ET.fromstring(acsm_bytes)
    title = ""
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == "title" and el.text and el.text.strip():
            title = el.text.strip()
            break
    return {
        "operatorURL": _text(root, "operatorURL"),
        "transaction": _text(root, "transaction"),
        "resource": _text(root, "resourceItemInfo", "resource"),
        "title": title,
    }


# ---- 用户凭证 ----
def load_user_credentials(account_dir):
    """activation.xml 的 credentials/pkcs12（devkey 解）→ (priv_der, cert_der)。cryptography 解 pkcs12。"""
    act = ET.fromstring(_read_text(account_dir, "activation.xml"))
    p12 = act.find(f".//{_ad('credentials')}/{_ad('pkcs12')}")
    if p12 is None or not p12.text:
        raise RuntimeError("adobe: activation.xml 无 credentials/pkcs12")
    devsalt = (Path(account_dir) / "devicesalt").read_bytes()
    # pkcs12 密码 = base64(devsalt)（libadobe: parse_pkcs12(pkcs12, b64encode(devkey))），非 raw devsalt
    loaded = _pkcs12.load_pkcs12(base64.b64decode(p12.text), base64.b64encode(devsalt))
    priv_der = loaded.key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption())
    return priv_der, loaded.cert.certificate.public_bytes(serialization.Encoding.DER)


def export_user_key(account_dir) -> bytes:
    """activation.xml 的 credentials/privateLicenseKey 剥 26B PKCS#8 头 → 用户 RSA 私钥（供 adept_drm 解密）。"""
    act = ET.fromstring(_read_text(account_dir, "activation.xml"))
    elem = act.find(f".//{_ad('credentials')}/{_ad('privateLicenseKey')}")
    if elem is None:
        raise RuntimeError("adobe: activation.xml 无 credentials/privateLicenseKey")
    return base64.b64decode(elem.text)[26:]


# ---- 报文构造（可本地单测）----
def build_auth_request(account_dir, cert_der) -> str:
    act = ET.fromstring(_read_text(account_dir, "activation.xml"))
    user = act.findtext(f".//{_ad('credentials')}/{_ad('user')}")
    lic_cert = act.findtext(f".//{_ad('credentials')}/{_ad('licenseCertificate')}")
    auth_cert = act.findtext(f".//{_ad('credentials')}/{_ad('authenticationCertificate')}")
    return "".join([
        '<?xml version="1.0"?>',
        f'<adept:credentials xmlns:adept="{_ADE}">',
        f"<adept:user>{user}</adept:user>",
        f"<adept:certificate>{base64.b64encode(cert_der).decode()}</adept:certificate>",
        f"<adept:licenseCertificate>{lic_cert}</adept:licenseCertificate>",
        f"<adept:authenticationCertificate>{auth_cert}</adept:authenticationCertificate>",
        "</adept:credentials>",
    ])


def build_fulfill_request(account_dir, acsm_root) -> str:
    dev = ET.fromstring(_read_text(account_dir, "device.xml"))
    act = ET.fromstring(_read_text(account_dir, "activation.xml"))
    user = act.findtext(f".//{_ad('credentials')}/{_ad('user')}")
    device = act.findtext(f".//{_ad('activationToken')}/{_ad('device')}")
    fp = dev.findtext(_ad("fingerprint"))
    dtype = dev.findtext(_ad("deviceType"))
    hobbes = dev.findtext(_ad("version") + "[@name='hobbes']") or ""
    client_os = dev.findtext(_ad("version") + "[@name='clientOS']") or ""
    locale = dev.findtext(_ad("version") + "[@name='clientLocale']") or ""
    acsm_str = ET.tostring(acsm_root, encoding="unicode", xml_declaration=False)
    return "".join([
        '<?xml version="1.0"?>',
        f'<adept:fulfill xmlns:adept="{_ADE}">',
        f"<adept:user>{user}</adept:user>",
        f"<adept:device>{device}</adept:device>",
        f"<adept:deviceType>{dtype}</adept:deviceType>",
        acsm_str,
        "<adept:targetDevice>",
        f"<adept:softwareVersion>{hobbes}</adept:softwareVersion>",
        f"<adept:clientOS>{client_os}</adept:clientOS>",
        f"<adept:clientLocale>{locale}</adept:clientLocale>",
        f"<adept:deviceType>{dtype}</adept:deviceType>",
        "<adept:productName>ADOBE Digitial Editions</adept:productName>",
        f"<adept:fingerprint>{fp}</adept:fingerprint>",
        f"<adept:activationToken><adept:user>{user}</adept:user>"
        f"<adept:device>{device}</adept:device></adept:activationToken>",
        "</adept:targetDevice>",
        "</adept:fulfill>",
    ])


def build_init_license_service_request(account_dir, operator_url, priv_der) -> str:
    """<adept:licenseServiceRequest> 报文，供 operator Auth 后 InitLicenseService。"""
    act = ET.fromstring(_read_text(account_dir, "activation.xml"))
    user = act.findtext(f".//{_ad('credentials')}/{_ad('user')}")
    nonce, exp = add_nonce()
    parts = [
        '<?xml version="1.0"?>',
        f'<adept:licenseServiceRequest xmlns:adept="{_ADE}" identity="user">',
        f"<adept:operatorURL>{operator_url}</adept:operatorURL>",
        f"<adept:nonce>{nonce}</adept:nonce>",
        f"<adept:expiration>{exp}</adept:expiration>",
        f"<adept:user>{user}</adept:user>",
    ]
    body = "".join(parts) + "</adept:licenseServiceRequest>"
    sig = sign_node(ET.fromstring(body), priv_der)
    return body.replace("</adept:licenseServiceRequest>",
                        f"<adept:signature>{sig}</adept:signature></adept:licenseServiceRequest>")


def build_rights(license_token_node, account_dir) -> str:
    act = ET.fromstring(_read_text(account_dir, "activation.xml"))
    lic_url = _text(license_token_node, "licenseURL")
    cert = ""
    for info in act.findall(f".//{_ad('licenseServices')}/{_ad('licenseServiceInfo')}"):
        if info.findtext(_ad("licenseURL")) == lic_url:
            cert = info.findtext(_ad("certificate")) or ""
            break
    return "".join([
        '<?xml version="1.0"?>',
        f'<adept:rights xmlns:adept="{_ADE}">',
        ET.tostring(license_token_node, encoding="unicode", xml_declaration=False),
        "<adept:licenseServiceInfo>",
        f"<adept:licenseURL>{lic_url}</adept:licenseURL>",
        f"<adept:certificate>{cert}</adept:certificate>",
        "</adept:licenseServiceInfo>",
        "</adept:rights>",
    ])


class FulfillError(RuntimeError):
    """兑现失败（网络/服务器/协议）。"""


def fulfill_acsm(account_dir, acsm_bytes) -> tuple[bytes, dict, str]:
    """兑现 .acsm → (带 rights.xml 的加密 epub 字节, meta, resource_id)。需连 operator/Adobe 实测。"""
    meta = parse_acsm(acsm_bytes)
    acsm_root = ET.fromstring(acsm_bytes)
    priv_der, cert_der = load_user_credentials(account_dir)

    op_url = meta["operatorURL"]
    # operatorAuth = doOperatorAuth（libgourou）：POST Auth + POST InitLicenseService
    auth_url = op_url[:-1] if op_url.endswith("/Fulfill") else op_url   # libgourou: 去尾部 /Fulfill
    ah, ap = split_url(auth_url + "/Auth")
    req = build_auth_request(account_dir, cert_der)
    st, body = _http().request(ah, "POST", ap, body=req.encode("utf-8"),
                               content_type="application/vnd.adobe.adept+xml")
    if not (st == 200 and b"<success" in body):
        raise FulfillError(f"adobe operator Auth HTTP {st}: {body.decode('utf-8','ignore')[:200]}")

    # InitLicenseService（建立 license 服务认证，E_ADEPT_USER_AUTH 的关键）
    act = ET.fromstring(_read_text(account_dir, "activation.xml"))
    activation_url = act.findtext(f".//{_ad('activationServiceInfo')}/{_ad('activationURL')}")
    if activation_url:
        init_req = build_init_license_service_request(account_dir, auth_url, priv_der)
        ih, ip = split_url(activation_url)
        st3, body3 = _http().request(ih, "POST", ip + "/InitLicenseService", body=init_req.encode("utf-8"),
                                     content_type="application/vnd.adobe.adept+xml")
        if not (st3 == 200 and b"<success" in body3):
            raise FulfillError(f"adobe InitLicenseService HTTP {st3}: {body3.decode('utf-8','ignore')[:200]}")

    # 签名 + POST /Fulfill
    fulfill_req = build_fulfill_request(account_dir, acsm_root)
    node = ET.fromstring(fulfill_req)
    sig = sign_node(node, priv_der)
    node.append(ET.fromstring(f"<adept:signature xmlns:adept=\"{_ADE}\">{sig}</adept:signature>"))
    signed = ET.tostring(node, encoding="unicode")
    fh, fp = split_url(op_url + "/Fulfill")
    st2, body2 = _http().request(fh, "POST", fp, body=signed.encode("utf-8"),
                                 content_type="application/vnd.adobe.adept+xml")
    if st2 != 200 or b"<error" in body2:
        raise FulfillError(f"adobe fulfill HTTP {st2}: {body2.decode('utf-8','ignore')[:200]}")

    resp = ET.fromstring(body2)
    src = _text(resp, "fulfillmentResult", "resourceItemInfo", "src")
    res_id = _text(resp, "fulfillmentResult", "resourceItemInfo", "resource")
    lic_tok = resp.find(f".//{_ad('fulfillmentResult')}/{_ad('resourceItemInfo')}/{_ad('licenseToken')}")
    if lic_tok is None:
        raise FulfillError("adobe fulfill 响应缺 licenseToken")
    lic_url = _text(lic_tok, "licenseURL")

    # 下载 → 写 rights.xml → 返回
    epub = _download(src)
    rights = build_rights(lic_tok, account_dir)
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(epub)) as zin:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                zout.writestr(item, zin.read(item.filename))
            zout.writestr("META-INF/rights.xml", rights.encode("utf-8"))
    return buf.getvalue(), meta, res_id


def _download(url) -> bytes:
    host, path = split_url(url)
    st, body = _http().request(host, "GET", path)
    if st != 200:
        raise FulfillError(f"adobe download HTTP {st}")
    return body
