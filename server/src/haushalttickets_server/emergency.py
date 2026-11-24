"""Emergency web UI: a small HTTPS site inside the LXC for when Home Assistant is down.

One local login (password set with ``haushalttickets-server emergency password``) is
bound to one admin. Every change goes through the normal services as that admin via
the channel ``emergency_ui``, so the usual rules and the ticket history apply. The site
runs on its own port (``HAUSHALTTICKETS_EMERGENCY_PORT``, default 8443) with the
certificate from :mod:`tls` and answers only the networks listed in
``HAUSHALTTICKETS_EMERGENCY_CLIENTS``; while that is empty the site is not started.

Pages are plain server-rendered HTML forms without JavaScript.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import hashlib
import hmac
import html
from ipaddress import ip_address
import logging
import secrets
import ssl
import threading
import time
from typing import Annotated, Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
import uvicorn

from . import services, tls
from .config import Settings
from .db import make_engine, make_sessionmaker
from .enums import (
    AUTOMATION_USER_ID,
    CLOSED_STATUSES,
    ApprovalState,
    Role,
    Status,
    TicketType,
    Urgency,
    Via,
)
from .models import Setting, Ticket, User
from .permissions import Actor, PermissionDenied
from .schemas import CommentCreate, TicketCreate
from .services import Invalid, NotFound

_LOGGER = logging.getLogger(__name__)

LOGIN_KEY = "emergency_login"
SESSION_COOKIE = "ht_emergency"
SESSION_SECONDS = 12 * 3600
MAX_FAILURES = 5
LOCK_SECONDS = 15 * 60
MIN_PASSWORD_LENGTH = 10
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}
_SCRYPT_MAXMEM = 64 * 1024 * 1024
# An own certificate gets a ticket this long before it expires (the self-signed one
# renews itself); checked at start and then every CHECK_SECONDS.
REMIND_BEFORE = timedelta(days=14)
REMINDER_KEY = "emergency_cert_reminder"
CHECK_SECONDS = 6 * 3600

STATUS_ORDER = [
    Status.NEW,
    Status.DISCUSSING,
    Status.IN_PROGRESS,
    Status.WAITING_FOR_PARTS,
    Status.ACCEPTANCE,
    Status.DONE,
    Status.REJECTED,
]
_URGENCY_RANK = {"burning": 0, "this_week": 1, "someday": 2}


# Password and login


def hash_password(password: str) -> str:
    """Salted scrypt hash."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, maxmem=_SCRYPT_MAXMEM, dklen=32, **_SCRYPT
    )
    n, r, p = _SCRYPT["n"], _SCRYPT["r"], _SCRYPT["p"]
    return f"scrypt${n}${r}${p}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Check a password against :func:`hash_password` output."""
    try:
        algorithm, n, r, p, salt, digest = stored.split("$")
        if algorithm != "scrypt":
            return False
        expected = bytes.fromhex(digest)
        actual = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=_SCRYPT_MAXMEM,
            dklen=len(expected),
        )
    except ValueError:
        return False
    return hmac.compare_digest(actual, expected)


@dataclass(frozen=True)
class Login:
    """The one emergency login."""

    user_id: str
    password: str
    secret: bytes


def get_login(session: Session) -> Login | None:
    """The configured login, if any."""
    row = session.get(Setting, LOGIN_KEY)
    if row is None or not isinstance(row.value, dict):
        return None
    try:
        return Login(
            user_id=row.value["user_id"],
            password=row.value["password"],
            secret=bytes.fromhex(row.value["secret"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def find_admin(session: Session, name: str | None) -> User:
    """The admin the login belongs to: by id or display name, or the only admin."""
    admins = [
        u
        for u in session.scalars(select(User).where(User.role == Role.ADMIN))
        if u.active
    ]
    if name:
        wanted = name.casefold()
        for user in admins:
            if wanted in (user.ha_user_id.casefold(), user.display_name.casefold()):
                return user
        raise Invalid("admin_required", f"No active admin named {name!r}.")
    if len(admins) != 1:
        names = ", ".join(sorted(u.display_name for u in admins)) or "none"
        raise Invalid("choose_admin", f"Choose the admin with --user ({names}).")
    return admins[0]


def set_password(session: Session, user: User, password: str) -> None:
    """Bind the login to ``user`` with a new password; ends all sessions."""
    if user.role != Role.ADMIN or not user.active:
        raise Invalid("admin_required", "The emergency login must be an active admin.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise Invalid(
            "password_too_short",
            f"Use at least {MIN_PASSWORD_LENGTH} characters.",
        )
    value = {
        "user_id": user.ha_user_id,
        "password": hash_password(password),
        "secret": secrets.token_hex(32),
    }
    row = session.get(Setting, LOGIN_KEY)
    if row is None:
        session.add(Setting(key=LOGIN_KEY, value=value))
    else:
        row.value = value


# Sessions (signed cookie, no server-side state)


def _sign(secret: bytes, data: str) -> str:
    return hmac.new(secret, data.encode(), hashlib.sha256).hexdigest()


def make_session(login: Login, now: float) -> str:
    """Cookie value for a fresh session."""
    payload = f"{login.user_id}|{int(now) + SESSION_SECONDS}|{secrets.token_hex(8)}"
    raw = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    return f"{raw}.{_sign(login.secret, raw)}"


def read_session(login: Login, cookie: str, now: float) -> str | None:
    """Nonce of a valid session cookie, else None."""
    raw, _, signature = cookie.partition(".")
    if not raw or not hmac.compare_digest(signature, _sign(login.secret, raw)):
        return None
    try:
        payload = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()
        user_id, expires, nonce = payload.split("|")
        if user_id != login.user_id or int(expires) < now:
            return None
    except ValueError:
        return None
    return nonce


def csrf_token(login: Login, nonce: str) -> str:
    """Form token bound to the session."""
    return _sign(login.secret, f"csrf|{nonce}")[:32]


class Throttle:
    """Lock the login after too many wrong passwords (one login, one counter)."""

    def __init__(self) -> None:
        """Initialize."""
        self._lock = threading.Lock()
        self._failures: list[float] = []
        self._until = 0.0

    def wait(self, now: float) -> int:
        """Seconds until the next attempt is allowed."""
        with self._lock:
            return max(0, int(self._until - now + 0.999))

    def failed(self, now: float) -> None:
        """Count a wrong password."""
        with self._lock:
            recent = [t for t in self._failures if now - t < LOCK_SECONDS]
            recent.append(now)
            if len(recent) >= MAX_FAILURES:
                self._until = now + LOCK_SECONDS
                recent = []
            self._failures = recent

    def succeeded(self) -> None:
        """Reset after a correct password."""
        with self._lock:
            self._failures = []


# Texts

TEXTS: dict[str, dict[str, Any]] = {
    "de": {
        "lang": "de",
        "app": "Haushalttickets · Notfall",
        "login": "Anmelden",
        "password": "Passwort",
        "sign_out": "Abmelden",
        "wrong_password": "Falsches Passwort.",
        "locked": "Zu viele Fehlversuche. Wieder möglich in {minutes} Min.",
        "not_set_up": "Noch nicht eingerichtet. Im LXC ausführen: "
        "haushalttickets-ctl emergency-setup",
        "tickets": "Tickets",
        "new": "Neues Ticket",
        "certificate": "Zertifikat",
        "search": "Suchen",
        "open": "Offen",
        "closed": "Zuletzt geschlossen",
        "none": "Keine Tickets.",
        "type": "Typ",
        "status": "Status",
        "urgency": "Dringlichkeit",
        "hearts": "Herzen",
        "due": "Fällig",
        "due_on": "fällig {date}",
        "requested_by": "Gewünscht von",
        "assignees": "Zuständig",
        "created": "Angelegt",
        "title": "Titel",
        "description": "Beschreibung",
        "checklist": "Checkliste",
        "attachments": "Anhänge",
        "comments": "Kommentare",
        "history": "Verlauf",
        "comment": "Kommentar",
        "add_comment": "Kommentieren",
        "change_status": "Status ändern",
        "create": "Anlegen",
        "back": "Zurück zur Liste",
        "ok_created": "Ticket angelegt.",
        "ok_comment": "Kommentar gespeichert.",
        "ok_status": "Status geändert.",
        "ok_uploaded": "Zertifikat übernommen, gilt ab der nächsten Verbindung.",
        "ok_removed": "Eigenes Zertifikat entfernt, es gilt wieder das selbst "
        "signierte.",
        "approval_pending": "Freigabe ausstehend – freigeben oder ablehnen nur in "
        "der Karte.",
        "recurring": "Wiederkehrend",
        "price_estimated": "Preis geschätzt",
        "price_actual": "Preis tatsächlich",
        "vendor": "Händler",
        "product_url": "Link",
        "warranty_until": "Garantie bis",
        "contractor": "Handwerker",
        "appointment": "Termin",
        "cost_estimated": "Kosten geschätzt",
        "cost_actual": "Kosten tatsächlich",
        "warranty_case": "Garantiefall",
        "ha_targets": "Betroffen in HA",
        "yes": "ja",
        "h_created": "angelegt",
        "h_override": "Admin-Abnahme",
        "via": {
            "mcp": "über KI",
            "nightly_run": "Nachtlauf",
            "emergency_ui": "Notfall",
            "automation": "Automation",
            "recurrence": "Wiederholung",
        },
        "statuses": {
            "new": "Offen",
            "discussing": "Wird beredet",
            "in_progress": "In Arbeit",
            "waiting_for_parts": "Wartet auf Teile",
            "acceptance": "Abnahme",
            "done": "Erledigt",
            "rejected": "Träum weiter",
        },
        "urgencies": {
            "someday": "Irgendwann",
            "this_week": "Diese Woche",
            "burning": "Brennt",
        },
        "types": {
            "repair": "Reparatur",
            "purchase": "Anschaffung",
            "home_assistant": "Home Assistant",
        },
        "cert_active": "Aktives Zertifikat",
        "cert_custom": "Eigenes Zertifikat",
        "cert_self": "Selbst signiert (vom Server erzeugt)",
        "subject": "Inhaber",
        "issuer": "Aussteller",
        "valid": "Gültig",
        "names": "Namen (SAN)",
        "fingerprint": "Fingerabdruck SHA-256",
        "upload_title": "Eigenes Zertifikat hochladen",
        "upload_hint": "Entweder eine PKCS#12-Datei (.p12/.pfx) oder Zertifikat "
        "(PEM, gern mit Kette) und privaten Schlüssel (PEM). Das Passwort gilt für "
        "die PKCS#12-Datei bzw. einen verschlüsselten Schlüssel.",
        "pkcs12": "PKCS#12-Datei",
        "or": "oder",
        "cert_file": "Zertifikat",
        "key_file": "Privater Schlüssel",
        "file_password": "Passwort (falls nötig)",
        "upload": "Hochladen",
        "remove_custom": "Eigenes Zertifikat entfernen",
        "warnings": {
            "expired": "Abgelaufen.",
            "expires_soon": "Läuft in weniger als 30 Tagen ab.",
            "validity_too_long": "Gilt länger als 825 Tage – iPhone/macOS lehnen "
            "es ab.",
            "no_san": "Keine Namen/Adressen (SAN) – Browser lehnen es ab.",
            "no_server_auth": "Nicht für Webserver freigegeben (EKU serverAuth).",
        },
        "errors": {
            "csrf": "Sitzung abgelaufen, bitte Seite neu laden.",
            "not_found": "Nicht gefunden.",
            "invalid_status": "Unbekannter Status.",
            "invalid_input": "Bitte Eingaben prüfen: {detail}",
            "files_missing": "Bitte eine PKCS#12-Datei oder Zertifikat und "
            "Schlüssel wählen.",
            "wrong_password": "Falsches Passwort für die Datei.",
            "key_mismatch": "Der Schlüssel passt zu keinem Zertifikat in der Datei.",
            "no_certificate": "Kein Zertifikat in der Datei gefunden.",
            "expired": "Das Zertifikat ist abgelaufen.",
            "too_large": "Datei zu groß.",
            "unreadable": "Datei nicht lesbar: {detail}",
            "unusable": "Zertifikat nicht nutzbar: {detail}",
            "openssl_missing": "openssl fehlt im LXC.",
            "openssl_failed": "openssl: {detail}",
        },
    },
    "en": {
        "lang": "en",
        "app": "Haushalttickets · Emergency",
        "login": "Sign in",
        "password": "Password",
        "sign_out": "Sign out",
        "wrong_password": "Wrong password.",
        "locked": "Too many failed attempts. Try again in {minutes} min.",
        "not_set_up": "Not set up yet. Run in the LXC: "
        "haushalttickets-ctl emergency-setup",
        "tickets": "Tickets",
        "new": "New ticket",
        "certificate": "Certificate",
        "search": "Search",
        "open": "Open",
        "closed": "Recently closed",
        "none": "No tickets.",
        "type": "Type",
        "status": "Status",
        "urgency": "Urgency",
        "hearts": "Hearts",
        "due": "Due",
        "due_on": "due {date}",
        "requested_by": "Requested by",
        "assignees": "Assignees",
        "created": "Created",
        "title": "Title",
        "description": "Description",
        "checklist": "Checklist",
        "attachments": "Attachments",
        "comments": "Comments",
        "history": "History",
        "comment": "Comment",
        "add_comment": "Add comment",
        "change_status": "Change status",
        "create": "Create",
        "back": "Back to the list",
        "ok_created": "Ticket created.",
        "ok_comment": "Comment saved.",
        "ok_status": "Status changed.",
        "ok_uploaded": "Certificate installed; it is used from the next connection.",
        "ok_removed": "Own certificate removed; the self-signed one is used again.",
        "approval_pending": "Approval pending – approve or decline in the card only.",
        "recurring": "Recurring",
        "price_estimated": "Estimated price",
        "price_actual": "Actual price",
        "vendor": "Vendor",
        "product_url": "Link",
        "warranty_until": "Warranty until",
        "contractor": "Contractor",
        "appointment": "Appointment",
        "cost_estimated": "Estimated cost",
        "cost_actual": "Actual cost",
        "warranty_case": "Warranty case",
        "ha_targets": "Affected in HA",
        "yes": "yes",
        "h_created": "created",
        "h_override": "admin acceptance",
        "via": {
            "mcp": "via AI",
            "nightly_run": "nightly run",
            "emergency_ui": "emergency",
            "automation": "automation",
            "recurrence": "recurrence",
        },
        "statuses": {
            "new": "Open",
            "discussing": "Discussing",
            "in_progress": "In progress",
            "waiting_for_parts": "Waiting for parts",
            "acceptance": "Review",
            "done": "Done",
            "rejected": "Dream on",
        },
        "urgencies": {
            "someday": "Someday",
            "this_week": "This week",
            "burning": "Urgent",
        },
        "types": {
            "repair": "Repair",
            "purchase": "Purchase",
            "home_assistant": "Home Assistant",
        },
        "cert_active": "Active certificate",
        "cert_custom": "Own certificate",
        "cert_self": "Self-signed (created by the server)",
        "subject": "Subject",
        "issuer": "Issuer",
        "valid": "Valid",
        "names": "Names (SAN)",
        "fingerprint": "SHA-256 fingerprint",
        "upload_title": "Upload an own certificate",
        "upload_hint": "Either a PKCS#12 file (.p12/.pfx) or a certificate (PEM, "
        "chain welcome) and its private key (PEM). The password is for the PKCS#12 "
        "file or an encrypted key.",
        "pkcs12": "PKCS#12 file",
        "or": "or",
        "cert_file": "Certificate",
        "key_file": "Private key",
        "file_password": "Password (if needed)",
        "upload": "Upload",
        "remove_custom": "Remove own certificate",
        "warnings": {
            "expired": "Expired.",
            "expires_soon": "Expires in less than 30 days.",
            "validity_too_long": "Valid for more than 825 days – iPhone/macOS "
            "reject it.",
            "no_san": "No names/addresses (SAN) – browsers reject it.",
            "no_server_auth": "Not allowed for web servers (EKU serverAuth).",
        },
        "errors": {
            "csrf": "Session expired, please reload the page.",
            "not_found": "Not found.",
            "invalid_status": "Unknown status.",
            "invalid_input": "Please check the input: {detail}",
            "files_missing": "Choose a PKCS#12 file or a certificate and key.",
            "wrong_password": "Wrong password for the file.",
            "key_mismatch": "The key matches no certificate in the file.",
            "no_certificate": "No certificate found in the file.",
            "expired": "The certificate has expired.",
            "too_large": "File too large.",
            "unreadable": "File not readable: {detail}",
            "unusable": "Certificate not usable: {detail}",
            "openssl_missing": "openssl is missing in the LXC.",
            "openssl_failed": "openssl: {detail}",
        },
    },
}


def _texts(request: Request) -> dict[str, Any]:
    header = request.headers.get("accept-language", "").strip().lower()
    return TEXTS["de" if header.startswith("de") else "en"]


def e(value: Any) -> str:
    """Escape for HTML text and attribute values."""
    return html.escape("" if value is None else str(value), quote=True)


_CSS = """
:root{color-scheme:light dark;--fg:#1d1d1f;--bg:#f5f5f7;--card:#fff;--line:#d2d2d7;
--accent:#0a66c2;--warn:#b45309;--bad:#c62828;--ok:#2e7d32}
@media (prefers-color-scheme:dark){:root{--fg:#f5f5f7;--bg:#1c1c1e;--card:#2c2c2e;
--line:#3a3a3c;--accent:#64a8ff;--warn:#f0b35a;--bad:#ff6b6b;--ok:#7bd88f}}
*{box-sizing:border-box}body{margin:0;font:16px/1.45 -apple-system,system-ui,
sans-serif;color:var(--fg);background:var(--bg)}
header{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;
padding:10px 14px;background:var(--card);border-bottom:1px solid var(--line)}
header b{margin-right:auto}nav{display:flex;flex-wrap:wrap;gap:12px;align-items:center}
nav form{margin:0}a{color:var(--accent)}main{max-width:820px;margin:0 auto;padding:14px}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:12px 14px;margin:0 0 14px}h1{font-size:21px;margin:4px 0 12px}
h2{font-size:16px;margin:0 0 8px}ul.t{list-style:none;margin:0;padding:0}
ul.t li{padding:8px 0;border-top:1px solid var(--line)}ul.t li:first-child{border:0}
ul.t small,.muted{display:block;opacity:.75;font-size:13px}.burning a{color:var(--bad);
font-weight:600}dl{display:grid;grid-template-columns:max-content 1fr;gap:4px 14px;
margin:0}dt{opacity:.75}dd{margin:0;overflow-wrap:anywhere}
.pre{white-space:pre-wrap;overflow-wrap:anywhere}
input,select,textarea,button{font:inherit;padding:8px 10px;border-radius:8px;
border:1px solid var(--line);background:var(--bg);color:var(--fg);max-width:100%}
textarea{width:100%;min-height:90px}input[type=text],input[type=password],
input[type=search]{width:100%}label{display:block;margin:10px 0 4px;font-size:14px}
button{background:var(--accent);border-color:var(--accent);color:#fff;cursor:pointer}
nav button{background:none;color:var(--accent);border:0;padding:0}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.row input,.row select{flex:1;width:auto;min-width:0}
dl.stack{grid-template-columns:1fr;gap:0}dl.stack dd{margin-bottom:8px}
.note{padding:8px 12px;border-radius:8px;margin:0 0 12px;border:1px solid}
.note.ok{border-color:var(--ok)}.note.bad{border-color:var(--bad)}
.note.warn{border-color:var(--warn)}
.thumbs{display:flex;flex-wrap:wrap;gap:8px}.thumbs img{width:110px;height:110px;
object-fit:cover;border-radius:8px;border:1px solid var(--line)}
details summary{cursor:pointer}.c{border-top:1px solid var(--line);padding:8px 0}
.c:first-of-type{border:0}code{overflow-wrap:anywhere}
"""


@dataclass
class Ctx:
    """A signed-in request."""

    session: Session
    actor: Actor
    login: Login
    csrf: str
    t: dict[str, Any]
    tz: ZoneInfo


class _LoginRequired(Exception):
    pass


def _page(
    t: dict[str, Any],
    title: str,
    body: str,
    *,
    csrf: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    nav = ""
    if csrf:
        nav = (
            f'<nav><a href="/">{e(t["tickets"])}</a><a href="/new">{e(t["new"])}</a>'
            f'<a href="/certificate">{e(t["certificate"])}</a>'
            f'<form method="post" action="/logout"><input type="hidden" name="csrf" '
            f'value="{e(csrf)}"><button>{e(t["sign_out"])}</button></form></nav>'
        )
    document = (
        f'<!DOCTYPE html><html lang="{t["lang"]}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex">'
        f"<title>{e(title)} · {e(t['app'])}</title><style>{_CSS}</style></head>"
        f"<body><header><b>{e(t['app'])}</b>{nav}</header><main>{body}</main>"
        "</body></html>"
    )
    return HTMLResponse(document, status_code=status_code)


def _note(kind: str, text: str) -> str:
    return f'<p class="note {kind}">{e(text)}</p>' if text else ""


def _error_text(t: dict[str, Any], code: str, detail: str = "") -> str:
    template = t["errors"].get(code)
    if template is None:
        return detail or code
    return template.format(detail=detail)


def _hidden_csrf(ctx: Ctx) -> str:
    return f'<input type="hidden" name="csrf" value="{e(ctx.csrf)}">'


def _date(t: dict[str, Any], value: date | None) -> str:
    if value is None:
        return ""
    return value.strftime("%d.%m.%Y") if t["lang"] == "de" else value.isoformat()


def _datetime(ctx: Ctx, value: datetime | None) -> str:
    if value is None:
        return ""
    local = value.astimezone(ctx.tz)
    return local.strftime(
        "%d.%m.%Y %H:%M" if ctx.t["lang"] == "de" else "%Y-%m-%d %H:%M"
    )


def _money(t: dict[str, Any], value: Decimal | None) -> str:
    if value is None:
        return ""
    text = f"{value:,.2f}"
    if t["lang"] == "de":
        text = text.replace(",", "\0").replace(".", ",").replace("\0", ".")
    return f"{text} €"


def _names(session: Session) -> dict[str, str]:
    return {u.ha_user_id: u.display_name for u in session.scalars(select(User))}


def _who(names: dict[str, str], user_id: str | None) -> str:
    return names.get(user_id or "", user_id or "–")


# Application


def db(request: Request) -> Iterator[Session]:
    """Database session; handlers commit explicitly."""
    session: Session = request.app.state.sessionmaker()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


DbDep = Annotated[Session, Depends(db)]


def signed_in(request: Request, session: DbDep) -> Ctx:
    """The signed-in admin, else back to the login page."""
    login = get_login(session)
    cookie = request.cookies.get(SESSION_COOKIE)
    nonce = (
        read_session(login, cookie, request.app.state.clock())
        if login and cookie
        else None
    )
    if login is None or nonce is None:
        raise _LoginRequired
    user = session.get(User, login.user_id)
    if user is None or not user.active:
        raise _LoginRequired
    return Ctx(
        session=session,
        actor=Actor(user=user, via=Via.EMERGENCY_UI),
        login=login,
        csrf=csrf_token(login, nonce),
        t=_texts(request),
        tz=request.app.state.tz,
    )


CtxDep = Annotated[Ctx, Depends(signed_in)]
Csrf = Annotated[str, Form()]


def create_emergency_app(
    settings: Settings, factory: sessionmaker[Session] | None = None
) -> FastAPI:
    """The emergency site (without TLS; :func:`make_server` adds it)."""
    app = FastAPI(
        title="Haushalttickets emergency UI",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.sessionmaker = factory or make_sessionmaker(
        make_engine(settings.database_url)
    )
    app.state.throttle = Throttle()
    app.state.clock = time.time
    app.state.reload_certificate = None
    app.state.tz = ZoneInfo(settings.timezone)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        """Allowed networks only, same-origin forms, strict headers."""
        try:
            client = ip_address(request.client.host if request.client else "")
        except ValueError:
            client = None
        networks = settings.emergency_networks
        if client is None or not any(client in net for net in networks):
            _LOGGER.warning("Emergency UI: %s is not in the allowed networks", client)
            return PlainTextResponse("Forbidden", status_code=403)
        if request.method == "POST":
            # Browsers send "null" for some same-origin forms; forms carry a token
            # and the session cookie is SameSite=Strict, so only refuse a real
            # foreign origin here.
            origin = request.headers.get("origin")
            if (
                origin
                and origin != "null"
                and urlsplit(origin).netloc != request.headers.get("host")
            ):
                _LOGGER.warning("Emergency UI: form from foreign origin %s", origin)
                return PlainTextResponse("Forbidden", status_code=403)
        response: Response = await call_next(request)
        # No HSTS: it would stop browsers from letting you past a self-signed
        # certificate.
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
            "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        # "same-origin", not "no-referrer": with the latter browsers send forms with
        # "Origin: null".
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(_LoginRequired)
    async def login_required(request: Request, exc: _LoginRequired) -> Response:
        return RedirectResponse("/login", status_code=303)

    @app.exception_handler(NotFound)
    async def not_found(request: Request, exc: NotFound) -> Response:
        t = _texts(request)
        return _page(
            t,
            t["errors"]["not_found"],
            _note("bad", t["errors"]["not_found"]),
            status_code=404,
        )

    def check_csrf(ctx: Ctx, token: str) -> HTMLResponse | None:
        if hmac.compare_digest(token.encode(), ctx.csrf.encode()):
            return None
        return _page(
            ctx.t,
            ctx.t["app"],
            _note("bad", ctx.t["errors"]["csrf"]),
            csrf=ctx.csrf,
            status_code=403,
        )

    # Login

    def login_page(
        t: dict[str, Any], session: Session, message: str = "", status_code: int = 200
    ) -> HTMLResponse:
        if get_login(session) is None:
            return _page(t, t["login"], _note("warn", t["not_set_up"]))
        body = (
            f"<section><h1>{e(t['login'])}</h1>{_note('bad', message)}"
            '<form method="post" action="/login">'
            f'<label for="pw">{e(t["password"])}</label>'
            '<input id="pw" type="password" name="password" required '
            'autocomplete="current-password" autofocus>'
            f"<p><button>{e(t['login'])}</button></p></form></section>"
        )
        return _page(t, t["login"], body, status_code=status_code)

    @app.get("/login", response_class=HTMLResponse)
    def show_login(request: Request, session: DbDep) -> HTMLResponse:
        return login_page(_texts(request), session)

    @app.post("/login", response_class=HTMLResponse)
    def do_login(
        request: Request, session: DbDep, password: Annotated[str, Form()] = ""
    ) -> Response:
        t = _texts(request)
        throttle: Throttle = request.app.state.throttle
        now = request.app.state.clock()
        login = get_login(session)
        if login is None:
            return login_page(t, session)
        if wait := throttle.wait(now):
            minutes = (wait + 59) // 60
            return login_page(t, session, t["locked"].format(minutes=minutes), 429)
        if not verify_password(password, login.password):
            throttle.failed(now)
            host = request.client.host if request.client else "?"
            _LOGGER.warning("Emergency UI: wrong password from %s", host)
            return login_page(t, session, t["wrong_password"], 401)
        throttle.succeeded()
        _LOGGER.info("Emergency UI: signed in from %s", request.client.host)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            make_session(login, now),
            max_age=SESSION_SECONDS,
            secure=True,
            httponly=True,
            samesite="strict",
        )
        return response

    @app.post("/logout")
    def logout(ctx: CtxDep, csrf: Csrf = "") -> Response:
        if denied := check_csrf(ctx, csrf):
            return denied
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(
            SESSION_COOKIE, secure=True, httponly=True, samesite="strict"
        )
        return response

    # Tickets

    def ticket_line(ctx: Ctx, ticket: Ticket, names: dict[str, str]) -> str:
        t = ctx.t
        meta = [t["types"].get(ticket.type, ticket.type)]
        if ticket.status in (Status.WAITING_FOR_PARTS,):
            meta.append(t["statuses"][ticket.status])
        if ticket.urgency != "someday":
            meta.append(t["urgencies"].get(ticket.urgency, ticket.urgency))
        if ticket.due_date:
            meta.append(t["due_on"].format(date=_date(t, ticket.due_date)))
        if ticket.assignee_ids:
            meta.append(", ".join(_who(names, u) for u in ticket.assignee_ids))
        if ticket.series_id:
            meta.append(t["recurring"])
        burning = " class='burning'" if ticket.urgency == "burning" else ""
        return (
            f'<li{burning}><a href="/t/{ticket.id}">#{ticket.id} {e(ticket.title)}</a>'
            f"<small>{e(' · '.join(meta))}</small></li>"
        )

    @app.get("/", response_class=HTMLResponse)
    def index(ctx: CtxDep, q: str = "") -> HTMLResponse:
        t, session = ctx.t, ctx.session
        query = q.strip()[:200] or None
        names = _names(session)
        _, open_tickets = services.list_tickets(
            session, include_closed=False, query=query, limit=500
        )
        _, closed = services.list_tickets(
            session, status=list(CLOSED_STATUSES), query=query, limit=20
        )
        parts = [
            '<section><form method="get" action="/" class="row">'
            f'<input type="search" name="q" value="{e(q)}" '
            f'placeholder="{e(t["search"])}" aria-label="{e(t["search"])}">'
            f"<button>{e(t['search'])}</button></form></section>"
        ]
        columns = [
            (Status.NEW, [Status.NEW]),
            (Status.DISCUSSING, [Status.DISCUSSING]),
            (Status.IN_PROGRESS, [Status.IN_PROGRESS, Status.WAITING_FOR_PARTS]),
            (Status.ACCEPTANCE, [Status.ACCEPTANCE]),
        ]
        for column, statuses in columns:
            tickets = sorted(
                (x for x in open_tickets if x.status in statuses),
                key=lambda x: (_URGENCY_RANK.get(x.urgency, 3), -x.id),
            )
            if not tickets:
                continue
            items = "".join(ticket_line(ctx, x, names) for x in tickets)
            parts.append(
                f"<section><h2>{e(t['statuses'][column])} ({len(tickets)})</h2>"
                f'<ul class="t">{items}</ul></section>'
            )
        if not open_tickets:
            parts.append(f"<section><p>{e(t['none'])}</p></section>")
        if closed:
            items = "".join(ticket_line(ctx, x, names) for x in closed)
            parts.append(
                f"<section><details><summary>{e(t['closed'])} ({len(closed)})"
                f'</summary><ul class="t">{items}</ul></details></section>'
            )
        return _page(t, t["tickets"], "".join(parts), csrf=ctx.csrf)

    def new_form(
        ctx: Ctx, values: dict[str, str], message: str = "", status_code: int = 200
    ) -> HTMLResponse:
        t = ctx.t

        def options(choices: dict[str, str], current: str) -> str:
            return "".join(
                f'<option value="{e(k)}"{" selected" if k == current else ""}>'
                f"{e(v)}</option>"
                for k, v in choices.items()
            )

        body = (
            f"<section><h1>{e(t['new'])}</h1>{_note('bad', message)}"
            f'<form method="post" action="/new">{_hidden_csrf(ctx)}'
            f'<label for="ty">{e(t["type"])}</label><select id="ty" name="type">'
            f"{options(t['types'], values.get('type', 'repair'))}</select>"
            f'<label for="ti">{e(t["title"])}</label><input id="ti" type="text" '
            f'name="title" maxlength="200" required value="{e(values.get("title"))}">'
            f'<label for="de">{e(t["description"])}</label>'
            f'<textarea id="de" name="description" maxlength="20000">'
            f"{e(values.get('description'))}</textarea>"
            f'<label for="ur">{e(t["urgency"])}</label><select id="ur" name="urgency">'
            f"{options(t['urgencies'], values.get('urgency', 'someday'))}</select>"
            f'<label for="du">{e(t["due"])}</label><input id="du" type="date" '
            f'name="due_date" value="{e(values.get("due_date"))}">'
            f"<p><button>{e(t['create'])}</button></p></form></section>"
        )
        return _page(t, t["new"], body, csrf=ctx.csrf, status_code=status_code)

    @app.get("/new", response_class=HTMLResponse)
    def show_new(ctx: CtxDep) -> HTMLResponse:
        return new_form(ctx, {})

    @app.post("/new", response_class=HTMLResponse)
    def create(
        ctx: CtxDep,
        csrf: Csrf = "",
        type: Annotated[str, Form()] = "repair",
        title: Annotated[str, Form()] = "",
        description: Annotated[str, Form()] = "",
        urgency: Annotated[str, Form()] = "someday",
        due_date: Annotated[str, Form()] = "",
    ) -> Response:
        if denied := check_csrf(ctx, csrf):
            return denied
        values = {
            "type": type,
            "title": title.strip(),
            "description": description,
            "urgency": urgency,
            "due_date": due_date,
        }
        data: dict[str, Any] = {"type": type, "title": values["title"]}
        if description.strip():
            data["description"] = description
        data["urgency"] = urgency
        if due_date:
            data["due_date"] = due_date
        try:
            payload = TicketCreate(**data)
            ticket = services.create_ticket(ctx.session, ctx.actor, payload)
            ctx.session.commit()
        except ValidationError as err:
            ctx.session.rollback()
            detail = "; ".join(
                f"{'.'.join(str(p) for p in x['loc'])}: {x['msg']}"
                for x in err.errors()
            )
            message = _error_text(ctx.t, "invalid_input", detail)
            return new_form(ctx, values, message, 422)
        except (PermissionDenied, Invalid) as err:
            ctx.session.rollback()
            return new_form(ctx, values, err.message, 422)
        return RedirectResponse(f"/t/{ticket.id}?ok=created", status_code=303)

    def ticket_page(
        ctx: Ctx, ticket: Ticket, notice: str = "", error: str = "", status_code=200
    ) -> HTMLResponse:
        t, session = ctx.t, ctx.session
        names = _names(session)
        rows: list[tuple[str, str]] = [
            (t["type"], e(t["types"].get(ticket.type, ticket.type))),
            (t["status"], e(t["statuses"].get(ticket.status, ticket.status))),
            (t["urgency"], e(t["urgencies"].get(ticket.urgency, ticket.urgency))),
        ]
        if ticket.importance:
            rows.append((t["hearts"], "♥" * ticket.importance))
        if ticket.due_date:
            rows.append((t["due"], e(_date(t, ticket.due_date))))
        rows.append((t["requested_by"], e(_who(names, ticket.requested_by_id))))
        if ticket.assignee_ids:
            who = ", ".join(_who(names, u) for u in ticket.assignee_ids)
            rows.append((t["assignees"], e(who)))
        rows.append((t["created"], e(_datetime(ctx, ticket.created_at))))
        if ticket.series_id:
            rows.append((t["recurring"], t["yes"]))
        if ticket.type == TicketType.PURCHASE:
            for field in ("price_estimated", "price_actual"):
                if getattr(ticket, field) is not None:
                    rows.append((t[field], e(_money(t, getattr(ticket, field)))))
            if ticket.vendor:
                rows.append((t["vendor"], e(ticket.vendor)))
            if ticket.product_url:
                url = ticket.product_url
                link = (
                    f'<a href="{e(url)}" rel="noreferrer noopener">{e(url)}</a>'
                    if url.lower().startswith(("https://", "http://"))
                    else e(url)
                )
                rows.append((t["product_url"], link))
            if ticket.warranty_until:
                rows.append((t["warranty_until"], e(_date(t, ticket.warranty_until))))
        if ticket.type == TicketType.REPAIR:
            contractor = " · ".join(
                x for x in (ticket.contractor_name, ticket.contractor_phone) if x
            )
            if contractor:
                rows.append((t["contractor"], e(contractor)))
            if ticket.contractor_appointment:
                rows.append(
                    (t["appointment"], e(_datetime(ctx, ticket.contractor_appointment)))
                )
            for field in ("cost_estimated", "cost_actual"):
                if getattr(ticket, field) is not None:
                    rows.append((t[field], e(_money(t, getattr(ticket, field)))))
            if ticket.warranty_case:
                rows.append((t["warranty_case"], t["yes"]))
        if ticket.ha_targets:
            rows.append((t["ha_targets"], e(", ".join(ticket.ha_targets))))
        facts = "".join(f"<dt>{e(k)}</dt><dd>{v}</dd>" for k, v in rows)

        parts = [
            _note("ok", notice),
            _note("bad", error),
            f"<section><h1>#{ticket.id} {e(ticket.title)}</h1><dl>{facts}</dl>",
        ]
        if ticket.approval_state == ApprovalState.PENDING:
            parts.append(_note("warn", t["approval_pending"]))
        if ticket.description:
            parts.append(f'<p class="pre">{e(ticket.description)}</p>')
        parts.append("</section>")

        status_options = "".join(
            f'<option value="{s}"{" selected" if s == ticket.status else ""}>'
            f"{e(t['statuses'][s])}</option>"
            for s in STATUS_ORDER
        )
        parts.append(
            f"<section><h2>{e(t['change_status'])}</h2>"
            f'<form method="post" action="/t/{ticket.id}/status" class="row">'
            f'{_hidden_csrf(ctx)}<select name="status" aria-label="{e(t["status"])}">'
            f"{status_options}</select><button>{e(t['change_status'])}</button>"
            "</form></section>"
        )

        if ticket.checklist:
            items = "".join(
                f"<li>{'✓' if item.done else '○'} {e(item.text)}</li>"
                for item in sorted(ticket.checklist, key=lambda i: i.position)
            )
            parts.append(
                f"<section><h2>{e(t['checklist'])}</h2>"
                f'<ul class="t">{items}</ul></section>'
            )

        attachments = services.list_attachments(session, ticket)
        if attachments:
            pictures, files = [], []
            for item in attachments:
                if item.content_type.startswith("image/"):
                    pictures.append(
                        f'<a href="/a/{e(item.id)}"><img src="/a/{e(item.id)}/thumb" '
                        f'alt="{e(item.filename)}"></a>'
                    )
                else:
                    files.append(
                        f'<li><a href="/a/{e(item.id)}">{e(item.filename)}</a></li>'
                    )
            parts.append(
                f"<section><h2>{e(t['attachments'])} ({len(attachments)})</h2>"
                f'<div class="thumbs">{"".join(pictures)}</div>'
                f"{f'<ul>{chr(10).join(files)}</ul>' if files else ''}</section>"
            )

        comments = services.list_comments(session, ticket)
        rendered = "".join(
            f'<div class="c"><span class="muted">{e(_who(names, c.author_id))} · '
            f"{e(_datetime(ctx, c.created_at))}"
            f"{e(' · ' + t['via'][c.via]) if c.via in t['via'] else ''}</span>"
            f'<div class="pre">{e(c.body)}</div></div>'
            for c in comments
        )
        parts.append(
            f"<section><h2>{e(t['comments'])} ({len(comments)})</h2>{rendered}"
            f'<form method="post" action="/t/{ticket.id}/comment">{_hidden_csrf(ctx)}'
            f'<label for="co">{e(t["comment"])}</label>'
            '<textarea id="co" name="body" maxlength="20000" required></textarea>'
            f"<p><button>{e(t['add_comment'])}</button></p></form></section>"
        )

        history = services.list_history(session, ticket)
        lines = "".join(
            f"<li>{e(_datetime(ctx, h.at))} · {e(_who(names, h.actor_id))}"
            f"{e(' (' + t['via'][h.via] + ')') if h.via in t['via'] else ''}: "
            f"{e(_history_text(t, names, h.field, h.old, h.new))}</li>"
            for h in reversed(history)
        )
        parts.append(
            f"<section><details><summary>{e(t['history'])} ({len(history)})"
            f'</summary><ul class="t">{lines}</ul></details></section>'
            f'<p><a href="/">{e(t["back"])}</a></p>'
        )
        return _page(
            t,
            f"#{ticket.id}",
            "".join(parts),
            csrf=ctx.csrf,
            status_code=status_code,
        )

    @app.get("/t/{ticket_id}", response_class=HTMLResponse)
    def show_ticket(ctx: CtxDep, ticket_id: int, ok: str = "") -> HTMLResponse:
        ticket = services.get_ticket(ctx.session, ticket_id)
        notice = (
            ctx.t.get(f"ok_{ok}", "") if ok in ("created", "comment", "status") else ""
        )
        return ticket_page(ctx, ticket, notice=notice)

    @app.post("/t/{ticket_id}/comment", response_class=HTMLResponse)
    def comment(
        ctx: CtxDep,
        ticket_id: int,
        csrf: Csrf = "",
        body: Annotated[str, Form()] = "",
    ) -> Response:
        if denied := check_csrf(ctx, csrf):
            return denied
        ticket = services.get_ticket(ctx.session, ticket_id)
        try:
            payload = CommentCreate(body=body)
            services.add_comment(ctx.session, ctx.actor, ticket, payload.body)
            ctx.session.commit()
        except ValidationError:
            ctx.session.rollback()
            message = _error_text(ctx.t, "invalid_input", ctx.t["comment"])
            return ticket_page(ctx, ticket, error=message, status_code=422)
        except PermissionDenied as err:
            ctx.session.rollback()
            return ticket_page(ctx, ticket, error=err.message, status_code=403)
        return RedirectResponse(f"/t/{ticket.id}?ok=comment", status_code=303)

    @app.post("/t/{ticket_id}/status", response_class=HTMLResponse)
    def change_status(
        ctx: CtxDep,
        ticket_id: int,
        csrf: Csrf = "",
        status: Annotated[str, Form()] = "",
    ) -> Response:
        if denied := check_csrf(ctx, csrf):
            return denied
        ticket = services.get_ticket(ctx.session, ticket_id)
        try:
            new = Status(status)
        except ValueError:
            error = ctx.t["errors"]["invalid_status"]
            return ticket_page(ctx, ticket, error=error, status_code=422)
        try:
            services.set_status(ctx.session, ctx.actor, ticket, new)
            ctx.session.commit()
        except (PermissionDenied, Invalid) as err:
            ctx.session.rollback()
            ctx.session.refresh(ticket)
            return ticket_page(ctx, ticket, error=err.message, status_code=403)
        return RedirectResponse(f"/t/{ticket.id}?ok=status", status_code=303)

    @app.get("/a/{attachment_id}")
    def attachment(ctx: CtxDep, attachment_id: str) -> FileResponse:
        item = services.get_attachment(ctx.session, attachment_id)
        return FileResponse(
            item.storage_path,
            media_type=item.content_type,
            filename=item.filename,
            content_disposition_type="inline",
        )

    @app.get("/a/{attachment_id}/thumb")
    def thumbnail(ctx: CtxDep, attachment_id: str) -> FileResponse:
        item = services.get_attachment(ctx.session, attachment_id)
        path = services.make_thumbnail(item)
        if path is None:
            raise NotFound(attachment_id)
        return FileResponse(path, media_type="image/jpeg")

    # Certificate

    def certificate_page(
        ctx: Ctx, notice: str = "", error: str = "", status_code: int = 200
    ) -> HTMLResponse:
        t = ctx.t
        try:
            info = tls.active(settings.tls_dir)
        except (tls.CertificateError, KeyError, ValueError, OSError) as err:
            info = None
            error = error or str(err)
        parts = [_note("ok", notice), _note("bad", error)]
        if info is not None:
            valid = (
                f"{_datetime(ctx, info.not_before)} – {_datetime(ctx, info.not_after)}"
            )
            rows = [
                (t["cert_active"], t["cert_custom"] if info.custom else t["cert_self"]),
                (t["subject"], info.subject),
                (t["issuer"], info.issuer),
                (t["valid"], valid),
                (t["names"], ", ".join(info.names)),
            ]
            facts = "".join(f"<dt>{e(k)}</dt><dd>{e(v)}</dd>" for k, v in rows)
            facts += f"<dt>{e(t['fingerprint'])}</dt><dd><code>{e(info.fingerprint)}</code></dd>"
            warnings = "".join(
                _note("warn", t["warnings"].get(w, w)) for w in info.warnings()
            )
            parts.append(
                f'<section><h1>{e(t["certificate"])}</h1><dl class="stack">{facts}</dl>'
                f"{warnings}</section>"
            )
            if info.custom:
                parts.append(
                    '<section><form method="post" action="/certificate/remove">'
                    f"{_hidden_csrf(ctx)}<button>{e(t['remove_custom'])}</button>"
                    "</form></section>"
                )
        parts.append(
            f"<section><h2>{e(t['upload_title'])}</h2><p class='muted'>"
            f"{e(t['upload_hint'])}</p>"
            '<form method="post" action="/certificate" enctype="multipart/form-data">'
            f"{_hidden_csrf(ctx)}"
            f'<label for="p12">{e(t["pkcs12"])}</label>'
            '<input id="p12" type="file" name="pkcs12" accept=".p12,.pfx">'
            f"<p class='muted'>{e(t['or'])}</p>"
            f'<label for="crt">{e(t["cert_file"])}</label>'
            '<input id="crt" type="file" name="cert" accept=".pem,.crt,.cer,.der">'
            f'<label for="key">{e(t["key_file"])}</label>'
            '<input id="key" type="file" name="key" accept=".pem,.key,.der">'
            f'<label for="pw">{e(t["file_password"])}</label>'
            '<input id="pw" type="password" name="password" autocomplete="off">'
            f"<p><button>{e(t['upload'])}</button></p></form></section>"
        )
        return _page(
            t, t["certificate"], "".join(parts), csrf=ctx.csrf, status_code=status_code
        )

    def reload_certificate(request: Request) -> str:
        reload = request.app.state.reload_certificate
        if reload is None:
            return ""
        try:
            reload()
        except Exception as err:
            _LOGGER.exception("Emergency UI: certificate reload failed")
            return str(err)
        return ""

    @app.get("/certificate", response_class=HTMLResponse)
    def show_certificate(ctx: CtxDep, ok: str = "") -> HTMLResponse:
        notice = ctx.t.get(f"ok_{ok}", "") if ok in ("uploaded", "removed") else ""
        return certificate_page(ctx, notice=notice)

    @app.post("/certificate", response_class=HTMLResponse)
    def upload_certificate(
        request: Request,
        ctx: CtxDep,
        csrf: Csrf = "",
        pkcs12: Annotated[UploadFile | None, File()] = None,
        cert: Annotated[UploadFile | None, File()] = None,
        key: Annotated[UploadFile | None, File()] = None,
        password: Annotated[str, Form()] = "",
    ) -> Response:
        if denied := check_csrf(ctx, csrf):
            return denied

        def read(upload: UploadFile | None) -> bytes | None:
            if upload is None:
                return None
            return upload.file.read(tls.MAX_UPLOAD_BYTES + 1) or None

        try:
            installed = tls.import_custom(
                settings.tls_dir,
                pkcs12=read(pkcs12),
                cert=read(cert),
                key=read(key),
                password=password or None,
            )
        except tls.CertificateError as err:
            message = _error_text(ctx.t, err.code, err.message)
            return certificate_page(ctx, error=message, status_code=422)
        _LOGGER.warning("Emergency UI: own certificate installed")
        certificate_replaced(settings, request.app.state.sessionmaker, installed)
        if failed := reload_certificate(request):
            return certificate_page(ctx, error=failed, status_code=500)
        return RedirectResponse("/certificate?ok=uploaded", status_code=303)

    @app.post("/certificate/remove", response_class=HTMLResponse)
    def remove_certificate(request: Request, ctx: CtxDep, csrf: Csrf = "") -> Response:
        if denied := check_csrf(ctx, csrf):
            return denied
        tls.remove_custom(settings.tls_dir)
        _LOGGER.warning("Emergency UI: own certificate removed")
        if failed := reload_certificate(request):
            return certificate_page(ctx, error=failed, status_code=500)
        return RedirectResponse("/certificate?ok=removed", status_code=303)

    return app


def _history_text(
    t: dict[str, Any], names: dict[str, str], field: str, old: Any, new: Any
) -> str:
    if field == "created":
        return t["h_created"]
    if field == "admin_override":
        return t["h_override"]
    if field == "status":
        return (
            f"{t['status']}: {t['statuses'].get(old, old)} → "
            f"{t['statuses'].get(new, new)}"
        )

    def show(value: Any) -> str:
        if value is None or value == "" or value == []:
            return "–"
        if isinstance(value, list):
            return ", ".join(show(v) for v in value)
        text = names.get(value, str(value)) if isinstance(value, str) else str(value)
        return text if len(text) <= 80 else text[:79] + "…"

    return f"{field}: {show(old)} → {show(new)}"


# Serving


def _tls_context(info: tls.CertInfo) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(info.cert, info.key)
    return context


def make_server(
    settings: Settings, factory: sessionmaker[Session]
) -> uvicorn.Server | None:
    """HTTPS server for the emergency UI; None (logged) if it cannot start."""
    try:
        info = tls.active(settings.tls_dir)
    except (tls.CertificateError, OSError, KeyError, ValueError) as err:
        _LOGGER.error("Emergency UI not started, no certificate: %s", err)
        return None
    for warning in info.warnings():
        _LOGGER.warning("Emergency UI certificate: %s", warning)
    app = create_emergency_app(settings, factory)
    config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.emergency_port,
        ssl_certfile=str(info.cert),
        ssl_keyfile=str(info.key),
        proxy_headers=False,
        server_header=False,
    )
    config.load()
    server = uvicorn.Server(config)

    # Switching certificates at runtime: loading a second certificate into the
    # running context would keep the first one as well (OpenSSL holds one per key
    # type, e.g. the self-signed EC one next to an own RSA one), and browsers would
    # still get the old one. Instead every handshake switches to the context of the
    # current certificate; OpenSSL calls this also without SNI (e.g. for an IP).
    current = {"context": _tls_context(info)}

    def choose(ssl_object: Any, server_name: str | None, context: Any) -> None:
        ssl_object.context = current["context"]

    config.ssl.sni_callback = choose

    def reload() -> None:
        """Serve the current certificate from the next handshake on."""
        current["context"] = _tls_context(tls.active(settings.tls_dir))

    app.state.reload_certificate = reload
    server.config_reload_certificate = reload  # type: ignore[attr-defined]
    return server


def start_in_background(settings: Settings, factory: sessionmaker[Session]) -> None:
    """Run the emergency UI next to the API, in its own thread and event loop.

    A failure here (no certificate, port in use) is logged and leaves the API alone.
    """
    if not settings.emergency_networks:
        return
    server = make_server(settings, factory)
    if server is None:
        return

    def run() -> None:
        try:
            server.run()
        except BaseException:
            _LOGGER.exception("Emergency UI stopped")

    def watch() -> None:
        while True:
            try:
                check_certificate(
                    settings, factory, reload=server.config_reload_certificate
                )
            except Exception:
                _LOGGER.exception("Emergency UI: certificate check failed")
            time.sleep(CHECK_SECONDS)

    threading.Thread(target=run, name="emergency-ui", daemon=True).start()
    threading.Thread(target=watch, name="emergency-cert", daemon=True).start()
    _LOGGER.info(
        "Emergency UI on https://%s:%s/", settings.host, settings.emergency_port
    )


# Certificate expiry


REMINDER_TEXTS = {
    "de": {
        "title": "Zertifikat der Notfall-Oberfläche erneuern",
        "body": (
            "Das eigene Zertifikat der Notfall-Oberfläche läuft am {date} ab.\n\n"
            "Inhaber: {subject}\nAussteller: {issuer}\n"
            "Fingerabdruck SHA-256: {fingerprint}\n\n"
            "Neues Zertifikat ausstellen (z. B. in xca) und auf der Notfall-Oberfläche "
            "unter „Zertifikat“ hochladen. Danach rückt dieses Ticket in die Abnahme. "
            "Läuft es ab, warnt der Browser; dann hilft auch „Eigenes Zertifikat "
            "entfernen“ (es gilt wieder das selbst signierte)."
        ),
        "replaced": "Neues Zertifikat installiert, gültig bis {date}.\n"
        "Fingerabdruck SHA-256: {fingerprint}",
    },
    "en": {
        "title": "Renew the emergency UI certificate",
        "body": (
            "The own certificate of the emergency UI expires on {date}.\n\n"
            "Subject: {subject}\nIssuer: {issuer}\n"
            "SHA-256 fingerprint: {fingerprint}\n\n"
            "Issue a new certificate (e.g. in xca) and upload it in the emergency UI "
            'under "Certificate". This ticket then moves to review. Once it has '
            'expired, browsers warn; "Remove own certificate" brings back the '
            "self-signed one."
        ),
        "replaced": "New certificate installed, valid until {date}.\n"
        "SHA-256 fingerprint: {fingerprint}",
    },
}


def _reminder_texts(settings: Settings) -> dict[str, str]:
    return REMINDER_TEXTS["de" if settings.language.startswith("de") else "en"]


def _local_date(settings: Settings, value: datetime) -> date:
    return value.astimezone(ZoneInfo(settings.timezone)).date()


def _shown_date(settings: Settings, value: date) -> str:
    return (
        value.strftime("%d.%m.%Y")
        if settings.language.startswith("de")
        else value.isoformat()
    )


def _owner(session: Session) -> User | None:
    """Who looks after the certificate: the emergency login, else the only admin."""
    login = get_login(session)
    user = session.get(User, login.user_id) if login else None
    if user is not None and user.active:
        return user
    try:
        return find_admin(session, None)
    except Invalid:
        return None


def check_certificate(
    settings: Settings,
    factory: sessionmaker[Session],
    *,
    reload: Any = None,
    now: datetime | None = None,
) -> int | None:
    """Renew the self-signed certificate when due; before an own certificate expires,
    file a ticket once for it. Returns the new ticket number, if one was filed.
    """
    now = now or datetime.now(UTC)
    custom = tls.custom_info(settings.tls_dir)
    if custom is None:
        old_cert = settings.tls_dir / "self-signed.crt"
        old = old_cert.read_bytes() if old_cert.exists() else None
        current = tls.ensure_self_signed(settings.tls_dir, now=now)
        if old is not None and current.cert.read_bytes() != old:
            _LOGGER.warning("Emergency UI: self-signed certificate renewed")
            if reload is not None:
                reload()
        return None
    if custom.not_after - now > REMIND_BEFORE:
        return None
    with factory() as session:
        row = session.get(Setting, REMINDER_KEY)
        if row is not None and row.value.get("fingerprint") == custom.fingerprint:
            return None
        automation = session.get(User, AUTOMATION_USER_ID)
        if automation is None:
            _LOGGER.error(
                "Emergency UI: no user %r to file tickets", AUTOMATION_USER_ID
            )
            return None
        owner = _owner(session)
        texts = _reminder_texts(settings)
        expires = _local_date(settings, custom.not_after)
        data = TicketCreate(
            type=TicketType.HOME_ASSISTANT,
            title=texts["title"],
            description=texts["body"].format(
                date=_shown_date(settings, expires),
                subject=custom.subject,
                issuer=custom.issuer,
                fingerprint=custom.fingerprint,
            ),
            urgency=Urgency.BURNING if custom.not_after <= now else Urgency.THIS_WEEK,
            due_date=expires,
            requested_by_id=owner.ha_user_id if owner else None,
            assignee_ids=[owner.ha_user_id] if owner else [],
        )
        actor = Actor(user=automation, via=Via.AUTOMATION)
        ticket = services.create_ticket(session, actor, data)
        value = {"fingerprint": custom.fingerprint, "ticket_id": ticket.id}
        if row is None:
            session.add(Setting(key=REMINDER_KEY, value=value))
        else:
            row.value = value
        session.commit()
        _LOGGER.warning(
            "Emergency UI: certificate expires %s, ticket #%s filed", expires, ticket.id
        )
        return ticket.id


def certificate_replaced(
    settings: Settings, factory: sessionmaker[Session], installed: tls.CertInfo
) -> None:
    """A new own certificate: note it on the open reminder ticket, move it to review."""
    try:
        with factory() as session:
            row = session.get(Setting, REMINDER_KEY)
            if row is None or row.value.get("fingerprint") == installed.fingerprint:
                return
            ticket = session.get(Ticket, row.value.get("ticket_id"))
            automation = session.get(User, AUTOMATION_USER_ID)
            if ticket is None or automation is None or ticket.status in CLOSED_STATUSES:
                return
            actor = Actor(user=automation, via=Via.AUTOMATION)
            texts = _reminder_texts(settings)
            expires = _local_date(settings, installed.not_after)
            services.add_comment(
                session,
                actor,
                ticket,
                texts["replaced"].format(
                    date=_shown_date(settings, expires),
                    fingerprint=installed.fingerprint,
                ),
            )
            if ticket.status != Status.ACCEPTANCE:
                services.set_status(session, actor, ticket, Status.ACCEPTANCE)
            session.commit()
    except (PermissionDenied, Invalid, NotFound):
        _LOGGER.exception("Emergency UI: reminder ticket not updated")
