from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.services.release_images import expand_legacy_image_name

ALLOWED_RELEASE_ARCH = frozenset({"amd64", "arm64"})


class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class CustomerIn(BaseModel):
    name: str = Field(min_length=1)
    contact: str = ""
    status: str = "trial"
    notes: str = ""


class CustomerOut(BaseModel):
    id: int
    name: str
    contact: str
    status: str
    notes: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ReleaseCreateIn(BaseModel):
    version: str = Field(min_length=1)
    arch: str = Field(min_length=1)

    @field_validator("arch")
    @classmethod
    def _arch_ok(cls, v: str) -> str:
        a = v.strip().lower()
        if a.startswith("linux/"):
            a = a.split("/", 1)[1]
        if a not in ALLOWED_RELEASE_ARCH:
            raise ValueError("arch 仅支持 amd64 或 arm64")
        return a


class ReleaseUpdateIn(BaseModel):
    version: str = Field(min_length=1)


class LicenseUpdateIn(BaseModel):
    status: str = "active"
    starts_at: datetime | None = None
    expires_at: datetime | None = None
    machine_id: str | None = None


class ReleaseOut(BaseModel):
    id: int
    version: str
    arch: str
    image_name: str
    digest: str
    status: str
    log_path: str
    artifact_path: str
    error_message: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @model_validator(mode="after")
    def _expand_legacy_image_list(self) -> ReleaseOut:
        self.image_name = expand_legacy_image_name(
            self.image_name,
            version=self.version,
            arch=self.arch,
        )
        return self


class ReleaseArtifactOut(BaseModel):
    name: str
    label: str
    filename: str
    size_bytes: int
    available: bool



class LicenseCreateIn(BaseModel):
    customer_id: int
    release_id: int
    starts_at: datetime | None = None
    expires_at: datetime
    machine_id: str = Field(min_length=64, max_length=64)


class LicenseCheckIn(BaseModel):
    license_id: str = Field(min_length=1)
    machine_id: str = Field(min_length=64, max_length=64)


class LeaseClaimOut(BaseModel):
    id: int
    license_id: str
    customer_name: str
    machine_id: str
    ok: bool
    detail: str
    lease_expires_at: datetime | None
    client_ip: str
    created_at: datetime

    model_config = {"from_attributes": True}


class LeaseClaimPage(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[LeaseClaimOut]


class LicenseOut(BaseModel):
    id: int
    license_id: str
    customer_id: int
    release_id: int
    starts_at: datetime
    expires_at: datetime
    issued_at: datetime
    file_path: str
    file_checksum: str
    status: str
    machine_id: str = ""
    created_at: datetime

    model_config = {"from_attributes": True}


class DeliveryCreateIn(BaseModel):
    customer_id: int
    release_id: int
    license_id: int
    include_image_tar: bool = True


class DeliveryOut(BaseModel):
    id: int
    customer_id: int
    release_id: int
    license_id: int
    package_path: str
    checksum: str
    created_at: datetime

    model_config = {"from_attributes": True}


class DeliveryEntryOut(BaseModel):
    path: str
    size: int
    is_dir: bool


class DeliveryContentsOut(BaseModel):
    delivery_id: int
    package_name: str
    checksum: str
    file_count: int
    total_size: int
    package_path: str = ""
    local_dir: str = ""
    local_path: str = ""
    entries: list[DeliveryEntryOut]


class SystemSettingOut(BaseModel):
    key: str
    value: str
    label: str
    description: str
    used_by: str
    updated_at: datetime

    model_config = {"from_attributes": True}


class SystemSettingUpdateIn(BaseModel):
    value: str


class SystemSettingsBatchIn(BaseModel):
    items: list[dict[str, str]] = Field(default_factory=list)


class NoteCreateIn(BaseModel):
    title: str = Field(min_length=1)
    body: str = ""


class NoteUpdateIn(BaseModel):
    title: str = Field(min_length=1)
    body: str = ""


class NoteOut(BaseModel):
    id: int
    title: str
    body: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class HealthOut(BaseModel):
    status: str
    product: str
