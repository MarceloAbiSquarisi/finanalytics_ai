# scripts/_archive/

Scripts one-shot que rodaram historicamente para corrigir incidentes ou
experimentar features. Mantidos por valor histórico — útil pra debugar
recorrência de bugs antigos — mas **não devem ser executados em produção**.

Movidos pra cá em **2026-05-01** durante limpeza de repo (`scripts/` tinha
acumulado 38 patch/fix/diag soltos, dificultando navegar pro script real).

## Conteúdo

### patch_*.py
Patches Python que aplicaram modificações pontuais em código durante
incidentes (callbacks DLL, async sequence, market_data ingestion).

### fix_live_*.py / fix_ws*.py
Hotfixes para endpoints live/WebSocket aplicados em produção. A correção
canônica está agora em `src/`.

### diag_*.py
Diagnósticos de callbacks ProfitDLL feitos durante investigação de bugs
P1-P11 (ver `Melhorias.md`). Substituídos por `profit_agent_validators.py`
+ unit tests em `tests/unit/workers/`.

### fix_mojibake.py / fix_ohlc_scale.py / fix_profit_price_scale.py
Correções pontuais em DB que rodaram uma vez. Padronização de OHLC scale
agora coberta pela Decisão 21 + `populate_daily_bars.py`.

### _backups_april2026/
Snapshots de scripts em datas específicas de abril/2026 — preserva versão
anterior de scripts que tiveram refactor.

## Recuperar um script daqui

```bash
git mv scripts/_archive/<nome>.py scripts/<nome>.py
```

Mas reconsidere: se está aqui é porque já não roda regularmente. Provável
que precise adaptação antes de rodar de novo.
