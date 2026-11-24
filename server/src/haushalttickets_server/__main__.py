"""Command line: ``haushalttickets-server [serve|migrate|recur|token|emergency|nightly]``."""

import argparse
import getpass
import json
import sys

from .config import Settings
from .db import make_engine, make_sessionmaker, upgrade
from .services import Invalid, NotFound, create_token, revoke_token


def main(argv: list[str] | None = None) -> int:
    """Run the command line."""
    parser = argparse.ArgumentParser(prog="haushalttickets-server")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="run the API server (default)")
    sub.add_parser("migrate", help="apply database migrations")
    sub.add_parser("recur", help="create the recurring tickets that are due now")
    token = sub.add_parser("token", help="manage API tokens")
    token_sub = token.add_subparsers(dest="action", required=True)
    token_sub.add_parser("create", help="create a token").add_argument("name")
    token_sub.add_parser("revoke", help="revoke a token").add_argument("name")
    emergency = sub.add_parser("emergency", help="emergency web UI")
    emergency_sub = emergency.add_subparsers(dest="action", required=True)
    password = emergency_sub.add_parser("password", help="set the login password")
    password.add_argument("--user", help="admin (id or name); default: the only one")
    cert = emergency_sub.add_parser("cert", help="show (or create) the certificate")
    cert.add_argument("--renew", action="store_true", help="new self-signed one")
    cert.add_argument(
        "--remove-custom", action="store_true", help="delete the own certificate"
    )
    night = sub.add_parser("nightly", help="nightly AI run")
    night_sub = night.add_subparsers(dest="action", required=True)
    night_token = night_sub.add_parser(
        "token", help="create (or replace) the runner token for the AI account"
    )
    night_token.add_argument("--user", required=True, help="AI account (id or name)")
    night_sub.add_parser("revoke", help="remove the runner token")
    night_sub.add_parser("status", help="token, last run and waiting tickets")
    args = parser.parse_args(argv)
    settings = Settings()

    if args.command in (None, "serve"):
        import uvicorn

        from .app import create_app
        from .emergency import start_in_background

        app = create_app(settings)
        start_in_background(settings, app.state.sessionmaker)
        uvicorn.run(app, host=settings.host, port=settings.port)
        return 0

    if args.command == "emergency" and args.action == "cert":
        return _emergency_cert(settings, renew=args.renew, remove=args.remove_custom)

    if args.command == "migrate":
        upgrade(settings.database_url)
        return 0

    factory = make_sessionmaker(make_engine(settings.database_url))
    if args.command == "recur":
        from . import recurrence

        recurrence.TIMEZONE = settings.timezone
        with factory() as session:
            created = recurrence.run(session)
            session.commit()
        print(f"{len(created)} recurring ticket(s) created")
        return 0
    if args.command == "emergency":
        return _emergency_password(factory, args.user)
    if args.command == "nightly":
        return _nightly(factory, args)
    with factory() as session:
        try:
            if args.action == "create":
                print(create_token(session, args.name))
            else:
                revoke_token(session, args.name)
            session.commit()
        except (Invalid, NotFound) as err:
            print(err, file=sys.stderr)
            return 1
    return 0


def _nightly(factory, args: argparse.Namespace) -> int:
    from . import nightly

    with factory() as session:
        try:
            if args.action == "token":
                user, value = nightly.create_access(session, args.user)
                session.commit()
                print(value)
                print(
                    f"Runner token for {user.display_name}; any older one is void.",
                    file=sys.stderr,
                )
            elif args.action == "revoke":
                nightly.revoke_access(session)
                session.commit()
                print("Runner token removed.")
            else:
                print(json.dumps(nightly.status(session), indent=2, default=str))
        except (Invalid, NotFound) as err:
            print(err, file=sys.stderr)
            return 1
    return 0


def _emergency_password(factory, user_name: str | None) -> int:
    from .emergency import find_admin, set_password

    with factory() as session:
        try:
            user = find_admin(session, user_name)
            first = getpass.getpass(
                f"New emergency UI password for {user.display_name}: "
            )
            if first != getpass.getpass("Repeat: "):
                print("The passwords differ.", file=sys.stderr)
                return 1
            set_password(session, user, first)
            session.commit()
        except Invalid as err:
            print(err, file=sys.stderr)
            return 1
    print(f"Password set; the login acts as {user.display_name}. Open sessions ended.")
    return 0


def _emergency_cert(settings: Settings, *, renew: bool, remove: bool) -> int:
    from . import tls

    try:
        if remove:
            tls.remove_custom(settings.tls_dir)
            print("Own certificate removed.")
        if renew:
            tls.create_self_signed(settings.tls_dir)
        info = tls.active(settings.tls_dir)
    except tls.CertificateError as err:
        print(err, file=sys.stderr)
        return 1
    kind = "own" if info.custom else "self-signed"
    print(f"Certificate ({kind}): {info.cert}")
    print(f"  Subject:     {info.subject}")
    print(f"  Issuer:      {info.issuer}")
    print(f"  Valid:       {info.not_before:%Y-%m-%d} to {info.not_after:%Y-%m-%d}")
    print(f"  Names:       {', '.join(info.names) or '-'}")
    print(f"  SHA-256:     {info.fingerprint}")
    for warning in info.warnings():
        print(f"  Warning:     {warning}")
    if remove or renew:
        print("Restart the server to serve it: systemctl restart haushalttickets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
