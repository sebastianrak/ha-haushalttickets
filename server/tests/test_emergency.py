"""Tests for the emergency web UI, its certificates and the expiry reminder."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import re
import shutil
import subprocess
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from haushalttickets_server import emergency, tls
from haushalttickets_server.config import Settings
from haushalttickets_server.emergency import (
    LOCK_SECONDS,
    MAX_FAILURES,
    check_certificate,
    create_emergency_app,
    hash_password,
    set_password,
    verify_password,
)
from haushalttickets_server.models import Comment, HistoryEntry, Ticket, User

from .conftest import Api, error_code, uid

PASSWORD = "richtig-geheim-42"
BERLIN = ZoneInfo("Europe/Berlin")
needs_openssl = pytest.mark.skipif(
    shutil.which("openssl") is None, reason="openssl not installed"
)


def _settings(app: FastAPI, tmp_path: Path, clients: str = "172.16.0.0/16") -> Settings:
    return Settings(
        database_url=app.state.settings.database_url,
        attachments_dir=tmp_path / "attachments",
        emergency_clients=clients,
        tls_dir=tmp_path / "tls",
    )


@pytest.fixture
def eapp(app: FastAPI, household: None, tmp_path: Path) -> FastAPI:
    """Emergency UI on the test database, for 172.16.0.0/16."""
    return create_emergency_app(_settings(app, tmp_path), app.state.sessionmaker)


def _client(eapp: FastAPI, ip: str = "172.16.3.20") -> TestClient:
    return TestClient(
        eapp,
        base_url="https://testserver",
        client=(ip, 50000),
        headers={"accept-language": "de-DE,de;q=0.9"},
    )


def _set_password(app: FastAPI, password: str = PASSWORD, name: str = "alex") -> None:
    with app.state.sessionmaker() as session:
        set_password(session, session.get(User, uid(name)), password)
        session.commit()


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf" value="([0-9a-f]+)"', html)
    assert match, html
    return match.group(1)


def _login(client: TestClient, password: str = PASSWORD) -> str:
    response = client.post("/login", data={"password": password})
    assert response.status_code == 200, response.text
    assert "Abmelden" in response.text
    return _csrf(response.text)


def test_password_hash() -> None:
    """scrypt hashes are salted and verify only the right password."""
    stored = hash_password("geheim")
    assert stored.startswith("scrypt$") and stored != hash_password("geheim")
    assert verify_password("geheim", stored)
    assert not verify_password("Geheim", stored)
    assert not verify_password("geheim", "garbage")


def test_networks(app: FastAPI, household: None, tmp_path: Path) -> None:
    """Without listed networks nothing is served; other addresses are refused."""
    off = create_emergency_app(_settings(app, tmp_path, ""), app.state.sessionmaker)
    assert _client(off).get("/login").status_code == 403
    on = create_emergency_app(_settings(app, tmp_path), app.state.sessionmaker)
    assert _client(on, ip="192.168.1.5").get("/login").status_code == 403
    assert _client(on).get("/login").status_code == 200


def test_not_set_up(eapp: FastAPI) -> None:
    """Without a password the login explains how to set one."""
    response = _client(eapp).get("/login")
    assert "emergency-setup" in response.text
    assert 'type="password"' not in response.text


def test_password_rules(app: FastAPI, household: None) -> None:
    """Only active admins, at least ten characters."""
    with app.state.sessionmaker() as session:
        with pytest.raises(emergency.Invalid) as err:
            set_password(session, session.get(User, uid("robin")), PASSWORD)
        assert err.value.code == "admin_required"
        with pytest.raises(emergency.Invalid) as err:
            set_password(session, session.get(User, uid("alex")), "kurz")
        assert err.value.code == "password_too_short"
        assert emergency.find_admin(session, None).ha_user_id == uid("alex")
        assert emergency.find_admin(session, "ALEX").ha_user_id == uid("alex")


def test_login_and_actions(app: FastAPI, eapp: FastAPI) -> None:
    """Sign in, file, comment and move a ticket as the admin via emergency_ui."""
    _set_password(app)
    client = _client(eapp)
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"

    response = client.post(
        "/login", data={"password": PASSWORD}, follow_redirects=False
    )
    assert response.status_code == 303
    cookie = response.headers["set-cookie"]
    flags = cookie.lower()
    assert "secure" in flags and "httponly" in flags and "samesite=strict" in flags
    assert "Strict-Transport-Security" not in response.headers
    page = client.get("/")
    csrf = _csrf(page.text)
    assert "default-src 'none'" in page.headers["content-security-policy"]

    response = client.post(
        "/new",
        data={
            "csrf": csrf,
            "type": "repair",
            "title": "Heizung <b>kalt</b>",
            "description": "Seit heute früh",
            "urgency": "burning",
            "due_date": "2026-12-01",
        },
    )
    assert response.status_code == 200
    assert "Ticket angelegt." in response.text
    assert "Heizung &lt;b&gt;kalt&lt;/b&gt;" in response.text
    assert "<b>kalt</b>" not in response.text

    with app.state.sessionmaker() as session:
        ticket = session.query(Ticket).one()
        assert ticket.created_via == "emergency_ui"
        assert ticket.requested_by_id == uid("alex")
        assert ticket.urgency == "burning"
        number = ticket.id

    client.post(f"/t/{number}/comment", data={"csrf": csrf, "body": "Monteur kommt"})
    response = client.post(f"/t/{number}/status", data={"csrf": csrf, "status": "done"})
    assert "Status geändert." in response.text
    listing = client.get("/").text
    assert "Zuletzt geschlossen (1)" in listing

    with app.state.sessionmaker() as session:
        comment = session.query(Comment).one()
        assert (comment.author_id, comment.via) == (uid("alex"), "emergency_ui")
        status = session.query(HistoryEntry).filter_by(field="status").one()
        assert (status.via, status.new) == ("emergency_ui", "done")

    response = client.post("/logout", data={"csrf": csrf}, follow_redirects=False)
    assert response.headers["location"] == "/login"
    assert client.get("/", follow_redirects=False).status_code == 303


def test_permissions_still_apply(app: FastAPI, eapp: FastAPI, api: Api) -> None:
    """A purchase awaiting approval cannot be moved on in the emergency UI either."""
    response = api("robin").post(
        "/api/v1/tickets",
        json={"type": "purchase", "title": "Sofa", "price_estimated": "900"},
    )
    number = response.json()["id"]
    _set_password(app)
    client = _client(eapp)
    csrf = _login(client)
    page = client.get(f"/t/{number}").text
    assert "Freigabe ausstehend" in page
    response = client.post(
        f"/t/{number}/status", data={"csrf": csrf, "status": "in_progress"}
    )
    assert response.status_code == 403
    assert "approval" in response.text


def test_lockout(app: FastAPI, eapp: FastAPI) -> None:
    """Five wrong passwords lock the login for 15 minutes."""
    _set_password(app)
    now = [1_800_000_000.0]
    eapp.state.clock = lambda: now[0]
    client = _client(eapp)
    for _ in range(MAX_FAILURES):
        response = client.post("/login", data={"password": "falsch"})
        assert response.status_code in (401, 429)
    response = client.post("/login", data={"password": PASSWORD})
    assert response.status_code == 429
    assert "Zu viele Fehlversuche" in response.text
    now[0] += LOCK_SECONDS + 1
    assert "Abmelden" in client.post("/login", data={"password": PASSWORD}).text


def test_browser_origin(app: FastAPI, eapp: FastAPI) -> None:
    """Login works with the Origin headers real browsers send (regression 0.7.0).

    With "Referrer-Policy: no-referrer" Chrome sent "Origin: null" and every login
    ended in 403.
    """
    _set_password(app)
    client = _client(eapp)
    page = client.get("/login")
    assert page.headers["referrer-policy"] == "same-origin"
    for origin in ("https://testserver", "null"):
        response = client.post(
            "/login", data={"password": PASSWORD}, headers={"origin": origin}
        )
        assert "Abmelden" in response.text, origin


def test_forged_requests(app: FastAPI, eapp: FastAPI) -> None:
    """Forms need the session's token and must come from the same origin."""
    _set_password(app)
    client = _client(eapp)
    csrf = _login(client)
    data = {"type": "repair", "title": "X", "urgency": "someday"}
    assert client.post("/new", data=data).status_code == 403
    assert client.post("/new", data={**data, "csrf": "0" * 32}).status_code == 403
    response = client.post(
        "/new", data={**data, "csrf": csrf}, headers={"origin": "https://evil.example"}
    )
    assert response.status_code == 403
    with app.state.sessionmaker() as session:
        assert session.query(Ticket).count() == 0


def test_sessions_end(app: FastAPI, eapp: FastAPI) -> None:
    """Tampered or expired cookies and a new password end the session."""
    _set_password(app)
    client = _client(eapp)
    _login(client)
    good = client.cookies["ht_emergency"]

    client.cookies.set("ht_emergency", good[:-2] + "00", domain="testserver.local")
    assert client.get("/", follow_redirects=False).status_code == 303
    client.cookies.clear()
    client.cookies.set("ht_emergency", good, domain="testserver.local")
    assert client.get("/", follow_redirects=False).status_code == 200

    eapp.state.clock = lambda: datetime.now(UTC).timestamp() + 13 * 3600
    assert client.get("/", follow_redirects=False).status_code == 303
    eapp.state.clock = lambda: datetime.now(UTC).timestamp()
    assert client.get("/", follow_redirects=False).status_code == 200

    _set_password(app, "ganz-neues-passwort")
    assert client.get("/", follow_redirects=False).status_code == 303


def test_api_reserves_channel(api: Api) -> None:
    """Clients cannot claim to be the emergency UI."""
    response = api("alex", via="emergency_ui").get("/api/v1/tickets")
    assert response.status_code == 400
    assert error_code(response) == "invalid_via"


# Certificates


def _openssl(*args: str, cwd: Path) -> None:
    subprocess.run(["openssl", *args], cwd=cwd, check=True, capture_output=True)


def _make_cert(
    folder: Path, name: str = "own", days: int = 365, san: bool = True
) -> tuple[Path, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    config = folder / f"{name}.cnf"
    extensions = "extendedKeyUsage = serverAuth\n"
    if san:
        extensions += "subjectAltName = DNS:tickets.example, IP:172.16.3.20\n"
    config.write_text(
        "[req]\ndistinguished_name = dn\nx509_extensions = ext\nprompt = no\n"
        f"[dn]\nCN = {name}\n[ext]\n{extensions}"
    )
    _openssl(
        "req", "-x509", "-config", str(config), "-newkey", "rsa:2048", "-nodes",
        "-days", str(days), "-keyout", f"{name}.key", "-out", f"{name}.crt",
        cwd=folder,
    )  # fmt: skip
    return folder / f"{name}.crt", folder / f"{name}.key"


@needs_openssl
def test_self_signed(tmp_path: Path) -> None:
    """Created once with names and server use, kept, renewed when due."""
    folder = tmp_path / "tls"
    first = tls.active(folder)
    assert not first.custom and first.server_auth is True
    assert "DNS:localhost" in first.names and "IP:127.0.0.1" in first.names
    assert first.warnings() == []
    assert (first.key.stat().st_mode & 0o777) == 0o600
    assert tls.active(folder).fingerprint == first.fingerprint
    due = first.not_after - timedelta(days=30)
    assert tls.ensure_self_signed(folder, now=due).fingerprint != first.fingerprint


@needs_openssl
def test_import_formats(tmp_path: Path) -> None:
    """PEM with encrypted key, PKCS#12, wrong password, mismatching key."""
    folder = tmp_path / "tls"
    cert, key = _make_cert(tmp_path / "src")
    _openssl(
        "pkey", "-in", "own.key", "-aes256", "-passout", "pass:xca", "-out", "enc.key",
        cwd=tmp_path / "src",
    )  # fmt: skip
    _openssl(
        "pkcs12", "-export", "-in", "own.crt", "-inkey", "own.key", "-out", "own.p12",
        "-passout", "pass:xca",
        cwd=tmp_path / "src",
    )  # fmt: skip
    encrypted = (tmp_path / "src" / "enc.key").read_bytes()

    with pytest.raises(tls.CertificateError) as err:
        tls.import_custom(folder, cert=cert.read_bytes(), key=encrypted, password="no")
    assert err.value.code == "wrong_password"
    info = tls.import_custom(
        folder, cert=cert.read_bytes(), key=encrypted, password="xca"
    )
    assert info.custom and "DNS:tickets.example" in info.names
    assert tls.active(folder).fingerprint == info.fingerprint

    p12 = (tmp_path / "src" / "own.p12").read_bytes()
    with pytest.raises(tls.CertificateError) as err:
        tls.import_custom(folder, pkcs12=p12, password="falsch")
    assert err.value.code == "wrong_password"
    assert tls.import_custom(folder, pkcs12=p12, password="xca").custom

    _, other_key = _make_cert(tmp_path / "src", "other")
    with pytest.raises(tls.CertificateError) as err:
        tls.import_custom(folder, cert=cert.read_bytes(), key=other_key.read_bytes())
    assert err.value.code == "key_mismatch"
    with pytest.raises(tls.CertificateError) as err:
        tls.import_custom(folder, cert=cert.read_bytes())
    assert err.value.code == "files_missing"
    with pytest.raises(tls.CertificateError) as err:
        later = datetime.now(UTC) + timedelta(days=400)
        tls.import_custom(
            folder, cert=cert.read_bytes(), key=key.read_bytes(), now=later
        )
    assert err.value.code == "expired"

    tls.remove_custom(folder)
    assert not tls.active(folder).custom


@needs_openssl
def test_import_chain_from_own_ca(tmp_path: Path) -> None:
    """A PKCS#12 file with the own CA: the server certificate is served first."""
    src = tmp_path / "src"
    src.mkdir()
    _openssl(
        "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650",
        "-subj", "/CN=Haus-CA", "-keyout", "ca.key", "-out", "ca.crt",
        cwd=src,
    )  # fmt: skip
    (src / "ext.cnf").write_text(
        "subjectAltName = DNS:tickets.example\nextendedKeyUsage = serverAuth\n"
    )
    _openssl(
        "req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=tickets",
        "-keyout", "srv.key", "-out", "srv.csr",
        cwd=src,
    )  # fmt: skip
    _openssl(
        "x509", "-req", "-in", "srv.csr", "-CA", "ca.crt", "-CAkey", "ca.key",
        "-CAcreateserial", "-days", "397", "-extfile", "ext.cnf", "-out", "srv.crt",
        cwd=src,
    )  # fmt: skip
    _openssl(
        "pkcs12", "-export", "-in", "srv.crt", "-inkey", "srv.key",
        "-certfile", "ca.crt", "-out", "chain.p12", "-passout", "pass:xca",
        cwd=src,
    )  # fmt: skip
    info = tls.import_custom(
        tmp_path / "tls", pkcs12=(src / "chain.p12").read_bytes(), password="xca"
    )
    assert info.subject == "CN=tickets" and info.issuer == "CN=Haus-CA"
    assert info.cert.read_bytes().count(b"BEGIN CERTIFICATE") == 2
    assert info.warnings() == []


@needs_openssl
def test_import_warnings(tmp_path: Path) -> None:
    """Certificates iPhones refuse are installed but flagged."""
    cert, key = _make_cert(tmp_path / "src", days=1000, san=False)
    info = tls.import_custom(
        tmp_path / "tls", cert=cert.read_bytes(), key=key.read_bytes()
    )
    assert set(info.warnings()) == {"validity_too_long", "no_san"}


@needs_openssl
def test_upload_page(app: FastAPI, eapp: FastAPI, tmp_path: Path) -> None:
    """Upload through the UI, reload the server's certificate, remove it again."""
    reloads = []
    eapp.state.reload_certificate = lambda: reloads.append(1)
    _set_password(app)
    client = _client(eapp)
    csrf = _login(client)
    assert "Selbst signiert" in client.get("/certificate").text

    cert, key = _make_cert(tmp_path / "src")
    response = client.post(
        "/certificate",
        data={"csrf": csrf, "password": ""},
        files={
            "cert": ("own.crt", cert.read_bytes()),
            "key": ("own.key", key.read_bytes()),
            "pkcs12": ("", b""),
        },
    )
    assert "Zertifikat übernommen" in response.text
    assert "Eigenes Zertifikat" in response.text and reloads == [1]

    response = client.post(
        "/certificate", data={"csrf": csrf}, files={"cert": ("x.crt", b"kaputt")}
    )
    assert response.status_code == 422

    response = client.post("/certificate/remove", data={"csrf": csrf})
    assert "Eigenes Zertifikat entfernt" in response.text and reloads == [1, 1]


# Expiry reminder


@needs_openssl
def test_reminder_ticket(app: FastAPI, eapp: FastAPI, tmp_path: Path) -> None:
    """14 days before an own certificate expires, one ticket for the admin."""
    settings = eapp.state.settings
    factory = app.state.sessionmaker
    _set_password(app)
    cert, key = _make_cert(tmp_path / "src", days=30)
    info = tls.import_custom(
        settings.tls_dir, cert=cert.read_bytes(), key=key.read_bytes()
    )

    assert check_certificate(settings, factory) is None  # 30 days left
    soon = info.not_after - timedelta(days=13)
    number = check_certificate(settings, factory, now=soon)
    assert number is not None
    assert check_certificate(settings, factory, now=soon) is None  # only once
    with factory() as session:
        ticket = session.get(Ticket, number)
        assert ticket.title == "Zertifikat der Notfall-Oberfläche erneuern"
        assert (ticket.requested_by_id, ticket.assignee_ids) == (
            uid("alex"),
            [uid("alex")],
        )
        assert ticket.created_by_id == "automation"
        assert ticket.urgency == "this_week"
        assert ticket.due_date == info.not_after.astimezone(BERLIN).date()
        assert info.fingerprint in ticket.description

    new_cert, new_key = _make_cert(tmp_path / "src", "renewed", days=365)
    installed = tls.import_custom(
        settings.tls_dir, cert=new_cert.read_bytes(), key=new_key.read_bytes()
    )
    emergency.certificate_replaced(settings, factory, installed)
    with factory() as session:
        ticket = session.get(Ticket, number)
        assert ticket.status == "acceptance"
        comment = session.query(Comment).filter_by(ticket_id=number).one()
        assert installed.fingerprint in comment.body
    assert check_certificate(settings, factory) is None


@needs_openssl
def test_self_signed_renewal_reloads(app: FastAPI, eapp: FastAPI) -> None:
    """The self-signed certificate renews itself; no ticket for it."""
    settings = eapp.state.settings
    first = tls.active(settings.tls_dir)
    reloads = []
    due = first.not_after - timedelta(days=30)
    result = check_certificate(
        settings, app.state.sessionmaker, reload=lambda: reloads.append(1), now=due
    )
    assert result is None and reloads == [1]
    with app.state.sessionmaker() as session:
        assert session.query(Ticket).count() == 0
