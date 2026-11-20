from pathlib import Path

from app.services.build_runner import IMAGE_TARS, pushbroom_customer_dockerfile, worker_customer_dockerfile
from app.services.packager import PUSHBROOM_TAR

_REPO = Path(__file__).resolve().parents[4]


def test_repo_pushbroom_dockerfile_compiles_and_enforces_license():
    path = pushbroom_customer_dockerfile(_REPO)
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "cython_modules_pushbroom.txt pb_mosaic" in text
    assert "MOSAIC_REQUIRE_LICENSE=1" in text
    assert "license_public.pem" in text
    assert PUSHBROOM_TAR in IMAGE_TARS
    modules = (_REPO / "release" / "build" / "cython_modules_pushbroom.txt").read_text(encoding="utf-8")
    names = [ln.strip() for ln in modules.splitlines() if ln.strip() and not ln.startswith("#")]
    pkg = _REPO / "mosaic" / "server" / "pushbroom-worker" / "pb_mosaic"
    assert [n for n in names if not (pkg / f"{n}.py").is_file()] == []
    # python -m 启动的入口必须保留源码
    entries = {"worker_main", "job_child", "__main__"}
    assert not entries & set(names)
    # 新加的算法模块必须进清单，否则会以源码交付
    shipped_as_source = {
        p.relative_to(pkg).with_suffix("").as_posix()
        for p in pkg.rglob("*.py")
        if p.name != "__init__.py"
    } - set(names) - entries
    assert shipped_as_source == set()


def test_amd64_uses_gpu_customer_dockerfile(tmp_path: Path):
    gpu = tmp_path / "release" / "build" / "Dockerfile.worker-customer-gpu"
    cpu = tmp_path / "release" / "build" / "Dockerfile.worker-customer"
    gpu.parent.mkdir(parents=True)
    gpu.write_text("FROM gpu\n", encoding="utf-8")
    cpu.write_text("FROM cpu\n", encoding="utf-8")
    got = worker_customer_dockerfile(tmp_path, "amd64")
    assert got == gpu


def test_arm64_uses_cpu_customer_dockerfile(tmp_path: Path):
    gpu = tmp_path / "release" / "build" / "Dockerfile.worker-customer-gpu"
    cpu = tmp_path / "release" / "build" / "Dockerfile.worker-customer"
    gpu.parent.mkdir(parents=True)
    gpu.write_text("FROM gpu\n", encoding="utf-8")
    cpu.write_text("FROM cpu\n", encoding="utf-8")
    got = worker_customer_dockerfile(tmp_path, "arm64")
    assert got == cpu


def test_repo_amd64_gpu_dockerfile_is_cuda_cupy():
    path = worker_customer_dockerfile(_REPO, "amd64")
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "nvidia/cuda:12.2.2-runtime-ubuntu22.04" in text
    assert "requirements-gpu.txt" in text
    assert "cupy" in text
    cpu = worker_customer_dockerfile(_REPO, "arm64")
    assert cpu.name == "Dockerfile.worker-customer"
    assert cpu.is_file()
