from __future__ import annotations

import json
from app.routers.licenses import update_license
from app.schemas import LicenseUpdateIn


def test_update_clear_machine_id_resigns(db_session, bound_license, settings, admin_user):
    lic, _ = bound_license
    assert lic.machine_id
    row = update_license(
        lic.id,
        LicenseUpdateIn(status="active", machine_id=""),
        db_session,
        admin_user,
        settings,
    )
    assert row.machine_id == ""
    doc = json.loads(open(row.file_path, encoding="utf-8").read())
    assert "machine_id" not in doc.get("payload", {})