"""Runtime configuration (environment variables; see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _b(name: str, default: bool) -> bool:
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("CLEARWAY_DATA_DIR", Path(__file__).resolve().parents[1] / "data")))
    demo_mode: bool = field(default_factory=lambda: _b("CLEARWAY_DEMO_MODE", True))
    retention_days: int = field(default_factory=lambda: int(os.getenv("CLEARWAY_RETENTION_DAYS", "14")))
    token_encryption_key: str | None = field(default_factory=lambda: os.getenv("CLEARWAY_TOKEN_KEY"))
    close_after_clear_observations: int = field(default_factory=lambda: int(os.getenv("CLEARWAY_CLOSE_AFTER_CLEAR", "1")))  # demo incidents
    live_close_after_clear: int = field(default_factory=lambda: int(os.getenv("CLEARWAY_LIVE_CLOSE_AFTER_CLEAR", "2")))  # live Ring incidents
    live_min_clear_separation_s: float = field(default_factory=lambda: float(os.getenv("CLEARWAY_LIVE_MIN_CLEAR_SEPARATION_S", "60")))
    media_url_ttl_s: int = field(default_factory=lambda: int(os.getenv("CLEARWAY_MEDIA_URL_TTL_S", "900")))
    max_upload_mb: float = field(default_factory=lambda: float(os.getenv("CLEARWAY_MAX_UPLOAD_MB", "25")))
    max_video_seconds: float = field(default_factory=lambda: float(os.getenv("CLEARWAY_MAX_VIDEO_SECONDS", "60")))
    max_image_pixels: int = field(default_factory=lambda: int(os.getenv("CLEARWAY_MAX_IMAGE_PIXELS", str(4096 * 4096))))
    event_max_attempts: int = field(default_factory=lambda: int(os.getenv("CLEARWAY_EVENT_MAX_ATTEMPTS", "3")))  # per claim
    event_max_total_attempts: int = field(default_factory=lambda: int(os.getenv("CLEARWAY_EVENT_MAX_TOTAL_ATTEMPTS", "9")))  # then DEAD
    event_lease_s: float = field(default_factory=lambda: float(os.getenv("CLEARWAY_EVENT_LEASE_S", "300")))
    event_retry_after_s: float = field(default_factory=lambda: float(os.getenv("CLEARWAY_EVENT_RETRY_AFTER_S", "120")))
    event_sweep_interval_s: float = field(default_factory=lambda: float(os.getenv("CLEARWAY_EVENT_SWEEP_INTERVAL_S", "30")))
    # Live household (Ring credentials) — provisioned only when BOTH tokens are set; never exposed by demo endpoints.
    live_household_token: str | None = field(default_factory=lambda: os.getenv("CLEARWAY_LIVE_HOUSEHOLD_TOKEN"))
    live_approver_token: str | None = field(default_factory=lambda: os.getenv("CLEARWAY_LIVE_APPROVER_TOKEN"))
    live_helper_token: str | None = field(default_factory=lambda: os.getenv("CLEARWAY_LIVE_HELPER_TOKEN"))

    # Ring (documented Partner API). All calls are server-to-server.
    ring_api_base: str = field(default_factory=lambda: os.getenv("RING_API_BASE", "https://api.amazonvision.com"))
    ring_oauth_url: str = field(default_factory=lambda: os.getenv("RING_OAUTH_URL", "https://oauth.ring.com/oauth/token"))
    ring_client_id: str | None = field(default_factory=lambda: os.getenv("RING_CLIENT_ID"))
    ring_client_secret: str | None = field(default_factory=lambda: os.getenv("RING_CLIENT_SECRET"))
    ring_access_token: str | None = field(default_factory=lambda: os.getenv("RING_ACCESS_TOKEN"))
    ring_refresh_token: str | None = field(default_factory=lambda: os.getenv("RING_REFRESH_TOKEN"))
    ring_device_id: str | None = field(default_factory=lambda: os.getenv("RING_DEVICE_ID"))
    ring_component_id: str | None = field(default_factory=lambda: os.getenv("RING_COMPONENT_ID"))
    ring_user_agent: str = field(default_factory=lambda: os.getenv("RING_USER_AGENT", "ClearWayAccessGraph/2.0"))
    ring_hardware_id: str | None = field(default_factory=lambda: os.getenv("RING_HARDWARE_ID"))
    ring_webhook_secret: str | None = field(default_factory=lambda: os.getenv("RING_WEBHOOK_SECRET"))
    ring_source_label: str = field(default_factory=lambda: os.getenv("RING_SOURCE_LABEL", "RING_PLAYGROUND"))
    ring_emulator_control_token: str | None = field(default_factory=lambda: os.getenv("RING_EMULATOR_CONTROL_TOKEN"))
    ring_max_session_s: float = field(default_factory=lambda: float(os.getenv("RING_MAX_SESSION_S", "25")))

    simulator_secret: str = field(default_factory=lambda: os.getenv("SIMULATOR_WEBHOOK_SECRET", "local-simulator-secret-not-ring"))

    # AWS / Bedrock (Stage 3 escalation only; never the source of geometry)
    aws_enabled: bool = field(default_factory=lambda: _b("AWS_ENABLED", False))
    aws_region: str = field(default_factory=lambda: os.getenv("AWS_REGION", "us-east-1"))
    bedrock_model_id: str = field(default_factory=lambda: os.getenv("BEDROCK_MODEL_ID", "us.amazon.nova-2-lite-v1:0"))

    @property
    def ring_is_official_host(self) -> bool:
        from urllib.parse import urlparse
        return (urlparse(self.ring_api_base).hostname or "") == "api.amazonvision.com"

    @property
    def effective_ring_source_label(self) -> str:
        """Media from any API host other than Ring's own is ALWAYS labelled RING_API_EMULATOR, whatever
        RING_SOURCE_LABEL says, so emulated footage can never be presented as Ring footage."""
        if not self.ring_is_official_host:
            return "RING_API_EMULATOR"
        return self.ring_source_label if self.ring_source_label in ("RING_PLAYGROUND", "RING_LIVE") else "RING_PLAYGROUND"

    @property
    def ring_configured(self) -> bool:
        return bool(self.ring_access_token or (self.ring_refresh_token and self.ring_client_id and self.ring_client_secret))


settings = Settings()
