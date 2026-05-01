# R3 Pairs — Expansão de Universo (research 01/mai/2026)

> Resultado do screening expandido contra `cointegration_screen.py` original (8 tickers, 252d, p<0.05). Universo ampliado pra **30 tickers em 10 setores intra-setor** × **3 lookbacks** × **2 thresholds**, totalizando **189 testes**. Output completo em `r3_pairs_expansion_results.txt`.

## Sumário executivo

**Saímos de 2 → 16 pares tradeable** (`p < 0.05` + `half_life ∈ [5, 50] dias` = janela operacional R3.2.B).

```
SETOR                  PARES TRADEABLE
varejo                       5  (AMER3 hub)
papel_celulose               3  (KLBN11-SUZB3 estável em 3 lookbacks)
mineracao_metalurgia         2  (CSNA3-USIM5 + CMIN3-VALE3)
petroleo                     2  (PRIO3-RECV3 + RAPT4-RECV3)
bancos                       2  (BBDC4-BPAC11 em 2 lookbacks)
alimentos                    1  (JBSS3-MRFG3)
TOTAL TRADEABLE             16
```

## Top candidatos (recomendados pra R3.2.B)

Ordenado por estabilidade multi-lookback (cointegração persiste em ≥2 janelas) + p-value baixo:

| # | Par | Setor | Lookbacks cointegrados | Best p | half_life |
|---|-----|-------|------------------------|--------|-----------|
| 1 | **KLBN11-SUZB3** | papel/celulose | **3/3** (252+504+756d) | 0.0038 | 7-26d |
| 2 | **AMER3-PETZ3** | varejo | 2/3 (252+756d) | 0.0001 | 6.5-29d |
| 3 | **AMER3-RENT3** | varejo | 2/3 (252+756d) | 0.0001 | 8.6-29d |
| 4 | **BBDC4-BPAC11** | bancos | 2/3 (252+504d) | 0.0309 | 8.8-15.8d |
| 5 | **AMER3-MGLU3** | varejo | 1/3 (756d) | 0.0000 | 28.2d |
| 6 | **AMER3-LREN3** | varejo | 1/3 (756d) | 0.0002 | 30.3d |
| 7 | **CSNA3-USIM5** | siderurgia | 1/3 (756d) | 0.0099 | 29.4d |
| 8 | **PRIO3-RECV3** | petróleo júnior | 1/3 (504d) | 0.0378 | 16.7d |
| 9 | **JBSS3-MRFG3** | alimentos | 1/3 (504d) | 0.0376 | 24.3d |
| 10 | **CMIN3-VALE3** | mineração | 1/3 (504d) | 0.0452 | 26.9d |
| 11 | **RAPT4-RECV3** | petróleo | 1/3 (504d) | 0.0483 | 21.7d |

**Insight #1**: papel/celulose (KLBN11-SUZB3) é o **único par cointegrado nos 3 lookbacks** (252d, 504d, 756d). Top robustez pra arrancar R3.2.B em produção.

**Insight #2**: **AMER3 funciona como hub de varejo** — cointegrado com MGLU3, RENT3, PETZ3, LREN3 simultaneamente (em 756d). Possível usar como ancore de carteira.

**Insight #3**: cointegração **vai e volta com a janela**. Mesmo pares "robustos" (PRIO3-RECV3 em 504d) saem da lista em 252d ou 756d. Implicação operacional: re-screen diário (já implementado em `cointegration_screen_job` 06:30 BRT) é obrigatório.

## Setores que NÃO cooperam

```
SETOR              CONCLUSÃO
telecom            TIMS3-VIVT3 p=0.95+ em todos LB     🚫 sem chance
saneamento         SAPR11-SBSP3 p=0.46+                 🚫 sem chance
educacao           COGN3-YDUQ3 p=0.97+                  🚫 sem chance
energia            CMIG4 vs ENGI11/EQTL3/TAEE11 p=0.4+ 🚫 sem chance
bancos grandes     ITUB4-BBDC4-SANB11-BBAS3 cruzados   🚫 não cointegram entre si
```

**Evidência**: `bancos grandes` é surpreendente. Apesar do consenso de mercado (mesma indústria, mesma macro), os 4 bancões NÃO cointegram entre si. Apenas BBDC4-BPAC11 (incluindo o digital BPAC11) saiu cointegrado.

**Hipótese**: ações de empresas dominantes do mesmo setor (M&A, regulação, share recapture) divergem em momentos diferentes — não há "arrasto comum" ESTACIONÁRIO. Cointegração precisa de mean reversion sustentado, e o spread de bancões parece random walk.

## Próximos passos

### Imediato (antes Segunda 04/mai)
1. **Persistir os 16 candidatos** em `cointegrated_pairs` (UPSERT idempotente). Vai alimentar dispatcher R3.2.B no smoke live de Segunda. Comando:
   ```powershell
   .venv\Scripts\python.exe scripts\cointegration_expand.py --persist
   ```
   Cuidado: o `cointegration_screen_job` 06:30 BRT vai re-rodar com universo **original** (8 tickers) e potencialmente sobrescrever. Ver "Risco de race condition" abaixo.

2. **Atualizar `DEFAULT_WATCHLIST`** em `scripts/cointegration_screen.py` pra incluir os tickers que apareceram na lista expandida (KLBN11, SUZB3, AMER3, MGLU3, RENT3, PETZ3, LREN3, CSNA3, USIM5, CMIN3, BPAC11, MRFG3, JBSS3, PRIO3, RECV3, RAPT4). De 8 → 24 tickers, C(24,2)=276 pares — borderline pesado pra job 06:30 mas ok offline.

### Médio prazo
3. **Diversificação setorial em R3.2.B**: configurar dispatcher pra **não abrir mais de 1 par por setor simultâneo** (evita concentração de risco macro). Setores onde temos múltiplos cointegrados: varejo (5), papel (1), siderurgia (2), petro (2), bancos (1).

4. **Filtro adicional `multi_lookback_consistency`**: dar peso/prioridade pra pares cointegrados em ≥2 lookbacks (proxy de robustez vs ruído single-window). KLBN11-SUZB3 e BBDC4-BPAC11 lideram nesse critério.

5. **Investigar AMER3 isoladamente**: 4 cointegrações em 756d sugere que AMER3 está ancora a um "fator varejo discricionário B3". Pode ser interessante construir índice composto `mean(MGLU3, RENT3, PETZ3, LREN3)` e cointegrar contra AMER3 — possível alpha mais robusto que pares 1×1.

### Risco de race condition
O `cointegration_screen_job` 06:30 BRT diário **sobrescreve** a tabela `cointegrated_pairs` com universo original (8 tickers). Se rodarmos `cointegration_expand.py --persist` agora, os 16 pares persistidos serão removidos no próximo job às 06:30. **Soluções**:
- (a) Atualizar `DEFAULT_WATCHLIST` em `cointegration_screen.py` antes de Segunda — universo expandido vira o default
- (b) Adicionar flag `--watchlist-mode` ao screen job (`default | expanded`) controlado por env var
- (c) Usar tabela separada (`cointegrated_pairs_research`) e R3.2.B lê das duas

Recomendação: **(a)** — mais simples, atualiza o universo de produção e elimina o problema na raiz. Diff trivial.

## Como reproduzir

```powershell
# Dry-run (só imprime):
.venv\Scripts\python.exe scripts\cointegration_expand.py --dry

# Por setor:
.venv\Scripts\python.exe scripts\cointegration_expand.py --sector varejo --dry

# Persistir em cointegrated_pairs:
.venv\Scripts\python.exe scripts\cointegration_expand.py --persist

# Output bruto está em docs/r3_pairs_expansion_results.txt (328 linhas)
```

## Checagem antes de smoke Segunda

- [ ] Tickers usados aparecem em `fintz_cotacoes_ts` com cobertura ≥504d
- [ ] `cointegrated_pairs` tem ao menos KLBN11-SUZB3 com `cointegrated=true` e `last_test_date=hoje`
- [ ] R3.2.B reads `cointegrated_pairs` (validar via `/api/v1/pairs/active`)
- [ ] Z-score real-time aparece pros pares persistidos em `/pairs` UI
