"""Probe TLS endpoints and report cert issues to Sentry.

Runs once per invocation. Sends Sentry events for any cert that is:
- already expired (level=fatal),
- expiring within 7 days (level=error),
- expiring within 14 days (level=warning),
- presenting a hostname mismatch or unreachable (level=error / exception).

Also reports a check-in to Sentry Cron Monitor "cert-watcher" so a missed
run pages us — that's the bug class that bit us when pic-certbot died silently.
"""

import datetime as dt
import os
import socket
import ssl
import sys

import sentry_sdk
from cryptography import x509
from cryptography.x509.oid import ExtensionOID, NameOID
from sentry_sdk.crons import monitor

TARGETS = [
    "panamaincontext.com",
    "therelevantdev.com",
]
WARN_DAYS = 14
ERROR_DAYS = 7
PORT = 443
CONNECT_TIMEOUT = 10

sentry_sdk.init(
    dsn=os.environ["SENTRY_DSN"],
    environment=os.environ.get("ENVIRONMENT", "production"),
    traces_sample_rate=0.0,
)


def fetch_cert(hostname: str) -> x509.Certificate:
    """Return the server's leaf certificate. We disable verification so we can
    inspect (and report on) certs that would otherwise raise — but we
    explicitly verify the SAN/CN and expiry ourselves below."""
    ctx = ssl._create_unverified_context()
    with socket.create_connection((hostname, PORT), timeout=CONNECT_TIMEOUT) as sock:
        with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
            der = ssock.getpeercert(binary_form=True)
    return x509.load_der_x509_certificate(der)


def cert_names(cert: x509.Certificate) -> list[str]:
    names: list[str] = []
    try:
        san = cert.extensions.get_extension_for_oid(
            ExtensionOID.SUBJECT_ALTERNATIVE_NAME
        ).value
        names.extend(n.lower() for n in san.get_values_for_type(x509.DNSName))
    except x509.ExtensionNotFound:
        pass
    for attr in cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME):
        names.append(attr.value.lower())
    return names


def hostname_matches(cert: x509.Certificate, hostname: str) -> bool:
    h = hostname.lower()
    for n in cert_names(cert):
        if n == h:
            return True
        if n.startswith("*.") and h.endswith(n[1:]) and h.count(".") == n.count("."):
            return True
    return False


@monitor(
    monitor_slug="cert-watcher",
    monitor_config={
        "schedule": {"type": "interval", "value": 1, "unit": "day"},
        "checkin_margin": 60,  # minutes
        "max_runtime": 5,  # minutes
        "timezone": "UTC",
    },
)
def check_all() -> None:
    any_failure = False
    for host in TARGETS:
        with sentry_sdk.new_scope() as scope:
            scope.set_tag("domain", host)
            try:
                cert = fetch_cert(host)
                not_after = cert.not_valid_after_utc
                days = (not_after - dt.datetime.now(dt.timezone.utc)).days
                scope.set_extra("not_after", not_after.isoformat())
                scope.set_extra("days_remaining", days)
                scope.set_extra("cert_names", cert_names(cert))

                if not hostname_matches(cert, host):
                    sentry_sdk.capture_message(
                        f"SSL cert served for {host} has no matching SAN/CN",
                        level="error",
                    )
                    any_failure = True
                    print(f"{host}: HOSTNAME MISMATCH", flush=True)
                    continue

                if days < 0:
                    sentry_sdk.capture_message(
                        f"SSL cert EXPIRED for {host} ({-days} days ago)",
                        level="fatal",
                    )
                    any_failure = True
                elif days < ERROR_DAYS:
                    sentry_sdk.capture_message(
                        f"SSL cert expires in {days} days for {host}",
                        level="error",
                    )
                elif days < WARN_DAYS:
                    sentry_sdk.capture_message(
                        f"SSL cert expires in {days} days for {host}",
                        level="warning",
                    )
                print(f"{host}: {days} days remaining", flush=True)
            except Exception as exc:
                sentry_sdk.capture_exception(exc)
                print(f"{host}: ERROR {exc!r}", flush=True)
                any_failure = True

    if any_failure:
        raise RuntimeError("cert-watcher detected at least one failing target")


if __name__ == "__main__":
    try:
        check_all()
    except Exception:
        sys.exit(1)