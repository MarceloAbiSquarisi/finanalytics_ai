-- ─────────────────────────────────────────────────────────────────────────
-- seed_robot_smoke.sql — fixture para smoke test live R1.5 + R2
--
-- Habilita TsmomMlOverlayStrategy (R2) com 2 tickers liquidos da B3.
-- Use no proximo pregao com worker em DRY_RUN=false (simulacao).
--
-- Pre-requisitos no host:
--   - AUTO_TRADER_ENABLED=true   (env do container auto_trader)
--   - AUTO_TRADER_DRY_RUN=false  (env do container auto_trader)
--   - SCHEDULE_INTERVAL_SEC=60   (default; cada ciclo 1m)
--   - Conta ativa em investment_accounts (AccountService injeta credenciais)
--
-- Como rodar:
--   docker exec -i finanalytics_timescale psql -U finanalytics -d market_data \
--     < scripts/seed_robot_smoke.sql
--
-- Como pausar (sem rebuild):
--   curl -X PUT http://localhost:8000/api/v1/robot/pause \
--     -H "X-Sudo-Token: <admin sudo token>"
--
-- Como reverter o seed (zera tudo):
--   docker exec -i finanalytics_timescale psql -U finanalytics -d market_data \
--     < scripts/seed_robot_smoke_revert.sql
-- ─────────────────────────────────────────────────────────────────────────

BEGIN;

-- 1. Silenciar strategies preexistentes (dummy_heartbeat polui logs).
UPDATE robot_strategies SET enabled = FALSE
 WHERE name IN ('dummy_heartbeat', 'ml_signals');

-- 2. Garantir kill switch DESLIGADO p/ hoje (ops pode ter pausado em sessao
--    anterior). Insere row do dia se nao existe.
INSERT INTO robot_risk_state (date, paused, paused_reason)
VALUES (CURRENT_DATE, FALSE, NULL)
ON CONFLICT (date) DO UPDATE
   SET paused = FALSE,
       paused_reason = NULL,
       updated_at = NOW();

-- 3. Habilitar tsmom_ml_overlay (R2) com 2 tickers.
--
--    config_json:
--      tickers                 — PETR4 + VALE3 (liquidos, calibrados em ticker_ml_config)
--      capital_per_strategy    — R$10k (smoke conservador; sizing em qty inteira de acao)
--      target_vol_annual       — 15% (default Moskowitz)
--      kelly_fraction          — 0.25 (1/4 do Kelly otimo, padrao literatura)
--      max_position_pct        — 0.10 (10% do capital por posicao, ~R$1k notional)
--      atr_period              — 14 (Wilder padrao)
--      atr_sl_mult / atr_tp_mult — 2x SL, 3x TP -> risk:reward 1:1.5
--      vol_lookback_days       — 20
--      momentum_lookback_days  — 252 (Moskowitz)
--      is_daytrade             — FALSE p/ smoke nao forcar liquidacao fim-de-pregao
--                                (troque p/ true se quiser DayTrade real com OCO ate 17h)
INSERT INTO robot_strategies (name, enabled, config_json, description)
VALUES (
  'tsmom_ml_overlay',
  TRUE,
  '{
    "tickers": ["PETR4", "VALE3"],
    "capital_per_strategy": 10000,
    "target_vol_annual": 0.15,
    "kelly_fraction": 0.25,
    "max_position_pct": 0.10,
    "atr_period": 14,
    "atr_sl_mult": 2.0,
    "atr_tp_mult": 3.0,
    "vol_lookback_days": 20,
    "momentum_lookback_days": 252,
    "is_daytrade": false
  }'::jsonb,
  'R2 smoke 02/mai — TSMOM filter sobre ML p/ PETR4+VALE3'
)
ON CONFLICT (name) DO UPDATE
   SET enabled     = TRUE,
       config_json = EXCLUDED.config_json,
       description = EXCLUDED.description,
       updated_at  = NOW()
RETURNING id, name, enabled, config_json, description;

COMMIT;

-- ── Verificacao apos seed (rodar manualmente) ─────────────────────────────
--
-- Strategies ativas:
--   SELECT id, name, enabled, jsonb_pretty(config_json), description
--     FROM robot_strategies WHERE enabled = TRUE;
--
-- Kill switch hoje:
--   SELECT * FROM robot_risk_state WHERE date = CURRENT_DATE;
--
-- Apos worker rodar 1-2 ciclos (~60-120s):
--   SELECT id, strategy_name, ticker, action, sent_to_dll, local_order_id,
--          reason_skipped, payload_json->>'reason' AS reason
--     FROM robot_signals_log
--    WHERE computed_at > NOW() - INTERVAL '5 minutes'
--    ORDER BY computed_at DESC LIMIT 20;
--
-- Ordens enviadas:
--   SELECT i.id, i.ticker, i.side, i.quantity, i.take_profit, i.stop_loss,
--          i.local_order_id, i.cl_ord_id, i.sent_at, i.error_msg,
--          o.status, o.cl_ord_id AS profit_cl_ord_id, o.source
--     FROM robot_orders_intent i
--     LEFT JOIN profit_orders o ON o.local_id = i.local_order_id
--    WHERE i.created_at > NOW() - INTERVAL '5 minutes'
--    ORDER BY i.created_at DESC;
--
-- OCO atrelado:
--   SELECT * FROM oco_groups
--    WHERE created_at > NOW() - INTERVAL '5 minutes'
--    ORDER BY created_at DESC;
