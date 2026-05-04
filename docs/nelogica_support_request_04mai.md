# Template — Suporte Nelogica sobre conta SIM 32003

Mensagem pronta pra enviar à Nelogica (suporte ProfitDLL/ProfitPro). Adapte canal (email/chat/ticket).

---

**Assunto:** Conta SIM 32003 / 1000498192 rejeitando todas as ordens — "Cliente não está logado"

**Corpo:**

Olá,

Estou tendo um problema persistente em uma conta simulador. Todas as ordens enviadas (BUY ou SELL, market ou limit) estão sendo rejeitadas instantaneamente pelo broker, com a mensagem **"Cliente não está logado"** (RejectedMercuryLegacy).

**Identificação:**
- Conta SIM: BrokerID=`32003` AccountID=`1000498192`
- Titular: MARCELO ABI CHAHINE SQUARISI
- Login: marceloabisquarisi@gmail.com
- ProfitDLL Activation Key: `1834404599450006070`

**Sintomas observados (04/mai/2026, pregão 10h-17h BRT):**

1. **Login OK** — `StateCallback: cstRoteamento | crBrokerConnected` confirma conexão estabelecida.
2. **AccountListCallback OK** — broker_id=32003 e owner=MARCELO ABI são listados corretamente.
3. **Subscriptions OK** — 383 tickers subscritos via `SubscribeTicker`, ticks chegando normalmente (3M+).
4. **`SendOrder` retorna LocalOrderID válido** — DLL aceita estrutura.
5. **Mas ordem é REJEITADA em ~0.1s** com:

```
TradingMessageResultCallback: 32003 | <msgID> | RejectedMercuryLegacy | Cliente não está logado.
OrderCallback: PETR4 | ... | 204 | Cliente não está logado.
```

6. **Reproduzido também via Profit Pro Desktop** (cliente Delphi nativo) — mesmo erro, mesma mensagem.
7. **`crDisconnected` aparece repetidas vezes** no StateCallback durante o uso, sugerindo que a subconnection com a corretora flapa intermitentemente.

**Diagnóstico minha:**

Parece que a conta SIM está com algum tipo de bloqueio ou a sessão é derrubada antes do broker processar a ordem. Login OK + AccountList OK + ticks OK descartam problema de credencial ou licença.

**Tentativas:**

- 12 ordens (BUY + SELL, market + limit) ao longo de 5h — todas `OrderStatus=8` (Rejected) sem fill.
- Restart do agent + restart da DLL — comportamento persiste.
- Validação via cliente Delphi oficial (sample da Nelogica) — mesmo erro.
- Routing password validado (login_ok=true, AccountList retorna conta correta).

**Pedido:**

1. A conta SIM 32003/1000498192 está ativa e operável hoje?
2. Há alguma manutenção / problema de servidor com a corretora/SIM hoje?
3. Há limite de sessões simultâneas por conta? (Profit Pro + DLL ao mesmo tempo)
4. Qual o caminho correto de troubleshoot quando "Cliente não está logado" persiste mesmo com login válido?

Posso fornecer logs adicionais (StateCallback completo, OrderCallback bytes, capturas dos erros) se precisar.

Obrigado,
Marcelo

---

## Coleta opcional pra anexar

Se a Nelogica pedir mais dados:

```powershell
# Trecho do log do agent (Windows host)
Get-Content "D:\Projetos\finanalytics_ai_fresh\logs\profit_agent.stdout.log" -Tail 200 | Out-File nelogica_agent_log.txt

# Status da conexão + ticks recentes
Invoke-RestMethod "http://localhost:8002/status" | ConvertTo-Json > nelogica_status.json

# Ordens enviadas hoje (todas status=8)
Invoke-RestMethod "http://localhost:8002/orders?limit=20" | ConvertTo-Json > nelogica_orders.json
```
