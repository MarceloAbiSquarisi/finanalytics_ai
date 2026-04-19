Adicio# Melhorias — Renda Fixa

<!-- Documento de roadmap de renda fixa para o FinAnalyticsAI.
     Incorporar ao CLAUDE.md junto com funcionalidades_do_sistema_quantitativo.md em momento posterior. -->

---

## Visão geral

Este documento consolida as funcionalidades de renda fixa a serem implementadas no FinAnalyticsAI. O mercado de renda fixa brasileiro — especialmente via DI1 Futuro e curva ANBIMA — oferece oportunidades de alpha complementares ao portfólio de ações, com dados gratuitos e infraestrutura já presente no sistema.

**Princípio guia:** todas as implementações devem reutilizar componentes existentes (HMM, XGBoost, FinBERT, TimescaleDB, Kafka, Airflow) — renda fixa é uma extensão, não uma reescrita.

---

## Instrumentos e fontes de dados

### Brasil

| Instrumento | Fonte | Custo | Latência |
|---|---|---|---|
| DI1 Futuro (tick) | Profit DLL — já integrada | Zero | Real-time |
| DI1 Futuro (histórico) | PYield (`b3.futuro`) | Zero | EOD |
| Curva pré-fixada (ETTJ PRE) | API ANBIMA REST | Zero | 20h diário |
| Curva IPCA (ETTJ IPCA) | API ANBIMA REST | Zero | 20h diário |
| NTN-B / LTN / NTN-F (preços indicativos) | PYield (`ntnb`, `ltn`, `ntnf`) | Zero | 20h diário |
| VNA (LFT, NTN-B, NTN-C) | API ANBIMA REST | Zero | 10h diário |
| Atas COPOM / Relatório Focus | BCB RSS + site | Zero | Semanal / reunião |
| SELIC, IPCA, CDI, PTAX | BCB SGS API | Zero | Diário |

### Estados Unidos

| Instrumento | Fonte | Custo | Latência |
|---|---|---|---|
| Treasury yields (3m → 30Y) | FRED API — já integrada | Zero | EOD |
| Fed Funds Rate | FRED série DFF | Zero | Diário |
| Breakeven inflation (5Y, 10Y) | FRED séries T5YIE, T10YIE | Zero | Diário |
| ETFs TLT / SHY / BND | Alpaca / Polygon — já integrados | Plano existente | Real-time |

### Biblioteca Python recomendada

```bash
pip install pyield
```

```python
from pyield import ltn, ntnb, ntnf, b3, bc

# DI1 Futuro histórico
df_di1 = b3.futuro("31-05-2024", "DI1")
# Colunas: data_referencia, codigo_negociacao, data_vencimento, dias_uteis, taxa_ajuste

# NTN-B taxas indicativas ANBIMA
df_ntnb = ntnb.dados("23-08-2024")

# Opções digitais COPOM — probabilidade implícita de corte/manutenção/alta
# from pyield import selic  # probabilidades implícitas de mercado

# Interpolação flat forward (convenção 252 du/ano)
from pyield import Interpolador
interp = Interpolador(df_di1["dias_uteis"], df_di1["taxa_ajuste"], metodo="flat_forward")
taxa_45du = interp(45)
```

---

## Tier 1 — Implementar agora

> Reutiliza infra existente. Custo de integração baixo. Alto impacto imediato.

### 1.1 DI1 Futuro — estratégia direcional com ML

**O que é:** contrato futuro na B3 referenciado à taxa DI (CDI). Liquidez diária ~R$ 500 bilhões. O mais líquido da renda fixa brasileira.

**Lógica da estratégia:**
- Modelo prevê a direção da taxa DI nos próximos 5 pregões: `+1` (taxa sobe = preço cai) / `0` (neutro) / `-1` (taxa cai = preço sobe).
- Sinal `+1` → short no DI1 (ou long em contrato de taxa); sinal `-1` → long no DI1.
- Threshold de confiança: `proba > 0.60` para ativar a operação.

**Features do modelo:**

```python
FEATURES_DI1 = [
    # Curva de juros
    "slope_2y_10y",          # DI1 2 anos − DI1 10 anos (inclinação)
    "slope_1y_5y",           # DI1 1 ano − DI1 5 anos
    "curvatura_butterfly",   # DI1_2Y − (DI1_1Y + DI1_5Y) / 2

    # Macro BR
    "selic_over",            # SELIC Over diária (BCB SGS série 11)
    "ipca_12m",              # IPCA acumulado 12 meses
    "ipca_expectativa_12m",  # Focus — mediana expectativa 12m
    "copom_dias_restantes",  # dias úteis até próxima reunião COPOM
    "copom_alta_proba",      # probabilidade implícita de alta (opções digitais)

    # Macro global
    "fed_funds_rate",        # Fed Funds (FRED DFF)
    "us_10y_yield",          # Treasury 10Y (FRED DGS10)
    "vix",                   # VIX (FRED VIXCLS)
    "ptax",                  # PTAX compra (BCB SGS série 1)

    # Técnicos do DI1
    "di1_ret_1d",            # retorno 1 dia na taxa ajuste
    "di1_ret_5d",            # retorno 5 dias
    "di1_vol_20d",           # volatilidade histórica 20 dias
]
```

**Modelo:** `LightGBMClassifier` (CPU, reutiliza pipeline do Sprint S07).

**Integração com a infraestrutura:**

```
Profit DLL (DI1 tick) ──► rates_agent.py ──► market.rates.di1 (Kafka)
PYield (DI1 histórico) ─────────────────► yield_curves (TimescaleDB)
ANBIMA API (curva ETTJ) ─► yield_agent.py ─► yield_curves (TimescaleDB)
BCB SGS (SELIC, IPCA) ──► macro.py (já existe) ──► macro_data (TimescaleDB)

yield_curves + macro_data ──► feature_worker.py ──► features_rf (TimescaleDB)
features_rf ──► signal_agent_rf.py (LightGBM DI1) ──► signals.rates.di1 (Kafka)
signals.rates.di1 ──► execution_agent.py ──► ordem corretora
```

**Novos tópicos Kafka:**

```
market.rates.di1          → ticks DI1 em tempo real (via Profit DLL)
market.rates.ntnb         → preços indicativos NTN-B (ANBIMA, diário)
signals.rates.di1         → sinal direcional DI1 (LightGBM)
signals.rates.us_treasury → sinal direcional Treasuries (Random Forest)
```

**Schema TimescaleDB — nova hypertable:**

```sql
CREATE TABLE IF NOT EXISTS yield_curves (
    time           TIMESTAMPTZ NOT NULL,
    market         TEXT NOT NULL,        -- 'br_pre' | 'br_ipca' | 'us_treasury'
    vertice_du     INTEGER,              -- dias úteis (BR) ou calendar days (US)
    taxa_aa        DOUBLE PRECISION,     -- taxa ao ano (%)
    taxa_real_aa   DOUBLE PRECISION,     -- taxa real (apenas IPCA-linked)
    preco_pu       DOUBLE PRECISION,     -- PU ANBIMA (apenas TPF)
    source         TEXT                  -- 'anbima' | 'fred' | 'b3_ajuste'
);
SELECT create_hypertable('yield_curves', 'time', if_not_exists => TRUE,
    chunk_time_interval => INTERVAL '30 days');
CREATE INDEX IF NOT EXISTS idx_yc_market_vertice
    ON yield_curves (market, vertice_du, time DESC);

-- Tabela de inflação implícita (calculada)
CREATE TABLE IF NOT EXISTS breakeven_inflation (
    time           TIMESTAMPTZ NOT NULL,
    vertice_du     INTEGER,
    breakeven_aa   DOUBLE PRECISION,     -- (1+pré) / (1+real) - 1
    market         TEXT DEFAULT 'br'
);
SELECT create_hypertable('breakeven_inflation', 'time', if_not_exists => TRUE);
```

**Sprint de referência:** S07 (adicionar DI1 ao factor model existente).

---

### 1.2 Curva de juros ANBIMA — ingestão diária

**Novo Airflow DAG:** `yield_ingestion.py` — roda diariamente às 20h30 (após publicação ANBIMA).

```python
# dags/yield_ingestion.py
from airflow import DAG
from airflow.operators.python import PythonOperator
from datetime import datetime, timedelta
import pyield as yd
import asyncpg, asyncio

async def ingest_anbima_curves(data_ref: str):
    """Ingere curvas pré e IPCA da ANBIMA para a data de referência."""
    conn = await asyncpg.connect(TIMESCALE_URL)

    # Curva pré-fixada (LTN + NTN-F)
    df_pre = yd.ltn.dados(data_ref)
    # Curva IPCA (NTN-B)
    df_ipca = yd.ntnb.dados(data_ref)

    rows = []
    for _, r in df_pre.iterrows():
        rows.append((data_ref, 'br_pre', int(r['dias_uteis']),
                     float(r['taxa_indicativa']), None, float(r['pu']), 'anbima'))
    for _, r in df_ipca.iterrows():
        rows.append((data_ref, 'br_ipca', int(r['dias_uteis']),
                     float(r['taxa_indicativa']), float(r['taxa_real']),
                     float(r['pu']), 'anbima'))

    await conn.copy_records_to_table('yield_curves', records=rows,
        columns=['time','market','vertice_du','taxa_aa','taxa_real_aa','preco_pu','source'])
    await conn.close()

with DAG('yield_ingestion', schedule_interval='30 20 * * 1-5',
         start_date=datetime(2024,1,1), catchup=False) as dag:
    ingest = PythonOperator(task_id='ingest_anbima',
        python_callable=lambda: asyncio.run(ingest_anbima_curves('hoje')))
```

**Sprint de referência:** S02 (adicionar ao pipeline de ingestão histórica).

---

### 1.3 Inflação implícita (breakeven)

Calculada automaticamente como subproduto das curvas pré e IPCA. Nenhum dado adicional necessário.

```python
# src/features/rates.py

def calcular_breakeven(df_pre, df_ipca, base=252):
    """
    Inflação implícita por vértice:
    breakeven = (1 + pré)^(1/base) / (1 + real)^(1/base) - 1
    anualizada: ((1 + breakeven_diario) ** base) - 1
    """
    merged = df_pre.merge(df_ipca, on='vertice_du', suffixes=('_pre','_real'))
    merged['breakeven_aa'] = (
        ((1 + merged['taxa_aa_pre'] / 100) / (1 + merged['taxa_real_aa'] / 100)) - 1
    )
    return merged[['vertice_du', 'breakeven_aa']]

def build_rate_features(conn, symbol: str, date: str) -> dict:
    """Features de renda fixa para o modelo de ações e DI1."""
    # Buscar curvas do TimescaleDB
    curve_pre  = fetch_curve(conn, 'br_pre',  date)
    curve_ipca = fetch_curve(conn, 'br_ipca', date)

    # PCA da curva pré (level, slope, curvature)
    pca_factors = pca_yield_curve(curve_pre['taxa_aa'].values)

    breakeven = calcular_breakeven(curve_pre, curve_ipca)

    return {
        "slope_2y_10y":   taxa_em_vertice(curve_pre, 504) - taxa_em_vertice(curve_pre, 2520),
        "slope_1y_5y":    taxa_em_vertice(curve_pre, 252) - taxa_em_vertice(curve_pre, 1260),
        "pc1_level":      pca_factors[0],   # movimento paralelo
        "pc2_slope":      pca_factors[1],   # inclinação
        "pc3_curvature":  pca_factors[2],   # curvatura
        "breakeven_1y":   breakeven.query("vertice_du == 252")['breakeven_aa'].iloc[0],
        "breakeven_2y":   breakeven.query("vertice_du == 504")['breakeven_aa'].iloc[0],
        "breakeven_5y":   breakeven.query("vertice_du == 1260")['breakeven_aa'].iloc[0],
    }
```

**Onde usar essas features:**
- No `XGBoostFactorModel` do Sprint S07 — `slope_2y_10y` é um dos preditores mais fortes para ações.
- No `LightGBM DI1` — todas as features acima são input direto.
- No `GAT` do Sprint S14 — slope como feature de nó para ativos correlacionados com juros (bancos, utilities, real estate).

---

## Tier 2 — Implementar nos sprints seguintes

### 2.1 HMM de ciclo monetário

**Segunda instância do HMM** (além do de equities do Sprint S03), especializada em detectar o ciclo de política monetária.

```python
# src/features/hmm_rates.py
from hmmlearn import hmm
import numpy as np

RATE_STATES = {0: "easing", 1: "neutro", 2: "tightening"}

class MonetaryCycleHMM:
    """
    3 estados: easing (corte de juros) / neutro / tightening (alta de juros).
    Features: SELIC over, slope 2Y-10Y, IPCA 12m, variação mensal da taxa.
    Mesmo padrão do HMM de equities — só muda o input.
    """
    def __init__(self, n_components=3, covariance_type="full"):
        self.model = hmm.GaussianHMM(
            n_components=n_components,
            covariance_type=covariance_type,
            n_iter=200
        )

    def fit(self, df):
        X = df[["selic_over", "slope_2y_10y", "ipca_12m", "delta_selic_1m"]].values
        self.model.fit(X)

    def predict_regime(self, df) -> np.ndarray:
        X = df[["selic_over", "slope_2y_10y", "ipca_12m", "delta_selic_1m"]].values
        return self.model.predict(X)

    def current_regime(self, df) -> dict:
        states = self.predict_regime(df)
        current = int(states[-1])
        proba = self.model.predict_proba(
            df[["selic_over", "slope_2y_10y", "ipca_12m", "delta_selic_1m"]].values
        )[-1]
        return {
            "regime": RATE_STATES[current],
            "regime_id": current,
            "proba": proba.tolist()
        }
```

**Publicar em Kafka:** `signals.hmm.monetary_cycle → {regime: "tightening", proba: [0.05, 0.12, 0.83]}`

**Impacto operacional por regime:**

| Regime | Ações | DI1 | WDO |
|---|---|---|---|
| Easing (corte) | Overweight — especialmente growth | Long (taxa cai → preço sobe) | Neutro ou short |
| Neutro | Neutro — seguir fator model | Neutro | Neutro |
| Tightening (alta) | Underweight — especialmente growth | Short (taxa sobe → preço cai) | Long (dólar tende a apreciar) |

**Sprint de referência:** S03 (extensão do HMM existente — arquivo `hmm_rates.py`).

---

### 2.2 PCA da curva de juros

Decomposição nos três fatores clássicos via PCA. Estratégias de relative value baseadas na estrutura da curva.

```python
# src/features/yield_pca.py
from sklearn.decomposition import PCA
import numpy as np

VERTICE_PADRAO = [63, 126, 252, 378, 504, 756, 1008, 1260, 1764, 2520]  # du

class YieldCurvePCA:
    """
    PC1 = Level   (movimento paralelo — responde à SELIC)
    PC2 = Slope   (inclinação — responde ao crescimento esperado)
    PC3 = Curvature (curvatura — responde à incerteza de médio prazo)
    """
    def __init__(self, n_components=3):
        self.pca = PCA(n_components=n_components)

    def fit(self, yield_matrix: np.ndarray):
        """yield_matrix: (n_dias, n_vertices)"""
        self.pca.fit(yield_matrix)
        self.explained_variance = self.pca.explained_variance_ratio_

    def transform(self, yield_matrix: np.ndarray) -> np.ndarray:
        """Retorna (n_dias, 3) com PC1, PC2, PC3."""
        return self.pca.transform(yield_matrix)

    def nelson_siegel_fit(self, taxa: np.ndarray, vertice: np.ndarray) -> dict:
        """
        Alternativa: ajuste paramétrico Nelson-Siegel.
        Retorna: {beta0: level, beta1: slope, beta2: curvature, lambda: decay}
        """
        from scipy.optimize import curve_fit

        def ns_curve(t, b0, b1, b2, lam):
            term1 = (1 - np.exp(-lam * t)) / (lam * t)
            term2 = term1 - np.exp(-lam * t)
            return b0 + b1 * term1 + b2 * term2

        params, _ = curve_fit(ns_curve, vertice, taxa,
                               p0=[0.12, -0.02, 0.01, 0.5], maxfev=5000)
        return dict(zip(['beta0', 'beta1', 'beta2', 'lambda'], params))
```

**Estratégias derivadas:**
- **Butterfly:** compra DI1 curto + vende DI1 médio + compra DI1 longo quando curvatura está positiva além do histórico.
- **Steepener/Flattener:** long no vértice curto e short no longo quando a curva está anormalmente inclinada.
- **Duration neutral:** posição balanceada por duration para capturar apenas o movimento relativo sem risco de taxa paralela.

---

### 2.3 Análise de atas COPOM com NLP em português

Extensão do pipeline FinBERT (Sprint S06) para o mercado brasileiro. Sinal hawkish/dovish antes e após reuniões COPOM.

```python
# src/models/copom_bert.py
from transformers import AutoTokenizer, AutoModelForSequenceClassification
import torch

class COPOMSentimentModel:
    """
    Base: BERTimbau (BERT pré-treinado em português, ~110M params, ~450 MB)
    Fine-tuning: atas COPOM históricas (2000–2024) + relatório Focus
    Labels: 0=dovish / 1=neutro / 2=hawkish
    VRAM: ~1.5 GB — compatível com janela off-hours do orçamento de 20 GB
    """
    MODEL_NAME = "neuralmind/bert-base-portuguese-cased"

    def __init__(self):
        self.tokenizer = AutoTokenizer.from_pretrained(self.MODEL_NAME)
        self.model = AutoModelForSequenceClassification.from_pretrained(
            self.MODEL_NAME, num_labels=3
        ).cuda()

    def predict(self, texto: str) -> dict:
        inputs = self.tokenizer(texto, return_tensors="pt",
                                truncation=True, max_length=512).to("cuda")
        with torch.no_grad():
            logits = self.model(**inputs).logits
        proba = torch.softmax(logits, dim=-1).cpu().numpy()[0]
        label = int(proba.argmax())
        labels = {0: "dovish", 1: "neutro", 2: "hawkish"}
        return {"sinal": labels[label], "proba": proba.tolist()}

    def analisar_ata(self, texto_ata: str) -> dict:
        """Divide ata em parágrafos e agrega sentimento."""
        paragrafos = [p for p in texto_ata.split('\n\n') if len(p) > 100]
        sinais = [self.predict(p) for p in paragrafos[:20]]  # primeiros 20
        proba_media = [sum(s['proba'][i] for s in sinais) / len(sinais) for i in range(3)]
        return {
            "sinal_geral": ["dovish","neutro","hawkish"][int(max(range(3), key=lambda i: proba_media[i]))],
            "proba_dovish":  round(proba_media[0], 4),
            "proba_neutro":  round(proba_media[1], 4),
            "proba_hawkish": round(proba_media[2], 4),
        }
```

**Fonte dos textos:**
- Atas COPOM: `https://www.bcb.gov.br/copom/atas` (BCB publica em PDF → extrair com `pdfminer`)
- Relatório Focus: RSS semanal do BCB `https://www.bcb.gov.br/api/servico/sitebcb/boletimfocus`

**Publicar em Kafka:** `signals.copom.sentiment → {sinal: "hawkish", proba_hawkish: 0.74, data_ata: "2024-11-06"}`

**Sprint de referência:** S06 (extensão do pipeline FinBERT — arquivo `copom_bert.py`).

---

### 2.4 Treasuries US — Random Forest (FRED)

Para o módulo US (finanalytics_us). O FRED já está integrado no Sprint S02.

```python
# src/models/treasury_rf.py
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import TimeSeriesSplit
import pandas as pd

FEATURES_TREASURY = [
    "fed_funds_rate",        # FRED: DFF
    "cpi_yoy",               # FRED: CPIAUCSL (variação 12m)
    "gdp_growth_qoq",        # FRED: A191RL1Q225SBEA
    "slope_3m_10y",          # FRED: DGS10 - DGS3MO (preditor de recessão)
    "slope_2y_10y",          # FRED: DGS10 - DGS2
    "vix",                   # FRED: VIXCLS
    "hy_spread",             # FRED: BAMLH0A0HYM2 (high yield spread)
    "us_10y_ret_1m",         # retorno do 10Y no último mês
    "us_10y_vol_3m",         # volatilidade histórica 3 meses
    "breakeven_5y",          # FRED: T5YIE
    "breakeven_10y",         # FRED: T10YIE
]

class TreasuryDirectionalModel:
    """
    Previsão direcional do US 10Y Treasury: +1 (yield sobe) / -1 (yield cai)
    Random Forest performa bem em yields de curto prazo (Applied Sciences, 2025).
    Execução: TLT (yield cai → preço ETF sobe) ou SHY (yield curto prazo) via Alpaca.
    """
    def __init__(self):
        self.model = RandomForestClassifier(
            n_estimators=500,
            max_features="sqrt",
            n_jobs=-1,
            random_state=42
        )

    def fit(self, df: pd.DataFrame, horizonte_dias: int = 21):
        X = df[FEATURES_TREASURY].dropna()
        y = (df['us_10y_yield'].shift(-horizonte_dias) > df['us_10y_yield']).astype(int)
        y = y.loc[X.index].dropna()
        X = X.loc[y.index]
        tscv = TimeSeriesSplit(n_splits=5)
        for train_idx, val_idx in tscv.split(X):
            self.model.fit(X.iloc[train_idx], y.iloc[train_idx])

    def predict_signal(self, features: dict) -> dict:
        X = pd.DataFrame([features])[FEATURES_TREASURY]
        proba = self.model.predict_proba(X)[0]
        signal = 1 if proba[1] > 0.60 else (-1 if proba[0] > 0.60 else 0)
        return {
            "signal": signal,
            "proba_alta": round(float(proba[1]), 4),
            "proba_baixa": round(float(proba[0]), 4),
            "etf_acao": "TLT short" if signal == 1 else ("TLT long" if signal == -1 else "neutro")
        }
```

**Sprint de referência:** S07 (extensão do factor model — arquivo `treasury_rf.py`).

---

## Tier 3 — Baixa prioridade ou alta complexidade

### 3.1 Spread de crédito em debêntures

Modelo XGBoost para prever spread de crédito de debêntures sobre o DI.

**Impedimentos atuais:**
- API ANBIMA de debêntures requer credencial institucional.
- Liquidez baixa — slippage pode eliminar todo o alpha modelado.
- Dados de rating S&P/Moody's local são pagos.

**Quando considerar:** após live trading com DI1 e NTN-B estabelecido e capital suficiente para operar em mercado secundário de debêntures.

### 3.2 Carry trade BR-US

Posição long em NTN-B (real yield BR ~6% aa) com hedge cambial via WDO ou NDF.

**Impedimentos atuais:**
- Requer margem em múltiplas corretoras simultaneamente.
- Risco cambial precisa ser neutralizado com precisão — complexidade operacional alta.
- Custo de hedge (WDO ou NDF) pode consumir o diferencial de carry.

**Quando considerar:** Fase 5-6 do CLAUDE.md, após DRL e gestão de risco avançada estarem em produção.

---

## Gestão de risco — especificidades de renda fixa

### Medidas de risco adicionais

```python
# src/portfolio/risk_fixed_income.py

def duration_risk(taxa_aa: float, vertice_du: int, qty: int, notional: float) -> dict:
    """
    Calcula DV01 (risco de 1 basis point) para posição em DI1 ou NTN-B.
    DV01 = preço × duration_modificada × 0.0001
    """
    duration_mod = vertice_du / 252 / (1 + taxa_aa)
    du01 = notional * duration_mod * 0.0001
    return {"dv01_brl": du01, "duration_anos": vertice_du / 252}

def position_limit_rf(regime: str, instrumento: str) -> dict:
    """
    Limites de posição por regime monetário.
    Mais conservador em tightening (taxa subindo = risco de duration).
    """
    limites = {
        "easing":     {"DI1": 0.15, "NTN-B": 0.20, "TLT": 0.10},
        "neutro":     {"DI1": 0.10, "NTN-B": 0.10, "TLT": 0.08},
        "tightening": {"DI1": 0.05, "NTN-B": 0.05, "TLT": 0.05},
    }
    return limites.get(regime, limites["neutro"])
```

### Regras específicas de renda fixa

| Regra | Valor | Racional |
|---|---|---|
| DV01 máximo por operação | R$ 5.000 | Risco de 1 bp por operação — escalável com capital |
| Concentração máxima DI1 | 15% do capital | Futuros de taxa têm leverage implícita |
| Stop DI1 em taxa | 15 basis points | Equivale a ~0,5% do notional |
| Zeragem antes COPOM | T-1 às 17h | Reunião COPOM cria risco direcional pontual alto |
| Duration máxima portfólio | 2 anos | Equilibra carry com risco de taxa |

---

## Integração com modelos de ações (cross-asset features)

As features de renda fixa melhoram o modelo de ações. Adicionar ao `XGBoostFactorModel` no Sprint S07:

```python
# Adicionar a FEATURES_XGBOOST existente:
FEATURES_RF_CROSS_ASSET = [
    "slope_2y_10y",          # curva inclinada → favorável para banks, ruim para growth
    "breakeven_1y",          # inflação implícita → impacto em utilities, real estate
    "pc1_level",             # nível de taxa → inversamente correlacionado com P/L múltiplo
    "pc2_slope",             # inclinação → preditor de crescimento econômico
    "monetary_regime",       # HMM monetário: 0=easing / 1=neutro / 2=tightening
    "copom_hawkish_proba",   # probabilidade hawkish da ata mais recente
    "di1_vol_20d",           # volatilidade DI1 → proxy de incerteza macro BR
    "us_10y_yield",          # taxa americana → impacto em ativos de risco BR
    "hy_spread",             # high yield spread US → appetite por risco global
]
```

---

## Novos arquivos a criar

| Arquivo | Responsabilidade | Sprint |
|---|---|---|
| `src/ingestion/yield_agent.py` | Ingestão diária ANBIMA + PYield | S02 |
| `src/features/rates.py` | Features de curva + breakeven + PCA | S07 |
| `src/features/yield_pca.py` | PCA e Nelson-Siegel da curva | S07 |
| `src/models/hmm_rates.py` | HMM ciclo monetário | S03 |
| `src/models/copom_bert.py` | BERTimbau para atas COPOM | S06 |
| `src/models/treasury_rf.py` | Random Forest para Treasuries US | S07 |
| `src/portfolio/risk_fixed_income.py` | DV01, duration, limites de posição | S19 |
| `dags/yield_ingestion.py` | DAG Airflow para ANBIMA diário | S02 |

---

## Novos tópicos Kafka

```
market.rates.di1           → ticks DI1 tempo real (Profit DLL)
market.rates.ntnb          → preços indicativos NTN-B (ANBIMA, diário)
signals.hmm.monetary_cycle → regime monetário atual (HMM)
signals.rates.di1          → sinal direcional DI1 (LightGBM)
signals.rates.us_treasury  → sinal direcional Treasuries (Random Forest)
signals.copom.sentiment    → hawkish/dovish das atas COPOM (BERTimbau)
```

---

## KPIs de validação

| Módulo | KPI | Meta |
|---|---|---|
| DI1 direcional | IC rolling 63 dias | > 0,04 |
| DI1 direcional | Acurácia out-of-sample | > 56% |
| HMM monetário | Acurácia de regime | > 70% (ciclos 2010–2024) |
| COPOM NLP | F1-score hawkish/dovish | > 0,75 |
| Treasuries RF | RMSFE 1 mês | < 30 bps |
| Cross-asset | IC incremental no factor model | > 0,01 sobre baseline sem RF features |

---

## Dependências adicionais

```txt
# Adicionar ao requirements.txt
pyield>=0.48.0        # dados BR renda fixa (ANBIMA, BCB, B3)
pdfminer.six>=20221105 # extração texto das atas COPOM (PDF)
scipy>=1.11.0          # Nelson-Siegel curve fitting
# BERTimbau via HuggingFace — já coberto pelo transformers>=4.36 do CLAUDE.md
```

---

*Documento gerado em Abril 2026 — incorporar ao CLAUDE.md junto com funcionalidades_do_sistema_quantitativo_sem_itens_1_e_2.md.*
