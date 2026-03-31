import pathlib, ast

f = pathlib.Path('src/finanalytics_ai/interfaces/api/dependencies.py')
original = f.read_text(encoding='utf-8')

NEW_HEADER = '''"""
Injecao de Dependencia para rotas FastAPI.
"""

from collections.abc import AsyncGenerator

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from finanalytics_ai.application.services.event_processor import EventProcessor
from finanalytics_ai.application.services.portfolio_service import PortfolioService
from finanalytics_ai.application.services.watchlist_service import WatchlistService
from finanalytics_ai.domain.auth.entities import User
from finanalytics_ai.infrastructure.adapters.brapi_client import BrapiClient
from finanalytics_ai.infrastructure.adapters.cvm_client import CvmClient, get_cvm_client
from finanalytics_ai.infrastructure.adapters.dados_mercado_client import (
    DadosDeMercadoClient,
    get_dados_mercado_client,
)
from finanalytics_ai.infrastructure.adapters.focus_client import FocusClient, get_focus_client
from finanalytics_ai.infrastructure.database.connection import get_session_factory
from finanalytics_ai.infrastructure.database.repositories.event_store_repo import SQLEventStore
from finanalytics_ai.infrastructure.database.repositories.portfolio_repo import (
    SQLPortfolioRepository,
)

EventProcessorService = EventProcessor

'''

body_start = original.find('# OAuth2PasswordBearer')
body = original[body_start:]
result = NEW_HEADER + body
f.write_text(result, encoding='utf-8', newline='\n')
try:
    ast.parse(result)
    print('OK - sintaxe valida')
except SyntaxError as e:
    print(f'ERRO: {e}')