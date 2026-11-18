from pathlib import Path

from app.services.build_runner import worker_customer_dockerfile

_REPO = Path(__file__).resolve().parents[4]


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
