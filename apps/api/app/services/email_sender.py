"""Transactional email — security-hardened smtplib wrapper.

Hardening passes after the 2026-05-12 security review covered:
    B-1 — token in URL fragment (not query string) so it doesn't leak
          via Referer/history/access logs.
    B-2 — strip CRLF + NUL from `to` / `subject` / `from` to prevent
          email-header injection.
    B-3 — hard-allowlist the reset link hostname; refuse to send if
          PUBLIC_BASE_URL points anywhere else.
    B-6 — enforce TLS: SMTP_SSL on port 465, STARTTLS with verified
          context on 587, refuse to login() unless the connection is
          actually TLS-wrapped (defeats STARTTLS-strip MitM).
    B-7 — pin EMAIL_SMTP_HOST to a known-provider allowlist; refuse
          to send credentials to an arbitrary host.
    I-3 — html.escape + urllib.parse.quote on every value interpolated
          into HTML / URL.
    I-4 — `From:` header built via `email.utils.formataddr` (correct
          quoting of display-name special chars).
    I-11 — refuse to call `server.login()` over plaintext, period.
    N-3 — dry-run path logs at WARNING (not INFO) so an accidental
          prod deploy without EMAIL_SMTP_HOST surfaces in monitoring.

The two production callers (auth.recover_password, future invitation
flow) treat `send_email` as best-effort — it never raises into the
request handler.
"""
from __future__ import annotations

import html
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from typing import Optional
from urllib.parse import quote, urlparse

from app.core.config import settings

logger = logging.getLogger(__name__)


# B-7: SMTP host allowlist. The platform talks to one of these or it
# refuses. A misconfigured EMAIL_SMTP_HOST (env-var typo, hostile
# config injection) cannot ship the SMTP password to an arbitrary
# server. AWS SES is regional, so we accept any `email-smtp.<region>`.
_ALLOWED_SMTP_HOSTS = frozenset({
    "smtp.sendgrid.net",
    "smtp.postmarkapp.com",
    "smtp.mailgun.org",
    "smtp.gmail.com",
    "smtp.fastmail.com",
    # localhost is allowed but B-6 will still refuse to login() over
    # plaintext, which is the right call (you don't auth to localhost
    # SMTP in any realistic dev setup anyway).
    "localhost",
    "127.0.0.1",
    "mailhog",
    "mailpit",
})


def _smtp_host_is_allowed(host: str) -> bool:
    """Allowlist check covering both the static set and AWS SES regions."""
    if not host:
        return False
    host = host.lower().strip()
    if host in _ALLOWED_SMTP_HOSTS:
        return True
    # AWS SES has per-region hostnames: email-smtp.<region>.amazonaws.com
    if host.startswith("email-smtp.") and host.endswith(".amazonaws.com"):
        return True
    return False


# B-3: reset-link hostname allowlist. PUBLIC_BASE_URL is what the
# email body links to; a misconfigured value would phish users with
# attacker-controlled hosts but VALID tokens. We pin to the two
# production hostnames + localhost for dev.
_ALLOWED_LINK_HOSTNAMES = frozenset({
    "agentprovision.com",
    "app.agentprovision.com",
    "luna.agentprovision.com",
    "localhost",
    "127.0.0.1",
})


def _sanitize_header(value: str, max_len: int = 998) -> str:
    """Strip CR/LF/NUL from a value destined for an email header.

    RFC 5322 limits header lines to 998 chars; cap defensively. Python's
    `EmailMessage` rejects newlines in most versions but not all — the
    explicit strip + length cap is cheap insurance against B-2.
    """
    if not isinstance(value, str):
        return ""
    cleaned = value.replace("\r", "").replace("\n", "").replace("\0", "")
    return cleaned[:max_len]


def send_email(
    *,
    to: str,
    subject: str,
    text_body: str,
    html_body: Optional[str] = None,
) -> bool:
    """Send a transactional email. Never raises.

    Returns True if the message hit the SMTP server (or the log-only
    fallback fired with no exception). Returns False on a caught
    exception or a security-rule refusal — the caller (auth recovery,
    invitations) is expected to return the same generic success
    response to the user either way so a sender failure does not leak
    enumeration via a response-status difference.
    """
    # B-2: sanitise everything before it touches a header.
    to = _sanitize_header(to, max_len=254)
    subject = _sanitize_header(subject, max_len=200)
    from_name = _sanitize_header(settings.EMAIL_FROM_NAME, max_len=80)
    from_addr = _sanitize_header(settings.EMAIL_FROM, max_len=254)

    if not to or "@" not in to:
        logger.warning("email_sender: refused — bad/empty recipient address")
        return False

    if not settings.EMAIL_SMTP_HOST:
        # N-3: WARNING (not INFO) so a prod deploy without SMTP config
        # surfaces in any log-volume-by-severity alerting. Body NEVER
        # logged — even in dry-run, reset tokens are secret.
        logger.warning(
            "email_sender: dry-run (EMAIL_SMTP_HOST unset) — to=%s subject=%r",
            to,
            subject,
        )
        return True

    # B-7: host allowlist. Refuse before opening the socket.
    if not _smtp_host_is_allowed(settings.EMAIL_SMTP_HOST):
        logger.error(
            "email_sender: refused — EMAIL_SMTP_HOST=%r is not on the allowlist",
            settings.EMAIL_SMTP_HOST,
        )
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    # I-4: formataddr handles display-name quoting + RFC 2047 encoding
    # of non-ASCII characters correctly. The earlier f-string was a
    # latent injection foot-gun.
    msg["From"] = formataddr((from_name, from_addr))
    msg["To"] = to
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    try:
        # B-6: enforce TLS at the wire level.
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = True
        ssl_context.verify_mode = ssl.CERT_REQUIRED

        if settings.EMAIL_SMTP_PORT == 465:
            # Implicit-TLS port (SMTPS) — can't be STARTTLS-stripped
            # because the entire connection is TLS from the first byte.
            server = smtplib.SMTP_SSL(
                settings.EMAIL_SMTP_HOST,
                settings.EMAIL_SMTP_PORT,
                timeout=30,
                context=ssl_context,
            )
        else:
            server = smtplib.SMTP(
                settings.EMAIL_SMTP_HOST, settings.EMAIL_SMTP_PORT, timeout=30
            )

        with server:
            if settings.EMAIL_SMTP_PORT != 465:
                # 587 / msa: STARTTLS upgrade required. If it's disabled
                # in config OR the server doesn't advertise it, refuse —
                # we won't login() over plaintext (I-11).
                if not settings.EMAIL_SMTP_USE_TLS:
                    logger.error(
                        "email_sender: refused — EMAIL_SMTP_USE_TLS=false; "
                        "we don't ship credentials over plaintext"
                    )
                    return False
                server.ehlo()
                if not server.has_extn("starttls"):
                    logger.error(
                        "email_sender: refused — server %s did not advertise "
                        "STARTTLS (possible MitM downgrade)",
                        settings.EMAIL_SMTP_HOST,
                    )
                    return False
                server.starttls(context=ssl_context)
                # Post-upgrade EHLO to refresh extensions list.
                server.ehlo()
                # B-6 belt-and-suspenders: assert the socket is actually
                # TLS-wrapped. If anything went sideways inside starttls
                # without raising, this catches it before login() runs.
                if not isinstance(server.sock, ssl.SSLSocket):
                    logger.error(
                        "email_sender: refused — TLS upgrade did not produce "
                        "an SSLSocket; credentials would have been plaintext"
                    )
                    return False

            if settings.EMAIL_SMTP_USERNAME and settings.EMAIL_SMTP_PASSWORD:
                server.login(
                    settings.EMAIL_SMTP_USERNAME, settings.EMAIL_SMTP_PASSWORD
                )
            server.send_message(msg)

        logger.info(
            "email_sender: sent — to=%s subject=%r host=%s",
            to,
            subject,
            settings.EMAIL_SMTP_HOST,
        )
        return True
    except Exception as exc:
        # Don't swallow silently — log at WARN so a recurring SMTP
        # outage surfaces in monitoring. Never raise: the caller's
        # response shape is identical hit/miss to prevent enumeration,
        # so an exception only matters for our own observability.
        logger.warning(
            "email_sender: failed — to=%s subject=%r host=%s error=%s",
            to,
            subject,
            settings.EMAIL_SMTP_HOST,
            exc,
        )
        return False


# ── ready-baked templates for the only two callers today ─────────────


def send_password_reset_email(
    *, to: str, reset_token: str, public_base_url: str
) -> bool:
    """Compose + send the password-recovery email.

    Reset URL puts the token in the **fragment** (`#token=...`), NOT
    the query string. Fragments are never sent in Referer headers,
    never logged by HTTP intermediaries, and can be wiped via
    `history.replaceState` after the SPA reads them (B-1).

    `public_base_url` is sanity-checked against `_ALLOWED_LINK_HOSTNAMES`
    so a misconfigured PUBLIC_BASE_URL can't ship valid tokens to an
    attacker-controlled host (B-3).
    """
    # B-3: hostname allowlist + scheme pin.
    base = (public_base_url or "https://agentprovision.com").rstrip("/")
    try:
        parsed = urlparse(base)
    except Exception:
        parsed = None
    if (
        parsed is None
        or parsed.scheme not in ("https", "http")
        or (parsed.hostname or "").lower() not in _ALLOWED_LINK_HOSTNAMES
    ):
        logger.error(
            "send_password_reset_email: refused — PUBLIC_BASE_URL %r is not on "
            "the link-hostname allowlist",
            public_base_url,
        )
        return False
    # Production must use HTTPS; HTTP only allowed for localhost dev.
    if parsed.scheme == "http" and (parsed.hostname or "").lower() not in (
        "localhost",
        "127.0.0.1",
    ):
        logger.error(
            "send_password_reset_email: refused — non-localhost base "
            "URL %r uses http://; production must be https",
            public_base_url,
        )
        return False

    # B-1: token in URL FRAGMENT, not query string. Email field stays
    # in the query string (not the secret). I-3: URL-encode both.
    safe_token = quote(reset_token, safe="")
    safe_email = quote(to, safe="@")
    reset_url = f"{base}/reset-password?email={safe_email}#token={safe_token}"

    subject = "Reset your AgentProvision password"
    # I-N1: call out the same-browser requirement in the body. The
    # cookie binding refuses cross-device redemption, so a user who
    # requested the reset on their laptop and clicks the link on
    # their phone will hit the friendlier error from /reset-password
    # — but it's nicer if they don't have to fail through first.
    text_body = (
        "Hi,\n\n"
        "You (or someone using your email) requested a password reset for\n"
        "your AgentProvision account.\n\n"
        f"Reset link: {reset_url}\n\n"
        "Important: for your security, open this link in the same browser\n"
        "and device where you requested the reset. Clicking it from another\n"
        "device or a private/incognito tab won't work.\n\n"
        "The link expires in 24 hours. If you didn't request this, you can\n"
        "safely ignore this email — your password won't change.\n\n"
        "— AgentProvision\n"
    )

    # I-3: html.escape every value interpolated into the HTML body.
    safe_url_attr = html.escape(reset_url, quote=True)
    safe_url_text = html.escape(reset_url, quote=False)
    html_body = (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,'
        'sans-serif;color:#1a2433;max-width:520px;margin:auto;padding:24px">'
        '<h2 style="color:#2b7de9;margin:0 0 12px 0">Reset your password</h2>'
        '<p>You (or someone using your email) requested a password reset for '
        "your AgentProvision account.</p>"
        f'<p style="margin:24px 0"><a href="{safe_url_attr}" '
        'style="background:#2b7de9;color:#fff;padding:12px 20px;border-radius:6px;'
        'text-decoration:none;display:inline-block">Reset your password</a></p>'
        '<p style="font-size:12px;color:#64748b">Or copy this link: <br>'
        f'<a href="{safe_url_attr}" style="color:#2b7de9">{safe_url_text}</a></p>'
        '<p style="font-size:12px;color:#64748b;margin-top:24px;'
        'background:#fef9e7;border-left:3px solid #ca8a04;padding:8px 12px">'
        "<strong>Important:</strong> for your security, open this link in the "
        "same browser and device where you requested the reset. Clicking it "
        "from another device or a private/incognito tab won't work.</p>"
        '<p style="font-size:12px;color:#64748b">'
        "The link expires in 24 hours. If you didn't request this, you can "
        "safely ignore this email — your password won't change.</p>"
        '<p style="font-size:12px;color:#64748b">— AgentProvision</p>'
        "</div>"
    )
    return send_email(
        to=to,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
    )
