"""TLS certificate of the emergency UI.

Two sources: a self-signed certificate the server creates and renews itself, and an
own certificate the admin brings (for example from their own CA in xca), uploaded in
the emergency UI. The own certificate wins while it exists; the server never changes
it. All certificate work is done with the ``openssl`` command line tool, so the server
needs no extra Python package for it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import tempfile

# Apple platforms refuse TLS server certificates valid for longer than 825 days
# (https://support.apple.com/en-us/103769), so the self-signed one stays below.
MAX_DAYS = 825
SELF_SIGNED_DAYS = 820
RENEW_BEFORE = timedelta(days=60)
EXPIRES_SOON = timedelta(days=30)
MAX_UPLOAD_BYTES = 1024 * 1024

_SELF = ("self-signed.crt", "self-signed.key")
_CUSTOM = ("custom.crt", "custom.key")
_PEM_CERT = re.compile(
    rb"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", re.DOTALL
)


class CertificateError(Exception):
    """A certificate could not be created, read or imported."""

    def __init__(self, code: str, message: str, output: str = "") -> None:
        """Initialize; ``output`` is the full error text of openssl, if any."""
        super().__init__(message)
        self.code = code
        self.message = message
        self.output = output or message


@dataclass(frozen=True)
class CertInfo:
    """The certificate the emergency UI serves."""

    cert: Path
    key: Path
    custom: bool
    subject: str
    issuer: str
    not_before: datetime
    not_after: datetime
    fingerprint: str
    names: tuple[str, ...]
    server_auth: bool | None  # None: no extended key usage, so any use is allowed

    def warnings(self, now: datetime | None = None) -> list[str]:
        """Problems browsers on iPhone/macOS (and others) will complain about."""
        now = now or datetime.now(UTC)
        found = []
        if self.not_after <= now:
            found.append("expired")
        elif self.not_after - now < EXPIRES_SOON:
            found.append("expires_soon")
        if self.not_after - self.not_before > timedelta(days=MAX_DAYS):
            found.append("validity_too_long")
        if not self.names:
            found.append("no_san")
        if self.server_auth is False:
            found.append("no_server_auth")
        return found


def _openssl(
    *args: str, data: bytes | None = None, password: str | None = None
) -> bytes:
    exe = shutil.which("openssl")
    if exe is None:
        raise CertificateError("openssl_missing", "openssl is not installed.")
    env = {**os.environ, "HT_PASS": password or ""}
    result = subprocess.run(
        [exe, *args],
        input=data,
        capture_output=True,
        env=env,
        umask=0o077,
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        output = result.stderr.decode(errors="replace").strip()
        first = output.splitlines()[0] if output else "openssl"
        raise CertificateError("openssl_failed", first, output)
    return result.stdout


def _parse_time(value: str) -> datetime:
    return datetime.fromtimestamp(ssl.cert_time_to_seconds(value), UTC)


def read_info(cert: Path, key: Path, *, custom: bool) -> CertInfo:
    """Describe a certificate file (the first certificate in it)."""
    out = _openssl(
        "x509",
        "-in",
        str(cert),
        "-noout",
        "-subject",
        "-issuer",
        "-startdate",
        "-enddate",
        "-fingerprint",
        "-sha256",
        "-ext",
        "subjectAltName,extendedKeyUsage",
        "-nameopt",
        "RFC2253",
    ).decode(errors="replace")
    fields: dict[str, str] = {}
    names: tuple[str, ...] = ()
    server_auth: bool | None = None
    lines = out.splitlines()
    for index, line in enumerate(lines):
        following = lines[index + 1].strip() if index + 1 < len(lines) else ""
        if line.startswith("X509v3 Subject Alternative Name"):
            names = tuple(
                part.strip().replace("IP Address:", "IP:")
                for part in following.split(",")
                if part.strip()
            )
        elif line.startswith("X509v3 Extended Key Usage"):
            server_auth = "TLS Web Server Authentication" in following
        elif "=" in line and not line.startswith(" "):
            name, _, value = line.partition("=")
            fields[name.strip().lower()] = value.strip()
    return CertInfo(
        cert=cert,
        key=key,
        custom=custom,
        subject=fields.get("subject", ""),
        issuer=fields.get("issuer", ""),
        not_before=_parse_time(fields["notbefore"]),
        not_after=_parse_time(fields["notafter"]),
        fingerprint=fields.get("sha256 fingerprint", ""),
        names=names,
        server_auth=server_auth,
    )


def _primary_address() -> str | None:
    """Address of the interface with the default route (no packet is sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("192.0.2.1", 9))
            return probe.getsockname()[0]
        except OSError:
            return None


def _write_atomic(target: Path, data: bytes, mode: int) -> None:
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _paths(tls_dir: Path, names: tuple[str, str]) -> tuple[Path, Path]:
    return tls_dir / names[0], tls_dir / names[1]


def create_self_signed(tls_dir: Path) -> CertInfo:
    """Create (or replace) the self-signed certificate."""
    tls_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    hostname = socket.gethostname() or "haushalttickets"
    names = [f"DNS:{hostname}", "DNS:localhost", "IP:127.0.0.1"]
    if (address := _primary_address()) and address != "127.0.0.1":
        names.append(f"IP:{address}")
    with tempfile.TemporaryDirectory(dir=tls_dir) as work:
        config = Path(work, "req.cnf")
        config.write_text(
            "[req]\ndistinguished_name = dn\nx509_extensions = ext\nprompt = no\n"
            f"[dn]\nCN = {hostname}\nO = Haushalttickets\n"
            f"[ext]\nsubjectAltName = {','.join(names)}\n"
            "extendedKeyUsage = serverAuth\n"
            "basicConstraints = critical,CA:FALSE\n"
            "keyUsage = critical,digitalSignature\n"
            "subjectKeyIdentifier = hash\n"
        )
        cert, key = Path(work, "cert.pem"), Path(work, "key.pem")
        _openssl(
            "req",
            "-x509",
            "-config",
            str(config),
            "-newkey",
            "ec",
            "-pkeyopt",
            "ec_paramgen_curve:prime256v1",
            "-nodes",
            "-sha256",
            "-days",
            str(SELF_SIGNED_DAYS),
            "-keyout",
            str(key),
            "-out",
            str(cert),
        )
        target_cert, target_key = _paths(tls_dir, _SELF)
        _write_atomic(target_key, key.read_bytes(), 0o600)
        _write_atomic(target_cert, cert.read_bytes(), 0o644)
    return read_info(target_cert, target_key, custom=False)


def ensure_self_signed(
    tls_dir: Path, *, renew: bool = False, now: datetime | None = None
) -> CertInfo:
    """The self-signed certificate; created or renewed when needed."""
    cert, key = _paths(tls_dir, _SELF)
    if not renew and cert.exists() and key.exists():
        try:
            info = read_info(cert, key, custom=False)
        except (CertificateError, KeyError, ValueError):
            pass
        else:
            if info.not_after - (now or datetime.now(UTC)) > RENEW_BEFORE:
                return info
    return create_self_signed(tls_dir)


def custom_info(tls_dir: Path) -> CertInfo | None:
    """The own certificate, if one is installed."""
    cert, key = _paths(tls_dir, _CUSTOM)
    if not (cert.exists() and key.exists()):
        return None
    return read_info(cert, key, custom=True)


def active(tls_dir: Path) -> CertInfo:
    """The certificate to serve: the own one, otherwise the self-signed one."""
    return custom_info(tls_dir) or ensure_self_signed(tls_dir)


def remove_custom(tls_dir: Path) -> None:
    """Delete the own certificate; the self-signed one is served again."""
    for path in _paths(tls_dir, _CUSTOM):
        path.unlink(missing_ok=True)


def _looks_pem(data: bytes) -> bool:
    return b"-----BEGIN " in data


def _public_key(*args: str, data: bytes) -> bytes:
    return _openssl(*args, data=data)


def import_custom(
    tls_dir: Path,
    *,
    pkcs12: bytes | None = None,
    cert: bytes | None = None,
    key: bytes | None = None,
    password: str | None = None,
    now: datetime | None = None,
) -> CertInfo:
    """Install an own certificate from a PKCS#12 file or from certificate + key.

    The certificate file may contain the chain; the certificate matching the key is
    served first. Encrypted keys and PKCS#12 files need ``password``.
    """
    for part in (pkcs12, cert, key):
        if part is not None and len(part) > MAX_UPLOAD_BYTES:
            raise CertificateError("too_large", "File too large.")
    tls_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

    if pkcs12:
        certs_pem, key_pem = _from_pkcs12(pkcs12, password)
    elif cert and key:
        certs_pem = cert if _looks_pem(cert) else _der_cert(cert)
        key_pem = _plain_key(key, password)
    else:
        raise CertificateError(
            "files_missing", "Upload a PKCS#12 file or a certificate and its key."
        )

    blocks = _PEM_CERT.findall(certs_pem)
    if not blocks:
        raise CertificateError("no_certificate", "No certificate found.")
    key_public = _public_key("pkey", "-pubout", data=key_pem)
    leaf = None
    for block in blocks:
        try:
            if _public_key("x509", "-noout", "-pubkey", data=block) == key_public:
                leaf = block
                break
        except CertificateError:
            continue
    if leaf is None:
        raise CertificateError("key_mismatch", "The key does not match a certificate.")
    chain = [leaf, *(b for b in blocks if b != leaf)]

    with tempfile.TemporaryDirectory(dir=tls_dir) as work:
        new_cert, new_key = Path(work, "cert.pem"), Path(work, "key.pem")
        new_cert.write_bytes(b"\n".join(chain) + b"\n")
        new_key.write_bytes(key_pem)
        info = read_info(new_cert, new_key, custom=True)
        if info.not_after <= (now or datetime.now(UTC)):
            raise CertificateError("expired", "The certificate has expired.")
        try:
            ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(new_cert, new_key)
        except ssl.SSLError as err:
            raise CertificateError("unusable", str(err)) from None
        target_cert, target_key = _paths(tls_dir, _CUSTOM)
        _write_atomic(target_key, new_key.read_bytes(), 0o600)
        _write_atomic(target_cert, new_cert.read_bytes(), 0o644)
    return read_info(target_cert, target_key, custom=True)


def _from_pkcs12(data: bytes, password: str | None) -> tuple[bytes, bytes]:
    def run(*extra: str) -> tuple[bytes, bytes]:
        base = ("pkcs12", "-passin", "env:HT_PASS", *extra)
        certs = _openssl(*base, "-nokeys", data=data, password=password)
        key = _openssl(*base, "-nocerts", "-nodes", data=data, password=password)
        return certs, key

    try:
        certs, key_pem = run()
    except CertificateError as err:
        if "password" in err.output.lower() or "mac verify" in err.output.lower():
            raise CertificateError("wrong_password", "Wrong password.") from None
        try:  # older files use algorithms OpenSSL 3 keeps in its legacy provider
            certs, key_pem = run("-legacy")
        except CertificateError:
            raise CertificateError("unreadable", err.message) from None
    # openssl prints the key with "Bag Attributes"; keep only the PEM block
    return certs, _plain_key(key_pem, None)


def _der_cert(data: bytes) -> bytes:
    try:
        return _openssl("x509", "-inform", "DER", "-outform", "PEM", data=data)
    except CertificateError:
        raise CertificateError("unreadable", "Certificate not readable.") from None


def _plain_key(data: bytes, password: str | None) -> bytes:
    args: Sequence[str] = ("pkey", "-passin", "env:HT_PASS", "-outform", "PEM")
    if not _looks_pem(data):
        args = (*args, "-inform", "DER")
    try:
        return _openssl(*args, data=data, password=password)
    except CertificateError as err:
        lowered = err.output.lower()
        if "bad decrypt" in lowered or "password" in lowered:
            raise CertificateError("wrong_password", "Wrong password.") from None
        raise CertificateError("unreadable", "Key not readable.") from None
