"""重算拼接线前把上一版成果改名留档，方便和下一版对比。"""

from types import SimpleNamespace

import json

import numpy as np
from PIL import Image

from app.services.jobs import materialize_seam_workspace
from app.services.seam_map import (
    archive_current_products,
    describe_edit,
    list_product_versions,
    restore_archived_products,
    source_image_jpeg,
    start_seam_copy,
)


def _job(tmp_path):
    products = tmp_path / "拼图结果"
    extras = tmp_path / "附件"
    products.mkdir()
    extras.mkdir()
    return SimpleNamespace(products_dir=str(products), output_dir=str(tmp_path)), products, extras


def test_archive_keeps_the_original_and_adds_a_record(tmp_path):
    job, products, extras = _job(tmp_path)
    (products / "Orthomosaic_pix_surf_group0.tif").write_bytes(b"ortho-v1")
    (products / "DSM.tif").write_bytes(b"dsm-v1")
    (extras / "seamlines.geojson").write_text("{}", encoding="utf-8")
    (extras / "seam_edit.geojson").write_text('{"keep":true}', encoding="utf-8")

    first = archive_current_products(job)

    assert first["id"] == "v001"
    assert first["label"] == "原始成果"
    kept = extras / "versions" / "v001"
    assert (kept / "Orthomosaic_pix_surf_group0.tif").read_bytes() == b"ortho-v1"
    assert (kept / "DSM.tif").read_bytes() == b"dsm-v1"
    assert (kept / "seamlines.geojson").read_text(encoding="utf-8") == "{}"
    assert not (products / "Orthomosaic_pix_surf_group0.tif").exists()
    assert (extras / "seam_edit.geojson").read_text(encoding="utf-8") == '{"keep":true}'
    assert first["dsm"].endswith("v001/DSM.tif")

    (products / "Orthomosaic_pix_surf_group0.tif").write_bytes(b"ortho-v2")
    (products / "DSM.tif").write_bytes(b"dsm-v2")
    second = archive_current_products(job)

    assert second["id"] == "v002"
    assert second["label"] == "第 1 次调整"
    assert (extras / "versions" / "v001" / "Orthomosaic_pix_surf_group0.tif").read_bytes() == b"ortho-v1"
    assert (extras / "versions" / "v002" / "DSM.tif").read_bytes() == b"dsm-v2"
    listed = list_product_versions(job)
    assert listed["current"] is None
    assert [row["id"] for row in listed["versions"]] == ["v001", "v002"]
    restore_archived_products(job, second)
    assert (products / "Orthomosaic_pix_surf_group0.tif").read_bytes() == b"ortho-v2"
    assert (extras / "versions" / "v001" / "Orthomosaic_pix_surf_group0.tif").read_bytes() == b"ortho-v1"
    assert [row["id"] for row in list_product_versions(job)["versions"]] == ["v001"]
    assert list_product_versions(job)["current"]["label"] == "当前成果"
    assert first["change"]["summary"] == ""


def test_describe_edit_records_where_and_which_photo(tmp_path):
    extras = tmp_path / "附件"
    extras.mkdir()
    (extras / "seam_preview.json").write_text(json.dumps({"width": 90, "height": 90}), encoding="utf-8")
    photo = tmp_path / "MAX_0122_Color_D.jpg"
    Image.new("RGB", (8, 8), (1, 2, 3)).save(photo, format="JPEG")
    cache = tmp_path / "cache"
    cache.mkdir()
    meta = {"image_ids": [122], "image_paths": [str(photo)]}
    np.savez(cache / "at_result.npz", meta=np.frombuffer(json.dumps(meta).encode("utf-8"), dtype=np.uint8))
    job = SimpleNamespace(
        products_dir=str(tmp_path / "拼图结果"),
        output_dir=str(tmp_path),
        cache_dir=str(cache),
        params_snapshot={"cache_dir": str(cache), "output_dir": str(tmp_path)},
    )
    got = describe_edit(job, [{"image_id": 122, "points": [[10, 10], [20, 10], [12, 24]]}], False)
    assert got["places"] == [{"where": "左上", "name": "MAX 0122", "image_id": 122}]
    assert got["summary"] == "左上换成 MAX 0122"
    assert describe_edit(job, [], True)["summary"] == "整幅改回自动拼接线"


def test_start_seam_copy_keeps_the_source_file(tmp_path):
    job, products, extras = _job(tmp_path)
    ortho = products / "Orthomosaic_pix_surf_group0.tif"
    ortho.write_bytes(b"ortho-v1")
    (products / "DSM.tif").write_bytes(b"dsm-v1")
    (extras / "seamlines.geojson").write_text("{}", encoding="utf-8")
    archive_current_products(job)
    ortho.write_bytes(b"ortho-current")
    source = extras / "versions" / "v001" / "Orthomosaic_pix_surf_group0.tif"
    source_inode = source.stat().st_ino
    record = start_seam_copy(job, "v001")
    assert source.read_bytes() == b"ortho-v1"
    assert source.stat().st_ino == source_inode
    assert ortho.read_bytes() == b"ortho-v1"
    copied = extras / "versions" / record["id"] / "Orthomosaic_pix_surf_group0.tif"
    assert copied.read_bytes() == b"ortho-v1"
    assert copied.stat().st_ino != source_inode
    labels = [row["label"] for row in list_product_versions(job)["versions"]]
    assert "替换前的当前成果" in labels
    assert record["label"] == "基于原始成果"
    assert record["base_id"] == "v001"
    saved = next(row for row in list_product_versions(job)["versions"] if row["label"] == "替换前的当前成果")
    assert (extras / "versions" / saved["id"] / "Orthomosaic_pix_surf_group0.tif").read_bytes() == b"ortho-current"

    current_inode = ortho.stat().st_ino
    current_copy = start_seam_copy(job, "current")
    assert ortho.stat().st_ino == current_inode
    assert ortho.read_bytes() == b"ortho-v1"
    assert current_copy["label"] == "基于当前成果"
    assert (extras / "versions" / current_copy["id"] / "Orthomosaic_pix_surf_group0.tif").stat().st_ino != current_inode


def test_source_thumb_comes_from_the_catalog_photo(tmp_path):
    photo = tmp_path / "frame.jpg"
    Image.new("RGB", (32, 16), (12, 34, 56)).save(photo, format="JPEG")
    cache = tmp_path / "cache"
    cache.mkdir()
    meta = {"image_ids": [1888], "image_paths": [str(photo)]}
    np.savez(
        cache / "at_result.npz",
        meta=np.frombuffer(json.dumps(meta).encode("utf-8"), dtype=np.uint8),
    )
    (tmp_path / "附件").mkdir()
    job = SimpleNamespace(
        products_dir=str(tmp_path / "拼图结果"),
        output_dir=str(tmp_path),
        cache_dir=str(cache),
        params_snapshot={"cache_dir": str(cache), "output_dir": str(tmp_path)},
    )
    data = source_image_jpeg(job, 1888)
    assert data[:2] == b"\xff\xd8"
    assert (tmp_path / "附件" / "image_thumbs" / "1888.jpg").is_file()


def test_seam_task_copies_products_and_leaves_the_source(tmp_path):
    job, products, extras = _job(tmp_path)
    ortho = products / "Orthomosaic_pix_surf_group0.tif"
    ortho.write_bytes(b"ortho-live")
    (extras / "seamlines.geojson").write_text("{}", encoding="utf-8")
    job.params_snapshot = {}
    job.cache_dir = str(tmp_path / "cache")
    before = ortho.stat().st_ino
    paths = materialize_seam_workspace(job, tmp_path / "seam_out")
    assert ortho.stat().st_ino == before
    copied = tmp_path / "seam_out" / "拼图结果" / ortho.name
    assert copied.read_bytes() == b"ortho-live"
    assert copied.stat().st_ino != before
    assert (tmp_path / "seam_out" / "附件" / "seamlines.geojson").read_text(encoding="utf-8") == "{}"
    assert paths["products_dir"].endswith("拼图结果")


def test_seam_task_uses_archived_products_when_live_dir_is_empty(tmp_path):
    job, products, extras = _job(tmp_path)
    archived = extras / "versions" / "v001"
    archived.mkdir(parents=True)
    ortho = archived / "Orthomosaic_pix_surf_group0.tif"
    ortho.write_bytes(b"ortho-archived")
    (archived / "seamlines.geojson").write_text("{}", encoding="utf-8")
    (extras / "versions" / "index.json").write_text(
        json.dumps({"versions": [{"id": "v001", "label": "原始成果"}]}),
        encoding="utf-8",
    )
    job.params_snapshot = {}
    job.cache_dir = ""
    before = ortho.stat().st_ino
    materialize_seam_workspace(job, tmp_path / "seam_out")
    assert ortho.stat().st_ino == before
    assert not any(products.iterdir())
    copied = tmp_path / "seam_out" / "拼图结果" / ortho.name
    assert copied.read_bytes() == b"ortho-archived"
    assert copied.stat().st_ino != before
    assert (tmp_path / "seam_out" / "附件" / "seamlines.geojson").is_file()
