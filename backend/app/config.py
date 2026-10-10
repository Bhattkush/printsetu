from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    product_name: str = "PrintSetu"
    env: str = "local"
    database_url: str = "sqlite:///./printsetu.db"
    storage_dir: str = "./storage"
    jwt_secret: str = "change-me-in-production"
    demo_mode: bool = True
    payment_required: bool = False
    allow_unpaid_local_printing: bool = True
    agent_poll_seconds: int = 3
    server_url: str = "http://127.0.0.1:8000"
    public_url: str = ""
    resend_api_key: str = ""
    resend_sender: str = "PrintSetu <onboarding@resend.dev>"
    otp_mode: str = "development"
    razorpay_mode: str = "disabled"
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""
    cleanup_interval_seconds: int = 60
    document_retention_hours: int = 6
    max_upload_bytes: int = 25 * 1024 * 1024
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    def validate_production(self):
        if self.env != "production":
            return
        if len(self.jwt_secret) < 32 or self.jwt_secret == "change-me-in-production":
            raise ValueError("Production requires a unique PRINTSETU_JWT_SECRET of at least 32 characters")
        if not self.database_url.startswith("postgresql") or not self.public_url.startswith("https://"):
            raise ValueError("Production requires PostgreSQL and an HTTPS public URL")
        if self.otp_mode != "resend" or not self.resend_api_key:
            raise ValueError("Production requires Resend email verification")
        if self.demo_mode or self.allow_unpaid_local_printing or not self.payment_required:
            raise ValueError("Production must require payment and disable unpaid demo printing")
        if self.razorpay_mode != "live" or not self.razorpay_key_id.startswith("rzp_live_") or not self.razorpay_key_secret or not self.razorpay_webhook_secret:
            raise ValueError("Production requires explicit live Razorpay credentials and webhook secret")

    model_config = SettingsConfigDict(env_file=".env", env_prefix="PRINTSETU_")

    @property
    def checkout_ready(self) -> bool:
        prefix = {"test": "rzp_test_", "live": "rzp_live_"}.get(self.razorpay_mode)
        return bool(prefix and self.razorpay_key_id.startswith(prefix) and self.razorpay_key_secret)

    @property
    def unpaid_printing_allowed(self) -> bool:
        return self.env == "local" and self.demo_mode and not self.payment_required and self.allow_unpaid_local_printing


@lru_cache
def get_settings() -> Settings:
    return Settings()

