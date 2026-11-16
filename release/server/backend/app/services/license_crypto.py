from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

PRODUCT_NAME = "spectrum-mosaic"
PRIVATE_NAME = "ed25519_private.pem"
PUBLIC_NAME = "ed25519_public.pem"


class LicenseError(Exception):
    pass


def _canonical_payload(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _parse_expires(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def init_keys(keys_dir: Path) -> tuple[Path, Path]:
    keys_dir.mkdir(parents=True, exist_ok=True)
    priv_path = keys_dir / PRIVATE_NAME
    pub_path = keys_dir / PUBLIC_NAME
    if priv_path.exists() and pub_path.exists():
        return priv_path, pub_path
    signing = SigningKey.generate()
    priv_path.write_bytes(signing.encode())
    pub_path.write_bytes(signing.verify_key.encode())
    priv_path.chmod(0o600)
    return priv_path, pub_path


def load_signing_key(priv_path: Path) -> SigningKey:
    if not priv_path.exists():
        raise LicenseError("签发私钥不存在，请先初始化密钥")
    return SigningKey(priv_path.read_bytes())


def load_verify_key(pub_path: Path) -> VerifyKey:
    if not pub_path.exists():
        raise LicenseError("验签公钥不存在")
    return VerifyKey(pub_path.read_bytes())


def normalize_machine_id(value: str) -> str:
    text = value.strip().lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise LicenseError("machine_id 须为 64 位十六进制")
    return text


def sign_license(payload: dict[str, Any], private_key_path: Path) -> dict[str, Any]:
    required = {"license_id", "customer", "product", "release_version", "starts_at", "expires_at", "issued_at"}
    missing = required - set(payload)
    if missing:
        raise LicenseError(f"许可字段缺失: {', '.join(sorted(missing))}")
    if payload.get("product") != PRODUCT_NAME:
        raise LicenseError(f"product 必须为 {PRODUCT_NAME}")
    if payload.get("machine_id"):
        payload["machine_id"] = normalize_machine_id(str(payload["machine_id"]))
    starts = _parse_expires(str(payload["starts_at"]))
    expires = _parse_expires(str(payload["expires_at"]))
    if starts >= expires:
        raise LicenseError("生效时间必须早于到期时间")
    key = load_signing_key(private_key_path)
    signed = key.sign(_canonical_payload(payload))
    return {
        "payload": payload,
        "signature": base64.b64encode(signed.signature).decode("ascii"),
    }


def verify_license(
    doc: dict[str, Any],
    public_key_path: Path,
    *,
    now: datetime | None = None,
    expected_product: str = PRODUCT_NAME,
) -> dict[str, Any]:
    if not isinstance(doc, dict) or "payload" not in doc or "signature" not in doc:
        raise LicenseError("许可格式无效")
    payload = doc["payload"]
    if not isinstance(payload, dict):
        raise LicenseError("许可 payload 无效")
    if payload.get("product") != expected_product:
        raise LicenseError("许可产品不匹配")
    try:
        sig = base64.b64decode(str(doc["signature"]), validate=True)
    except Exception as exc:  # noqa: BLE001
        raise LicenseError("许可签名无法解码") from exc
    key = load_verify_key(public_key_path)
    try:
        key.verify(_canonical_payload(payload), sig)
    except BadSignatureError as exc:
        raise LicenseError("许可签名无效") from exc
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if "starts_at" in payload:
        starts = _parse_expires(str(payload["starts_at"]))
        if current < starts:
            raise LicenseError("许可尚未生效")
    expires = _parse_expires(str(payload["expires_at"]))
    if current > expires:
        raise LicenseError("许可已过期")
    return payload


def sign_lease(payload: dict[str, Any], private_key_path: Path) -> dict[str, Any]:
    required = {"license_id", "machine_id", "issued_at", "expires_at"}
    missing = required - set(payload)
    if missing:
        raise LicenseError(f"租约字段缺失: {', '.join(sorted(missing))}")
    payload["machine_id"] = normalize_machine_id(str(payload["machine_id"]))
    issued = _parse_expires(str(payload["issued_at"]))
    expires = _parse_expires(str(payload["expires_at"]))
    if issued >= expires:
        raise LicenseError("租约生效时间必须早于到期时间")
    key = load_signing_key(private_key_path)
    signed = key.sign(_canonical_payload(payload))
    return {
        "payload": payload,
        "signature": base64.b64encode(signed.signature).decode("ascii"),
    }


def write_license_file(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_license_file(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
