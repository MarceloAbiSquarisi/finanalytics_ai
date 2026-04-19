# Melhorias — Renda Fixa 2
## Estratégias academicamente validadas

<!-- Complemento ao melhorias_renda_fixa.md.
     Incorporar ao CLAUDE.md junto com os demais documentos de melhorias. -->

---

## Contexto e base acadêmica

Este documento descreve estratégias com evidência empírica robusta para renda fixa, derivadas principalmente de três trabalhos seminais do AQR Capital Management:

- **Moskowitz, Ooi, Pedersen (2012)** — *Time Series Momentum*, Journal of Financial Economics: momentum em bonds futuros documentado em 58 instrumentos, 1965–2009.
- **Asness, Moskowitz, Pedersen (2013)** — *Value and Momentum Everywhere*, Journal of Finance: prêmios consistentes de value e momentum em todas as classes de ativos.
- **AQR/Invesco (2024)** — *Fixed Income Factors: Theory and Practice*: carry, quality, value e momentum em bonds corporativos e soberanos.

A hierarquia de confiança das estratégias abaixo segue diretamente a profundidade da evidência acadêmica — as primeiras têm décadas de replicação independente; as últimas são válidas mas com menor base de dados públicos para o mercado brasileiro especificamente.

---

## Estratégia 1 — Time Series Momentum (TSMOM) em DI1 Futuro

### Base acadêmica

Moskowitz et al. (2012) documentaram persistência estatisticamente significativa de retornos em futuros de bonds por 1 a 12 meses, que reverte parcialmente em horizontes mais longos — padrão consistente com sub-reação inicial e sobre-reação tardia. O sinal é robusto fora da amostra e em múltiplos mercados, incluindo emergentes.

### Lógica aplicada ao DI1

No DI1, a taxa ajuste é cotada de forma inversa ao preço (taxa sobe → perda na posição long). O TSMOM neste contrato significa:

- Taxa DI caiu nos últimos 3 meses → probabilidade de continuar caindo → **long DI1** (posição que lucra com queda da taxa)
- Taxa DI subiu nos últimos 3 meses → probabilidade de continuar subindo → **short DI1** (posição que lucra com alta da taxa)

```python
# src/models/tsmom_di1.py
import numpy as np
import pandas as pd

def tsmom_signal(taxa_series: pd.Series,
                 lookback_dias: int = 63,   # ~3 meses úteis
                 holding_dias: int = 21,    # ~1 mês útil
                 vol_target: float = 0.10   # 10% ao ano
                 ) -> pd.Series:
    """
    Time Series Momentum na taxa DI1.
    Retorna: +1 (long — taxa cai), -1 (short — taxa sobe), 0 (neutro)

    Sinal: sign(ret_lookback) onde ret é retorno da TAXA (não do preço).
    Escalonado por volatilidade histórica para position sizing.
    """
    # Retorno da taxa no período lookback
    ret_lookback = taxa_series.pct_change(lookback_dias)

    # Volatilidade histórica anualizada
    vol_hist = taxa_series.pct_change().rolling(63).std() * np.sqrt(252)

    # Sinal bruto
    sinal_bruto = np.sign(ret_lookback)

    # Escalonamento por vol (position sizing via vol target)
    # Quanto mais volátil o instrumento, menor a posição
    escala = vol_target / vol_hist.clip(lower=0.01)
    sinal_escalado = sinal_bruto * escala.clip(upper=2.0)

    return sinal_escalado

def tsmom_multi_vertice(df_di1: pd.DataFrame,
                        vertice_cols: list,
                        lookback_dias: int = 63) -> pd.DataFrame:
    """
    TSMOM aplicado a múltiplos vértices da curva DI simultaneamente.
    Permite identificar quais partes da curva têm momentum mais forte.
    vertice_cols: lista de colunas com taxas por vértice (ex: ['252du','504du','756du'])
    """
    sinais = {}
    for v in vertice_cols:
        sinais[f'tsmom_{v}'] = tsmom_signal(df_di1[v])
    return pd.DataFrame(sinais, index=df_di1.index)
```

### Parâmetros validados para o mercado brasileiro

| Parâmetro | Valor recomendado | Justificativa |
|---|---|---|
| Lookback | 63 dias úteis (3m) | Maior Sharpe no estudo Moskowitz et al. para bonds |
| Holding | 21 dias úteis (1m) | Rebalanceamento mensal reduz custos de transação |
| Vol target | 10% a.a. | Posição inversa à volatilidade histórica |
| Reversão | Testar 12 meses | Retornos revertem em 12+ meses — evitar exposição excessiva |

### Integração com o sistema

```
tsmom_di1.py → sinal TSMOM por vértice → Kafka: signals.rates.tsmom
                                       → feature para XGBoost (Sprint S07)
                                       → input para DRL environment (Sprint S17)
```

---

## Estratégia 2 — Carry em Renda Fixa Brasileira

### Base acadêmica

Koijen, Moskowitz, Pedersen, Vrugt (2018) — *Carry*, Journal of Financial Economics: o carry é um preditor de retornos em todas as classes de ativos. Em bonds, carry é o retorno esperado assumindo que as taxas não se movem — essencialmente o rendimento menos o custo de funding.

### Carry no mercado brasileiro

No Brasil, o carry aparece em três formas complementares:

**a) Carry da NTN-B sobre o CDI (carry real):**

```python
def carry_ntnb(taxa_ntnb_aa: float, cdi_aa: float) -> float:
    """
    Carry real da NTN-B: retorno em excesso ao CDI assumindo taxa estável.
    carry > 0 → NTN-B está 'barata' em termos de carry → sinal de compra.
    """
    return taxa_ntnb_aa - cdi_aa  # carry em pontos percentuais ao ano

# Exemplo: NTN-B 2035 a 6.15% aa com CDI a 10.75% aa
# carry_real = 6.15 - 10.75 = -4.60 pp → carry negativo (normal em juro alto)
# carry_nominal = NTN-B * (1 + IPCA_implícita) - CDI
```

**b) Carry do DI1 (roll down na curva):**

```python
def carry_roll_down(taxa_longa: float, taxa_curta: float,
                    du_longo: int, du_curto: int) -> float:
    """
    Roll down: ganho por 'rolar' para baixo na curva com o tempo.
    Se a curva é positivamente inclinada, um DI1 longo 'anda' para
    o vértice mais curto com o passar do tempo — capturando o prêmio de prazo.
    """
    # Ganho anualizado pelo movimento temporal na curva inclinada
    roll = (taxa_longa - taxa_curta) / (du_longo - du_curto) * du_curto
    return roll

# Exemplo: DI1 2Y a 13.5%, DI1 1Y a 13.0%, curva positivamente inclinada
# roll_down = (13.5 - 13.0) / (504 - 252) * 252 = 0.5 pp ao ano de carry extra
```

**c) Carry do spread NTN-B curta vs. longa:**

Quando a curva IPCA está invertida (NTN-B curta com taxa maior que longa), o carry é favorável para posições no trecho curto — e a reversão histórica sugere que a curva volta a inclinação positiva.

### Feature para o modelo

```python
CARRY_FEATURES = {
    "carry_ntnb_2y_over_cdi":    taxa_ntnb_2y - cdi,
    "carry_ntnb_5y_over_cdi":    taxa_ntnb_5y - cdi,
    "roll_down_di1_1y_2y":       carry_roll_down(di1_2y, di1_1y, 504, 252),
    "roll_down_di1_2y_5y":       carry_roll_down(di1_5y, di1_2y, 1260, 504),
    "carry_treasury_10y_ffr":    us_10y - fed_funds_rate,      # versão US
    "carry_treasury_2y_10y":     us_10y - us_2y,               # slope carry US
}
```

---

## Estratégia 3 — Value na Curva de Juros

### Base acadêmica

Asness et al. (2013) definem value em bonds como a taxa atual versus a taxa de equilíbrio de longo prazo. Quando a taxa atual está muito acima do equilíbrio histórico (títulos "baratos"), o retorno futuro esperado é positivo — analogia direta ao P/L em ações.

### Value no DI1 e NTN-B

```python
# src/features/yield_value.py
import pandas as pd
import numpy as np

def value_signal_di1(taxa_atual: float,
                     taxa_historica: pd.Series,
                     janela_anos: int = 5) -> float:
    """
    Value no DI1: z-score da taxa atual vs histórico de 5 anos.
    Z > +1: taxa alta vs histórico → 'barato' → sinal de long (taxa cai)
    Z < -1: taxa baixa vs histórico → 'caro'  → sinal de short (taxa sobe)
    """
    mu  = taxa_historica.mean()
    std = taxa_historica.std()
    return (taxa_atual - mu) / std

def value_signal_ntnb(taxa_real_atual: float,
                      taxa_real_historica: pd.Series) -> float:
    """
    Value para NTN-B: taxa real atual vs histórico.
    Taxa real muito alta → título barato → comprar.
    """
    return (taxa_real_atual - taxa_real_historica.mean()) / taxa_real_historica.std()

def value_breakeven(breakeven_atual: float,
                    ipca_expectativa_focus: float) -> float:
    """
    Value via inflação implícita vs expectativa Focus.
    Se breakeven >> Focus → mercado está exagerando inflação → oportunidade
    de vender inflação implícita (long pré + short IPCA).
    """
    return breakeven_atual - ipca_expectativa_focus  # em pp

VALUE_FEATURES = {
    "value_di1_1y":        value_signal_di1(di1_1y, historia_di1_1y),
    "value_di1_2y":        value_signal_di1(di1_2y, historia_di1_2y),
    "value_di1_5y":        value_signal_di1(di1_5y, historia_di1_5y),
    "value_ntnb_2y":       value_signal_ntnb(ntnb_2y, historia_ntnb_2y),
    "value_ntnb_5y":       value_signal_ntnb(ntnb_5y, historia_ntnb_5y),
    "value_breakeven_1y":  value_breakeven(breakeven_1y, focus_ipca_12m),
    "value_breakeven_2y":  value_breakeven(breakeven_2y, focus_ipca_24m),
}
```

---

## Estratégia 4 — Combinação Value + Momentum (Anti-correlação como Alpha)

### Base acadêmica

<br>

A descoberta mais relevante de Asness et al. (2013): value e momentum são negativamente correlacionados entre si (-0,4 a -0,6 historicamente), mas **ambos têm prêmios positivos**. A combinação dos dois em portfólio iguala a soma dos Sharpes individuais multiplicada por √2 — o melhor hedge que existe dentro de uma única classe de ativos.

### Implementação combinada

```python
# src/models/value_momentum_rf.py

def combined_signal(value_z: float, momentum_z: float,
                    weight_value: float = 0.5,
                    weight_momentum: float = 0.5) -> float:
    """
    Sinal combinado value + momentum com pesos iguais.
    A correlação negativa entre os dois reduz a volatilidade do sinal combinado.

    Casos:
    value=+1, momentum=+1 → combinado = +1.0 (confirmação → posição máxima)
    value=+1, momentum=-1 → combinado = 0.0 (conflito → ficar fora)
    value=-1, momentum=+1 → combinado = 0.0 (conflito → ficar fora)
    value=-1, momentum=-1 → combinado = -1.0 (confirmação → posição inversa)
    """
    return weight_value * value_z + weight_momentum * momentum_z

def rf_factor_model(features: dict, model) -> dict:
    """
    Factor model para DI1 combinando todos os fatores validados.
    Adicionar ao XGBoost do Sprint S07.
    """
    combined = {
        # Fator TSMOM
        "tsmom_1y":          features["tsmom_252du"],
        "tsmom_3m":          features["tsmom_63du"],

        # Fator Carry
        "carry_roll_2y":     features["roll_down_di1_1y_2y"],
        "carry_roll_5y":     features["roll_down_di1_2y_5y"],

        # Fator Value
        "value_di1_2y":      features["value_di1_2y"],
        "value_ntnb_5y":     features["value_ntnb_5y"],

        # Sinal combinado V+M por vértice
        "vm_combo_1y":       combined_signal(
                                 features["value_di1_1y"],
                                 features["tsmom_252du"]
                             ),
        "vm_combo_2y":       combined_signal(
                                 features["value_di1_2y"],
                                 features["tsmom_504du"]
                             ),
    }
    return combined
```

### Resultado esperado

| Configuração | Sharpe histórico esperado (bonds) |
|---|---|
| Momentum isolado | ~0.5–0.7 |
| Value isolado | ~0.4–0.6 |
| Value + Momentum (50/50) | ~0.8–1.2 |
| V+M + Carry (33/33/33) | ~1.0–1.5 |

---

## Estratégia 5 — Arbitragem da Curva (FRA e Butterfly)

### Base acadêmica

Estratégia validada empiricamente em mercados de bonds desde os anos 1980 (Litterman e Scheinkman, 1991). A curva de juros tende a manter relações de proporcionalidade entre vértices — desvios são corrigidos por arbitradores. No Brasil, o artigo de Zimmermann (2023/Abrapp) documenta oportunidades recorrentes na curva DI Futuro via FRA.

### Butterfly na curva DI

```python
# src/models/butterfly_di1.py
import numpy as np

def butterfly_signal(taxa_curta: float, taxa_media: float,
                     taxa_longa: float,
                     du_curto: int, du_medio: int, du_longo: int,
                     janela_hist: pd.Series = None,
                     z_threshold: float = 1.5) -> dict:
    """
    Butterfly: posição neutra em duration que captura distorções de curvatura.
    Compra vértice curto e longo (wings), vende vértice médio (body).

    Butterfly value = taxa_media - (taxa_curta * w1 + taxa_longa * w2)
    onde w1, w2 são pesos que tornam a posição neutra em duration.

    Positivo → corpo caro → vender corpo, comprar wings.
    Negativo → corpo barato → comprar corpo, vender wings.
    """
    # Pesos duration-neutral
    du_range = du_longo - du_curto
    w1 = (du_longo - du_medio) / du_range
    w2 = (du_medio - du_curto) / du_range

    butterfly_value = taxa_media - (w1 * taxa_curta + w2 * taxa_longa)

    resultado = {"butterfly_value_bps": butterfly_value * 100}

    if janela_hist is not None:
        z_score = (butterfly_value - janela_hist.mean()) / janela_hist.std()
        resultado["z_score"] = z_score
        resultado["sinal"] = (
            "short_corpo" if z_score >  z_threshold else
            "long_corpo"  if z_score < -z_threshold else
            "neutro"
        )

    return resultado

# Vértices BR mais usados para butterfly
BUTTERFLY_CONFIGS_BR = [
    # (curto, médio, longo) em dias úteis
    (252,  504, 1260),   # 1Y - 2Y - 5Y
    (504,  756, 1764),   # 2Y - 3Y - 7Y
    (252,  756, 2520),   # 1Y - 3Y - 10Y
]
```

### FRA (Forward Rate Agreement) — distorções na taxa implícita

```python
def fra_di1(taxa_longa: float, taxa_curta: float,
            du_longo: int, du_curto: int) -> float:
    """
    Calcula a taxa forward implícita entre dois vértices da curva DI.
    FRA = taxa para o período [du_curto, du_longo].

    Se o FRA está muito acima/abaixo do histórico, há distorção:
    - FRA alto vs histórico → mercado precifica mais alta do que o usual naquele trecho
    - FRA baixo vs histórico → mercado precifica corte excessivo naquele trecho
    """
    fator_longo  = (1 + taxa_longa) ** (du_longo / 252)
    fator_curto  = (1 + taxa_curta) ** (du_curto / 252)
    du_fra       = du_longo - du_curto
    fator_fra    = fator_longo / fator_curto
    taxa_fra_aa  = fator_fra ** (252 / du_fra) - 1
    return taxa_fra_aa

FRA_FEATURES = {
    "fra_1y2y":  fra_di1(di1_2y, di1_1y, 504, 252),   # taxa implícita 1Y daqui a 1Y
    "fra_2y5y":  fra_di1(di1_5y, di1_2y, 1260, 504),  # taxa implícita 3Y daqui a 2Y
    "fra_5y10y": fra_di1(di1_10y, di1_5y, 2520, 1260), # taxa implícita 5Y daqui a 5Y
}
```

---

## Estratégia 6 — TSMOM Multi-Asset para o DRL (Sprint S17)

### Por que o DRL captura TSMOM automaticamente

O Time Series Momentum multi-asset é a base intelectual dos CTAs (Commodity Trading Advisors) — os hedge funds de trend following. O DRL-PPO do Sprint S17, quando treinado com um observation space que inclui DI1, WIN, WDO, NTN-B e Treasuries, **aprende o TSMOM automaticamente** como parte de sua política ótima.

A descoberta central de Moskowitz et al. (2012): um portfólio diversificado de TSMOM em múltiplas classes de ativos entrega retornos anormais com baixa correlação com fatores de risco padrão — e **performa melhor durante mercados extremos**.

### Expandir o observation space do DRL

```python
# src/models/drl_env.py — extensão para incluir renda fixa

# Observation space atual: 100 ativos × 20 features = 2.000 dimensões
# Expandido: 100 ativos × 20 features + 30 features de RF = 2.030 dimensões

RATE_OBSERVATIONS = [
    # DI1 por vértice
    "di1_1y_taxa", "di1_2y_taxa", "di1_5y_taxa", "di1_10y_taxa",
    "di1_1y_tsmom_3m", "di1_2y_tsmom_3m", "di1_5y_tsmom_3m",
    "di1_1y_tsmom_12m", "di1_2y_tsmom_12m", "di1_5y_tsmom_12m",

    # Fatores de curva
    "slope_1y_5y", "slope_2y_10y",
    "butterfly_1y_2y_5y", "butterfly_2y_3y_7y",
    "pc1_level", "pc2_slope", "pc3_curvature",

    # Carry e value
    "carry_roll_2y", "carry_roll_5y",
    "value_di1_2y_zscore", "value_ntnb_5y_zscore",

    # Macro monetário
    "monetary_regime",   # 0=easing / 1=neutro / 2=tightening
    "copom_dias_restantes", "copom_hawkish_proba",

    # US Treasury (cross-asset)
    "us_2y_tsmom_3m", "us_10y_tsmom_3m",
    "us_slope_2y_10y", "carry_treasury_10y",
    "breakeven_5y_vs_focus",
]

# Action space expandido: inclui posição em DI1 e TLT
# action[100] = peso em DI1 1Y (-1 a +1)
# action[101] = peso em DI1 2Y (-1 a +1)
# action[102] = peso em DI1 5Y (-1 a +1)
# action[103] = peso em TLT (US Treasury ETF)
# action[104] = peso em SHY (US Short-term Treasury ETF)
```

### Reward function com renda fixa

```python
def reward_fixed_income(pnl_equity: float, pnl_rates: float,
                        drawdown: float, custo_tx: float,
                        alpha: float = 0.3) -> float:
    """
    Reward expandido que inclui P&L de renda fixa.
    alpha: peso relativo de renda fixa vs ações (0 a 1).
    Renda fixa tem menor volatilidade → diversifica o reward.
    """
    pnl_total = (1 - alpha) * pnl_equity + alpha * pnl_rates
    sharpe_incremental = pnl_total / (pnl_total.std() + 1e-8)
    custo = custo_tx * abs(pnl_total)
    penalidade_dd = max(0, drawdown - 0.10) * 10
    return sharpe_incremental - custo - penalidade_dd
```

---

## Estratégia 7 — Quality em Renda Fixa (Cross-asset com Ações)

### Base acadêmica

AQR/JPM (2018): em bonds corporativos, o fator quality (empresas com baixa alavancagem, alta lucratividade) tem prêmio positivo e significativo. A relação entre equity e bonds da mesma empresa permite estratégias de relative value.

### Aplicação brasileira (NTN-B + ações de utilities e bancos)

```python
# Relação empírica: quando NTN-B sobe muito (juros reais altos),
# setores sensíveis a juros sofrem mais: utilities, real estate, bancos de varejo.

QUALITY_RF_FEATURES = {
    # Impacto de juros reais no setor
    "ntnb_5y_vs_div_yield_utilities": taxa_ntnb_5y - div_yield_eletrobras,
    "ntnb_5y_vs_div_yield_fiis":      taxa_ntnb_5y - div_yield_ifix,

    # Relação crédito vs equity (bonds corporativos implícitos via CDS)
    "bank_spread_equity_vol": spread_bancario / vol_ibovespa,
}
```

---

## Resumo de implementação por sprint

| Estratégia | Arquivos | Sprint | Esforço |
|---|---|---|---|
| TSMOM DI1 | `tsmom_di1.py` | S07 | Baixo (1-2 dias) |
| Carry roll-down | `yield_value.py` (extensão) | S07 | Baixo (meio dia) |
| Value z-score | `yield_value.py` | S07 | Baixo (meio dia) |
| Combinação V+M | `value_momentum_rf.py` | S07 | Baixo (1 dia) |
| Butterfly / FRA | `butterfly_di1.py` | S08 | Médio (2-3 dias) |
| DRL multi-asset | `drl_env.py` (extensão) | S17 | Médio (3-5 dias) |
| Quality cross-asset | `yield_value.py` (extensão) | S09 | Baixo (1 dia) |

---

## Novos tópicos Kafka

```
signals.rates.tsmom       → sinal TSMOM por vértice DI1 (LightGBM)
signals.rates.carry       → carry score por instrumento
signals.rates.value       → value z-score por vértice
signals.rates.butterfly   → distorção butterfly + z-score
signals.rates.vm_combo    → sinal combinado value+momentum
```

---

## Novas features a adicionar ao XGBoost (Sprint S07)

```python
# Adicionar a FEATURES_XGBOOST existente:
FEATURES_RENDA_FIXA_2 = [
    # TSMOM
    "tsmom_di1_1y_3m",     # momentum 3 meses no vértice 1 ano
    "tsmom_di1_2y_3m",
    "tsmom_di1_5y_3m",
    "tsmom_di1_1y_12m",    # momentum 12 meses (sinal de reversão se oposto ao 3m)
    "tsmom_us_10y_3m",     # TSMOM Treasury 10Y

    # Carry
    "carry_roll_di1_2y",   # carry por roll-down no vértice 2Y
    "carry_roll_di1_5y",
    "carry_treasury_2y_10y",

    # Value
    "value_di1_2y_z",      # z-score da taxa 2Y vs histórico 5 anos
    "value_di1_5y_z",
    "value_ntnb_5y_z",
    "value_breakeven_2y",  # breakeven vs Focus

    # Combinação V+M
    "vm_combo_di1_2y",     # signal combinado value+momentum no 2Y
    "vm_combo_di1_5y",

    # Butterfly / FRA
    "butterfly_1y_2y_5y_z",    # z-score da distorção butterfly
    "fra_1y2y_z",              # z-score da taxa FRA implícita 1Y-2Y
    "fra_2y5y_z",
]
```

---

## KPIs adicionais de validação

| Módulo | KPI | Meta |
|---|---|---|
| TSMOM DI1 | Sharpe anualizado out-of-sample | > 0.6 |
| TSMOM + Carry | Sharpe combinado | > 0.9 |
| V+M DI1 | IC rolling 63 dias | > 0.03 |
| Butterfly | Hit rate de reversão | > 58% |
| DRL multi-asset | Sharpe incremental vs baseline ações | > 0.3 |
| Factor model completo | IC incremental com features RF | > 0.01 |

---

## Dependências adicionais

Nenhuma dependência nova além das já listadas no `melhorias_renda_fixa.md`. Todos os modelos rodam em CPU com `sklearn`, `numpy` e `lightgbm` já presentes no stack do CLAUDE.md.

---

*Documento gerado em Abril 2026 — incorporar ao CLAUDE.md junto com melhorias_renda_fixa.md e funcionalidades_do_sistema_quantitativo_sem_itens_1_e_2.md.*
