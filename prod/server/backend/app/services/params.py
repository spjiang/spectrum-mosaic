from __future__ import annotations

from typing import Any, Sequence

from fastapi import HTTPException


def is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, (list, dict)) and len(value) == 0:
        return True
    return False


def is_required(defn: Any, values: dict[str, Any]) -> bool:
    if bool(getattr(defn, "required", False)):
        return True
    when = getattr(defn, "required_when", None)
    if not isinstance(when, dict) or not when:
        return False
    for key, expect in when.items():
        got = values.get(key)
        if isinstance(expect, list):
            if got not in expect:
                return False
        elif got != expect:
            return False
    return True


def _type_error(defn: Any, raw: Any) -> str | None:
    key = defn.key
    kind = defn.value_type
    if kind == "bool" and not isinstance(raw, bool):
        return f"{key} 应为布尔值"
    if kind == "int":
        if isinstance(raw, bool) or not isinstance(raw, int):
            if isinstance(raw, float) and raw.is_integer():
                return None
            return f"{key} 应为整数"
    if kind == "float":
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return f"{key} 应为数字"
    if kind in {"path", "string", "enum"} and not isinstance(raw, str):
        return f"{key} 应为文本"
    if kind == "string[]":
        if not isinstance(raw, list) or any(not isinstance(x, str) for x in raw):
            return f"{key} 应为文本列表"
    if kind == "path" and isinstance(raw, str):
        text = raw.strip()
        if key in {"input_dir", "output_dir", "cache_dir", "log_dir", "process_dir", "reuse_dsm"}:
            if not text.startswith("/data"):
                return f"{key} 须位于容器 /data 下"
    return None


def validate_param_values(defs: Sequence[Any], values: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for defn in defs:
        raw = values.get(defn.key)
        need = is_required(defn, values)
        if need and is_empty(raw):
            extra = "（当前运行模式）" if getattr(defn, "required_when", None) else ""
            errors.append(f"{defn.key} 为必填{extra}")
            continue
        if is_empty(raw):
            continue
        err = _type_error(defn, raw)
        if err:
            errors.append(err)
    return errors


def assert_param_values(defs: Sequence[Any], values: dict[str, Any]) -> None:
    errors = validate_param_values(defs, values)
    if errors:
        raise HTTPException(400, "；".join(errors))
