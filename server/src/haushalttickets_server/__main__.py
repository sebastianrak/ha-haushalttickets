"""Entry point: ``haushalttickets-server``."""

import uvicorn

from .app import create_app
from .config import Settings


def main() -> None:
    """Run the server."""
    settings = Settings()
    uvicorn.run(create_app(), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
