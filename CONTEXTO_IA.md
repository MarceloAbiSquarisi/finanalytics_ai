cld# Contexto do Projeto — finanalytics_ai
> Cole este arquivo no início de uma nova conversa para retomar de onde paramos.

## Projeto
- **Nome:** finanalytics_ai_fresh
- **Caminho:** `D:\Projetos\finanalytics_ai_fresh`
- **Stack:** Python 3.12 · FastAPI · PostgreSQL · SQLAlchemy 2.x async · asyncpg · uv · Docker Compose
- **Ambiente:** Windows 11 · PowerShell 7.5 · Python 64-bit · Docker rodando localmente

## Infraestrutura Docker (todos rodando)
| Container | Imagem | Porta |
|---|---|---|
| finanalytics_postgres | postgres:16-alpine | 5432 |
| finanalytics_timescale | timescaledb:latest-pg16 | 5433 |
| finanalytics_redis | redis:7.2-alpine | 6379 |
| finanalytics_kafka | cp-kafka:7.6.1 | 9092 |
| finanalytics_api | finanalytics-ai:latest | 8000 |
| finanalytics_worker | finanalytics-worker:latest | — |
| finanalytics_scheduler | finanalytics-worker:latest | — |

## .env (variáveis-chave)
```
DATABASE_URL=postgresql+asyncpg://finanalytics:secret@localhost:5432/finanalytics
APP_SECRET_KEY=dev-secret-key-local-alembic
FINTZ_API_KEY=<chave nova com plano backtest/quant — já funcional>
FINTZ_BASE_URL=https://api.fintz.com.br
```

## O que foi implementado

### Pipeline de ingestão Fintz
**Arquivos novos:**
- `src/finanalytics_ai/domain/fintz/__init__.py`
- `src/finanalytics_ai/domain/fintz/entities.py` — TypedDict rows, FintzDatasetSpec, ALL_DATASETS (80 datasets)
- `src/finanalytics_ai/domain/fintz/ports.py` — Protocol FintzRepository
- `src/finanalytics_ai/infrastructure/adapters/fintz_client.py` — cliente HTTP async + retry + hash SHA-256
- `src/finanalytics_ai/infrastructure/database/repositories/fintz_repo.py` — upsert chunked ON CONFLICT
- `src/finanalytics_ai/application/services/fintz_sync_service.py` — orquestração asyncio.Semaphore(5)
- `src/finanalytics_ai/workers/fintz_sync_worker.py` — loop async às 22h05 BRT
- `alembic/versions/0004_fintz.py` — 4 tabelas iniciais
- `alembic/versions/0005_fintz_schema_fix.py` — correção de schema após inspeção real dos parquets
- `scripts/validar_carga_fintz.py` — rotina de validação da carga
- `scripts/inspecionar_schema_fintz.py` — inspeção de schema dos parquets
- `tests/unit/application/test_fintz_sync_service.py`
- `tests/unit/domain/test_fintz_entities.py`
- `tests/unit/infrastructure/test_fintz_client.py`

**Arquivos modificados:**
- `src/finanalytics_ai/config.py` — 6 campos `fintz_*`
- `src/finanalytics_ai/exceptions.py` — FintzAPIError, FintzParseError, FintzSyncError
- `src/finanalytics_ai/metrics.py` — 5 contadores Prometheus fintz_sync_*
- `.env.example` — bloco Fintz documentado
- `docker-compose.yml` — serviço fintz_sync_worker corrigido (estava em volumes, movido para services)
- `src/finanalytics_ai/interfaces/api/routes/alerts.py` — removido from __future__ + TYPE_CHECKING
- `src/finanalytics_ai/interfaces/api/static/backtest.html` — corrigidos erros de sintaxe JS

### Schema real dos parquets Fintz (verificado em 2026-03-20)
| Dataset | Colunas principais |
|---|---|
| cotacoes | 14 colunas snake_case: data, ticker, preco_abertura, preco_fechamento, preco_maximo, preco_medio, preco_minimo, quantidade_negociada, quantidade_negocios, volume_negociado (float64), fator_ajuste, preco_fechamento_ajustado, fator_ajuste_desdobramentos, preco_fechamento_ajustado_desdobramentos |
| item_contabil | 4 colunas: ticker, item, data, valor (sem ano/trimestre/tipoDemonstracao) |
| indicador | 4 colunas: ticker, indicador, data, valor |

### Migrations executadas
```
0001_baseline
0002_portfolio_multi
0003_password_reset
0004_fintz           ← 4 tabelas Fintz criadas
0005_fintz_schema_fix ← schema corrigido após inspeção real
```

### Testes unitários
```
28/28 passando
uv run python -m pytest tests/unit/domain/test_fintz_entities.py tests/unit/infrastructure/test_fintz_client.py tests/unit/application/test_fintz_sync_service.py -v
```

## Status atual

### ✅ Funcionando
- Pipeline Fintz completo — carga histórica OK
- Tabelas `fintz_cotacoes`, `fintz_itens_contabeis`, `fintz_indicadores`, `fintz_sync_log` populadas
- `fintz_indicadores` com ~99M linhas (35 datasets ok no sync_log)
- API rodando em `http://localhost:8000`
- Backtesting funcionando em `http://localhost:8000/backtest`
- Todas as páginas HTML acessíveis sem `.html` (ex: `/hub`, `/screener`, `/backtest`)

### ⚠️ Pendente / Conhecido
- **`/docs` (Swagger)** — `Internal Server Error /openapi.json` por causa de `from __future__ import annotations` em múltiplas rotas. Não bloqueia funcionalidade. Correção: remover o `from __future__` de todas as rotas com modelos Pydantic.
- **45 datasets Fintz com erro** na carga inicial (cotacoes_ohlc e itens_contabeis falharam). Rodar novamente após limpar o sync_log de erros.
- **`fintz_cotacoes` vazia** — o dataset de cotações OHLC falhou por erro de tipo. Schema foi corrigido em 0005 mas a recarga ainda não foi executada.

### ProfitDLL (Nelogica) — em avaliação
- Profit Pro instalado em `C:\Users\marce\AppData\Roaming\Nelogica\Profit\profitchart.exe`
- Python 64-bit → precisaria da `ProfitDLL64.dll`
- Corretora: Clear/XP
- **Arquitetura já mapeada** (3 sprints: histórico → tempo real → ordens)

## Próximos passos (ordem sugerida)
1. **Recarregar datasets Fintz com erro** — limpar sync_log e rodar RUN_ONCE
2. **Corrigir /docs** — remover `from __future__ import annotations` das rotas
3. **Conectar backtesting aos dados Fintz** — atualmente usa Yahoo Finance; redirecionar para `fintz_cotacoes`
4. **ProfitDLL** — decidir se assina e implementar adapter

## Endpoints Fintz mapeados
| Dataset | Endpoint |
|---|---|
| Cotações OHLC | `/bolsa/b3/avista/cotacoes/historico/arquivos` |
| Itens contábeis PIT | `/bolsa/b3/avista/itens-contabeis/point-in-time/arquivos` |
| Indicadores PIT | `/bolsa/b3/avista/indicadores/point-in-time/arquivos` |

## URLs da aplicação
```
http://localhost:8000/hub
http://localhost:8000/backtest
http://localhost:8000/screener
http://localhost:8000/dashboard
http://localhost:8000/health
```

## Comandos úteis
```powershell
# Subir containers
cd D:\Projetos\finanalytics_ai_fresh
docker compose up -d

# Rebuildar API após mudanças no código
docker compose build api
docker compose up -d api

# Copiar arquivo HTML para o container sem rebuild
docker cp "D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai\interfaces\api\static\backtest.html" finanalytics_api:/app/src/finanalytics_ai/interfaces/api/static/backtest.html

# Recarregar datasets Fintz com erro
docker exec -it finanalytics_postgres psql -U finanalytics -d finanalytics -c "DELETE FROM fintz_sync_log WHERE status = 'error';"
$env:RUN_ONCE = "true"
uv run python -m finanalytics_ai.workers.fintz_sync_worker

# Rodar migration
uv run alembic upgrade head

# Verificar migration atual
uv run alembic current

# Rodar testes Fintz
uv run python -m pytest tests/unit/domain/test_fintz_entities.py tests/unit/infrastructure/test_fintz_client.py tests/unit/application/test_fintz_sync_service.py -v

# Monitorar carga Fintz
docker exec -it finanalytics_postgres psql -U finanalytics -d finanalytics -c "
SELECT 'cotacoes' AS tabela, COUNT(*) AS linhas FROM fintz_cotacoes
UNION ALL SELECT 'itens_contabeis', COUNT(*) FROM fintz_itens_contabeis
UNION ALL SELECT 'indicadores', COUNT(*) FROM fintz_indicadores
UNION ALL SELECT 'sync_log ok', COUNT(*) FROM fintz_sync_log WHERE status = 'ok'
UNION ALL SELECT 'sync_log error', COUNT(*) FROM fintz_sync_log WHERE status = 'error';"

# Logs da API
docker logs finanalytics_api --tail 30

# Carregar FINTZ_API_KEY no terminal
$env:FINTZ_API_KEY = (Select-String "FINTZ_API_KEY" "D:\Projetos\finanalytics_ai_fresh\.env" | ForEach-Object { $_.Line.Split("=")[1].Trim() } | Select-Object -Last 1)
```
