# Proposta — Novo `§14 Estado Atual` do CLAUDE.md

**Autor da proposta:** Claude (sessão de verificação de hardware de 16/abr/2026)
**Contexto:** o `§14` vigente do `CLAUDE.md v1.3` afirma que "SPRINT ATUAL: S01 (pendente de início)" e "SPRINTS CONCLUÍDOS: nenhum", o que contradiz o estado real da estação de dev — um stack FinAnalyticsAI em produção com 545 milhões de linhas de histórico de trades, ingestão diária funcional, API REST em uso e backup automatizado rodando há semanas. Esta proposta consolida a Era 4 do projeto (iteração ativa) como baseline explícito.

**Status do documento:** DRAFT. Não substitui o `§14` vigente até aprovação explícita do usuário. Ao aprovar, substituir in-place no `CLAUDE.md` (raiz do repo `D:\Projetos\finanalytics_ai_fresh\` e/ou versão de trabalho em `D:\Investimentos\FinAnalytics_AI\Melhorias\`, conforme a fonte de verdade escolhida — ver nota no final).

---

## 14. Estado Atual do Projeto

```
SPRINT ATUAL:       transição S02 → S03 (infra + ingestão operacionais; ML pendente)
FASE ATUAL:         1–2 parciais (Fundação + Ingestão em produção; XGBoost/FinBERT não iniciados)
ÚLTIMA ATUALIZAÇÃO: 16/abr/2026 (reescrita pós-diagnóstico de hardware e auditoria do stack Era 4)

SPRINTS — STATUS HONESTO

  S01 — Infraestrutura base — PARCIAL
    [x] Docker Compose estruturado (docker-compose.yml + override tuneado)
    [x] TimescaleDB (PostgreSQL 15+ext) rodando na porta 5433, DB 'market_data'
    [x] Postgres OLTP rodando na porta 5432, DB 'finanalytics'
    [x] Redis 7.2 rodando na porta 6379 com maxmemory 4GB
    [x] Kafka 7.6.1 Confluent (com Zookeeper, NÃO Kraft) na porta 9092
    [x] Schemas Postgres e TimescaleDB criados via init/ + alembic migrations
    [x] Tópicos Kafka com auto-create habilitado (retenção 168h = 7 dias)
    [x] Healthchecks em todos os serviços core (Postgres, TimescaleDB, Kafka, Redis, API)
    [ ] ChromaDB — não implantado
    [ ] MinIO — não implantado
    [ ] MLflow — não implantado
    [ ] Airflow — não implantado (scheduling atual via scheduler_worker.py próprio)
    [ ] Prometheus — não implantado (Grafana em uso como dashboard standalone)

  S02 — Ingestão histórica — SUBSTANCIALMENTE CONCLUÍDO
    [x] Scheduler diário de OHLCV 07:00 BRT (scheduler_worker.py)
    [x] Ingestão B3: 1.858 tickers (BDRs, ETFs, ações) via BRAPI + Yahoo + Fintz fallback
    [x] Ingestão intra-dia de ticks via profit_* (Profit DDE/RTD)
    [x] Ingestão Fintz: dados fundamentalistas (itens contábeis, indicadores, cotações)
    [x] Backup diário automatizado Postgres + TimescaleDB (retenção 7 dias)
    [ ] FRED API (macro USA) — não integrado (projeto pivotou para mercado BR)
    [ ] Validação automatizada de gaps, nulos, splits e dividendos — a confirmar

  S03 — HMM detecção de regime — NÃO INICIADO
  S04 — Backtesting framework — NÃO INICIADO (só scripts pontuais em scripts/)
  S05 a S08 (FASE 2 — XGBoost + FinBERT) — NÃO INICIADOS
  S09 a S12 (FASE 3 — LSTM/TFT Volatilidade) — NÃO INICIADOS
  S13 a S16 (FASE 4 — GNN 100 Ativos) — NÃO INICIADOS
  S17 a S20 (FASE 5 — DRL + Dados Alternativos) — NÃO INICIADOS
  S21 a S24 (FASE 6 — Dados Sintéticos + Produção) — NÃO INICIADOS

AMBIENTE DA ESTAÇÃO DE DEV

  Host:              Windows 11 Pro 25H2, build 26200.8037, canal GA
  Motherboard:       Gigabyte Z790 AORUS XTREME X
  CPU:               Intel Core i9-14900K (24C/32T, base 3,2 GHz, L3 36 MB)
  RAM:               192 GB DDR5 (4× Corsair CMH96GX5M2B5600C40 48 GB)
                     • rating do kit: 5600 CL40 (XMP/EXPO)
                     • rodando atualmente a: 4000 MHz (XMP desabilitado) — ver P11
  Storage:
    • Corsair MP700 PRO 2 TB (PCIe Gen5)    → E: 'Dados_MF'
    • Redragon Blaze 1 TB (PCIe Gen3) × 2   → C: 'Windows' e D: 'DADOS'
  GPU:               2× NVIDIA GeForce RTX 4090 (48 GB VRAM combinada)
                     • GPU 0 (PCIe bus 01:00.0): sem monitor, idle, 0 MiB — DEDICADA A COMPUTE
                     • GPU 1 (PCIe bus 08:00.0): com monitor, P0, ~2 GB — DESKTOP
                     • Ver Decisão 15 (proposta) para regra de uso
  NVIDIA driver:     591.86 (host), CUDA 13.1 reportada, compute cap 8.9 (Ada)
  Ambiente Linux:    WSL2 2.4.13.0 com Ubuntu 22.04 (kernel 5.15.167.4-1)
  Docker:            Docker Desktop 4.64.0 (Engine 29.2.1, containerd 2.2.1)
  Runtime nvidia:    integrado ao Docker Engine ✓
  Working dir:       D:\Projetos\finanalytics_ai_fresh\ (código) +
                     E:\finanalytics_data\ (dados) — NTFS, não WSL2 ext4
                     • Trade-off conhecido, ver nota sobre cross-FS no fim desta seção

ENTREGAS DE PRÉ-PROJETO (pasta Melhorias/)
  [x] Plano técnico completo — finanalyticsai_plano.docx
  [x] Solução para mercado BR — finanalytics_br_solucao.docx
  [x] Roadmap de sprints — finanalyticsai_sprints.html
  [x] Análise de hospedagem de produção (planilha + relatório) — 15/abr/2026
  [x] CLAUDE.md v1.3 com Seção 16 (decisão de infraestrutura) e Seção 17 (ordem de aplicação)
  [x] Verificação de hardware completa (verificacao_hardware_finanalyticsai.md) — 16/abr/2026
  [x] Proposta desta nova §14 (este arquivo)
  [x] Proposta Decisão 15 dual-GPU (proposta_decisao_15_dualgpu.md)
  [x] Plano operacional 17-19/abr (plano_proximos_passos_17-19abr.md)

SERVIÇOS RODANDO (16 containers ativos em docker-compose, 16/abr/2026)
  Camada de dados (operacional):
  [x] finanalytics_postgres     — DB 'finanalytics', 25 GB
  [x] finanalytics_timescale    — DB 'market_data',  88 GB
  [x] finanalytics_redis        — cache + backend de filas leves
  [x] finanalytics_zookeeper    — coordenação Kafka
  [x] finanalytics_kafka        — broker único, log retention 7 dias
  [x] finanalytics_backup       — pg_dump diário com retenção 7 dias (120 GB)

  Camada aplicação e workers:
  [x] finanalytics_api          — FastAPI REST na porta 8000 (finanalytics-ai:latest)
  [x] finanalytics_scheduler    — scheduler_worker.py (ingestão OHLCV diária 07:00 BRT)
  [x] finanalytics_worker       — event worker V1 (em migração para V2)
  [x] finanalytics_worker_v2    — event worker V2 (do compose.override, em coexistência)
  [x] finanalytics_ohlc_ingestor— QUEBRADO desde 13/abr (Kafka connection,
                                   módulos faltantes); preservado temporariamente;
                                   será removido do compose pós-consolidação
  [x] finanalytics_evolution    — Evolution API (WhatsApp notifications)

  Camada UI/observabilidade:
  [x] finanalytics_grafana      — dashboards (porta 3000)
  [x] finanalytics_pgadmin      — UI Postgres (porta 5050)
  [x] finanalytics_kafka_ui     — UI Kafka (porta 8080)
  [x] finanalytics_redisinsight — UI Redis (porta 8001)

  NÃO RODANDO (previstos no plano v1.3 para fases seguintes):
  [ ] MinIO, ChromaDB, MLflow, Airflow, Prometheus, vLLM, TorchServe

ARQUITETURA DE DADOS ATIVA
  Postgres 'finanalytics' (25 GB):
    fintz_itens_contabeis       17 GB   (fundamentos contábeis)
    fintz_indicadores           5,1 GB  (indicadores derivados)
    fintz_indicadores_dedup     1,6 GB  (deduplicado)
    fundos_informe_diario       427 MB
    ohlc_prices                 273 MB  (12.669 linhas confirmadas)
    fintz_cotacoes              224 MB
    ... + 14 tabelas menores (tickers, macro_indicators, trading_accounts, trades, events)

  TimescaleDB 'market_data' (88 GB):
    market_history_trades       85 GB   545.374.696 linhas  (trades tick-a-tick B3)
    11 hypertables ativas, incluindo:
      profit_ticks              2,4 GB  (ticks via Profit DDE/RTD)
      fintz_itens_contabeis_ts  515 MB
      fintz_cotacoes_ts         232 MB
      ohlc_1m, ohlc_bars, price_ticks, profit_order_book, ticks, ohlc (menores)

MODELOS TREINADOS
  [ ] HMM, FinBERT fine-tuned, XGBoost factor, LSTM, TFT, GAT, DRL (PPO), CTGAN
      — Tabelas ml_features e trades existem vazias como schema placeholder

INFRAESTRUTURA DE PRODUÇÃO
  Decisão imutável tomada em 15/abr:
    Colocation SP + Tier Pragmático a partir da Fase 4
    Ubuntu 22.04 LTS Server bare-metal (hardware NOVO, separado)
    CAPEX orçado: ~R$ 110.000
    TCO 36m: ~R$ 192.000
  Status:          não iniciado — aplicar otimizações software primeiro (§16.3)
  Revisão 16/abr:  parte das otimizações de §16.3 já foi aplicada no override
                   (shared_buffers 32GB, work_mem 256MB, jit=on, pg_stat_statements,
                   G1GC no Kafka, maxmemory Redis). Atualizar checklist.

PAPER TRADING
  Status:         não iniciado formalmente; existe dataset histórico para backtest
  Início:         —
  Dias completos: 0/60
  Sharpe:         —

LIVE TRADING
  Status:          não iniciado
  Capital alocado: 0%

OTIMIZAÇÕES DE SOFTWARE PRÉ-UPGRADE (Seção 16.3) — STATUS ATUALIZADO
  Já aplicadas (parcial ou total):
  [x] PostgreSQL tuning (shared_buffers 32GB, work_mem 256MB, jit=on)
  [x] TimescaleDB tuning (max_background_workers=16)
  [x] pg_stat_statements habilitado
  [x] Redis maxmemory + I/O threads
  [x] Kafka G1GC + heap 4GB
  [x] Connection pooling (DATABASE_POOL_SIZE=20, MAX_OVERFLOW=40 na API)
  [x] Compressão TimescaleDB (hypertable policies ativas — visível em
      compress_hyper_* chunks)
  [x] Backup automatizado com retenção 7 dias

  Pendentes:
  [ ] Quantização GGUF q4_K_M do FinGPT-8B (modelo não carregado)
  [ ] Mixed Precision FP16 em treinos (treinos não implementados)
  [ ] Flash Attention 2 no LLM (LLM não em uso)
  [ ] vLLM PagedAttention (vLLM não implantado)
  [ ] Gradient Checkpointing no GNN (GNN não implementado)
  [ ] INT8 quantization no FinBERT (FinBERT não implementado)
  [ ] Feature store Redis quente + Parquet frio (arquitetura a definir)
  [ ] Batch dinâmico no TorchServe (TorchServe não implantado)
  [ ] Pipelines async (asyncio adotado, aiokafka parcial)
  [ ] Compressão Zstandard em Kafka/MLflow (MLflow ausente; Kafka default)
  [ ] pgBouncer (não implantado; pool nativo do asyncpg está em uso)
  [ ] VRAM Manager com alertas (não implementado)
  [ ] VPS SP + WireGuard para agente de execução (Fase 3, não iniciado)
  [ ] Profiling PyTorch Profiler + py-spy (não iniciado, sem código PyTorch)
  [ ] Prometheus + Grafana + Loki (Grafana standalone em uso)
  [ ] Avaliação de IC real por modelo (modelos ainda não existem)

PENDÊNCIAS DE HARDWARE/INFRA DETECTADAS EM 16/abr (ver verificacao_hardware_finanalyticsai.md)
  P1   — .wslconfig vazio; WSL2 usando defaults do Win11 (~64 GB teto de RAM).
         Ação: popular com memory=128GB, swap=32GB em E:, autoMemoryReclaim=gradual,
         sparseVhd=true. Requer wsl --shutdown.
  P11  — RAM rodando a 4000 MHz; kit rated 5600 CL40. Habilitar XMP/EXPO na BIOS;
         se instável com 4 DIMMs, cair para 5200/5400. Requer reboot + BIOS.
  P12  — VHDX do Ubuntu-22.04 e do docker-desktop moram em C:. Migrar para E:
         via Docker Desktop → Resources → Advanced e wsl --export/--import.
         Requer wsl --shutdown + reconfiguração.
  P16  — Decisão formal colocation vs VPN-only na Fase 4 (gate do Bloco 6 de §17).
  P17  — Stack Era 4 vivo em paralelo ao plano v1.3; esta nova §14 resolve.
  P18  — Docker Desktop consumindo ~181 GB em C: (132 GB reclaimable após §17
         aprovada). Plano: pg_dump + image prune + builder prune após ingestão.
  P21  — Backup de 12-13/abr não rodou (incidente correlacionado com falha do
         finanalytics_ohlc_ingestor). Adicionar healthcheck + alerta.
  P22  — Containers de compute não têm GPU reservation; CUDA_VISIBLE_DEVICES=1
         aponta para GPU desktop (errada). Cabos de monitor foram trocados em
         algum momento, invertendo mapeamento lógico vs físico. Ver Decisão 15
         proposta no arquivo proposta_decisao_15_dualgpu.md.

NOTAS
  16/abr/2026 — Reescrita da §14 após auditoria completa do stack Era 4.
    Principais mudanças vs §14 anterior:
    • Reconhece 16 containers em produção em vez de "S01 pendente".
    • Documenta o pivô para mercado BR (B3/BRAPI/Fintz/Profit) vs plano USA original.
    • Separa "infra+ingestão construída" (Fases 1-2 parciais) de "ML pendente"
      (Fases 2-6 restantes).
    • Desmascara o mito do volume 'finanalytics_ai_fresh_pgdata' (37 GB órfão)
      vs o dado real em E:\finanalytics_data\docker\ (245 GB).
    • Registra P21 (backup incidente) e P22 (GPU mapping) como pendências novas.
    • Atualiza checklist de otimizações de §16.3 reconhecendo o que o override
      já entrega (pg tuning, pool, compressão TimescaleDB).

  16/abr/2026 — Correção de SO e ajustes de hardware (já aplicados em v1.3):
    Windows 11 Pro 25H2 GA confirmado; i9-14900K + 192 GB + RTX 4090 × 2 +
    Corsair MP700 PRO Gen5 confirmados. Hyper-V ativo. Recursos do Windows OK.
    Limpeza de 4 distros WSL2 órfãs concluída (−149 GB). Limpeza de Era 1
    (DSA + Ollama) e Era 2 (finanalytics-platform-*, finanalytics-observability-*,
    finanalytics-full_*) concluída (−19 GB imediatos + ~30 GB represados no
    build cache que serão liberados no pacote pós-ingestão).
```

---

## Nota sobre "fonte de verdade" do `CLAUDE.md`

O diagnóstico encontrou **dois** arquivos `CLAUDE.md` distintos:

1. `D:\Investimentos\FinAnalytics_AI\Melhorias\CLAUDE.md` — o "v1.3" forward-looking, escrito em 16/abr, que assume greenfield.
2. `D:\Projetos\finanalytics_ai_fresh\CLAUDE.md` — o do próprio repositório do projeto, última modificação 14/abr, provavelmente mais próximo do estado real (não foi lido nesta sessão).

**Pendência:** decidir qual é a fonte de verdade. Três opções:

- **(i)** Manter o da pasta `Melhorias/` como "documento de arquitetura/roadmap" e o do repo como "briefing operacional do Claude Code". Cada um no seu lugar.
- **(ii)** Consolidar em um só — manter no repo e sumir com o de `Melhorias/` após extrair o conteúdo estratégico.
- **(iii)** Manter os dois sincronizados com um script, com seções marcadas de "canônico no repo" / "canônico em Melhorias".

Recomendo **(i)** com uma linha de cabeçalho em cada `CLAUDE.md` informando o papel dele e referenciando o outro. Evita divergência sem criar dependência frágil.

Assim que você decidir, aplico a versão do `§14` acima no arquivo canônico que você apontar.

---

## O que falta fazer nesta §14 proposta

**Incerteza minha:** algumas linhas deste draft são projeção razoável, não fato verificado. Especificamente:

- "Ingestão intra-dia de ticks via profit_* (Profit DDE/RTD)" — inferi pela existência de tabelas `profit_ticks`, `profit_daily_bars`, `profit_order_book` no TimescaleDB. Pode ser outra fonte.
- Sprint S04 "só scripts pontuais em scripts/" — inferi pela existência da pasta `scripts/`; preciso ler o conteúdo para confirmar.
- Sprint S02 itens de validação (gaps, nulos, splits) — não auditado; marquei como "a confirmar".

Quando você ler este draft, me corrija onde estiver errado. Edito e submeto.
