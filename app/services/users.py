"""Users and access: request access -> administrator approves or denies -> sign in.

Local, dependency-free auth for a prototype: passwords are salted PBKDF2-HMAC-SHA256 hashes, accounts lock after
repeated failures, and every decision is written to the audit trail. It is NOT a replacement for company SSO
(see docs/design.md): it exists so that roles are enforced and administrator decisions are traceable.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

from app.config import db_path, get_config
from app.data import db
from app.observability.logging import log_event
from app.services import audit

ROLES = ("supervisor", "manager", "admin")
USERNAME_RE = re.compile(r"^[a-z][a-z0-9._-]{2,30}$")
_ITERATIONS = 200_000


class AccessError(Exception):
    """Sign-in or user-management failure with a message that is safe to show to the user."""


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _ITERATIONS).hex()


def _new_secret(password: str) -> tuple[str, str]:
    salt = secrets.token_hex(16)
    return salt, _hash(password, salt)


def _check_password_rules(password: str) -> None:
    n = get_config()["auth"]["min_password_length"]
    if len(password) < n:
        raise ValueError(f"Password must be at least {n} characters.")
    if password.lower() == password or not any(ch.isdigit() for ch in password):
        raise ValueError("Password needs an upper-case letter and a digit.")


def _public(u: dict | None) -> dict | None:
    if not u:
        return None
    return {k: v for k, v in u.items() if k not in ("pw_hash", "pw_salt")}


def get_user(username: str) -> dict | None:
    r = db.rows("SELECT * FROM users WHERE username=?", (username.strip().lower(),))
    return _public(r[0]) if r else None


def list_users(status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM users" + (" WHERE status=?" if status else "") + " ORDER BY created_at DESC"
    return [_public(r) for r in db.rows(sql, (status,) if status else ())]


def active_admins() -> int:
    return db.rows("SELECT COUNT(*) n FROM users WHERE role='admin' AND status='approved'")[0]["n"]


# ---------------------------------------------------------------- requests and sign-in
def request_access(username: str, display_name: str, password: str, requested_role: str = "supervisor",
                   email: str = "") -> dict:
    username = username.strip().lower()
    if not USERNAME_RE.match(username):
        raise ValueError("Username: 3-31 characters, start with a letter, use a-z 0-9 . _ -")
    if requested_role not in ROLES[:2]:           # nobody can request administrator rights for themselves
        raise ValueError("You can request Floor Supervisor or Plant Manager access.")
    if len(display_name.strip()) < 2:
        raise ValueError("Please enter your name.")
    _check_password_rules(password)
    salt, h = _new_secret(password)
    with db.tx() as c:
        if c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            raise ValueError("That username is taken.")
        c.execute("""INSERT INTO users (username, display_name, email, role, requested_role, status, pw_salt, pw_hash,
                     created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                  (username, display_name.strip()[:80], email.strip()[:120], "supervisor", requested_role, "pending",
                   salt, h, db.now_iso()))
        audit.record(username, "applicant", "access_requested", username, "pending",
                     details={"requested_role": requested_role}, conn=c)
    log_event("access", username=username, action="requested", requested_role=requested_role)
    return get_user(username)


def authenticate(username: str, password: str) -> dict:
    """Return the user on success; raise AccessError with a safe message otherwise."""
    username = username.strip().lower()
    cfg = get_config()["auth"]
    rows = db.rows("SELECT * FROM users WHERE username=?", (username,))
    generic = AccessError("Wrong username or password.")
    if not rows:
        _hash(password, "00" * 16)               # similar cost either way
        log_event("access", username=username, action="login_failed", reason="unknown_user")
        raise generic
    u = rows[0]
    now = datetime.now(timezone.utc)
    if u["locked_until"] and datetime.fromisoformat(u["locked_until"]) > now:
        raise AccessError(f"Too many failed attempts. Try again after {u['locked_until'][11:16]} UTC, or ask an administrator.")
    ok = hmac.compare_digest(_hash(password, u["pw_salt"]), u["pw_hash"])
    if not ok:
        fails = u["failed_attempts"] + 1
        lock = (now + timedelta(minutes=cfg["lockout_minutes"])).replace(microsecond=0).isoformat() \
            if fails >= cfg["max_failed_attempts"] else None
        with db.tx() as c:
            c.execute("UPDATE users SET failed_attempts=?, locked_until=? WHERE username=?",
                      (0 if lock else fails, lock, username))
            audit.record(username, u["role"], "login_failed", username, "denied", details={"attempt": fails}, conn=c)
        log_event("access", username=username, action="login_failed", attempts=fails, locked=bool(lock))
        raise generic
    if u["status"] != "approved":
        msgs = {"pending": "Your access request is waiting for administrator approval.",
                "denied": "Your access request was denied. Contact your administrator.",
                "disabled": "This account is disabled. Contact your administrator."}
        raise AccessError(msgs[u["status"]])
    with db.tx() as c:
        c.execute("UPDATE users SET last_login=?, failed_attempts=0, locked_until=NULL WHERE username=?",
                  (db.now_iso(), username))
        audit.record(username, u["role"], "login", username, "ok", conn=c)
    log_event("access", username=username, action="login_ok", role=u["role"])
    return get_user(username)


# ---------------------------------------------------------------- administrator actions
def _require_admin(actor: dict | str, actor_role: str | None = None) -> tuple[str, str]:
    name, role = (actor["username"], actor["role"]) if isinstance(actor, dict) else (actor, actor_role)
    if role != "admin":
        raise AccessError("Only an administrator can do this.")
    return name, role


def decide_access(username: str, decision: str, admin: dict | str, role: str = "supervisor", note: str = "",
                  admin_role: str | None = None) -> dict:
    """Approve (with a role) or deny a pending request."""
    a_name, a_role = _require_admin(admin, admin_role)
    if decision not in ("approved", "denied"):
        raise ValueError("decision must be approved or denied")
    if role not in ROLES:
        raise ValueError("Unknown role")
    with db.tx() as c:
        row = c.execute("SELECT status FROM users WHERE username=?", (username,)).fetchone()
        if not row:
            raise ValueError(f"Unknown user {username}")
        if row["status"] != "pending":
            raise ValueError(f"{username} is {row['status']}; only pending requests can be decided.")
        c.execute("""UPDATE users SET status=?, role=?, decided_by=?, decided_at=?, decision_note=? WHERE username=?""",
                  (decision, role if decision == "approved" else "supervisor", a_name, db.now_iso(), note[:300], username))
        audit.record(a_name, a_role, "access_" + decision, username, decision, details={"role": role, "note": note[:300]}, conn=c)
    log_event("access", username=username, action=decision, by=a_name)
    return get_user(username)


def set_role(username: str, role: str, admin: dict | str, admin_role: str | None = None, note: str = "") -> dict:
    a_name, a_role = _require_admin(admin, admin_role)
    if role not in ROLES:
        raise ValueError("Unknown role")
    u = get_user(username)
    if not u:
        raise ValueError(f"Unknown user {username}")
    if u["role"] == "admin" and role != "admin" and u["status"] == "approved" and active_admins() <= 1:
        raise AccessError("This is the last administrator. Create or approve another administrator first.")
    with db.tx() as c:
        c.execute("UPDATE users SET role=? WHERE username=?", (role, username))
        audit.record(a_name, a_role, "role_changed", username, role, details={"from": u["role"], "note": note[:300]}, conn=c)
    return get_user(username)


def set_status(username: str, status: str, admin: dict | str, admin_role: str | None = None, note: str = "") -> dict:
    """Disable or re-enable an approved account."""
    a_name, a_role = _require_admin(admin, admin_role)
    if status not in ("approved", "disabled"):
        raise ValueError("status must be approved or disabled")
    u = get_user(username)
    if not u or u["status"] not in ("approved", "disabled"):
        raise ValueError("Only approved or disabled accounts can be changed here.")
    if status == "disabled" and u["role"] == "admin" and active_admins() <= 1:
        raise AccessError("This is the last administrator and cannot be disabled.")
    if status == "disabled" and username == a_name:
        raise AccessError("You cannot disable your own account.")
    with db.tx() as c:
        c.execute("UPDATE users SET status=?, failed_attempts=0, locked_until=NULL WHERE username=?", (status, username))
        audit.record(a_name, a_role, "account_" + ("disabled" if status == "disabled" else "enabled"), username, status,
                     details={"note": note[:300]}, conn=c)
    return get_user(username)


def reset_password(username: str, new_password: str, admin: dict | str, admin_role: str | None = None) -> None:
    a_name, a_role = _require_admin(admin, admin_role)
    _check_password_rules(new_password)
    if not get_user(username):
        raise ValueError(f"Unknown user {username}")
    salt, h = _new_secret(new_password)
    with db.tx() as c:
        c.execute("UPDATE users SET pw_salt=?, pw_hash=?, failed_attempts=0, locked_until=NULL WHERE username=?",
                  (salt, h, username))
        audit.record(a_name, a_role, "password_reset", username, "ok", conn=c)


def access_activity(limit: int = 100) -> list[dict]:
    return db.rows("""SELECT ts, actor, role, action, target, decision, details FROM audit_log
                      WHERE action IN ('access_requested','access_approved','access_denied','role_changed',
                                       'account_disabled','account_enabled','password_reset','login','login_failed')
                      ORDER BY audit_id DESC LIMIT ?""", (limit,))


# ---------------------------------------------------------------- first-run accounts
def ensure_seed_users() -> dict[str, str]:
    """Create the first demo accounts when the table is empty. Returns {username: password} for NEW accounts only.

    Passwords are random per install (or FAST_ADMIN_PASSWORD for the administrator) and written to a file next to the
    database, which is git-ignored. No password is stored in the repository.
    """
    import os
    if db.rows("SELECT 1 FROM users LIMIT 1"):
        return {}
    creds: dict[str, str] = {}
    spec = [("admin", "Plant Administrator", "admin"), ("plant.manager", "Plant Manager", "manager"),
            ("sup.chen", "Chen (Floor Supervisor)", "supervisor")]
    now = db.now_iso()
    with db.tx() as c:
        for username, name, role in spec:
            pw = (os.getenv("FAST_ADMIN_PASSWORD") if role == "admin" else None) or (
                "Mk-" + secrets.token_urlsafe(9) + "7")
            salt, h = _new_secret(pw)
            c.execute("""INSERT INTO users (username, display_name, role, requested_role, status, pw_salt, pw_hash,
                         created_at, decided_by, decided_at, decision_note, is_synthetic)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,1)""",
                      (username, name, role, role, "approved", salt, h, now, "system", now, "seeded demo account"))
            creds[username] = pw
        audit.record("system", "system", "seed_users", "users", "ok", details={"users": list(creds)}, conn=c)
    out = db_path().parent / "initial_credentials.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("FAST demo accounts (synthetic). Change or delete these after first use.\n"
                   + "\n".join(f"{u}  /  {p}" for u, p in creds.items()) + "\n", encoding="utf-8")
    log_event("access", action="seeded_users", file=str(out))
    return creds
