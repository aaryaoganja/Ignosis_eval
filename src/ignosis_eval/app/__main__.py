"""`python -m ignosis_eval.app`: serve the review app on 0.0.0.0:$PORT (Railway sets PORT; default 8000)."""

from __future__ import annotations

import logging
import os


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "0.0.0.0")  # noqa: S104 - a container must listen on all interfaces
    uvicorn.run("ignosis_eval.app.server:create_app", factory=True, host=host, port=port,
                proxy_headers=True, forwarded_allow_ips="*", log_level="info")


if __name__ == "__main__":
    main()
