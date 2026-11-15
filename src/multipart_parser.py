"""src/multipart_parser.py
Zero-dependency multipart/form-data parser using standard Python.
Avoids requiring python-multipart while fully adhering to dependency governance.
"""

import email
from typing import Dict, Optional, Tuple


class ParsedFormData:
    def __init__(self):
        self.fields: Dict[str, str] = {}
        self.files: Dict[str, Tuple[str, bytes, str]] = {}  # name -> (filename, bytes, content_type)


def parse_multipart_body(body: bytes, content_type_header: str) -> ParsedFormData:
    """Parses multipart/form-data from raw body bytes and Content-Type header."""
    result = ParsedFormData()
    if not body or not content_type_header:
        return result

    # Prepend synthetic MIME header so email parser understands it
    safe_header = content_type_header.encode("utf-8", errors="replace")
    raw_mime = b"Content-Type: " + safe_header + b"\r\n\r\n" + body
    msg = email.message_from_bytes(raw_mime)

    if not msg.is_multipart():
        return result

    parts = msg.get_payload()
    if not isinstance(parts, list):
        return result

    for part in parts:
        disposition = part.get("Content-Disposition", "")
        if "form-data" not in disposition:
            continue

        name = part.get_param("name", header="Content-Disposition")
        if not name:
            continue

        filename = part.get_param("filename", header="Content-Disposition")
        part_content_type = part.get_content_type() or "application/octet-stream"
        payload = part.get_payload(decode=True)
        if payload is None:
            payload = b""

        if filename is not None:
            result.files[name] = (filename, payload, part_content_type)
        else:
            charset = part.get_content_charset() or "utf-8"
            result.fields[name] = payload.decode(charset, errors="replace")

    return result
