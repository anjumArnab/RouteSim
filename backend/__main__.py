"""Run the RouteSim server: `python -m backend`."""

import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from backend.app import FRONTEND_DIST, create_app
from backend.config import ConfigError, Settings


def main() -> None:
    # Real environment variables take precedence over the .env file.
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    try:
        settings = Settings.from_env()
    except ConfigError as error:
        sys.exit(f"Configuration error: {error}")

    if not FRONTEND_DIST.is_dir():
        print("Dashboard not built (frontend/dist missing). Serving API and MCP only.")
    print(f"RouteSim MCP endpoint: http://{settings.host}:{settings.port}/mcp", flush=True)

    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        # Open dashboard streams must not block shutdown.
        timeout_graceful_shutdown=3,
    )


if __name__ == "__main__":
    main()
