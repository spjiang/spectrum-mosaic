from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from app.services.params import assert_param_values, is_required, validate_param_values


def _d(**kwargs):
    base = dict(required=False, required_when=None, value_type="string")
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_required_input_dir():
    defs = [_d(key="input_dir", required=True, value_type="path")]
    assert validate_param_values(defs, {}) == ["input_dir 为必填"]
    assert validate_param_values(defs, {"input_dir": "/data/input/MAX_20251017/MAX_20251017_001"}) == []


def test_stop_after_required_when_until_stage():
    defs = [
        _d(key="run_mode", value_type="enum"),
        _d(key="stop_after_stage", value_type="enum", required_when={"run_mode": "until_stage"}),
    ]
    assert not is_required(defs[1], {"run_mode": "full"})
    errs = validate_param_values(defs, {"run_mode": "until_stage"})
    assert any("stop_after_stage" in e for e in errs)
    assert validate_param_values(defs, {"run_mode": "until_stage", "stop_after_stage": "S4_dsm"}) == []


def test_path_must_be_under_data():
    defs = [_d(key="output_dir", required=True, value_type="path")]
    errs = validate_param_values(defs, {"output_dir": "/tmp/out"})
    assert any("/data" in e for e in errs)


def test_max_features_must_be_positive_int():
    defs = [_d(key="max_features", value_type="int")]
    assert validate_param_values(defs, {}) == []
    assert validate_param_values(defs, {"max_features": 8192}) == []
    assert validate_param_values(defs, {"max_features": 0}) == ["特征点上限须为正整数"]
    assert validate_param_values(defs, {"max_features": -1}) == ["特征点上限须为正整数"]


def test_at_bands_subset_must_include_color():
    defs = [_d(key="at_bands", value_type="string[]")]
    assert validate_param_values(defs, {}) == []
    assert validate_param_values(defs, {"at_bands": ["Color", "800nm"]}) == []
    assert validate_param_values(defs, {"at_bands": ["800nm"]}) == ["空三波段必须包含 Color"]


def test_assert_raises_http():
    defs = [_d(key="input_dir", required=True, value_type="path")]
    with pytest.raises(HTTPException):
        assert_param_values(defs, {})
