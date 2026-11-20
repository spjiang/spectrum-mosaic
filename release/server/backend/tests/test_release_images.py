from app.services.release_images import (
    build_image_manifest,
    expand_legacy_image_name,
    resolve_pushbroom_image,
    resolve_worker_image,
)


def test_manifest_with_pushbroom_worker():
    text = build_image_manifest(
        worker="spectrum-mosaic-worker:0.2.0-amd64",
        pushbroom_worker="spectrum-mosaic-pushbroom-worker:0.2.0-amd64",
        backend="spectrum-mosaic-backend:0.2.0-amd64",
        frontend="spectrum-mosaic-frontend:0.2.0-amd64",
    )
    assert text.splitlines()[:2] == [
        "spectrum-mosaic-worker:0.2.0-amd64",
        "spectrum-mosaic-pushbroom-worker:0.2.0-amd64",
    ]
    assert resolve_pushbroom_image(text) == "spectrum-mosaic-pushbroom-worker:0.2.0-amd64"
    # 推扫 worker 的仓库名以 worker 结尾，不能被当成框幅 worker
    assert resolve_worker_image(text, version="0.2.0", arch="amd64") == "spectrum-mosaic-worker:0.2.0-amd64"


def test_old_manifest_has_no_pushbroom():
    old = build_image_manifest(
        worker="spectrum-mosaic-worker:0.1.0-amd64",
        backend="spectrum-mosaic-backend:0.1.0-amd64",
        frontend="spectrum-mosaic-frontend:0.1.0-amd64",
    )
    assert resolve_pushbroom_image(old) is None
    assert resolve_pushbroom_image("") is None
    assert resolve_pushbroom_image("spectrum-mosaic-worker:0.1.0-rc1-amd64") is None


def test_build_image_manifest_lists_full_stack():
    text = build_image_manifest(
        worker="spectrum-mosaic-worker:0.1.0-amd64",
        backend="spectrum-mosaic-backend:0.1.0-amd64",
        frontend="spectrum-mosaic-frontend:0.1.0-amd64",
    )
    assert text == (
        "spectrum-mosaic-worker:0.1.0-amd64\n"
        "spectrum-mosaic-backend:0.1.0-amd64\n"
        "spectrum-mosaic-frontend:0.1.0-amd64\n"
        "postgres:16-alpine\n"
        "rabbitmq:3.13-management-alpine"
    )


def test_resolve_worker_from_multiline_manifest():
    manifest = (
        "spectrum-mosaic-worker:0.1.0-rc1-amd64\n"
        "spectrum-mosaic-backend:0.1.0-rc1-amd64\n"
        "spectrum-mosaic-frontend:0.1.0-rc1-amd64\n"
        "postgres:16-alpine\n"
        "rabbitmq:3.13-management-alpine"
    )
    assert (
        resolve_worker_image(manifest, version="0.1.0-rc1", arch="amd64")
        == "spectrum-mosaic-worker:0.1.0-rc1-amd64"
    )


def test_resolve_worker_legacy_single_tag():
    assert (
        resolve_worker_image(
            "spectrum-mosaic-worker:0.1.0-rc1-amd64",
            version="0.1.0-rc1",
            arch="amd64",
        )
        == "spectrum-mosaic-worker:0.1.0-rc1-amd64"
    )


def test_resolve_worker_fallback_when_empty():
    assert (
        resolve_worker_image("", version="0.2.0", arch="arm64")
        == "spectrum-mosaic-worker:0.2.0-arm64"
    )


def test_expand_legacy_single_worker_for_display():
    assert expand_legacy_image_name(
        "spectrum-mosaic-worker:0.1.0-rc1-amd64",
        version="0.1.0-rc1",
        arch="amd64",
    ) == (
        "spectrum-mosaic-worker:0.1.0-rc1-amd64\n"
        "spectrum-mosaic-backend:0.1.0-rc1-amd64\n"
        "spectrum-mosaic-frontend:0.1.0-rc1-amd64\n"
        "postgres:16-alpine\n"
        "rabbitmq:3.13-management-alpine"
    )


def test_expand_legacy_keeps_multiline():
    multi = (
        "spectrum-mosaic-worker:0.1.0-amd64\n"
        "spectrum-mosaic-backend:0.1.0-amd64\n"
        "spectrum-mosaic-frontend:0.1.0-amd64\n"
        "postgres:16-alpine\n"
        "rabbitmq:3.13-management-alpine"
    )
    assert expand_legacy_image_name(multi, version="0.1.0", arch="amd64") == multi
