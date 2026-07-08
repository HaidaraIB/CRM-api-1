from unittest.mock import MagicMock

from openai import AuthenticationError, RateLimitError

from integrations.services.openai_errors import classify_openai_error


def _make_openai_error(exc_cls, body, message="error"):
    response = MagicMock()
    response.request = MagicMock()
    return exc_cls(message, response=response, body=body)


def test_classify_insufficient_quota():
    exc = _make_openai_error(
        RateLimitError,
        {
            "error": {
                "message": "You exceeded your current quota.",
                "type": "insufficient_quota",
                "code": "insufficient_quota",
            }
        },
        message="quota exceeded",
    )
    info = classify_openai_error(exc)
    assert info.code == "openai_insufficient_quota"
    assert info.disable_auto_analyze is True
    assert info.log_as_exception is False


def test_classify_invalid_api_key():
    exc = _make_openai_error(
        AuthenticationError,
        {"error": {"code": "invalid_api_key", "message": "Incorrect API key provided"}},
        message="invalid key",
    )
    info = classify_openai_error(exc)
    assert info.code == "openai_invalid_api_key"
    assert info.disable_auto_analyze is True
