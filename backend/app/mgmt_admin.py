"""Operator tooling for staff accounts that cannot be reached through the web app.

This exists for one narrow, unavoidable situation: an administrator has lost BOTH their
authenticator and every recovery code, or a brand-new deployment has no administrator yet.
Neither can be solved over HTTP without inventing a universal recovery code, so it is solved
where the authority actually is - a shell on the server, using the same database credentials
the application already holds.

It is not a hidden bypass in the API: there is no endpoint behind it, it cannot be triggered
remotely, and every action it takes is written to the audit log with ``cli:operator`` as the
actor so the web UI shows the change next to everything else.

    python -m app.mgmt_admin --list
    python -m app.mgmt_admin --create-admin ops@example.com --name "Ops Lead"
    python -m app.mgmt_admin --set-password ops@example.com
    python -m app.mgmt_admin --reset-mfa ops@example.com
    python -m app.mgmt_admin --revoke-published-secrets --dry-run
    python -m app.mgmt_admin --email-status
    python -m app.mgmt_admin --send-test-email you@example.org

Passwords are read from stdin (never from an argument, which would land in shell history and
process listings) and are never echoed or logged. ``--reset-mfa`` removes the second factor
so the account is routed to the in-app enrolment screen at its next sign-in; it does not
create a session and does not disable the mandatory-MFA policy.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.passwords import password_policy_error
from app.core.security import hash_password
from app.models.crm import AuditLog, User
from app.seed_data import DEV_MFA_SECRET_ADMIN, DEV_MFA_SECRET_SENIOR

PUBLISHED_SECRETS = (DEV_MFA_SECRET_ADMIN, DEV_MFA_SECRET_SENIOR)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _audit(db, action: str, email: Optional[str], detail: dict) -> None:
    db.add(AuditLog(actor_email="cli:operator", action=action, entity_type="users",
                    detail={"subject": email, **detail}))


def _read_password() -> str:
    if sys.stdin.isatty():
        first = getpass.getpass("New password: ")
        second = getpass.getpass("Repeat password: ")
        if first != second:
            raise SystemExit("Passwords did not match.")
        return first
    return sys.stdin.readline().strip()


def _validate_password(password: str) -> None:
    """Same policy the admin API enforces; the CLI reports it as a fatal, non-zero exit."""
    error = password_policy_error(password)
    if error:
        raise SystemExit(error)


def cmd_list(db) -> int:
    rows = db.execute(select(User).order_by(User.role, User.email)).scalars().all()
    for u in rows:
        enrolled = "mfa" if (u.mfa_secret and u.mfa_confirmed_at) else "-"
        published = "PUBLISHED-SECRET" if u.mfa_secret in PUBLISHED_SECRETS else ""
        codes = len(u.mfa_recovery_codes or [])
        print(f"{u.id:>4}  {u.role:<18} {u.email:<34} active={u.is_active!s:<5} "
              f"{enrolled:<3} recovery_codes={codes} {published}")
    return 0


def cmd_create_admin(db, email: str, name: str) -> int:
    address = email.lower().strip()
    if db.execute(select(User).where(User.email == address)).scalars().first():
        raise SystemExit(f"{address} already exists; use --set-password or --reset-mfa.")
    password = _read_password()
    _validate_password(password)
    user = User(email=address, full_name=name or "Administrator",
                password_hash=hash_password(password), role="ADMIN", is_active=True)
    db.add(user)
    _audit(db, "CLI_ADMIN_CREATED", address, {"role": "ADMIN"})
    db.commit()
    print(f"Created ADMIN {address}. Sign in at /admin and enrol an authenticator app; "
          f"two-factor is mandatory for this role.")
    return 0


def cmd_set_password(db, email: str) -> int:
    address = email.lower().strip()
    user = db.execute(select(User).where(User.email == address)).scalars().first()
    if not user:
        raise SystemExit(f"No such user: {address}")
    password = _read_password()
    _validate_password(password)
    user.password_hash = hash_password(password)
    user.failed_login_count = 0
    user.locked_until = None
    _audit(db, "CLI_PASSWORD_SET", address, {})
    db.commit()
    print(f"Password updated for {address}. No session was created.")
    return 0


def cmd_reset_mfa(db, email: str) -> int:
    address = email.lower().strip()
    user = db.execute(select(User).where(User.email == address)).scalars().first()
    if not user:
        raise SystemExit(f"No such user: {address}")
    user.mfa_secret = None
    user.mfa_confirmed_at = None
    user.mfa_recovery_codes = []
    user.mfa_last_used_step = None
    user.failed_login_count = 0
    user.locked_until = None
    _audit(db, "CLI_MFA_RESET", address, {"role": user.role})
    db.commit()
    # Tell the account holder. A reset nobody is told about looks like a compromise, and the
    # person locked out has no other signal that an operator acted. Delivery failure is logged
    # by the service and never undoes the reset.
    try:
        from app.services import notifications as notify

        notify.security_notice(db, user, "MFA_RESET",
                               change="Two-factor authentication was reset by an operator")
    except Exception as exc:                                     # noqa: BLE001
        print(f"(the security notification was not recorded: {type(exc).__name__})")
    print(f"Two-factor enrolment cleared for {address}. "
          f"The account will be asked to enrol a new authenticator at its next sign-in.")
    return 0


def cmd_revoke_published_secrets(db, dry_run: bool) -> int:
    rows = db.execute(
        select(User).where(User.mfa_secret.in_(PUBLISHED_SECRETS))
    ).scalars().all()
    if not rows:
        print("No account is enrolled with a published development secret.")
        return 0
    for user in rows:
        print(f"{'[dry-run] ' if dry_run else ''}revoking published TOTP secret: {user.email}")
        if not dry_run:
            user.mfa_secret = None
            user.mfa_confirmed_at = None
            user.mfa_recovery_codes = []
            user.mfa_last_used_step = None
            _audit(db, "MFA_PUBLISHED_SECRET_REVOKED", user.email, {})
    if dry_run:
        print("Nothing was written. Re-run without --dry-run to apply.")
        return 0
    db.commit()
    print("Done. These accounts will enrol a new authenticator at their next sign-in.")
    return 0


def cmd_set_contact(db, phone, whatsapp, email, facebook) -> int:
    """Seed the four public contact channels without a browser session."""
    from app.core import validation
    from app.models.content import SiteSetting

    row = db.get(SiteSetting, 1)
    if row is None:
        raise SystemExit("site_settings row 1 does not exist; run the migrations first.")
    updated: list[str] = []
    try:
        if phone:
            number = validation.normalize_phone(phone, field="Primary phone number")
            row.phones = [number] + [p for p in (row.phones or []) if p != number]
            updated.append("phones")
        if whatsapp:
            row.whatsapp_number = validation.normalize_phone(whatsapp, field="WhatsApp number")
            updated.append("whatsapp_number")
        if email:
            address = validation.normalize_email(email, field="Public contact email")
            row.emails = [address] + [e for e in (row.emails or []) if e != address]
            updated.append("emails")
        if facebook:
            row.facebook_page_url = validation.normalize_facebook_url(facebook)
            updated.append("facebook_page_url")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    _audit(db, "CLI_CONTACT_SETTINGS", None, {"fields": updated})
    db.commit()
    print(f"Updated: {', '.join(updated) or 'nothing'}. The public site reads these immediately.")
    return 0


def cmd_email_status() -> int:
    """Show whether outbound email can be delivered, and why not. Prints no secret."""
    from app.services import email

    state = email.status()
    print(f"configured : {'yes' if state['configured'] else 'no'}")
    if not state["configured"]:
        print(f"reason     : {state['reason']}")
    print(f"host       : {state['host'] or '-'}")
    print(f"port       : {state['port']} (valid={state['port_valid']})")
    print(f"security   : {state['security']} (certificates verified)")
    print(f"username   : {state['username'] or '-'}")
    print(f"from       : {state['from_address'] or '-'} ({state['from_name']})")
    print(f"timeout    : {state['timeout_seconds']}s, retries: {state['max_retries']}")
    if not state["configured"]:
        return 1
    result = email.verify_connection()
    print(f"connection : {'ok' if result.ok else 'FAILED'} - "
          f"{result.detail.get('message') or result.error}")
    return 0 if result.ok else 1


def cmd_send_test_email(address: str) -> int:
    """Send one real message, from the terminal, so live delivery can be proved.

    Not reachable over HTTP: proving delivery is an operator action, taken deliberately by
    someone with a shell, addressed to that person's own mailbox.
    """
    from app.services import email, email_templates

    if not email.configured():
        print(f"Email is not configured: {email.status()['reason']}")
        return 1
    rendered = email_templates.test_message(to=address, triggered_by="cli:operator")
    result = email.send(address, rendered.subject, rendered.text, rendered.html)
    if result.ok:
        print(f"Accepted by the server for {email.mask_email(address)} on attempt "
              f"{result.attempts} (message-id {result.message_id}).")
        print("Check that mailbox (and its spam folder) to confirm the last mile.")
        return 0
    print(f"Delivery failed after {result.attempts} attempt(s): {result.error}")
    return 1


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Scholara staff-account operator tooling")
    parser.add_argument("--list", action="store_true", help="show staff accounts and MFA state")
    parser.add_argument("--create-admin", metavar="EMAIL", help="create an ADMIN (password on stdin)")
    parser.add_argument("--name", default="", help="full name for --create-admin")
    parser.add_argument("--set-password", metavar="EMAIL", help="rotate a password (read from stdin)")
    parser.add_argument("--reset-mfa", metavar="EMAIL", help="clear MFA so the account re-enrols")
    parser.add_argument("--revoke-published-secrets", action="store_true",
                        help="clear MFA on accounts using the repository's development secrets")
    parser.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    parser.add_argument("--contact", nargs="*", metavar="VALUE",
                        help="set contact channels: --contact PHONE WHATSAPP EMAIL FACEBOOK (any subset, in order)")
    parser.add_argument("--email-status", action="store_true",
                        help="show the SMTP configuration and test the connection")
    parser.add_argument("--send-test-email", metavar="ADDRESS",
                        help="send one real test message through the configured SMTP account")
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        if args.list:
            return cmd_list(db)
        if args.create_admin:
            return cmd_create_admin(db, args.create_admin, args.name)
        if args.set_password:
            return cmd_set_password(db, args.set_password)
        if args.reset_mfa:
            return cmd_reset_mfa(db, args.reset_mfa)
        if args.revoke_published_secrets:
            return cmd_revoke_published_secrets(db, args.dry_run)
        if args.email_status:
            return cmd_email_status()
        if args.send_test_email:
            return cmd_send_test_email(args.send_test_email)
        if args.contact is not None:
            values = list(args.contact) + [None] * (4 - len(args.contact))
            return cmd_set_contact(db, *values[:4])
        parser.print_help()
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
