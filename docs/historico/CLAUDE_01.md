# FinAnalyticsAI — CLAUDE.md
<!-- Leia este arquivo COMPLETAMENTE antes de qualquer ação. Atualize a seção "Estado Atual" ao final de cada sprint. -->

---

## Como usar este arquivo

Este arquivo é o contexto completo do projeto FinAnalyticsAI para o Claude Code.
Leia-o do início ao fim antes de qualquer tarefa. Ao iniciar uma sessão, diga qual sprint deseja executar.
Ao concluir um sprint, atualize a seção **Estado Atual** antes de encerrar.

---

## 1. Identidade e Objetivo do Projeto

**Nome:** FinAnalyticsAI
**Objetivo:** Sistema de geração de alpha quantitativo baseado em IA, rodando 100% on-premise.
**Meta de performance:** Sharpe anualizado > 2,0 sustentado em 90 dias de live trading.
**Universo:** 100 ativos selecionados por liquidez e diversificação setorial.
**Filosofia:** on-premise first — nenhum serviço externo pago sem validação de ROI e sem API key fornecida pelo usuário.

---

## 2. Hardware Disponível

```
GPU:  NVIDIA RTX 4090 — 24 GB GDDR6X — 82,6 TFLOPS FP32 — dedicada sem monitores
RAM:  196 GB DDR5
CPU:  (atualizar com specs reais ao iniciar)
NVMe: (atualizar com espaço disponível ao iniciar)
OS:   Windows 11 Pro (host) + WSL2 com Ubuntu 22.04 LTS (ambiente Linux de trabalho)
      → Windows 11 é o SO host desta estação e NÃO será substituído nem
        posto em dual-boot. Todo o stack Linux (Docker, Kafka, TimescaleDB,
        Airflow, scripts bash, CLIs *.sh) roda dentro do WSL2 Ubuntu 22.04.
      → PyTorch/CUDA funcionam nativamente no Windows e também via WSL2;
        por padrão deste projeto, treinar dentro do WSL2 para paridade com
        o servidor de produção (Ubuntu Server — Seção 16.4).
CUDA: 12.1+ — Driver NVIDIA para Windows 535+ (ou superior) com suporte a
      WSL2 GPU passthrough (CUDA no WSL2 usa o driver do Windows — NÃO
      instalar driver NVIDIA dentro do Ubuntu do WSL2)
Docker: Docker Desktop 4.26+ para Windows com backend WSL2 habilitado e
        NVIDIA Container Toolkit instalado dentro da distro Ubuntu do WSL2
Terminal: Windows Terminal + PowerShell (host) e bash do WSL2 (trabalho)
```

### Notas de ambiente Windows 11 + WSL2 (LER ANTES DE TUDO)
- **Shell padrão** para todos os comandos deste CLAUDE.md: **bash do WSL2** (Ubuntu).
  Quando um comando precisar rodar no Windows host (ex.: `nvidia-smi.exe`,
  Docker Desktop, `wsl --shutdown`), isso será explicitamente indicado com
  prefixo `# [Windows host]`.
- **Caminho de trabalho recomendado:** `~/finanalyticsai/` dentro do WSL2
  (equivale a `\\wsl$\Ubuntu\home\<usuario>\finanalyticsai\` visto pelo Windows).
  NÃO usar `/mnt/c/...` para dados de treino, pois há penalidade significativa
  de I/O no acesso cross-filesystem entre WSL2 e NTFS.
- **GPU no WSL2:** validar com `nvidia-smi` dentro do WSL2 (usa o driver do
  Windows via passthrough). `nvidia-smi` do WSL2 e `nvidia-smi.exe` do host
  devem reportar a mesma GPU e mesmo uso de VRAM.
- **Git/fim-de-linha:** configurar `git config --global core.autocrlf input`
  no git do WSL2 para manter LF em scripts bash, YAML e arquivos Python.
- **systemd no WSL2:** habilitar em `/etc/wsl.conf` com
  `[boot]\nsystemd=true` e reiniciar o WSL2 (`wsl --shutdown` no PowerShell)
  para suportar cron, systemd-journald, Docker socket de sistema etc.
- **Recursos do Windows necessários:** "Subsistema do Windows para Linux",
  "Plataforma de Máquina Virtual" e "Hyper-V" (se disponível). Docker Desktop
  usa esses recursos e não coexiste com VMware Workstation antigo no mesmo host.
- **Memória do WSL2:** criar `C:\Users\<usuario>\.wslconfig` com
  `[wsl2]\nmemory=160GB\nprocessors=auto\nswap=32GB` para permitir uso
  pesado sem travar o Windows (ajustar conforme specs reais).

### Regras de uso de GPU
- **Horário de mercado (09h–18h):** apenas inferência — LLM 8B (~6 GB) + FinBERT (~2 GB) + LSTM (~1 GB) = ~9 GB
- **Off-hours (00h–07h):** treinamento sequencial — NUNCA treinar e inferir ao mesmo tempo
- **GNN (100 ativos):** único módulo que roda DURANTE o pregão (~2 GB) — treino às 12h
- **Limite seguro:** nunca ultrapassar 20 GB de VRAM total (deixar 4 GB de headroom)

---

## 3. Decisões Arquiteturais — IMUTÁVEIS

Estas decisões foram validadas e não devem ser revertidas sem aprovação explícita do usuário:

1. **100 ativos** no universo — não expandir sem validar VRAM do GNN
2. **GNN usa GAT** (Graph Attention Network) com neighbor sampling GraphSAGE — não GCN denso
3. **LLM local: FinGPT-8B** em GGUF q4_K_M (~6 GB VRAM) — não Llama 70B para inferência contínua
4. **LLM 70B** somente em batch off-hours com GGUF IQ2_M (~21 GB) se necessário
5. **Mixed Precision FP16** em TODOS os treinos de modelos PyTorch
6. **torch.compile()** em modelos de inferência (LSTM, FinBERT)
7. **Flash Attention 2** no LLM local
8. **Kafka** para streaming em tempo real — Airflow para batch agendado (nunca misturar)
9. **TimescaleDB** para todas as séries temporais — nunca usar SQLite ou CSV em produção
10. **MLflow self-hosted** para TODOS os experimentos — nenhum treino sem logging
11. **Kelly Fracionário** com fração 0,5 e cap máximo de 20% por posição
12. **Threshold de correlação GNN:** |r| > 0,35 em janela rolante de 60 dias
13. **SO da estação de dev:** Windows 11 Pro com WSL2 Ubuntu 22.04 — NÃO substituir por Ubuntu bare-metal nem criar dual-boot
14. **SO do servidor de produção:** Ubuntu 22.04 LTS Server bare-metal em colocation SP (hardware novo, separado — Seção 16.4)

---

## 4. Stack Tecnológica por Camada

### Dados e Streaming
| Componente | Versão | Uso |
|---|---|---|
| Apache Kafka | 3.6+ (Kraft mode) | Event streaming de mercado |
| TimescaleDB | PostgreSQL 15 + ext | Séries temporais OHLCV e features |
| Redis | 7.x | Cache de features e sinais em tempo real |
| MinIO | Latest | Object storage para modelos e documentos |
| ChromaDB | 0.4.x | Banco vetorial para embeddings textuais |
| Apache Airflow | 2.8.x | Orquestração de jobs batch |

### Machine Learning
| Componente | Versão | Uso |
|---|---|---|
| PyTorch | 2.1+ (CUDA 12.1) | Base de todos os modelos |
| PyTorch Lightning | 2.x | LSTM/TFT training |
| PyTorch Geometric | 2.4+ | GNN/GAT |
| HuggingFace Transformers | 4.36+ | FinBERT |
| XGBoost | 2.x | Factor model |
| hmmlearn | 0.3+ | Detecção de regime |
| Stable-Baselines3 | 2.x | DRL PPO/SAC |
| Ray RLlib | 2.9+ | Treinamento distribuído DRL |
| MLflow | 2.9+ | Experimentos e model registry |
| SDV / CTGAN | 1.x | Dados sintéticos |

### Serving e Produção
| Componente | Versão | Uso |
|---|---|---|
| vLLM | Latest | Serving do LLM local (FinGPT-8B) |
| TorchServe | 0.9+ | Serving FinBERT + LSTM |
| Grafana | 10.x | Dashboards de monitoramento |
| Prometheus | 2.48+ | Métricas e alertas |
| Kubernetes / k3s | 1.29 | Orquestração em produção (Fase 6) |

---

## 5. Mapa de Serviços e Portas

Todos os serviços rodam dentro do WSL2 (via Docker Desktop com backend WSL2).
As portas ficam acessíveis tanto no `localhost` do WSL2 quanto no `localhost`
do Windows (Docker Desktop faz port-forward automático entre WSL2 e o host).

```
TimescaleDB   → localhost:5432  (user: finai, db: finanalyticsai)
Redis         → localhost:6379
Kafka broker1 → localhost:9092
Kafka broker2 → localhost:9093
Kafka broker3 → localhost:9094
MinIO API     → localhost:9000  (console: 9001)
ChromaDB      → localhost:8000
Airflow UI    → localhost:8080  (user: admin)
MLflow        → localhost:5000
TorchServe    → localhost:8080 (inference) / 8081 (management)
vLLM (LLM)   → localhost:8090
Grafana       → localhost:3000
Prometheus    → localhost:9090
```

---

## 6. Estrutura de Diretórios

**Localização física:** dentro do WSL2 Ubuntu em `/home/<usuario>/finanalyticsai/`
(acessível do Windows via `\\wsl$\Ubuntu\home\<usuario>\finanalyticsai\` —
útil para abrir no VS Code ou Explorer, mas NUNCA mover dados de treino
para fora do filesystem do WSL2 por causa da penalidade de I/O).

```
~/finanalyticsai/                ← raiz dentro do WSL2
├── CLAUDE.md                    ← este arquivo
├── docker-compose.yml           ← todos os serviços de infra
├── .env                         ← variáveis de ambiente (NÃO commitar)
├── .env.example                 ← template sem segredos
├── data/
│   ├── raw/                     ← dados brutos do yfinance, FRED, EDGAR
│   ├── processed/               ← OHLCV limpo no TimescaleDB
│   ├── features/                ← features engenhadas
│   └── embeddings/              ← embeddings textuais
├── models/
│   ├── hmm/                     ← modelos HMM serializados
│   ├── finbert/                 ← FinBERT fine-tunado
│   ├── xgboost/                 ← modelos XGBoost por data
│   ├── lstm/                    ← checkpoints LSTM/TFT
│   ├── gnn/                     ← checkpoints GAT
│   ├── drl/                     ← checkpoints PPO
│   └── ctgan/                   ← CTGAN treinado
├── src/
│   ├── ingestion/               ← pipelines de dados
│   │   ├── ohlcv.py
│   │   ├── macro.py
│   │   ├── text.py
│   │   └── kafka_producer.py
│   ├── features/                ← feature engineering
│   │   ├── technical.py
│   │   ├── sentiment.py
│   │   └── regime.py
│   ├── models/
│   │   ├── hmm.py
│   │   ├── xgboost_model.py
│   │   ├── finbert.py
│   │   ├── lstm_vol.py
│   │   ├── tft_vol.py
│   │   ├── gat.py
│   │   ├── drl_env.py
│   │   ├── drl_agent.py
│   │   └── ctgan_synth.py
│   ├── portfolio/
│   │   ├── signals.py           ← agregador de sinais
│   │   ├── sizing.py            ← Kelly Fracionário
│   │   ├── risk.py              ← Risk Manager + VaR
│   │   └── execution.py         ← Trading Engine
│   └── serving/
│       ├── vllm_server.sh
│       ├── torchserve_config/
│       └── health_checks.py
├── dags/                        ← Airflow DAGs
│   ├── market_hours.py          ← DAG real-time (pregão)
│   ├── nightly_training.py      ← DAG treinamento off-hours
│   ├── gnn_retrain.py           ← DAG GNN diário (12h)
│   └── weekly_reports.py
├── k8s/                         ← Manifests Kubernetes (Fase 6)
│   ├── deployments/
│   ├── services/
│   └── configmaps/
├── backtests/                   ← resultados de backtesting
├── logs/                        ← logs de sistema e trades
└── notebooks/                   ← análises exploratórias
```

---

## 7. Regras de Comportamento do Claude Code

### SEMPRE fazer
- [ ] Ler este arquivo completamente antes de qualquer ação
- [ ] Executar comandos de shell dentro do **bash do WSL2** (salvo quando indicado `# [Windows host]`)
- [ ] Verificar se os serviços necessários estão rodando: `docker ps | grep finai`
- [ ] Executar o código e mostrar o output real — não apenas escrever código sem rodar
- [ ] Logar TODOS os experimentos de treino no MLflow
- [ ] Validar os KPIs do sprint antes de declarar concluído
- [ ] Commitar código ao git local após cada passo concluído: `git add -A && git commit -m "Sprint X: descrição"`
- [ ] Usar Mixed Precision FP16 em todos os treinos PyTorch
- [ ] Usar `torch.compile(model, mode='reduce-overhead')` em modelos de inferência
- [ ] Verificar VRAM disponível antes de qualquer treino: `nvidia-smi --query-gpu=memory.free --format=csv`
- [ ] Atualizar a seção **Estado Atual** ao final do sprint

### NUNCA fazer
- [ ] Ultrapassar 20 GB de VRAM total
- [ ] Iniciar treino de modelo grande durante horário de mercado (09h–18h) sem autorização
- [ ] Usar API externa sem a key ter sido fornecida pelo usuário no chat
- [ ] Pular validação de KPIs e declarar sprint concluído
- [ ] Usar `WidthType.PERCENTAGE` em tabelas DOCX (quebra no Google Docs)
- [ ] Inserir bullets unicode em documentos Word — usar `LevelFormat.BULLET`
- [ ] Deletar dados históricos ou modelos treinados sem backup
- [ ] Fazer `DROP TABLE` em produção sem confirmação explícita do usuário
- [ ] Commitar o arquivo `.env` no git
- [ ] Sugerir substituir o Windows 11 por Ubuntu bare-metal ou dual-boot nesta máquina — o Windows 11 permanece como host. Ubuntu Server só no servidor de produção em colocation (Seção 16.4).
- [ ] Armazenar dados de treino em `/mnt/c/...` — sempre dentro do filesystem nativo do WSL2

### Se encontrar um erro
1. Mostrar o traceback completo
2. Diagnosticar a causa raiz
3. Propor solução e aguardar confirmação se for destrutiva
4. Se GPU OOM: reduzir batch_size pela metade e retry com gradient_checkpointing=True
5. Se serviço down: `docker logs [container] --tail 50` e corrigir a causa
6. Se `nvidia-smi` no WSL2 não enxergar a GPU: verificar driver NVIDIA do Windows (mínimo 535+), confirmar que o Docker Desktop está rodando e reiniciar o WSL2 com `wsl --shutdown` no PowerShell do host

---

## 8. Tópicos Kafka

```
market.ohlcv.raw          → dados brutos de mercado (ingestão)
market.ohlcv.normalized   → após limpeza e normalização
market.macro              → dados macro do FRED
signals.hmm.regime        → estado HMM atual {regime: 0|1|2, proba: [...]}
signals.xgboost.factor    → scores de fator cross-seccional
signals.finbert.sentiment → scores FinBERT por ativo
signals.gnn.embedding     → embeddings dos nós do GAT
signals.sizing            → Kelly sizing por ativo
signals.aggregated        → sinal final combinado
risk.alerts               → alertas VaR, drawdown, VRAM
trades.paper              → execuções paper trading (Alpaca)
trades.live               → execuções live trading (IB)
```

---

## 9. Variáveis de Ambiente Necessárias

Criar `~/finanalyticsai/.env` (dentro do WSL2) com as seguintes variáveis.
As marcadas com `[USUÁRIO FORNECE]` devem ser solicitadas ao usuário antes de usar.

```bash
# Banco de dados
POSTGRES_USER=finai
POSTGRES_PASSWORD=finai_secure_2026
POSTGRES_DB=finanalyticsai
TIMESCALE_HOST=localhost
TIMESCALE_PORT=5432

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=

# Kafka
KAFKA_BOOTSTRAP=localhost:9092,localhost:9093,localhost:9094

# MinIO
MINIO_ROOT_USER=finai_minio
MINIO_ROOT_PASSWORD=finai_minio_2026
MINIO_ENDPOINT=localhost:9000

# MLflow
MLFLOW_TRACKING_URI=http://localhost:5000
MLFLOW_EXPERIMENT_NAME=finanalyticsai

# APIs de dados (usuário fornece)
NEWSAPI_KEY=           # [USUÁRIO FORNECE] — newsapi.org — plano gratuito
POLYGON_API_KEY=       # [USUÁRIO FORNECE] — polygon.io — plano gratuito ou pago
ALPACA_API_KEY=        # [USUÁRIO FORNECE] — alpaca.markets — gratuito para paper
ALPACA_SECRET_KEY=     # [USUÁRIO FORNECE]
ALPACA_PAPER=true      # true para paper trading, false para live

# Dados alternativos (Fase 5+)
QUIVER_API_KEY=        # [USUÁRIO FORNECE] — quiverquant.com
IB_HOST=localhost      # Interactive Brokers TWS (Fase 6+)
IB_PORT=7497
IB_CLIENT_ID=1

# Alertas
TELEGRAM_BOT_TOKEN=    # [USUÁRIO FORNECE] — @BotFather
TELEGRAM_CHAT_ID=      # [USUÁRIO FORNECE]

# GPU
CUDA_VISIBLE_DEVICES=0
PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:512
```

---

## 10. Comandos de Referência Rápida

> Salvo indicação `# [Windows host]`, todos os comandos rodam no **bash do WSL2**
> (abrir Windows Terminal → perfil Ubuntu, ou executar `wsl` no PowerShell).

### Verificação de saúde do sistema
```bash
# Status de todos os serviços (WSL2)
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

# VRAM disponível (WSL2 — usa driver do Windows via passthrough)
nvidia-smi --query-gpu=memory.used,memory.free,memory.total --format=csv,noheader

# Logs de um serviço
docker logs finai-kafka-1 --tail 50 --follow

# Conexão com TimescaleDB
psql -h localhost -U finai -d finanalyticsai -c "SELECT count(*) FROM ohlcv;"

# Espaço em disco (do filesystem do WSL2)
df -h /

# Jobs Airflow pendentes
airflow dags list-runs --dag-id nightly_training --state running
```

```powershell
# [Windows host] — comandos úteis no PowerShell do Windows
# Reiniciar WSL2 (ex.: após editar .wslconfig ou instalar novo driver NVIDIA)
wsl --shutdown

# Listar distros WSL e status
wsl -l -v

# Ver uso de memória da VM do WSL2
Get-Process vmmem  # (ou vmmemWSL em builds recentes)

# Verificar GPU pelo lado Windows
nvidia-smi.exe
```

### Kafka
```bash
# Verificar tópicos
kafka-topics.sh --bootstrap-server localhost:9092 --list

# Consumer lag
kafka-consumer-groups.sh --bootstrap-server localhost:9092 --describe --all-groups

# Publicar mensagem de teste
echo '{"test": true}' | kafka-console-producer.sh --topic signals.hmm.regime --bootstrap-server localhost:9092
```

### MLflow
```bash
# Ver experimentos
mlflow experiments list

# Comparar runs
mlflow runs list --experiment-id 1 --order-by "metrics.sharpe DESC"
```

### GPU e modelos
```bash
# Monitoramento contínuo da GPU (WSL2)
watch -n 2 nvidia-smi

# Limpar cache da GPU entre treinos
python -c "import torch; torch.cuda.empty_cache(); print('Cache limpo')"

# Verificar versões CUDA/PyTorch
python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.version.cuda)"
```

---

## 11. Resolução de Problemas Comuns

### GPU Out of Memory (OOM)
```python
# 1. Reduzir batch_size pela metade
# 2. Ativar gradient checkpointing
model.gradient_checkpointing_enable()
# 3. Limpar cache entre épocas
torch.cuda.empty_cache()
# 4. Usar offload para CPU se necessário
# 5. Reduzir max_model_len no vLLM
```

### TimescaleDB lento
```sql
-- Verificar queries lentas
SELECT query, mean_exec_time, calls
FROM pg_stat_statements
ORDER BY mean_exec_time DESC LIMIT 10;

-- VACUUM se necessário
VACUUM ANALYZE ohlcv;
```

### Kafka consumer lag alto
```bash
# Reiniciar consumer group com offset reset
kafka-consumer-groups.sh --bootstrap-server localhost:9092 \
  --group finai-signals --reset-offsets --to-latest --execute --topic signals.aggregated
```

### Airflow DAG falhando
```bash
# Ver logs do task
airflow tasks logs [dag_id] [task_id] [execution_date]

# Limpar estado e re-executar
airflow tasks clear [dag_id] --start-date [data] --yes
```

### Docker serviço não sobe
```bash
# Logs detalhados
docker logs [container] --tail 100

# Verificar volumes
docker volume ls
docker volume inspect [volume_name]

# Recriar container mantendo dados
docker-compose up -d --no-deps --force-recreate [service]
```

### WSL2 / Windows 11 — problemas específicos
```powershell
# [Windows host] — WSL2 sem GPU: driver desatualizado
# Baixar driver NVIDIA 535+ em nvidia.com/drivers (Game Ready ou Studio),
# instalar com limpeza (opção "clean install"), reiniciar o Windows.

# [Windows host] — WSL2 consumindo RAM demais
# Editar C:\Users\<usuario>\.wslconfig e reduzir "memory=" — depois:
wsl --shutdown

# [Windows host] — Docker Desktop não inicia
# Verificar: Hyper-V + Virtual Machine Platform + WSL2 habilitados em
# "Recursos do Windows". O recurso "Integridade de Memória" do Windows
# Security PODE conflitar; se necessário, desabilitar temporariamente.
```

```bash
# [WSL2] — relógio do WSL2 fora de sincronia (erros TLS/JWT)
sudo hwclock -s

# [WSL2] — Docker socket sem permissão
sudo usermod -aG docker $USER && newgrp docker
```

---

## 12. Protocolo de Início de Sprint

Ao iniciar um novo sprint, executar nesta ordem (tudo no bash do WSL2,
exceto onde marcado `# [Windows host]`):

```bash
# 1. Verificar sistema
docker ps | grep -E "timescaledb|kafka|redis|chromadb"
nvidia-smi
df -h ~

# 2. Ler o sprint atual neste arquivo
# 3. Confirmar pré-requisitos do sprint estão atendidos
# 4. Criar branch git para o sprint
cd ~/finanalyticsai
git checkout -b sprint-[N]-[nome]

# 5. Executar os passos do sprint em ordem
# 6. Validar KPIs ao final
# 7. Atualizar Estado Atual neste arquivo
# 8. Merge para main
git checkout main && git merge sprint-[N]-[nome]
```

---

## 13. Plano de Sprints — 24 Sprints / 12 Meses

### FASE 1 — Fundação e HMM (Meses 1–2)

**S01 | Sem 1–2 | Infraestrutura base**
- Docker Compose: TimescaleDB, Kafka (3 brokers), Redis, ChromaDB, MinIO
- Criar schemas TimescaleDB (hypertables OHLCV, features, sinais, trades)
- Criar tópicos Kafka com partições e replicação corretos
- Validar health checks em todos os containers
- KPI: Todos os 6 serviços em estado `healthy`

**S02 | Sem 3–4 | Ingestão histórica**
- yfinance: 100 ativos × 10 anos → TimescaleDB
- FRED API: CPI, juros, PIB, VIX, yield curve, spread HY
- Script de validação: gaps, nulos, splits e dividendos
- KPI: > 2,5M linhas OHLCV, zero gap > 5 dias em qualquer ativo

**S03 | Sem 5–6 | HMM — detecção de regime**
- GaussianHMM 3 estados com hmmlearn
- Features: retorno diário, vol 5d, volume normalizado
- Walk-forward re-fit com janela 252 dias úteis
- Publicar regime no Kafka `signals.hmm.regime`
- KPI: Accuracy > 70% out-of-sample, regimes corretos em 2008/2020/2022

**S04 | Sem 7–8 | Backtesting framework**
- Walk-forward sem data leakage com vectorbt
- Paralelização Ray: 32+ workers usando 80 GB RAM
- Métricas: Sharpe, Calmar, Max DD, Hit Rate, Profit Factor, VaR
- KPI: Backtest 10 anos < 5 min, zero data leakage confirmado

---

### FASE 2 — XGBoost + FinBERT (Meses 3–4)

**S05 | Sem 9–10 | Pipeline textual**
- Scrapers RSS: Reuters, WSJ, Bloomberg RSS, InfoMoney
- SEC EDGAR downloader: 8-K, 10-Q para os 100 ativos
- Airflow DAG diário às 07h30 → MinIO → TimescaleDB metadados
- KPI: > 50 documentos/dia, DAG sem falhas 5 dias consecutivos

**S06 | Sem 11–12 | FinBERT fine-tuning e serving**
- Download ProsusAI/finbert do HuggingFace
- Fine-tuning 5 épocas, FP16, batch 32, lr 2e-5
- TorchServe deployment com batching dinâmico
- KPI: F1 > 0,88 out-of-sample, latência < 50ms para texto ≤ 512 tokens

**S07 | Sem 13–14 | XGBoost factor model**
- 30+ features técnicas: EMA (5/20), RSI, MACD signal, ATR, Bollinger width, volume ratio
- Incluir regime HMM e score FinBERT como features
- Optuna: 100 trials de hiperparâmetros com cross-validation temporal
- SHAP para feature importance e interpretabilidade
- KPI: IC > 0,05 rolling 63 dias, p-value < 0,01

**S08 | Sem 15–16 | Portfólio factor + paper trading**
- Portfólio long/short: top decil × bottom decil, ponderado pelo score
- Filtro de regime: 50% de exposição em Bear, 0% em Transição extrema
- Integração Alpaca Paper Trading API (usuário fornece keys)
- KPI: Sharpe backtest 2 anos > 1,0 com custos reais de transação

---

### FASE 3 — LSTM/TFT Volatilidade (Meses 5–6)

**S09 | Sem 17–18 | Dataset vol + LSTM baseline**
- Volatilidade realizada: rolling std × sqrt(252) em janelas 5/10/21 dias
- Features exógenas FRED: VIX, spreads, inclinação da curva
- LSTM com PyTorch Lightning: hidden=128, layers=2, dropout=0.2, FP16
- MLflow rastreando hiperparâmetros, métricas e artefatos
- KPI: MLflow rastreia 100% dos treinos, baseline RMSE documentado

**S10 | Sem 19–20 | TFT multi-horizonte**
- pytorch-forecasting: TFT para 1, 3 e 5 dias simultâneos
- Attention weights visualizados sobre features exógenas
- Comparação TFT vs LSTM no MLflow com métricas consistentes
- KPI: TFT RMSE ≤ LSTM RMSE out-of-sample em horizonte 1 dia

**S11 | Sem 21–22 | Validação e Kelly Fracionário**
- Benchmark: LSTM, TFT e GARCH(1,1) no mesmo período out-of-sample
- Kelly Fracionário: fraction=0.5, cap=20% por posição
- Backtest de portfólio com vol forecast vs GARCH
- KPI: RMSE 20% inferior ao GARCH, Sharpe do portfólio +15%

**S12 | Sem 23–24 | TorchServe produção LSTM/TFT**
- Empacotar ambos os modelos em .mar e servir via TorchServe
- Handler customizado com fallback para GARCH se modelo falhar
- Load test: 100 ativos em batch simultâneo
- KPI: Latência P99 < 30ms para batch de 100 ativos

---

### FASE 4 — GNN 100 Ativos (Meses 7–8)

**S13 | Sem 25–26 | Seleção de ativos e grafo**
- Critérios: volume médio > USD 10M, cobertura 10 anos, máx 15 por setor GICS
- Grafo: correlação Pearson rolante 60 dias, aresta se |r| > 0,35
- PyTorch Geometric instalado e validado com grafo de teste
- KPI: Grafo com 300–900 arestas (6–18% de esparsidade)

**S14 | Sem 27–28 | GAT treinamento**
- GAT: 2 camadas GATConv, 8 heads de atenção, hidden=256, dropout=0.2
- Label: retorno relativo rank-normalizado no período seguinte
- Validar VRAM < 3 GB com stack de inferência ativa (~9 GB total = ~12 GB)
- KPI: IC do GAT ≥ IC XGBoost, VRAM < 3 GB confirmada durante treino

**S15 | Sem 29–30 | Re-treinamento diário**
- Airflow DAG às 12h00 (horário de almoço): rebuild grafo + re-treino GAT
- Atualizar ChromaDB com embeddings dos nós após cada treino
- Armazenar histórico de attention weights por data para análise de evolução
- KPI: Re-treino < 20 min em 5 dias consecutivos sem falhas

**S16 | Sem 31–32 | Comunidades e integração**
- Louvain community detection sobre grafo ativo
- Constraint de diversificação: máximo 20% de peso por comunidade
- Integrar output do GAT como feature adicional no XGBoost
- KPI: Nenhuma comunidade > 20% de peso, comunidades fazem sentido setorial

---

### FASE 5 — DRL + Dados Alternativos (Meses 9–10)

**S17 | Sem 33–34 | Ambiente Gym e setup DRL**
- TradingEnv: observation_space = 100 ativos × 20 features, action_space = pesos [-1,1]
- Reward: Sharpe incremental − α×custo − β×max(0, DD − 0.10)
- Validar ambiente: 1.000 steps aleatórios sem crash ou NaN
- KPI: Ambiente estável, reward bounds corretos, primeiras 100 iter PPO OK

**S18 | Sem 35–36 | PPO 500 iterações**
- Ray RLlib PPO: train_batch=4096, sgd_minibatch=256, lr=3e-4, clip=0.2
- 500 iterações durante janela off-hours (~2 horas na GPU)
- Seleção de checkpoint: melhor Sharpe out-of-sample no MLflow
- KPI: Agente validado com Sharpe > 1,5 out-of-sample

**S19 | Sem 37–38 | Risk Manager e monitoramento**
- VaR histórico: confidence=0.95, janela=252 dias, horizonte=1 dia
- Circuit breakers: DD > 8% → pausar novas ordens, DD > 12% → fechar posições
- Grafana dashboards: P&L, Sharpe rolling 30d, VRAM, latência, Kafka lag
- Alertas Telegram: DD > 6%, VRAM > 20 GB, latência > 200ms
- KPI: Alerta Telegram em < 5s após simulação de breach de limite

**S20 | Sem 39–40 | Paper trading — início**
- Pipeline end-to-end: dado → HMM → features → XGBoost+GNN+DRL → risco → Alpaca
- Logging completo de todos os trades com sinal de origem e modelo usado
- Dashboard P&L em tempo real no Grafana
- KPI: Primeiro dia completo sem erros — 60-day clock iniciado

---

### FASE 6 — Dados Sintéticos + Produção (Meses 11–12)

**S21 | Sem 41–42 | CTGAN stress scenarios**
- CTGAN (SDV): 500 épocas sobre retornos históricos dos 100 ativos
- Calibrar fat tails: kurtosis sintética ≥ 0,8 × kurtosis histórica
- 100+ cenários: crash 1929, Flash Crash 2010, COVID 2020, LTCM 1998, crise liquidez
- KPI: Max DD < 20% em 100% dos 100+ cenários

**S22 | Sem 43–44 | Validação 60 dias paper**
- Análise completa: Sharpe, Calmar, Hit Rate, decomposição por estratégia
- Top 5 trades lucrativos e top 5 perdedores com análise de causa
- Relatório formal de go/no-go com critérios documentados
- KPI: Sharpe > 1,5 em 60 dias → aprovado para live trading

**S23 | Sem 45–46 | Kubernetes e produção**
- k3s single-node NO SERVIDOR DE PRODUÇÃO (Ubuntu Server bare-metal em colocation SP) — NÃO no Windows 11 da estação de dev
- Manifests para todos os serviços com health checks, resource limits
- Rolling updates testados sem downtime
- Airflow produção com DAGs diurnos e noturnos separados
- KPI: Zero downtime em rolling update, todos os health checks passando

**S24 | Sem 47–48 | Live trading 10% capital**
- Interactive Brokers API integrada (usuário fornece credenciais)
- Deploy com 10% do capital alvo — monitoramento intensivo primeira semana
- Audit trail no TimescaleDB: todo trade com timestamp, modelo, versão, regime
- KPI: Uptime > 99,0%, 0 erros de execução na primeira semana

---

## 14. Estado Atual do Projeto

```
SPRINT ATUAL:       S01 (pendente de início)
FASE ATUAL:         1 — Fundação e HMM
ÚLTIMA ATUALIZAÇÃO: 16/abr/2026

SPRINTS CONCLUÍDOS: nenhum

AMBIENTE DA ESTAÇÃO DE DEV:
  Host:              Windows 11 Pro (não será substituído)
  Ambiente Linux:    WSL2 com Ubuntu 22.04 LTS
  Docker:            Docker Desktop com backend WSL2
  GPU passthrough:   NVIDIA driver 535+ (Windows) → WSL2 via CUDA-on-WSL
  Working dir:       ~/finanalyticsai/ dentro do WSL2

ENTREGAS DE PRÉ-PROJETO (pasta Melhorias/):
  [x] Plano técnico completo — finanalyticsai_plano.docx
  [x] Solução para mercado BR — finanalytics_br_solucao.docx
  [x] Roadmap de sprints — finanalyticsai_sprints.html
  [x] Análise de hospedagem de produção (planilha .xlsx + relatório .docx) — 15/abr/2026
  [x] CLAUDE.md v1.1 com Seção 16 (decisão de infraestrutura)
  [x] CLAUDE.md v1.3 — correção para Windows 11 + WSL2 na estação de dev (16/abr/2026)

SERVIÇOS RODANDO (todos via Docker Desktop/WSL2):
  [ ] TimescaleDB
  [ ] Kafka (3 brokers)
  [ ] Redis
  [ ] ChromaDB
  [ ] MinIO
  [ ] Airflow
  [ ] MLflow
  [ ] vLLM
  [ ] TorchServe
  [ ] Grafana/Prometheus

MODELOS TREINADOS:
  [ ] HMM
  [ ] FinBERT (fine-tuned)
  [ ] XGBoost factor model
  [ ] LSTM volatilidade
  [ ] TFT volatilidade
  [ ] GAT (GNN)
  [ ] DRL (PPO)
  [ ] CTGAN

INFRAESTRUTURA DE PRODUÇÃO:
  Decisão tomada:     Colocation SP + servidor Tier Pragmático (Fase 4+)
                      Ubuntu 22.04 LTS Server bare-metal — HARDWARE NOVO,
                      separado da estação de dev Windows 11
  CAPEX orçado:       ~R$ 110.000
  OPEX mensal alvo:   ~R$ 3.500 (colocation + energia + cross-connect B3)
  TCO 36m previsto:   ~R$ 192.000 (referência USD/BRL = 5,00)
  Status:             não iniciado — aplicar otimizações de software primeiro (Seção 16.3)

PAPER TRADING:
  Status: não iniciado
  Início: —
  Dias completos: 0/60
  Sharpe acumulado: —

LIVE TRADING:
  Status: não iniciado
  Capital alocado: 0%

OTIMIZAÇÕES DE SOFTWARE PRÉ-UPGRADE (Seção 16.3):
  [ ] Quantização GGUF q4_K_M do FinGPT-8B (24→6 GB VRAM)
  [ ] Mixed Precision FP16 em todos os treinos
  [ ] Flash Attention 2 no LLM
  [ ] vLLM PagedAttention substituindo HuggingFace
  [ ] gpu-memory-utilization=0.75 no vLLM
  [ ] Gradient Checkpointing no GNN
  [ ] INT8 quantization no FinBERT
  [ ] Feature store Redis quente + Parquet frio
  [ ] Batch dinâmico no TorchServe
  [ ] Pipelines async (asyncio, aiokafka)
  [ ] Compressão Zstandard em Kafka e MLflow
  [ ] Connection pooling TimescaleDB (pgBouncer)
  [ ] Jobs de treino nunca simultâneos (scheduling off-hours)
  [ ] VRAM Manager com limites rígidos e alertas > 20 GB
  [ ] VPS SP + WireGuard para agente de execução
  [ ] Profiling PyTorch Profiler + py-spy
  [ ] Prometheus + Grafana + Loki
  [ ] Health checks e circuit breakers por serviço
  [ ] Avaliação de IC real por modelo (eliminar os que não pagam o custo)
  [ ] Lista completa de 30 itens: planilha FinAnalyticsAI_Comparativo_Hospedagem.xlsx, aba "Otimizações pré-upgrade"

NOTAS:
  16/abr/2026 — Correção de SO: a estação de desenvolvimento é
    Windows 11 Pro e NÃO Ubuntu bare-metal. Windows 11 permanece como
    host; todo o stack Linux roda dentro do WSL2 (Ubuntu 22.04).
    CLAUDE.md v1.2 → v1.3. Principais ajustes: Seção 2 (hardware/SO),
    Seção 3 (decisões 13 e 14 — SO dev e SO produção imutáveis),
    Seção 5 (nota sobre port-forward WSL2↔Windows), Seção 6 (paths WSL2),
    Seção 7 (regras NUNCA e mensagem de erro específica de GPU no WSL2),
    Seção 9 (.env no WSL2), Seção 10 (comandos com marcação host/WSL2,
    bloco PowerShell), Seção 11 (troubleshooting Windows 11 + WSL2),
    Seção 12 (protocolo), Seção 13 S23 (k3s só no servidor prod, não no
    Windows), Seção 16.1 (separação dev/prod clarificada), Seção 16.4
    (SO Ubuntu bare-metal restrito ao servidor de produção),
    Seção 16.8 (risco de divergência dev/prod com mitigação),
    Bloco 0 pré-flight (instruções Windows 11 + WSL2) e princípios
    do Bloco 17 (proibir substituir Windows por Ubuntu bare-metal).

  15/abr/2026 — Análise de hospedagem de produção concluída.
    Decisão: máquina atual (RTX 4090 + 196 GB RAM) permanece como estação
    de dev/treino; produção será em colocation SP com hardware dedicado
    a partir da Fase 4. Regra imutável (Seção 16.1): nunca misturar dev
    e produção na mesma máquina física em regime live.
    Antes de qualquer CAPEX, aplicar as otimizações de software da
    Seção 16.3. Profiling obrigatório para identificar bottleneck real
    antes de aprovar hardware novo (Seção 16.9).
    Cloud GPU US (RunPod/Vast) autorizado APENAS para treino offline
    mensal (CTGAN, DRL) — latência 150-200 ms inviabiliza execução.
```

---

## 15. Como solicitar ao Claude Code

Para iniciar um sprint específico, diga ao Claude Code:

```
"Execute o Sprint 1 do FinAnalyticsAI conforme o CLAUDE.md.
Valide todos os KPIs antes de declarar concluído."
```

Para continuar de onde parou:
```
"Continue o projeto FinAnalyticsAI. Leia o CLAUDE.md,
verifique o Estado Atual e execute o próximo sprint pendente."
```

Para uma tarefa específica dentro de um sprint:
```
"No Sprint 3 do FinAnalyticsAI, implemente apenas o HMM.
Contexto no CLAUDE.md."
```

---

## 16. Análise de Hospedagem de Produção (adicionado em 15/abr/2026)

Esta seção consolida a decisão de infraestrutura de produção resultante da análise comparativa de hospedagem realizada em abril/2026. Os arquivos detalhados estão na pasta `Melhorias/`:

- `FinAnalyticsAI_Comparativo_Hospedagem.xlsx` — 6 abas: Resumo, Otimizações pré-upgrade (30 itens), Opções de hospedagem (12 opções), TCO 36 meses, Sensibilidade ao câmbio, Premissas e fontes.
- `FinAnalyticsAI_Relatorio_Hospedagem.docx` — Relatório executivo com hierarquia de decisão, otimizações, comparativo, TCO, recomendação por fase e riscos.

### 16.1 Separação dev vs. produção — IMUTÁVEL

A máquina atual (**Windows 11 Pro + WSL2 Ubuntu 22.04** + RTX 4090 + 196 GB RAM) é **estação de desenvolvimento e treino**. O Windows 11 é o SO host e **não será substituído** por Ubuntu bare-metal nem posto em dual-boot — WSL2 cobre 100% das necessidades de desenvolvimento Linux deste projeto. Produção deve rodar em **hardware dedicado separado**, idealmente em **colocation em São Paulo**, com **Ubuntu 22.04 LTS Server bare-metal** (sem Windows, sem WSL) para latência B3 < 5 ms e estabilidade 24/7. Nunca misturar dev e produção na mesma máquina física em regime live.

### 16.2 Hierarquia de decisão — executar em ordem

| # | Camada | Custo mensal | Fase | Quando |
|---|---|---|---|---|
| 1 | Otimizações de software (vLLM, quantização, FP16, scheduling) | R$ 0 | Imediato | Antes de qualquer outra coisa |
| 2 | VPS SP apenas para agente de execução | R$ 120-500 | 1-3 | Quando latência vira gargalo real |
| 3 | Servidor dedicado SP hospedado (sem GPU) | R$ 4-10 k | 2-3 | Quando uptime 24/7 for crítico |
| 4 | Colocation SP + servidor Tier Pragmático | R$ 3.500 | 4+ | Live trading com capital real |
| 5 | Colocation SP + Tier Robust (redundância total) | R$ 5.500 | 5-6 | Capital significativo, compliance |
| 6 | Cloud hyperscale SP reservado 3 anos | R$ 15-37 k | Alt. | Se workload for muito variável |
| 7 | Cloud GPU US (burst para treino mensal) | R$ 500-1.500 | Compl. | Para CTGAN/DRL pesado |

### 16.3 Otimizações de software obrigatórias ANTES de qualquer upgrade

Aplicar em ordem, todas antes de considerar CAPEX de hardware adicional:

**Modelo/GPU** — Quantização GGUF q4_K_M para FinGPT-8B (24→6 GB VRAM); Mixed Precision FP16 em todos os treinos; Flash Attention 2 no LLM; vLLM PagedAttention substituindo HuggingFace; Gradient Checkpointing no GNN; INT8 quantization no FinBERT; gpu-memory-utilization=0.75 no vLLM.

**Dados/Pipeline** — Feature store Redis quente + Parquet frio; batch dinâmico na inferência (TorchServe); pipelines async (asyncio, aiokafka); compressão Zstandard em Kafka/MLflow; connection pooling TimescaleDB (pgBouncer); RAM disk para dados temporários.

**Scheduling** — Jobs de treino nunca simultâneos; VRAM Manager com limites rígidos e alertas > 20 GB; cloud burst para backtesting em spot 2-4 h/mês.

**Arquitetura/Observability** — VPS SP + WireGuard para latência B3; profiling com PyTorch Profiler e py-spy antes de qualquer compra; Prometheus + Grafana + Loki; health checks e circuit breakers.

**Escopo** — Avaliar IC real de cada modelo e eliminar os que não pagam o custo; rolling window vs. retraining completo; usar LightGBM/XGBoost CPU onde IC for similar ao LSTM.

Lista completa com 30 itens, esforço e ganho esperado: aba "Otimizações pré-upgrade" da planilha.

### 16.4 Arquitetura-alvo (Fase 4+)

**Servidor principal em colocation SP (Tier Pragmático, CAPEX ~R$ 110 k):**
- CPU: AMD Threadripper PRO 7975WX (32c/64t) ou 7985WX (64c/128t)
- Placa-mãe: ASUS Pro WS WRX90E-SAGE SE
- RAM: 256 GB DDR5-5600 ECC RDIMM (8× 32 GB)
- GPU: NVIDIA RTX 6000 Ada Generation (48 GB GDDR6 ECC, 300 W, blower single-slot)
- Storage: 2× 2 TB NVMe Gen4 (RAID 1 OS) + 4× 4 TB NVMe U.2 (RAID 10 dados) + 1× 4 TB spare
- Rede: Dual 10 GbE Intel X710 (bonded)
- PSU: 1600 W 80+ Titanium
- UPS: APC Smart-UPS SRT 3000VA online double-conversion
- **SO: Ubuntu 22.04 LTS Server (bare-metal)** — aplicável APENAS a este
  servidor de produção, que é hardware novo e dedicado. **NÃO se aplica
  à estação de dev do usuário**, que roda Windows 11 Pro com WSL2
  (Seção 2 e Decisão 13).

**Hospedagem:** Colocation em Equinix SP, Ascenty SP ou ODATA SP, 1-2U, cross-connect dedicado 1 Gbps à B3, latência < 5 ms.

**Upgrade para Tier Robust (Fase 5-6, CAPEX ~R$ 220 k):** chassi Supermicro 2U dual-socket, 2× EPYC 9354, 384 GB ECC, NVIDIA L40S (48 GB ECC datacenter-grade), 8× 3,84 TB NVMe U.2 RAID 10, PSU redundante 2× 2000 W, 2× 25 GbE SFP28. Também Ubuntu 22.04 LTS Server bare-metal.

### 16.5 TCO 36 meses (USD/BRL = 5,00)

| Opção | TCO 36m BRL |
|---|---|
| VPS SP apenas (agente) | R$ 18 k |
| RunPod/Vast US (só treino offline) | R$ 52 k |
| **Colocation SP + Tier Pragmático (recomendado)** | **R$ 192 k** |
| Servidor dedicado hospedado SP | R$ 216 k |
| Híbrido Colocation + RunPod burst | R$ 219 k |
| GCP SP g2-standard-24 | R$ 262 k |
| Oracle Cloud SP | R$ 270 k |
| Colocation SP + Tier Robust | R$ 330 k |
| Azure SP NV36ads A10 v5 | R$ 463 k |
| AWS SP g6.12xlarge 3-yr reserved | R$ 532 k |
| AWS SP g6.12xlarge on-demand | R$ 1.339 k |

Colocation com servidor próprio é **3 a 5× mais barato** que cloud hyperscale em SP no horizonte de 36 meses.

### 16.6 Cloud GPU US — uso restrito

RunPod, Vast.ai, Lambda e CoreWeave oferecem L40S a preços muito abaixo dos hyperscalers, porém a latência de 150-200 ms entre US e SP **inviabiliza execução** na B3. Uso permitido apenas para:
- Jobs de treino mensais pesados (CTGAN, DRL PPO/SAC) em spot instances.
- Backtesting massivo paralelo em janelas off-hours.
- Experimentação de modelos não críticos.

Nunca rotear decisões de trading em tempo real através de cloud US.

### 16.7 Roadmap de migração por fase

- **Mês 1-3 (Fase 1-2):** aplicar otimizações de software na estação Windows 11 + WSL2. Máquina atual como dev. Zero CAPEX.
- **Mês 4-6 (Fase 3):** subir VPS SP (R$ 120-500/mês) para agente de execução com WireGuard. Paper trading.
- **Mês 7-9 (Fase 4):** adquirir servidor Tier Pragmático (Ubuntu Server bare-metal) e colocar em colocation SP. Live com 10% do capital.
- **Mês 10-12 (Fase 5-6):** avaliar upgrade para Tier Robust ou adicionar nó hot-standby. Cloud burst mensal para CTGAN/DRL.

### 16.8 Riscos e mitigações de produção

- **Falha de GPU** (baixa/crítico): spare RTX 6000 Ada (USD 2-3 k); fallback FinBERT em CPU; alertas imediatos.
- **Queda de link do data center** (baixa/alto): colocation dual-carrier; agente em VPS SP como backup.
- **Câmbio USD/BRL subindo** (média/médio): OPEX cloud em USD vira risco — CAPEX em BRL protege; avaliar hedge em futuros de dólar.
- **Latência > 5 ms intermitente** (baixa/alto): monitoramento com SLO; failover para broker backup.
- **Downtime prolongado do DC** (muito baixa/crítico): colocation Tier-3 com SLA ≥ 99,98%; réplica hot em segundo DC na Fase 5-6.
- **Compliance/auditoria B3** (média/alto): audit trail de 5 anos em MinIO + réplica em S3 Deep Archive; logs imutáveis WORM.
- **Divergência dev (WSL2) vs. produção (Ubuntu Server)** (média/médio): mesma distro base (Ubuntu 22.04) e mesmas imagens Docker com tags pinadas; versões de CUDA/Python/pacotes travadas em `requirements.txt` / `pyproject.toml`; CI roda testes em container equivalente ao de produção para capturar diferenças de kernel/glibc cedo.

### 16.9 Regra prática

Antes de aprovar qualquer CAPEX de hardware, confirmar que os itens de software da Seção 16.3 foram aplicados e que o bottleneck real foi identificado via profiling. Sem isso, hardware novo não resolve problema — apenas mascara custo.

---

## 17. Ordem ideal de aplicação das sugestões (adicionado em 15/abr/2026)

Esta seção é a sequência executável das decisões das Seções 16 e do plano de sprints (Seção 13). Cada bloco tem pré-requisitos claros, e nenhum bloco deve começar antes do anterior estar validado. A ordem é pensada para: (a) entregar ganhos mensuráveis primeiro, (b) construir observability antes de otimizar, (c) só gastar CAPEX depois de esgotar software, (d) ir de paper trading a live trading com risco controlado.

### Bloco 0 — Pré-flight (Dia 0-3, antes do Sprint 1)

Requisitos de ambiente e governança antes de qualquer código. Executado na estação Windows 11 do usuário.

1. Confirmar specs da máquina atual (CPU model, espaço NVMe livre) e atualizar Seção 2 do CLAUDE.md. Confirmar também: build do Windows 11 Pro, versão do WSL2 (`wsl -l -v` no PowerShell), distro Ubuntu 22.04 instalada e ativa.
2. Criar repositório git local com branch `main` protegida e estrutura de diretórios da Seção 6 **dentro do WSL2** (`~/finanalyticsai/`), nunca em `/mnt/c/`.
3. **No Windows 11 host:** instalar Docker Desktop 4.26+ com backend WSL2 habilitado; driver NVIDIA para Windows 535+ (com suporte a CUDA-on-WSL); Windows Terminal. Habilitar em "Recursos do Windows": "Subsistema do Windows para Linux", "Plataforma de Máquina Virtual" e "Hyper-V". Criar `C:\Users\<usuario>\.wslconfig` com alocação de memória adequada (ex.: `memory=160GB`).
   **Dentro do WSL2 Ubuntu 22.04:** instalar NVIDIA Container Toolkit (`sudo apt install nvidia-container-toolkit`), Python 3.11 (via `pyenv` recomendado), git e utilitários base (`build-essential`, `curl`, `jq`, `unzip`).
   **NÃO instalar** driver NVIDIA nativo dentro do Ubuntu do WSL2 — ele usa o driver do Windows via passthrough. **NÃO instalar Ubuntu bare-metal, dual-boot ou substituir o Windows 11** — Windows 11 permanece como host.
4. Gerar e armazenar `.env` (dentro do WSL2, em `~/finanalyticsai/.env`) com variáveis da Seção 9 (não commitar). Preencher apenas o que estiver disponível; o resto fica para quando o usuário fornecer as keys.
5. Validar `nvidia-smi` dentro do WSL2 (deve mostrar a RTX 4090 com mesma VRAM que `nvidia-smi.exe` do host); `docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi`; acesso de rede para GitHub, PyPI e HuggingFace.
6. Fazer snapshot inicial do ambiente (Windows build, versões de WSL2/driver NVIDIA/Docker/CUDA/Python/libs) em `docs/baseline.md`.

### Bloco 1 — Observability primeiro (Semana 1-2, paralelo ao Sprint 1)

Sem telemetria não há como saber o que otimizar. Tudo depois depende disso.

7. Subir Prometheus + Grafana + Loki via docker-compose (stack mínima).
8. Exportadores: node_exporter (CPU/RAM/disco do WSL2), dcgm-exporter (GPU — funciona no WSL2 via container Docker), postgres_exporter, kafka-exporter.
9. Dashboard Grafana base: VRAM, GPU util, RAM, lag de Kafka, latência TimescaleDB. Adicionar painel de RAM do VM do WSL2 (node_exporter dentro do WSL2 expõe os limites do `.wslconfig`).
10. Instrumentar código Python com OpenTelemetry (traces para pipelines críticos).
11. Configurar alertas iniciais: VRAM > 20 GB, CPU > 90% por 5 min, disco < 20%, memória do WSL2 > 90% do teto configurado em `.wslconfig`.
12. Rodar **py-spy** e **PyTorch Profiler** em qualquer pipeline existente e registrar o baseline de performance. Sem isso, não há como provar que as otimizações funcionaram.

### Bloco 2 — Quick wins de configuração (Semana 2-3)

Ganhos imediatos sem mudanças de código substanciais.

13. Mixed Precision FP16 em todos os treinos PyTorch.
14. `gpu-memory-utilization=0.75` no vLLM.
15. Compressão Zstandard em Kafka (producer/topic config) e em artefatos MLflow.
16. Connection pooling TimescaleDB via pgBouncer (transaction pooling).
17. RAM disk (`tmpfs`) para diretório de dados temporários de treino e shuffle — no WSL2 usar `tmpfs` montado em `/mnt/ramdisk`; ajustar `.wslconfig` se necessário.
18. Scheduling rígido: jobs de treino nunca simultâneos (Airflow DAG com concurrency=1).
19. VRAM Manager como sidecar: processo que lê `nvidia-smi` a cada 10 s e publica alerta se > 20 GB.

**Gate de saída do Bloco 2:** comparar métricas contra o baseline do item 12. Registrar ganho efetivo no MLflow.

### Bloco 3 — Otimizações de modelo (Semana 4-8, Fase 1-2)

Aqui estão os maiores ganhos de VRAM e throughput.

20. Substituir inferência HuggingFace por **vLLM** com PagedAttention (Sprint 1-2).
21. Quantizar **FinGPT-8B** para GGUF q4_K_M (24→6 GB VRAM).
22. Ativar **Flash Attention 2** no LLM local.
23. **Gradient Checkpointing** nos treinos de LSTM e GNN.
24. **INT8 quantization** no FinBERT (usar Tensor Cores Ada).
25. **Batch dinâmico** no TorchServe (com padding inteligente).

**Gate de saída do Bloco 3:** confirmar que FinGPT-8B + FinBERT + LSTM coexistem em < 10 GB de VRAM durante pregão.

### Bloco 4 — Pipeline de dados (Semana 8-12, Fase 2)

Reduz latência de features e pressão sobre TimescaleDB.

26. **Feature store** em Redis (hot) + Parquet (cold) com TTL configurável por feature.
27. **Pipelines async** com `asyncio` e `aiokafka` para producers/consumers críticos.
28. Particionamento e compressão das hypertables do TimescaleDB (1 partição/dia, compressão após 7 dias).
29. Cache de queries frequentes no Redis com invalidação por evento Kafka.

### Bloco 5 — Avaliação de escopo (Mês 3-4, fim da Fase 2)

Ponto de decisão: quais modelos valem o custo?

30. Calcular **IC rolling** de cada modelo isolado (HMM, XGBoost, FinBERT, LSTM).
31. Calcular **IC combinado** vs. **IC marginal** de cada modelo.
32. **Decisão formal:** modelos com IC marginal < 0,02 ou com custo de VRAM/compute desproporcional são desativados ou convertidos para versão CPU (LightGBM).
33. Avaliar **rolling window** vs. retraining completo para cada modelo — adotar rolling window onde a acurácia não degrada > 10%.
34. Registrar a decisão no `docs/model_registry.md` com justificativa e data.

**Gate de saída do Bloco 5:** stack enxuta, só com modelos que pagam seu custo. Economia de VRAM/compute validada.

### Bloco 6 — Latência de execução (Mês 4-6, Fase 3)

Resolve o problema de latência B3 sem comprar hardware novo.

35. Contratar **VPS em São Paulo** (R$ 120-500/mês). Requisito mínimo: 4 vCPU, 8 GB RAM, disco NVMe, link com baixa latência à B3. Ubuntu Server 22.04 na VPS.
36. Configurar **WireGuard** entre dev local (WSL2) e VPS SP com chaves dedicadas. Túnel roda como serviço systemd no WSL2 e sobe automaticamente com o host.
37. Deployar **apenas o agente de execução** na VPS: recebe sinais do core (dev local no WSL2) via Kafka cross-VPN e envia ordens à corretora (IB/Alpaca).
38. Medir latência round-trip sinal → ordem → ACK por 7 dias e registrar no dashboard.
39. Failover: se túnel WireGuard cair, agente entra em modo "hold" (mantém posições, não abre novas).
40. Iniciar **paper trading** de 60 dias (Sprint 8/20).

**Gate de saída do Bloco 6:** 60 dias de paper trading com Sharpe > 1,5 e latência P99 < 200 ms.

### Bloco 7 — Produção on-premise (Mês 7-9, Fase 4)

Só entra aqui depois que os Blocos 1-6 estiverem todos validados. Profiling do item 12 + métricas do Bloco 5 comprovaram o bottleneck real. A máquina de dev (Windows 11 + WSL2) permanece como está; produção usa hardware novo, separado.

41. **Especificar hardware** com base no profiling: confirmar que o Tier Pragmático (Seção 16.4) é suficiente ou se precisa Tier Robust. SO: Ubuntu 22.04 LTS Server bare-metal.
42. **Cotar** com 3 fornecedores: servidor montado, colocation SP (Equinix, Ascenty, ODATA) e cross-connect B3.
43. **Aprovar CAPEX** formalmente (R$ 110 k Pragmático ou R$ 220 k Robust).
44. **Montar servidor** e rodar burn-in de 72 h no Ubuntu Server (stress CPU, GPU, memória, storage).
45. **Instalar em colocation SP**, configurar rede dual-carrier e cross-connect dedicado 1 Gbps à B3.
46. **Migrar stack** do dev (WSL2) para produção (Ubuntu Server) via Ansible/Terraform — zero configuração manual. Mesmas imagens Docker, mesma versão de CUDA, Python e dependências pinadas.
47. **Replicar** 14 dias de dados históricos em paralelo com a dev antes de cortar para produção.
48. **Cutover supervisionado** em janela off-hours (fim de semana).
49. **Live trading com 10%** do capital (Sprint 24).

**Gate de saída do Bloco 7:** 7 dias de live trading sem incidente, audit trail completo funcionando.

### Bloco 8 — Escala e resiliência (Mês 10-12, Fase 5-6)

50. Configurar **cloud burst** em RunPod/Vast para CTGAN/DRL mensal (Seção 16.6). Apenas jobs offline — nunca inferência em tempo real.
51. **Backup off-site**: réplica incremental de MinIO para Backblaze B2 ou Wasabi (R$ 100-300/mês).
52. **Audit trail imutável**: logs críticos em S3 Deep Archive com retenção de 5 anos (WORM).
53. Avaliar **upgrade para Tier Robust** com base em capacidade real.
54. Se o resultado justificar, adicionar **nó hot-standby** em segundo data center SP (réplica ativa com failover automático).
55. Expandir capital de 10% → 25% → 50% → 100% conforme KPI (Sprint 24 e seguintes).

### Princípios que nunca devem ser violados

- Não pular blocos. Bloco 7 sem Bloco 1 = hardware novo mascarando problema.
- Não aprovar CAPEX sem profiling (Seção 16.9).
- Não usar cloud US para execução em tempo real (Seção 16.6).
- Não misturar dev e produção no mesmo hardware em regime live (Seção 16.1).
- Não substituir o Windows 11 por Ubuntu bare-metal nem criar dual-boot na estação de dev (Seção 2, Decisão 13 e Seção 16.1). WSL2 cobre 100% das necessidades de desenvolvimento com paridade de stack com a produção.
- Todo ganho de otimização deve ser **medido contra o baseline** (item 12) e registrado no MLflow.
- Cada bloco tem um **gate de saída**. Sem gate passado, próximo bloco não inicia.

---

*FinAnalyticsAI — CLAUDE.md v1.3 | Gerado em Abril 2026 | Seções 16 e 17 adicionadas em 15/abr/2026 | v1.3 — correção de SO para Windows 11 + WSL2 na estação de dev em 16/abr/2026*
*Atualizar seção Estado Atual ao final de cada sprint.*
