# Alembic Audit — 02/mai/2026

> Trigger: bug encontrado em 02/mai pré-smoke onde migration `0025_b3_delisted_tickers` estava marcada em `alembic_version` mas a tabela física não existia (provável `alembic stamp` em vez de `upgrade`). Audit completo executado pra detectar outros casos similares.

## Estado canônico atual (02/mai)

| DB | alembic_version | head do branch | Mecanismo de DDL |
|---|---|---|---|
| Postgres principal (`finanalytics`) | `0025_b3_delisted_tickers` + `ts_0004` (2 rows) | `0025_b3_delisted_tickers` | Alembic `alembic/versions/0xxx_*.py` |
| Timescale (`market_data`) | **não tem tabela `alembic_version`** | `ts_0004` (registry-only) | Init scripts `init_timescale/*.sql` |

## Achados

### 1. ⚠️ Tabelas `robot_*` zumbi em Postgres (resolved 02/mai)

- `ts_0004_robot_trade.py` rodou DDL `CREATE TABLE robot_strategies/signals_log/orders_intent/risk_state` que **deveria ir pro Timescale** (foi escrito pra documentar o schema).
- Mas `alembic/env.py:30` aponta `sqlalchemy.url` apenas pra Postgres (`settings.database_url`). Migrations `ts_*` rodaram contra Postgres.
- Resultado: 4 tabelas zumbi em Postgres (0 rows, zero FKs), enquanto as tabelas reais em Timescale foram criadas via `init_timescale/006_robot_trade.sql`.
- **Fix aplicado**: DROP das 4 zumbi em Postgres. `robot_pair_positions` (legítima do 0024 em Postgres) preservada.

### 2. ⚠️ `alembic stamp` aconteceu em vez de `upgrade` para 0025

- `alembic_version` marcava `0025_b3_delisted_tickers` mas tabela física não existia.
- Sintoma: `\d b3_delisted_tickers` retornava "Did not find any relation".
- **Fix aplicado**: criação manual da tabela via SQL (idêntico ao `upgrade()` da migration). VARCHAR(10)→(20) ajustado pra acomodar placeholder UNK_<14 dígitos>.

### 3. ⚠️ Timescale sem `alembic_version`

- Não há controle de versão local no Timescale. Migrations `ts_*` são "registry-only" (rodam DDL contra Postgres por engano, mas o efeito útil é só atualizar `alembic_version` no Postgres).
- Tabelas Timescale reais vêm de `init_timescale/*.sql` aplicados no boot do container (`docker-entrypoint-initdb.d`).

### 4. ✓ Chain de migrations consistente

- 2 heads: `0025_b3_delisted_tickers` (ramo Postgres `0xxx`) + `ts_0004_robot_trade` (ramo `ts_xxxx`)
- Merge único em `53e92a4075c2_merge_main_and_timescale.py` (down_revision tupla `('0007', '0001_ts')`)
- 28 revision files, sem ramos órfãos.

## Recomendações operacionais

### Para criar nova migration daqui em diante

**Schema em Postgres (Postgres principal — `finanalytics` DB)**:
- Criar `alembic/versions/00XX_<name>.py` no padrão atual
- `down_revision` aponta pra última do ramo `0xxx`
- `alembic upgrade head` aplica
- ⚠️ Sempre validar com `\dt <tabela>` no Postgres pós-upgrade pra detectar `stamp` acidental

**Schema em Timescale (`market_data` DB)**:
- Criar `init_timescale/00X_<name>.sql` com DDL idempotente (`CREATE TABLE IF NOT EXISTS ...`)
- Aplicar manual com `docker exec -i finanalytics_timescale psql -U finanalytics -d market_data < init_timescale/00X_<name>.sql`
- Opcional: criar `alembic/versions/ts_00XX_<name>.py` apenas pra registrar versão no `alembic_version` Postgres (registry-only). DDL no `upgrade()` é redundante e causa zumbi em Postgres — preferível um `pass` ou comentar.

### Refactor de longo prazo (não urgente)

Para resolver de vez: implementar 2 contextos no `alembic/env.py` (`postgres` e `timescale`) com `version_table` separado em cada DB. Roda `alembic --name=postgres upgrade head` e `alembic --name=timescale upgrade head` separado. Custo ~1d. Zero benefício imediato — só vale se aparecer 3ª migration de Timescale.

## Comando de validação periódica

```bash
# 1. Heads atuais
docker exec finanalytics_api alembic heads

# 2. Tabelas zumbi em Postgres (devem ser apenas robot_pair_positions)
docker exec finanalytics_postgres psql -U finanalytics -d finanalytics -tAc \
  "SELECT table_name FROM information_schema.tables
   WHERE table_schema='public' AND table_name LIKE 'robot_%'"

# 3. Schema sanity em Timescale
docker exec finanalytics_timescale psql -U finanalytics -d market_data -c "\dt robot_*"
```
