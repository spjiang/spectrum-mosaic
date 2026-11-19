"""许可校验：规则、环境变量、许可文件和公钥与框幅 worker（ms_mosaic.license_check）完全相同。

只有时钟记录和租约缓存用推扫自己的文件，避免两个 worker 同时写同一个文件。
改规则时两边一起改。
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("pb_mosaic.license")

PRODUCT_NAME = "spectrum-mosaic"
# Default when LICENSE_CLOCK_TOLERANCE_HOURS is unset (overridden by env / delivery compose).
DEFAULT_CLOCK_TOLERANCE_HOURS = 1.0
DEFAULT_CLOCK_PATH = "/data/.license/pushbroom-clock.json"
DEFAULT_LEASE_PATH = "/data/.license/pushbroom-lease.json"
DEFAULT_MACHINE_ID_PATH = "/host/machine-id"
DEFAULT_LEASE_CHECK_INTERVAL_HOURS = 6.0
LEASE_CHECK_INTERVAL_SECONDS = int(DEFAULT_LEASE_CHECK_INTERVAL_HOURS * 3600)


class LicenseError(Exception):
    pass


class LeaseRejected(LicenseError):
    """许可服务器明确拒绝（停用、机器不符、过期）。不得回退到本地租约。"""


class LeaseNetworkError(LicenseError):
    """暂时连不上许可服务器。"""


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


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


def _fmt_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def clock_state_path() -> Path:
    return Path(os.environ.get("LICENSE_CLOCK_PATH", DEFAULT_CLOCK_PATH))


def lease_check_interval_seconds() -> float:
    """运行中自动领租约的间隔。交付包写入 LICENSE_LEASE_CHECK_INTERVAL_HOURS。"""
    raw = os.environ.get("LICENSE_LEASE_CHECK_INTERVAL_HOURS", "").strip()
    hours = DEFAULT_LEASE_CHECK_INTERVAL_HOURS
    if raw:
        try:
            hours = float(raw)
        except ValueError:
            hours = DEFAULT_LEASE_CHECK_INTERVAL_HOURS
    if hours <= 0:
        hours = DEFAULT_LEASE_CHECK_INTERVAL_HOURS
    return hours * 3600


def clock_rollback_tolerance() -> timedelta:
    raw = os.environ.get("LICENSE_CLOCK_TOLERANCE_HOURS", "").strip()
    hours = DEFAULT_CLOCK_TOLERANCE_HOURS
    if raw:
        try:
            hours = float(raw)
        except ValueError:
            hours = DEFAULT_CLOCK_TOLERANCE_HOURS
    if hours < 0:
        hours = 0.0
    return timedelta(hours=hours)


# Back-compat alias for tests / imports.
CLOCK_ROLLBACK_TOLERANCE = timedelta(hours=DEFAULT_CLOCK_TOLERANCE_HOURS)


def load_clock_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    return data


def save_clock_state(path: Path, *, license_id: str, last_seen_at: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "license_id": license_id,
        "last_seen_at": _fmt_utc(last_seen_at),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def check_clock_rollback(
    *,
    license_id: str,
    now: datetime,
    path: Path | None = None,
    tolerance: timedelta | None = None,
) -> None:
    """Raise LicenseError if wall clock rolled back beyond tolerance vs persisted last_seen."""
    tol = clock_rollback_tolerance() if tolerance is None else tolerance
    state_path = path or clock_state_path()
    current = now.astimezone(timezone.utc)
    state = load_clock_state(state_path)
    if state and str(state.get("license_id") or "") == license_id:
        raw = state.get("last_seen_at")
        if raw:
            last_seen = _parse_expires(str(raw))
            if current < last_seen - tol:
                raise LicenseError("系统时间异常，疑似时钟回拨")
    save_clock_state(state_path, license_id=license_id, last_seen_at=current)


def verify_license_doc(
    doc: dict[str, Any],
    public_key_bytes: bytes,
    *,
    now: datetime | None = None,
    expected_product: str = PRODUCT_NAME,
) -> dict[str, Any]:
    from nacl.exceptions import BadSignatureError
    from nacl.signing import VerifyKey

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
    key = VerifyKey(public_key_bytes)
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
        raise LicenseError("许可无效或已过期")
    return payload


def machine_fingerprint(machine_id_text: str) -> str:
    mid = machine_id_text.strip().lower()
    if not mid:
        raise LicenseError("无法读取机器标识")
    return hashlib.sha256(mid.encode("utf-8")).hexdigest()


def local_machine_id() -> str:
    path = Path(os.environ.get("HOST_MACHINE_ID_PATH", DEFAULT_MACHINE_ID_PATH))
    if not path.is_file():
        raise LicenseError("无法读取机器标识")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LicenseError("无法读取机器标识") from exc
    return machine_fingerprint(raw)


def lease_state_path() -> Path:
    return Path(os.environ.get("LICENSE_LEASE_PATH", DEFAULT_LEASE_PATH))


def verify_lease_doc(
    doc: dict[str, Any],
    public_key_bytes: bytes,
    *,
    license_id: str,
    machine_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    from nacl.exceptions import BadSignatureError
    from nacl.signing import VerifyKey

    if not isinstance(doc, dict) or "payload" not in doc or "signature" not in doc:
        raise LicenseError("租约无效")
    payload = doc["payload"]
    if not isinstance(payload, dict):
        raise LicenseError("租约无效")
    try:
        sig = base64.b64decode(str(doc["signature"]), validate=True)
    except Exception as exc:  # noqa: BLE001
        raise LicenseError("租约无效") from exc
    try:
        VerifyKey(public_key_bytes).verify(_canonical_payload(payload), sig)
    except BadSignatureError as exc:
        raise LicenseError("租约无效") from exc
    if str(payload.get("license_id") or "") != license_id:
        raise LicenseError("租约与许可不匹配")
    if str(payload.get("machine_id") or "").strip().lower() != machine_id:
        raise LicenseError("租约与当前机器不匹配")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    expires = _parse_expires(str(payload.get("expires_at") or ""))
    if current > expires:
        raise LicenseError("许可租约已过期，请恢复与许可服务器的连接")
    return payload


def _save_lease(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _load_lease(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


def fetch_lease(server_url: str, *, license_id: str, machine_id: str, timeout: float = 10) -> dict[str, Any]:
    url = server_url.rstrip("/") + "/api/license/check"
    body = json.dumps({"license_id": license_id, "machine_id": machine_id}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            parsed = json.loads(exc.read().decode("utf-8"))
            detail = str(parsed.get("detail") or "")
        except Exception:  # noqa: BLE001
            detail = ""
        raise LeaseRejected(detail or "许可服务器拒绝") from exc
    except urllib.error.URLError as exc:
        raise LeaseNetworkError("无法连接许可服务器") from exc
    if not isinstance(data, dict):
        raise LeaseRejected("许可服务器返回无效")
    return data


def ensure_online_lease(
    *,
    license_id: str,
    machine_id: str,
    public_key_bytes: bytes,
    now: datetime | None = None,
) -> None:
    server = os.environ.get("LICENSE_SERVER_URL", "").strip()
    if not server:
        raise LicenseError("未配置许可服务器，无法校验绑定许可")
    path = lease_state_path()
    try:
        doc = fetch_lease(server, license_id=license_id, machine_id=machine_id)
    except LeaseRejected:
        path.unlink(missing_ok=True)
        raise
    except LeaseNetworkError:
        cached = _load_lease(path)
        if cached is None:
            raise LicenseError("无法连接许可服务器，且没有有效租约") from None
        verify_lease_doc(cached, public_key_bytes, license_id=license_id, machine_id=machine_id, now=now)
        log.warning("license server unreachable, using cached lease")
        return
    verify_lease_doc(doc, public_key_bytes, license_id=license_id, machine_id=machine_id, now=now)
    _save_lease(path, doc)


def enforce_license_or_raise() -> None:
    """客户镜像设 MOSAIC_REQUIRE_LICENSE=1 时强制验签；开发环境默认跳过。"""
    if not _truthy(os.environ.get("MOSAIC_REQUIRE_LICENSE")):
        return
    lic_path = Path(os.environ.get("LICENSE_PATH", "/license/license.lic"))
    pub_path = Path(os.environ.get("LICENSE_PUBLIC_KEY_PATH", "/app/license_public.pem"))
    if not lic_path.exists():
        raise LicenseError("许可无效或已过期：找不到许可文件")
    if not pub_path.exists():
        raise LicenseError("许可无效或已过期：找不到公钥")
    try:
        doc = json.loads(lic_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise LicenseError("许可无效或已过期：许可文件无法解析") from exc
    pub_bytes = pub_path.read_bytes()
    payload = verify_license_doc(doc, pub_bytes)
    license_id = str(payload.get("license_id") or "")
    if not license_id:
        raise LicenseError("许可无效或已过期：缺少 license_id")
    bound = str(payload.get("machine_id") or "").strip().lower()
    if bound:
        local = local_machine_id()
        if local != bound:
            raise LicenseError("许可与当前机器不匹配")
        ensure_online_lease(license_id=license_id, machine_id=local, public_key_bytes=pub_bytes)
    check_clock_rollback(license_id=license_id, now=datetime.now(timezone.utc))
    log.info(
        "license ok: %s starts=%s expires=%s machine=%s",
        payload.get("license_id"),
        payload.get("starts_at"),
        payload.get("expires_at"),
        "bound" if bound else "unbound",
    )
