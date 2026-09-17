from types import SimpleNamespace

from ckanext.providerharvest.notifications import (
    CONSECUTIVE_FAILURES_BEFORE_NOTIFY,
    notify,
    should_notify,
)


def _source(**overrides):
    defaults = dict(consecutive_failure_count=0, notification_email=None)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_no_notification_below_threshold():
    calls = []
    source = _source(consecutive_failure_count=CONSECUTIVE_FAILURES_BEFORE_NOTIFY - 1,
                      notification_email="ops@example.com")
    sent = notify(source, source_title="Demo", failed_at="now", reason="timeout",
                  mail_fn=lambda **kw: calls.append(kw))
    assert sent is False
    assert calls == []


def test_notifies_once_threshold_reached():
    calls = []
    source = _source(consecutive_failure_count=CONSECUTIVE_FAILURES_BEFORE_NOTIFY,
                      notification_email="ops@example.com")
    sent = notify(source, source_title="Demo", failed_at="2026-09-17T03:00:00", reason="auth_failure",
                  mail_fn=lambda **kw: calls.append(kw))
    assert sent is True
    assert len(calls) == 1
    assert calls[0]["recipient_email"] == "ops@example.com"
    assert "Demo" in calls[0]["subject"]
    assert "Authentication failed" in calls[0]["body"]


def test_no_notification_without_contact():
    calls = []
    source = _source(consecutive_failure_count=CONSECUTIVE_FAILURES_BEFORE_NOTIFY,
                      notification_email=None)
    sent = notify(source, source_title="Demo", failed_at="now", reason="timeout",
                  mail_fn=lambda **kw: calls.append(kw))
    assert sent is False
    assert calls == []


def test_should_notify_boundary():
    assert should_notify(CONSECUTIVE_FAILURES_BEFORE_NOTIFY - 1) is False
    assert should_notify(CONSECUTIVE_FAILURES_BEFORE_NOTIFY) is True
