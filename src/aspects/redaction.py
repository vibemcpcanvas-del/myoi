"""src/aspects/redaction.py
Aspect 1: Secret Protection & Upstream Error Redaction.
Decouples secret masking, sanitization, and credential leak prevention from core route handlers.
"""

import functools
import re
from typing import Any, Callable, Dict, List, Optional, Set, Union
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from fastapi import HTTPException

from src.aspects.base import is_coroutine_callable


REDACTION_TOKEN = "[REDACTED]"

# Regex patterns for common secret formats
GENERIC_SECRET_PATTERNS = [
    re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.]+", re.IGNORECASE),
    re.compile(r"(api[_-]?key\s*[:=]\s*['\"]?)[A-Za-z0-9_\-\.]+(['\"]?)", re.IGNORECASE),
    re.compile(r"(token\s*[:=]\s*['\"]?)[A-Za-z0-9_\-\.]+(['\"]?)", re.IGNORECASE),
    re.compile(r"(secret\s*[:=]\s*['\"]?)[A-Za-z0-9_\-\.]+(['\"]?)", re.IGNORECASE),
]


class SecretRedactor:
    """Centralized redactor that maintains active sensitive tokens and regex patterns."""

    def __init__(self, exact_secrets: Optional[Set[str]] = None):
        self._exact_secrets: Set[str] = set()
        if exact_secrets:
            for s in exact_secrets:
                if s and len(s.strip()) > 0:
                    self._exact_secrets.add(s.strip())

    def register_secret(self, secret: Optional[str]) -> None:
        """Dynamically registers a secret token to be redacted."""
        if secret and len(secret.strip()) > 0:
            self._exact_secrets.add(secret.strip())

    def unregister_secret(self, secret: Optional[str]) -> None:
        if secret in self._exact_secrets:
            self._exact_secrets.remove(secret)

    def redact_text(self, text: str) -> str:
        """Redacts known secrets and sensitive patterns from a string."""
        if not text:
            return text

        result = str(text)

        # 1. Exact string matches of registered secrets
        for sec in self._exact_secrets:
            if sec and sec in result:
                result = result.replace(sec, REDACTION_TOKEN)

        # 2. Pattern-based redaction
        for pat in GENERIC_SECRET_PATTERNS:
            repl = r"\g<1>[REDACTED]\g<2>" if pat.groups >= 2 else r"\g<1>[REDACTED]"
            result = pat.sub(repl, result)

        return result

    def redact_data(self, data: Any) -> Any:
        """Recursively traverses and redacts dictionary, list, or string structures."""
        if isinstance(data, str):
            return self.redact_text(data)
        elif isinstance(data, dict):
            cleaned: Dict[str, Any] = {}
            for k, v in data.items():
                # If key itself implies a secret, redact value directly
                if any(sec_term in str(k).lower() for sec_term in ("secret", "token", "password", "api_key", "apikey")):
                    cleaned[k] = REDACTION_TOKEN
                else:
                    cleaned[k] = self.redact_data(v)
            return cleaned
        elif isinstance(data, list):
            return [self.redact_data(item) for item in data]
        elif isinstance(data, tuple):
            return tuple(self.redact_data(item) for item in data)
        elif isinstance(data, (set, frozenset)):
            return {self.redact_data(item) for item in data}
        return data


# Global singleton redactor
global_redactor = SecretRedactor()


def get_global_redactor() -> SecretRedactor:
    """Returns the shared secret redactor instance, ensuring current config secret is registered."""
    try:
        from src.config import get_config

        cfg = get_config()
        if cfg.inference_api_key:
            global_redactor.register_secret(cfg.inference_api_key)
    except Exception:
        pass
    return global_redactor


def redact_secrets(redactor: Optional[SecretRedactor] = None) -> Callable:
    """AOP Decorator: Intercepts function execution to ensure error details and responses
    never echo server-side secret tokens or credentials.
    """
    active_redactor = redactor or get_global_redactor()

    def decorator(func: Callable) -> Callable:
        if is_coroutine_callable(func):

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                try:
                    res = await func(*args, **kwargs)
                    return active_redactor.redact_data(res)
                except HTTPException as he:
                    # Sanitize HTTPException detail across str, dict, list, tuple
                    he.detail = active_redactor.redact_data(he.detail)
                    raise he
                except Exception as ex:
                    sanitized_msg = active_redactor.redact_text(str(ex))
                    try:
                        raise type(ex)(sanitized_msg) from ex
                    except TypeError:
                        if hasattr(ex, "args") and ex.args:
                            ex.args = tuple(active_redactor.redact_data(a) for a in ex.args)
                        raise ex

            return async_wrapper
        else:

            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                try:
                    res = func(*args, **kwargs)
                    return active_redactor.redact_data(res)
                except HTTPException as he:
                    he.detail = active_redactor.redact_data(he.detail)
                    raise he
                except Exception as ex:
                    sanitized_msg = active_redactor.redact_text(str(ex))
                    try:
                        raise type(ex)(sanitized_msg) from ex
                    except TypeError:
                        if hasattr(ex, "args") and ex.args:
                            ex.args = tuple(active_redactor.redact_data(a) for a in ex.args)
                        raise ex

            return sync_wrapper

    return decorator


class SecretRedactionMiddleware(BaseHTTPMiddleware):
    """FastAPI/Starlette Middleware: Redacts secrets from outgoing response headers and bodies."""

    def __init__(self, app, redactor: Optional[SecretRedactor] = None):
        super().__init__(app)
        self.redactor = redactor or get_global_redactor()

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Refresh registered secret from config
        try:
            from src.config import get_config

            cfg = get_config()
            if cfg.inference_api_key:
                self.redactor.register_secret(cfg.inference_api_key)
        except Exception:
            pass

        response = await call_next(request)

        # Sanitize sensitive headers
        for h in ("Authorization", "Proxy-Authorization", "X-Api-Key"):
            if h in response.headers:
                response.headers[h] = REDACTION_TOKEN

        # Sanitize sensitive response bodies (JSON and text payloads)
        content_type = response.headers.get("content-type", "")
        if ("application/json" in content_type or "text/" in content_type) and hasattr(response, "body") and response.body:
            try:
                body_str = response.body.decode("utf-8")
                sanitized_body = self.redactor.redact_text(body_str)
                if sanitized_body != body_str:
                    new_bytes = sanitized_body.encode("utf-8")
                    new_headers = dict(response.headers)
                    new_headers["content-length"] = str(len(new_bytes))
                    response = Response(
                        content=new_bytes,
                        status_code=response.status_code,
                        headers=new_headers,
                        media_type=response.media_type,
                    )
            except Exception:
                pass

        return response
