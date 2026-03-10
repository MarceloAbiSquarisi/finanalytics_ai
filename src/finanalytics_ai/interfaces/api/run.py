"""Entrypoint da API. Rodar com: python -m finanalytics_ai.interfaces.api.run"""

import uvicorn

from finanalytics_ai.logging_config import configure_logging

configure_logging()
from finanalytics_ai.interfaces.api.app import create_app

app = create_app()

if __name__ == "__main__":
    uvicorn.run("finanalytics_ai.interfaces.api.run:app", host="0.0.0.0", port=8000, reload=True)
