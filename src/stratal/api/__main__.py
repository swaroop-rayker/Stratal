"""Run the API: ``python -m stratal.api``.

Started this way, not through the ``uvicorn`` command, so that logging is
configured first and uvicorn's own lines come out as JSON too (NFR-10).
"""

import uvicorn

from stratal.api.app import create_app
from stratal.core.logging import configure_logging
from stratal.core.settings import get_settings


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    uvicorn.run(
        create_app(),
        host=settings.api_host,
        port=settings.api_port,
        log_config=None,
        access_log=True,
    )


if __name__ == "__main__":
    main()
