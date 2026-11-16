import pytest
from pydantic import ValidationError

from app.schemas import ReleaseCreateIn


def test_arch_normalizes_linux_prefix():
    r = ReleaseCreateIn(version="0.1.0", arch="linux/amd64")
    assert r.arch == "amd64"


def test_arch_accepts_bare():
    r = ReleaseCreateIn(version="0.1.0", arch="arm64")
    assert r.arch == "arm64"


def test_arch_rejects_unknown():
    with pytest.raises(ValidationError):
        ReleaseCreateIn(version="0.1.0", arch="ppc64")
