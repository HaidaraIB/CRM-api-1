"""
OpenAI API error classification for tenant BYOK integrations.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OpenAIErrorInfo:
    code: str
    message: str
    disable_auto_analyze: bool = False
    log_as_exception: bool = True


def _extract_error_code(exc: Exception) -> str | None:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            code = err.get("code")
            if code:
                return str(code)
    return None


def classify_openai_error(exc: Exception) -> OpenAIErrorInfo:
    api_code = _extract_error_code(exc) or ""
    exc_type = type(exc).__name__

    if api_code == "insufficient_quota":
        return OpenAIErrorInfo(
            code="openai_insufficient_quota",
            message=(
                "OpenAI API quota exceeded. Add billing or credits in your OpenAI account, "
                "then re-enable automatic analysis."
            ),
            disable_auto_analyze=True,
            log_as_exception=False,
        )

    if api_code in {"invalid_api_key", "authentication_error"} or exc_type == "AuthenticationError":
        return OpenAIErrorInfo(
            code="openai_invalid_api_key",
            message="OpenAI API key is invalid or revoked. Update your key in integration settings.",
            disable_auto_analyze=True,
            log_as_exception=False,
        )

    if api_code == "model_not_found":
        return OpenAIErrorInfo(
            code="openai_model_not_found",
            message="The configured OpenAI model is not available for this API key.",
            log_as_exception=False,
        )

    if exc_type == "RateLimitError" and api_code != "insufficient_quota":
        return OpenAIErrorInfo(
            code="openai_rate_limited",
            message="OpenAI rate limit reached. Try again in a few minutes.",
            log_as_exception=False,
        )

    return OpenAIErrorInfo(
        code="openai_api_error",
        message=str(exc)[:500],
    )


def persist_openai_settings_error(settings, exc: Exception) -> OpenAIErrorInfo:
    info = classify_openai_error(exc)
    update_fields = ["last_error"]
    settings.last_error = info.message
    if info.disable_auto_analyze and settings.auto_analyze_enabled:
        settings.auto_analyze_enabled = False
        update_fields.append("auto_analyze_enabled")
    settings.save(update_fields=update_fields)
    return info


def log_openai_failure(logger, exc: Exception, *, company_id, context: str) -> OpenAIErrorInfo:
    info = classify_openai_error(exc)
    if info.log_as_exception:
        logger.exception("OpenAI %s failed for company %s", context, company_id)
    else:
        logger.warning(
            "OpenAI %s failed for company %s (%s): %s",
            context,
            company_id,
            info.code,
            info.message,
        )
    return info
