"""src/config.py
Configuration and Security Settings for SCHALE Tactical Tracking Server.
Ensures API keys and credentials are kept securely on the server and never exposed to the frontend.
"""

import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class AppConfig:
    host: str = os.getenv("SCHALE_HOST", "127.0.0.1")
    port: int = int(os.getenv("SCHALE_PORT", "8000"))
    inference_api_url: str = os.getenv(
        "INFERENCE_API_URL",
        os.getenv(
            "SCHALE_INFERENCE_URL",
            "https://predict-6a740e7e1ca26b8eb95079d4-dproatj77a-du.a.run.app/predict",
        ),
    )
    # The API key must ONLY be read from the environment
    inference_api_key: Optional[str] = os.getenv(
        "INFERENCE_API_KEY",
        os.getenv("SCHALE_INFERENCE_KEY", None),
    )
    mock_inference: bool = os.getenv("MOCK_INFERENCE", "false").lower() in ("1", "true", "yes")
    request_timeout_sec: float = float(os.getenv("INFERENCE_TIMEOUT_SEC", "10.0"))
    max_upload_size_bytes: int = 10 * 1024 * 1024  # 10 MB

    @property
    def is_api_key_configured(self) -> bool:
        return bool(self.inference_api_key and len(self.inference_api_key.strip()) > 0)


def get_config() -> AppConfig:
    return AppConfig()
