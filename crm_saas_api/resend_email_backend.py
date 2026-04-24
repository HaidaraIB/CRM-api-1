"""
Django email backend that sends via Resend HTTP API (https://resend.com/).
"""
from __future__ import annotations

import base64
import logging
from email.mime.base import MIMEBase
from typing import Any

from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.message import EmailMessage

logger = logging.getLogger(__name__)


def _attachments_to_resend(attachments: list) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for att in attachments:
        if isinstance(att, MIMEBase):
            filename = att.get_filename() or "attachment"
            raw = att.get_payload(decode=True)
            if raw is None:
                raw = (att.get_payload() or "").encode("utf-8")
            encoded = base64.b64encode(raw).decode("ascii")
            item: dict[str, Any] = {"filename": filename, "content": encoded}
            ctype = att.get_content_type()
            if ctype:
                item["content_type"] = ctype
            out.append(item)
            continue
        if isinstance(att, tuple):
            filename = att[0]
            content = att[1]
            mimetype = att[2] if len(att) > 2 else None
            if isinstance(content, str):
                raw = content.encode("utf-8")
            else:
                raw = bytes(content)
            item = {
                "filename": str(filename),
                "content": base64.b64encode(raw).decode("ascii"),
            }
            if mimetype:
                item["content_type"] = str(mimetype)
            out.append(item)
            continue
        logger.warning("Skipping unsupported attachment type: %s", type(att).__name__)
    return out


def _message_to_resend_params(message: EmailMessage) -> dict[str, Any]:
    alternatives = getattr(message, "alternatives", None) or []
    html = None
    for content, mimetype in alternatives:
        if mimetype == "text/html":
            html = content
            break

    text = message.body or None
    if not html and not text:
        text = ""

    params: dict[str, Any] = {
        "from": message.from_email,
        "to": list(message.to or []),
        "subject": message.subject or "",
    }
    if html:
        params["html"] = html
    if text is not None:
        params["text"] = text

    if message.cc:
        params["cc"] = list(message.cc)
    if message.bcc:
        params["bcc"] = list(message.bcc)
    if message.reply_to:
        params["reply_to"] = list(message.reply_to)

    if message.attachments:
        params["attachments"] = _attachments_to_resend(list(message.attachments))

    return params


class ResendEmailBackend(BaseEmailBackend):
    """
    Sends EmailMessage instances through Resend's API.
    Expects ``api_key`` to be a non-empty Resend API key.
    """

    def __init__(self, fail_silently: bool = False, api_key: str | None = None, **kwargs):
        super().__init__(fail_silently=fail_silently, **kwargs)
        self.api_key = (api_key or "").strip()

    def send_messages(self, email_messages: list[EmailMessage]) -> int:
        if not email_messages:
            return 0
        if not self.api_key:
            if self.fail_silently:
                return 0
            raise ValueError("ResendEmailBackend requires a non-empty api_key.")

        import resend

        resend.api_key = self.api_key
        num_sent = 0
        for message in email_messages:
            try:
                params = _message_to_resend_params(message)
                if not params.get("to"):
                    logger.warning("Resend: skipping message with empty recipients.")
                    continue
                resend.Emails.send(params)
                num_sent += 1
            except Exception:
                if not self.fail_silently:
                    raise
                logger.exception("Resend send failed for message subject=%r", message.subject)
        return num_sent
