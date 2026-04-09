# Projeto Forex — Contexto e Configuração

## Visão Geral do Projeto

- **Corretora:** Pepperstone
- **Estilo de operação:** Day Trade
- **Ativos:** Commodities (foco em Ouro - XAUUSD, Petróleo - USOIL/UKOIL)
- **Metodologia base:** Oliver Velez (Pristine / iFundTraders)
- **Plataforma de trading:** cTrader ou MetaTrader 5 (disponíveis na Pepperstone)
- **Conta recomendada:** Razor (spread bruto + comissão)

---

## Metodologia — Oliver Velez

### Pilares
1. **Price Action puro** — gráfico limpo, sem excesso de indicadores
2. **Candles de sinal** — Engulfing, Inside Bar, Outside Bar, Hammer, Shooting Star
3. **Zonas de Suporte e Resistência** — preço respeita memória de níveis anteriores
4. **Pristine Buy/Sell Zone** — zonas de alta probabilidade identificadas por topos/fundos, gaps e consolidações

### Tipos de Trade
| Tipo           | Descrição                                              |
|----------------|--------------------------------------------------------|
| Trend Trade    | Entrada na direção da tendência em pullbacks           |
| Counter-Trend  | Reversão em zonas extremas (maior risco)               |
| Guerrilla Trade| Scalp rápido em momentum ("Hit and Run") — o mais popular |

### Guerrilla Trading
- Timeframe: 1 a 5 minutos
- Entrada em rompimentos de candles de força
- Stop curto, objetivo definido
- Saída parcial rápida para garantir lucro

### Regras de Gestão (Velez)
- Nunca mova o stop para pior
- Realize lucros parciais no primeiro alvo
- Consistência > performance pontual
- Mercado = jogo de probabilidades

---

## Commodities — Referência Rápida

| Commodity     | Símbolo  | Característica              |
|---------------|----------|-----------------------------|
| Petróleo WTI  | USOIL    | Alta liquidez, muito volátil|
| Petróleo Brent| UKOIL    | Referência global           |
| Ouro          | XAUUSD   | Safe haven, muito popular   |
| Prata         | XAGUSD   | Mais volátil que o ouro     |
| Gás Natural   | NATGAS   | Alta volatilidade sazonal   |
| Cobre         | COPPER   | Termômetro da economia global|

---

## Horários de Operação (Horário de Brasília)

| Ativo         | Melhor janela (Brasília)        |
|---------------|---------------------------------|
| Ouro / Prata  | ~05h–13h (abertura de Londres)  |
| Petróleo      | ~10h–14h (abertura de Nova York)|

> **Evitar:** baixa liquidez e períodos de notícias de alto impacto (ver calendário econômico)

---

## Calendário Econômico — Eventos Críticos

| Evento                  | Ativo afetado          | Dia/Frequência        |
|-------------------------|------------------------|-----------------------|
| EIA (estoques petróleo) | USOIL, UKOIL           | Toda quarta-feira     |
| NFP (Non-Farm Payrolls) | XAUUSD, DXY            | Primeira sexta do mês |
| CPI (inflação EUA)      | XAUUSD, DXY            | Mensal                |
| Decisão FED (juros)     | Todos os ativos        | ~8x por ano           |

---

## Gestão de Risco

- Risco máximo por operação: **1% a 2% do capital**
- Sempre usar **stop loss**
- Relação risco/retorno mínima: **1:2**
- Alavancagem sugerida para início: **1:10 a 1:20**
- Usar **ATR (Average True Range)** para calibrar o stop conforme a volatilidade

---

## Estrutura sugerida para o Projeto PyCharm

```
forex_project/
│
├── data/                        # Dados históricos e feeds
│   ├── raw/                     # OHLCV bruto
│   └── processed/               # Dados tratados
│
├── strategy/                    # Lógica de estratégia
│   ├── signal_detector.py       # Detecção de candles de sinal (Velez)
│   ├── support_resistance.py    # Mapeamento de zonas S/R
│   └── guerrilla_trade.py       # Lógica do Guerrilla Trading
│
├── risk/                        # Gestão de risco
│   ├── position_sizing.py       # Cálculo de lote por risco %
│   └── atr_stop.py              # Stop baseado no ATR
│
├── broker/                      # Integração com Pepperstone
│   └── pepperstone_api.py       # Conexão via MT5 ou cTrader API
│
├── journal/                     # Diário de trades
│   ├── trade_logger.py          # Registro de operações
│   └── trades.csv               # Base de dados de trades
│
├── backtest/                    # Backtesting
│   └── backtest_engine.py       # Motor de backtesting
│
├── reports/                     # Relatórios e análises
│   └── performance_report.py
│
├── config.py                    # Configurações globais (símbolos, risco, etc.)
├── main.py                      # Ponto de entrada do projeto
└── requirements.txt             # Dependências Python
```

---

## Dependências Python Sugeridas (requirements.txt)

```
MetaTrader5          # Integração com MT5 / Pepperstone
pandas               # Manipulação de dados
numpy                # Cálculos numéricos
ta                   # Indicadores técnicos (ATR, médias, RSI etc.)
mplfinance           # Gráficos de candlestick
matplotlib           # Visualizações gerais
plotly               # Gráficos interativos
backtesting          # Motor de backtesting (backtesting.py)
python-dotenv        # Gerenciamento de variáveis de ambiente
schedule             # Agendamento de tarefas
requests             # Chamadas HTTP (calendário econômico, etc.)
```

---

## Referências e Materiais

- **Livro:** "Tools and Tactics for the Master Day Trader" — Oliver Velez
- **Livro:** "Swing Trading" — Oliver Velez & Gavin Holmes
- **Site:** https://ifundtraders.com
- **Calendário econômico:** https://www.forexfactory.com | https://www.investing.com/economic-calendar/
- **Pepperstone:** https://pepperstone.com
- **MetaTrader 5 Python API:** https://www.mql5.com/en/docs/python_metatrader5

---

## Notas Fiscais (Brasil)

- Ganhos no forex são tributados como **ganho de capital**
- Alíquotas: 15% a 22,5% conforme o valor do ganho
- Declarar na Receita Federal (DIRPF)
- Consultar contador especializado em investimentos no exterior
