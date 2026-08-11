"""Polled endpoints don't spam `Forbidden:` warnings; every other 403 stays loud."""

import logging

from crm_saas_api.logging_filters import SkipExpectedForbiddenFilter


def _record(msg, level=logging.WARNING):
    return logging.LogRecord(
        name="django.request", level=level, pathname=__file__, lineno=1,
        msg=msg, args=(), exc_info=None,
    )


def test_polled_whatsapp_forbidden_is_dropped():
    f = SkipExpectedForbiddenFilter()
    assert not f.filter(_record("Forbidden: /api/v1/integrations/whatsapp/unread-count/"))
    assert not f.filter(_record("Forbidden: /api/v1/integrations/whatsapp/conversations/"))


def test_other_forbidden_paths_still_logged():
    f = SkipExpectedForbiddenFilter()
    assert f.filter(_record("Forbidden: /api/v1/integrations/templates/"))
    assert f.filter(_record("Forbidden: /api/v1/accounts/users/"))
    assert f.filter(_record("Forbidden: /api/v1/integrations/whatsapp/send/"))


def test_non_forbidden_and_errors_pass_through():
    f = SkipExpectedForbiddenFilter()
    assert f.filter(_record("Unauthorized: /api/v1/integrations/whatsapp/unread-count/"))
    assert f.filter(
        _record("Forbidden: /api/v1/integrations/whatsapp/unread-count/", level=logging.ERROR)
    )
