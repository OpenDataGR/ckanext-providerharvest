"""Provider-facing failure notifications.

The provider owns their own registration, so they own responding to its
problems too -- rather than relying on someone watching a dashboard, a
run failure actively notifies the provider's registered contact. Fires
after N consecutive failures (not on the first blip) to avoid noise from
a single transient timeout.
"""

from __future__ import annotations

CONSECUTIVE_FAILURES_BEFORE_NOTIFY = 3

FAILURE_REASON_MESSAGES = {
    "timeout": "Connection to your source timed out.",
    "auth_failure": "Authentication failed -- check your configured credentials.",
    "mapping_error": "A record did not match your configured field mapping.",
    "network_target_rejected": "Your configured endpoint failed a network safety check.",
    "unknown": "The harvest run failed for an unspecified reason.",
}


def build_failure_message(*, source_title: str, failed_at, reason: str, detail: str = "") -> str:
    reason_text = FAILURE_REASON_MESSAGES.get(reason, FAILURE_REASON_MESSAGES["unknown"])
    lines = [
        "Your source '%s' failed on %s." % (source_title, failed_at),
        reason_text,
    ]
    if detail:
        lines.append("Detail: %s" % detail)
    lines.append(
        "Log in to review the run history and update your configuration if needed."
    )
    return "\n".join(lines)


def should_notify(consecutive_failure_count: int) -> bool:
    return consecutive_failure_count >= CONSECUTIVE_FAILURES_BEFORE_NOTIFY


def notify(provider_source, *, source_title: str, failed_at, reason: str, detail: str = "",
           mail_fn=None) -> bool:
    """Sends the notification if the failure-count threshold is met and a
    contact is registered. Returns True if a notification was actually
    sent (useful for tests / callers wanting to log the outcome).

    ``mail_fn`` is injected (defaults to CKAN's own mailer) so this stays
    unit-testable without sending real email.
    """
    if not should_notify(provider_source.consecutive_failure_count or 0):
        return False
    if not provider_source.notification_email:
        return False

    if mail_fn is None:
        from ckan.lib.mailer import mail_recipient
        mail_fn = mail_recipient

    body = build_failure_message(
        source_title=source_title, failed_at=failed_at, reason=reason, detail=detail
    )
    mail_fn(
        recipient_name=source_title,
        recipient_email=provider_source.notification_email,
        subject="[data.gov.gr] Source '%s' failed" % source_title,
        body=body,
    )
    return True
