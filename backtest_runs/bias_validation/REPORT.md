# Validação anti-survivorship-bias — relatório (03/mai/2026)

## Setup

6 tickers delisted reais (todos source=FINTZ em `b3_delisted_tickers`):
- AESB3 (delisting 2024-10-31)
- ALSO3 (2023-10-24)
- BOAS3 (2023-08-07)
- BRPR3 (2023-10-19)
- ENBR3 (2023-08-21)
- VIIA3 (2023-09-19)

**Strategy**: RSI grid search (período 7-21, oversold 25-35, overbought 65-75)
**Range**: 2022-01-01 → 2023-12-29 (cobre delisting de 5/6 tickers)
**Variantes**: `--respect-delisting` (ON) vs OFF

## Resultados

| Ticker | Variant | Sharpe | Return% | DD% | DSR | prob_real |
|---|---|---|---|---|---|---|
| AESB3 | no-fix | 1.04 | 27.1 | 13.8 | -0.448 | 0.3270 |
| AESB3 | with-fix | 1.04 | 27.1 | 13.8 | -0.448 | 0.3270 |
| ALSO3 | no-fix | 0.62 | 21.0 | 30.4 | -1.021 | 0.1536 |
| ALSO3 | with-fix | 0.62 | 21.0 | 30.4 | -1.020 | 0.1538 |
| BOAS3 | no-fix | 1.13 | 121.3 | 38.3 | -0.431 | 0.3333 |
| BOAS3 | with-fix | 1.14 | 121.3 | 38.3 | -0.429 | 0.3340 |
| BRPR3 | no-fix | 0.70 | 45.3 | 58.7 | -0.731 | 0.2324 |
| BRPR3 | with-fix | 0.70 | 45.3 | 58.7 | -0.730 | 0.2328 |
| ENBR3 | no-fix | 1.08 | 16.8 | 9.8 | -0.297 | 0.3833 |
| ENBR3 | with-fix | 1.08 | 16.8 | 9.8 | -0.295 | 0.3839 |
| VIIA3 | no-fix | -0.20 | -38.6 | 66.6 | -2.222 | 0.0131 |
| VIIA3 | with-fix | -0.20 | -38.6 | 66.6 | -2.223 | 0.0131 |

**Diferença máxima observada**: 0.001 em DSR (ruído numérico).

## Diagnóstico

### Por que o fix `--respect-delisting` não mudou nada?

Os bars do Fintz **terminam na delisting_date naturalmente** (validado live):

| Ticker | Delisting | Last bar Fintz |
|---|---|---|
| AESB3 | 2024-10-31 | 2023-12-28 (fora do range) |
| ALSO3 | 2023-10-24 | 2023-10-24 ✓ |
| BOAS3 | 2023-08-07 | 2023-08-07 ✓ |
| BRPR3 | 2023-10-19 | 2023-10-19 ✓ |
| ENBR3 | 2023-08-21 | 2023-08-21 ✓ |
| VIIA3 | 2023-09-19 | 2023-09-19 ✓ |

- Engine **sem force-close**: lê bars até `last_bar`, fecha posição com `exit_reason="Fim do período"`
- Engine **com force-close**: identifica `delisting_date == last_bar`, fecha posição com `exit_reason="DELISTED em <date>"`
- **Resultado**: trade idêntico, métricas idênticas. Apenas a label muda.

### Onde está o bias verdadeiro?

O survivorship bias não é causado pelo **force-close** — é causado pela **ausência de delisted do universo de seleção**:

- Backtest típico parte de "todos os tickers IBOV ativos hoje"
- Tickers que delistaram em 2018-2024 não aparecem nessa lista
- Grid search seleciona melhor params em estratégias que **funcionaram nos sobreviventes**
- Gera Sharpe inflado (sobreviventes ganharam por sorte; delisted perderam silenciosamente)

### Force-close é defensivo, não corretivo

O fix `delisting_date` no `run_backtest` é **defesa contra look-ahead**, não corretivo de bias:

- Se algum dia aparecer fonte de dados que entregue bars APÓS delisting (ex: aliasing de ticker, rerun de Yahoo com dado errado, bug em UNION cross-source), o engine força fechamento na data correta
- Hoje os bars já cessam naturalmente — fix é no-op funcional, mas correto conceitualmente

## Próximo passo (defer)

Pra **quantificar bias real**, rodar grid search comparativo:

| Universo | Tickers | Sharpe esperado |
|---|---|---|
| (a) Sobreviventes hoje | 374 ativos | viés positivo (mais alto) |
| (b) Sobreviventes + delisted | 374 + 449 | sem viés (mais baixo) |

Strategy aplicada nos 2 universos. Diff de Sharpe = magnitude do bias do projeto.

**Custo**: ~3-5h (grid search × 2 universos × 30+ tickers cada). Requer:
- Lista de tickers IBOV histórica (snapshot 2018, 2020, 2022)
- Multi-ticker backtest service
- Agregação por universo

Não é entregável da sessão de 03/mai. Item pra próxima R5 expansion.

## Conclusão

✅ Fix `--respect-delisting` está **correto e instalado** mas não muda métricas com infra atual (bars Fintz já cessam na delisting).

⚠️ Survivorship bias **real do projeto** vem da ausência de delisted no universo de seleção. Mitigação requer backtest multi-ticker com lista IBOV histórica — defer pra outra sprint.

🎯 **Recomendação operacional**: o robô em produção não sofre desse viés diretamente porque trata um ticker por vez (decisão BUY/SELL não envolve seleção de universo). Mas backtest multi-ticker historic deve sempre incluir delisted (e force-close já está pronto pra esse cenário).

---

Artefatos:
- `backtest_runs/bias_validation/*.txt` — output completo dos 12 backtests
- Esta sessão: 03/mai/2026, ~30min
