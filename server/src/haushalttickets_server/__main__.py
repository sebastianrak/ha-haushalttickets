"""Command line: ``haushalttickets-server [serve|migrate|token ...]``."""

import argparse
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
    token = sub.add_parser("token", help="manage API tokens")
    token_sub = token.add_subparsers(dest="action", required=True)
    token_sub.add_parser("create", help="create a token").add_argument("name")
    token_sub.add_parser("revoke", help="revoke a token").add_argument("name")
    args = parser.parse_args(argv)
    settings = Settings()

    if args.command in (None, "serve"):
        import uvicorn

        from .app import create_app

        uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
        return 0

    if args.command == "migrate":
        upgrade(settings.database_url)
        return 0

    factory = make_sessionmaker(make_engine(settings.database_url))
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


if __name__ == "__main__":
    sys.exit(main())
