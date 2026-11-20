"""相机标定：建议值凭证、任务注入、不能手填、非管理员接口。"""

import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import jwt
import pytest
from fastapi import HTTPException

from app.config import Settings
from app.routers import pushbroom as routes
from app.services import pushbroom_calibration as cal

SETTINGS = Settings(jwt_secret="test-secret", jwt_algorithm="HS256")
COMBINED = {
    "along_offset_m": 1.78,
    "boresight_px": -49.8,
    "along_se_m": 0.11,
    "boresight_se_px": 2.9,
}
NAMES = ["FigSpec_A", "FigSpec_B"]


def test_no_calibration_leaves_job_params_unchanged():
    params = cal.with_active_calibration({"gsd_m": 0.04}, None)
    assert params == {"gsd_m": 0.04}
    assert "along_offset_m" not in params and "boresight_px" not in params


def test_active_calibration_is_copied_into_job_params():
    row = SimpleNamespace(along_offset_m=1.78, boresight_px=-49.8)
    params = cal.with_active_calibration({"bands": [1, 2]}, row)
    assert params["along_offset_m"] == 1.78
    assert params["boresight_px"] == -49.8
    assert params["bands"] == [1, 2]


def test_job_cannot_carry_sensor_fields():
    with pytest.raises(HTTPException) as exc:
        cal.with_active_calibration({"along_offset_m": 0.1}, None)
    assert exc.value.status_code == 400
    assert "不能在任务里填写" in exc.value.detail


def test_run_token_roundtrip_and_rejects_tamper_or_expiry():
    token = cal.issue_run_token(SETTINGS, COMBINED, NAMES)
    cal.assert_run_token(SETTINGS, token, {**COMBINED, "flights": NAMES})
    with pytest.raises(HTTPException) as bad:
        cal.assert_run_token(SETTINGS, token, {**COMBINED, "along_offset_m": 0.2, "flights": NAMES})
    assert bad.value.status_code == 400
    expired = jwt.encode(
        {"typ": cal.TOKEN_TYPE, **COMBINED, "flights": NAMES, "exp": datetime.now(timezone.utc) - timedelta(seconds=5)},
        SETTINGS.jwt_secret,
        algorithm=SETTINGS.jwt_algorithm,
    )
    with pytest.raises(HTTPException) as old:
        cal.assert_run_token(SETTINGS, expired, {**COMBINED, "flights": NAMES})
    assert "重新标定" in old.value.detail
    loginish = jwt.encode({"typ": "access", "sub": "1"}, SETTINGS.jwt_secret, algorithm=SETTINGS.jwt_algorithm)
    with pytest.raises(HTTPException):
        cal.assert_run_token(SETTINGS, loginish, {**COMBINED, "flights": NAMES})


def test_unusable_result_has_no_token_and_error_is_502():
    out = cal.present_run(SETTINGS, {"flights": [], "combined": {"usable": False, "note": "往返航带不够"}})
    assert "run_token" not in out
    assert out["combined"]["usable"] is False
    with pytest.raises(HTTPException) as exc:
        cal.present_run(SETTINGS, {"error": "标定进程失败"})
    assert exc.value.status_code == 502


def test_usable_result_gets_token_without_changing_numbers():
    result = {
        "flights": [
            {"flight": "FigSpec_A", "calibration": {"usable": True}},
            {"flight": "FigSpec_B", "calibration": {"usable": True}},
        ],
        "combined": {"usable": True, **COMBINED, "flights": 2},
    }
    out = cal.present_run(SETTINGS, result)
    assert out["combined"]["along_offset_m"] == 1.78
    assert out["combined"]["boresight_px"] == -49.8
    cal.assert_run_token(SETTINGS, out["run_token"], {**COMBINED, "flights": NAMES})


def test_apply_rejects_out_of_range_and_inserts_when_token_matches():
    token = cal.issue_run_token(SETTINGS, COMBINED, NAMES)
    body = {**COMBINED, "flights": NAMES, "run_token": token}
    with pytest.raises(HTTPException) as wide:
        cal.apply_calibration(MagicMock(), SETTINGS, SimpleNamespace(id=1, username="admin"), {**body, "boresight_px": 900})
    assert wide.value.status_code == 400
    db = MagicMock()
    row = cal.apply_calibration(db, SETTINGS, SimpleNamespace(id=7, username="admin"), body)
    assert row.along_offset_m == 1.78 and row.flights == NAMES and row.created_by == 7
    db.add.assert_called()
    db.commit.assert_called()


def test_public_state_without_row_is_builtin():
    assert cal.public_state(MagicMock(), None) == {
        "active": False,
        "builtin": {"along_offset_m": 1.7, "boresight_px": -19.0},
    }


def test_calibration_routes_are_admin_only():
    for fn in (routes.get_calibration, routes.run_calibration, routes.apply_calibration):
        param = "user" if "user" in inspect.signature(fn).parameters else "_user"
        allowed = inspect.signature(fn).parameters[param].default.dependency.__closure__[0].cell_contents
        assert allowed == ("admin",)
