-- Reverte seed_robot_smoke.sql — desliga strategies smoke + reativa kill
-- switch (defesa em profundidade enquanto se inspeciona logs).

BEGIN;

UPDATE robot_strategies SET enabled = FALSE
 WHERE name IN ('tsmom_ml_overlay', 'ml_signals');

INSERT INTO robot_risk_state (date, paused, paused_reason, paused_at)
VALUES (CURRENT_DATE, TRUE, 'smoke_revert_manual', NOW())
ON CONFLICT (date) DO UPDATE
   SET paused = TRUE,
       paused_reason = 'smoke_revert_manual',
       paused_at = NOW(),
       updated_at = NOW();

COMMIT;

-- Verificar:
-- SELECT name, enabled FROM robot_strategies ORDER BY name;
-- SELECT * FROM robot_risk_state WHERE date = CURRENT_DATE;
