-- Migração: consolida ativos de teste em UMA carteira "Teste"
--   1. Cria conta Teste + portfolio 1:1
--   2. Move trades / positions / crypto / rf / other dos contas antigas pra ela
--   3. Soft-delete das 2 contas antigas (XP + BTG)
--   4. Adiciona NOT NULL em investment_account_id (todos ativos devem ter carteira)
--
-- Idempotente — pode rodar de novo sem efeito colateral.

BEGIN;

-- ── 1. Conta Teste (user_id = master) ──────────────────────────────────────
INSERT INTO investment_accounts (
  id, user_id, institution_name, institution_code, country, currency,
  account_type, is_active, titular, cpf, apelido, is_dll_active
)
SELECT
  'eeee5555-5555-5555-5555-555555555555',
  '09d05145-bf74-481e-ab1d-efa3ea9775b5',
  'Carteira Consolidada Teste',
  'TEST',
  'BRA',
  'BRL',
  'corretora',
  TRUE,
  'Marcelo Abi Squarisi',
  '00000000000',
  'Teste',
  FALSE
ON CONFLICT (id) DO NOTHING;

-- ── 2. Portfolio 1:1 ───────────────────────────────────────────────────────
INSERT INTO portfolios (id, user_id, name, currency, cash, is_active, investment_account_id)
SELECT
  'fffff666-6666-6666-6666-666666666666',
  '09d05145-bf74-481e-ab1d-efa3ea9775b5',
  'Portfolio',
  'BRL',
  0,
  TRUE,
  'eeee5555-5555-5555-5555-555555555555'
ON CONFLICT (id) DO NOTHING;

-- ── 3. Migrar ativos das contas antigas (XP + BTG) → Teste ────────────────
UPDATE trades
   SET investment_account_id = 'eeee5555-5555-5555-5555-555555555555',
       portfolio_id          = 'fffff666-6666-6666-6666-666666666666'
 WHERE investment_account_id IN (
   'aaaa1111-1111-1111-1111-111111111111',
   'bbbb2222-2222-2222-2222-222222222222'
 );

UPDATE positions
   SET investment_account_id = 'eeee5555-5555-5555-5555-555555555555',
       portfolio_id          = 'fffff666-6666-6666-6666-666666666666'
 WHERE investment_account_id IN (
   'aaaa1111-1111-1111-1111-111111111111',
   'bbbb2222-2222-2222-2222-222222222222'
 );

UPDATE crypto_holdings
   SET investment_account_id = 'eeee5555-5555-5555-5555-555555555555',
       portfolio_id          = 'fffff666-6666-6666-6666-666666666666'
 WHERE investment_account_id IN (
   'aaaa1111-1111-1111-1111-111111111111',
   'bbbb2222-2222-2222-2222-222222222222'
 );

UPDATE other_assets
   SET investment_account_id = 'eeee5555-5555-5555-5555-555555555555',
       portfolio_id          = 'fffff666-6666-6666-6666-666666666666'
 WHERE investment_account_id IN (
   'aaaa1111-1111-1111-1111-111111111111',
   'bbbb2222-2222-2222-2222-222222222222'
 );

-- rf_holdings: tem investment_account_id e portfolio_id (FK para portfolios.id)
UPDATE rf_holdings
   SET investment_account_id = 'eeee5555-5555-5555-5555-555555555555',
       portfolio_id          = 'fffff666-6666-6666-6666-666666666666'
 WHERE portfolio_id IN (
   'ccccc111-1111-1111-1111-111111111111',
   'ddddd222-2222-2222-2222-222222222222'
 );

-- ── 4. Soft-delete das contas + portfolios antigos ────────────────────────
UPDATE investment_accounts
   SET is_active = FALSE
 WHERE id IN ('aaaa1111-1111-1111-1111-111111111111', 'bbbb2222-2222-2222-2222-222222222222');

UPDATE portfolios
   SET is_active = FALSE
 WHERE id IN ('ccccc111-1111-1111-1111-111111111111', 'ddddd222-2222-2222-2222-222222222222');

-- ── 5. Constraint: todo ativo DEVE ter investment_account_id ──────────────
-- Antes de aplicar, valida que não há rows com NULL (deveria ser zero após migração).
DO $$
DECLARE
  v_null_trades    INT;
  v_null_positions INT;
  v_null_crypto    INT;
  v_null_rf        INT;
  v_null_other     INT;
BEGIN
  SELECT COUNT(*) INTO v_null_trades    FROM trades          WHERE investment_account_id IS NULL;
  SELECT COUNT(*) INTO v_null_positions FROM positions       WHERE investment_account_id IS NULL;
  SELECT COUNT(*) INTO v_null_crypto    FROM crypto_holdings WHERE investment_account_id IS NULL;
  SELECT COUNT(*) INTO v_null_rf        FROM rf_holdings     WHERE investment_account_id IS NULL;
  SELECT COUNT(*) INTO v_null_other     FROM other_assets    WHERE investment_account_id IS NULL;
  IF v_null_trades + v_null_positions + v_null_crypto + v_null_rf + v_null_other > 0 THEN
    RAISE EXCEPTION 'Existem rows sem investment_account_id — migração incompleta. trades=%, positions=%, crypto=%, rf=%, other=%',
      v_null_trades, v_null_positions, v_null_crypto, v_null_rf, v_null_other;
  END IF;
END $$;

ALTER TABLE trades          ALTER COLUMN investment_account_id SET NOT NULL;
ALTER TABLE positions       ALTER COLUMN investment_account_id SET NOT NULL;
ALTER TABLE crypto_holdings ALTER COLUMN investment_account_id SET NOT NULL;
ALTER TABLE rf_holdings     ALTER COLUMN investment_account_id SET NOT NULL;
ALTER TABLE other_assets    ALTER COLUMN investment_account_id SET NOT NULL;

COMMIT;

-- ── Verificação ────────────────────────────────────────────────────────────
SELECT 'investment_accounts ativas' AS status, COUNT(*) FROM investment_accounts WHERE is_active
UNION ALL SELECT 'trades',          COUNT(*) FROM trades          WHERE investment_account_id = 'eeee5555-5555-5555-5555-555555555555'
UNION ALL SELECT 'positions',       COUNT(*) FROM positions       WHERE investment_account_id = 'eeee5555-5555-5555-5555-555555555555'
UNION ALL SELECT 'crypto_holdings', COUNT(*) FROM crypto_holdings WHERE investment_account_id = 'eeee5555-5555-5555-5555-555555555555'
UNION ALL SELECT 'rf_holdings',     COUNT(*) FROM rf_holdings     WHERE investment_account_id = 'eeee5555-5555-5555-5555-555555555555'
UNION ALL SELECT 'other_assets',    COUNT(*) FROM other_assets    WHERE investment_account_id = 'eeee5555-5555-5555-5555-555555555555';
