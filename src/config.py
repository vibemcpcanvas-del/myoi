"""src/config.py
Configuration and Security Settings for SCHALE Tactical Tracking Server.
Ensures API keys and credentials are kept securely on the server and never exposed to the frontend.
"""

import os
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DEFAULT_INFERENCE_API_URL = "https://predict-6af9e8ecbe647c167e82ae7d-dproatj77a-du.a.run.app/predict"


def normalize_inference_url(raw_url: Optional[str]) -> str:
    """Normalizes an inference endpoint URL.
    - Strips whitespace.
    - Ensures scheme defaults to https:// if omitted.
    - Ensures standard Cloud Run / REST inference path '/predict' is present if omitted or root.
    - Cleanly preserves query strings, fragments, and custom sub-paths.
    """
    if not raw_url:
        return DEFAULT_INFERENCE_API_URL
    cleaned = raw_url.strip()
    if not cleaned:
        return DEFAULT_INFERENCE_API_URL

    if not cleaned.startswith(("http://", "https://")):
        cleaned = f"https://{cleaned}"

    parts = urllib.parse.urlsplit(cleaned)
    path = parts.path.rstrip("/")
    if not path or path == "":
        new_path = "/predict"
    else:
        new_path = path

    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, new_path, parts.query, parts.fragment))


def resolve_api_key() -> Optional[str]:
    """Resolves the inference API key securely from server-side sources.
    Priority:
    1. INFERENCE_API_KEY in os.environ (if explicitly set, even if empty, respect it)
    2. SCHALE_INFERENCE_KEY in os.environ
    3. Project-local .env file (d:/myoi/.env)
    4. Windows Registry User Environment (HKCU) / System Environment (HKLM)
    """
    # 1. Direct process environment check
    for env_var in ("INFERENCE_API_KEY", "SCHALE_INFERENCE_KEY"):
        if env_var in os.environ:
            val = os.environ[env_var]
            if val is not None:
                cleaned_val = val.strip().strip('"').strip("'")
                # If set to non-empty string, return it; if explicitly set to empty, respect it and do not fallback
                return cleaned_val if cleaned_val else None

    # 2. Local .env file check
    try:
        repo_root = Path(__file__).resolve().parent.parent
        dotenv_path = repo_root / ".env"
        if dotenv_path.exists():
            with open(dotenv_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.split("#", 1)[0].strip().strip('"').strip("'")
                        if k in ("INFERENCE_API_KEY", "SCHALE_INFERENCE_KEY") and v:
                            return v
    except Exception:
        pass

    # 3. Windows registry fallback (HKCU -> HKLM)
    if sys.platform == "win32":
        try:
            import winreg

            for hkey, subpath in [
                (winreg.HKEY_CURRENT_USER, r"Environment"),
                (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
            ]:
                try:
                    with winreg.OpenKey(hkey, subpath) as k:
                        for key_name in ("INFERENCE_API_KEY", "SCHALE_INFERENCE_KEY"):
                            try:
                                val, _ = winreg.QueryValueEx(k, key_name)
                                if val:
                                    cleaned = str(val).strip().strip('"').strip("'")
                                    if cleaned:
                                        return cleaned
                            except FileNotFoundError:
                                continue
                except Exception:
                    continue
        except Exception:
            pass

    return None


@dataclass(frozen=True)
class AppConfig:
    host: str = "127.0.0.1"
    port: int = 8000
    inference_api_url: str = DEFAULT_INFERENCE_API_URL
    inference_api_key: Optional[str] = None
    mock_inference: bool = False
    request_timeout_sec: float = 30.0
    max_upload_size_bytes: int = 10 * 1024 * 1024  # 10 MB

    @property
    def is_api_key_configured(self) -> bool:
        return bool(self.inference_api_key and len(self.inference_api_key.strip()) > 0)


def get_config() -> AppConfig:
    api_url = normalize_inference_url(
        os.getenv("INFERENCE_API_URL") or os.getenv("SCHALE_INFERENCE_URL") or DEFAULT_INFERENCE_API_URL
    )
    api_key = resolve_api_key()
    mock_mode = os.getenv("MOCK_INFERENCE", "false").lower() in ("1", "true", "yes")
    timeout = float(os.getenv("INFERENCE_TIMEOUT_SEC", "30.0"))
    host = os.getenv("SCHALE_HOST", "127.0.0.1")
    port = int(os.getenv("SCHALE_PORT", "8000"))

    return AppConfig(
        host=host,
        port=port,
        inference_api_url=api_url,
        inference_api_key=api_key,
        mock_inference=mock_mode,
        request_timeout_sec=timeout,
    )
