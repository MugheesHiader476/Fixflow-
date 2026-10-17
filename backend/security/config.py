from pydantic import Field

from backend.schemas.pipeline import Contract


class SecurityConfig(Contract):
    read_requests_per_minute: int = Field(default=240, ge=10, le=10000)
    write_requests_per_minute: int = Field(default=60, ge=1, le=10000)
    sensitive_requests_per_minute: int = Field(default=10, ge=1, le=1000)
    auth_failures_per_minute: int = Field(default=10, ge=1, le=1000)
    auth_failures_global_per_minute: int = Field(default=300, ge=1, le=10000)
    email_dispatches_per_hour: int = Field(default=30, ge=1, le=1000)
    active_runs_per_owner: int = Field(default=2, ge=1, le=20)
