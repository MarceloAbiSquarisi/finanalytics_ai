-- Seed de 2 contas teste populadas (26/abr/2026).
-- Pré-requisito: tables limpas via hard delete (account_transactions, trades,
-- crypto_holdings, rf_holdings, other_assets, portfolio_name_history,
-- portfolios, investment_accounts).
--
-- Uso:
--   docker exec -i finanalytics_postgres psql -U finanalytics -d finanalytics \
--     < scripts/seed_test_accounts.sql
--
-- Cria:
--   1. Conta "Teste Ações XP" (XP, BRL) — 6 trades de ações + 1 crypto BTC + 1 ETF other
--   2. Conta "Teste Renda Fixa BTG" (BTG, BRL) — 3 RF (CDB/LCI/Tesouro) + 1 FII + 1 trade
--
-- Reaproveita user master (marceloabisquarisi@gmail.com) — ID hardcoded.

\set USER_ID '\'09d05145-bf74-481e-ab1d-efa3ea9775b5\''
\set ACC1_ID '\'aaaa1111-1111-1111-1111-111111111111\''
\set ACC2_ID '\'bbbb2222-2222-2222-2222-222222222222\''
\set PF1_ID  '\'ccccc111-1111-1111-1111-111111111111\''
\set PF2_ID  '\'ddddd222-2222-2222-2222-222222222222\''

BEGIN;

-- ── Conta 1: XP — Teste Ações ────────────────────────────────────────────────
INSERT INTO investment_accounts (
  id, user_id, institution_name, institution_code, agency, account_number,
  country, currency, account_type, is_active, titular, cpf, apelido,
  cash_balance, created_at, updated_at
) VALUES (
  :ACC1_ID, :USER_ID, 'XP Investimentos', '102', '0001', '12345-6',
  'BR', 'BRL', 'corretora', true,
  'Marcelo Abi Squarisi', '12345678901', 'Teste Ações XP',
  50000.00, NOW(), NOW()
);

-- ── Conta 2: BTG — Teste Renda Fixa ──────────────────────────────────────────
INSERT INTO investment_accounts (
  id, user_id, institution_name, institution_code, agency, account_number,
  country, currency, account_type, is_active, titular, cpf, apelido,
  cash_balance, created_at, updated_at
) VALUES (
  :ACC2_ID, :USER_ID, 'BTG Pactual', '208', '0050', '78901-2',
  'BR', 'BRL', 'corretora', true,
  'Marcelo Abi Squarisi', '12345678901', 'Teste Renda Fixa BTG',
  30000.00, NOW(), NOW()
);

-- ── Portfolio 1 (XP) ─────────────────────────────────────────────────────────
INSERT INTO portfolios (
  id, user_id, investment_account_id, name, description, benchmark,
  is_active, currency, cash, created_at, updated_at
) VALUES (
  :PF1_ID, :USER_ID, :ACC1_ID, 'Portfolio',
  'Carteira de ações Teste XP', 'IBOV', true, 'BRL', 0, NOW(), NOW()
);

-- ── Portfolio 2 (BTG) ────────────────────────────────────────────────────────
INSERT INTO portfolios (
  id, user_id, investment_account_id, name, description, benchmark,
  is_active, currency, cash, created_at, updated_at
) VALUES (
  :PF2_ID, :USER_ID, :ACC2_ID, 'Portfolio',
  'Carteira RF Teste BTG', 'CDI', true, 'BRL', 0, NOW(), NOW()
);

-- ── Trades XP (6 ações + variedade) ──────────────────────────────────────────
-- Compras (mar/abr 2026)
INSERT INTO trades (id, user_id, investment_account_id, portfolio_id, ticker, asset_class, operation, quantity, unit_price, total_cost, fees, currency, trade_date, note, created_at) VALUES
('t-001', :USER_ID, :ACC1_ID, :PF1_ID, 'PETR4',  'stock', 'buy', 100, 35.50, 3550.00, 5.00, 'BRL', '2026-03-15', 'Compra inicial Petrobras', NOW()),
('t-002', :USER_ID, :ACC1_ID, :PF1_ID, 'VALE3',  'stock', 'buy', 50,  68.20, 3410.00, 5.00, 'BRL', '2026-03-20', 'Compra Vale', NOW()),
('t-003', :USER_ID, :ACC1_ID, :PF1_ID, 'ITUB4',  'stock', 'buy', 200, 32.10, 6420.00, 5.00, 'BRL', '2026-04-01', 'Compra Itaú', NOW()),
('t-004', :USER_ID, :ACC1_ID, :PF1_ID, 'WEGE3',  'stock', 'buy', 80,  42.80, 3424.00, 5.00, 'BRL', '2026-04-05', 'Compra WEG', NOW()),
('t-005', :USER_ID, :ACC1_ID, :PF1_ID, 'BBSE3',  'stock', 'buy', 150, 38.40, 5760.00, 5.00, 'BRL', '2026-04-10', 'Compra BB Seguridade', NOW()),
('t-006', :USER_ID, :ACC1_ID, :PF1_ID, 'KNRI11', 'fii',   'buy', 30,  140.50, 4215.00, 5.00, 'BRL', '2026-04-15', 'FII Kinea Renda', NOW()),
-- Uma venda parcial pra ter delta
('t-007', :USER_ID, :ACC1_ID, :PF1_ID, 'PETR4', 'stock', 'sell', 30, 39.20, 1176.00, 5.00, 'BRL', '2026-04-22', 'Venda parcial PETR4 lucro', NOW());

-- ── Trades BTG (1 ação + 2 ETFs) ─────────────────────────────────────────────
INSERT INTO trades (id, user_id, investment_account_id, portfolio_id, ticker, asset_class, operation, quantity, unit_price, total_cost, fees, currency, trade_date, note, created_at) VALUES
('t-008', :USER_ID, :ACC2_ID, :PF2_ID, 'BBAS3',  'stock', 'buy', 100, 28.90, 2890.00, 4.50, 'BRL', '2026-03-25', 'Compra Banco do Brasil', NOW()),
('t-009', :USER_ID, :ACC2_ID, :PF2_ID, 'BOVA11', 'etf',   'buy', 50,  130.00, 6500.00, 4.50, 'BRL', '2026-04-08', 'ETF Ibovespa', NOW()),
('t-010', :USER_ID, :ACC2_ID, :PF2_ID, 'DIVO11', 'etf',   'buy', 100, 110.40, 11040.00, 5.00, 'BRL', '2026-04-02', 'ETF Dividendos', NOW());

-- ── Trades XP — ETFs adicionais ──────────────────────────────────────────────
INSERT INTO trades (id, user_id, investment_account_id, portfolio_id, ticker, asset_class, operation, quantity, unit_price, total_cost, fees, currency, trade_date, note, created_at) VALUES
('t-etf01', :USER_ID, :ACC1_ID, :PF1_ID, 'IVVB11', 'etf', 'buy', 25, 320.50, 8012.50, 5.00, 'BRL', '2026-03-12', 'ETF S&P 500 hedgeado', NOW()),
('t-etf02', :USER_ID, :ACC1_ID, :PF1_ID, 'SMAL11', 'etf', 'buy', 60, 95.20, 5712.00, 5.00, 'BRL', '2026-03-18', 'ETF Small Caps', NOW());

-- ── FIIs adicionais (1 XP + 1 BTG) ───────────────────────────────────────────
INSERT INTO trades (id, user_id, investment_account_id, portfolio_id, ticker, asset_class, operation, quantity, unit_price, total_cost, fees, currency, trade_date, note, created_at) VALUES
('t-fii01', :USER_ID, :ACC1_ID, :PF1_ID, 'MXRF11', 'fii', 'buy', 200, 10.50, 2100.00, 5.00, 'BRL', '2026-03-22', 'FII Maxi Renda recebíveis', NOW()),
('t-fii02', :USER_ID, :ACC2_ID, :PF2_ID, 'HGLG11', 'fii', 'buy', 50, 165.30, 8265.00, 5.00, 'BRL', '2026-03-28', 'FII CSHG Logística', NOW());

-- ── Fundos de investimento CVM (sem ticker B3, via other_assets) ────────────
INSERT INTO other_assets (id, user_id, investment_account_id, portfolio_id, name, asset_type, current_value, invested_value, currency, acquisition_date, ir_exempt, note, created_at, updated_at) VALUES
('o-fund01', :USER_ID, :ACC1_ID, :PF1_ID, 'Pacto Multistrategy FIC FIM', 'fundo', 12500.00, 10000.00, 'BRL', '2025-09-15', false, 'CNPJ 00.000.001/0001-01 - multimercado', NOW(), NOW()),
('o-fund02', :USER_ID, :ACC2_ID, :PF2_ID, 'BTG Renda Fixa Premium FIC FIRF', 'fundo', 21500.00, 20000.00, 'BRL', '2025-12-10', false, 'CNPJ 00.000.002/0001-02 - renda fixa', NOW(), NOW());

-- ── Positions table (derivada de trades, mas consumida direto pelo dividend match) ─
-- Necessária pra match_to_positions encontrar tickers via investment_account_id.
INSERT INTO positions (portfolio_id, investment_account_id, ticker, quantity, average_price, asset_class) VALUES
(:PF1_ID, :ACC1_ID, 'PETR4',  70,  35.50,  'stock'),  -- 100 buy - 30 sell
(:PF1_ID, :ACC1_ID, 'VALE3',  50,  68.20,  'stock'),
(:PF1_ID, :ACC1_ID, 'ITUB4',  200, 32.10,  'stock'),
(:PF1_ID, :ACC1_ID, 'WEGE3',  80,  42.80,  'stock'),
(:PF1_ID, :ACC1_ID, 'BBSE3',  150, 38.40,  'stock'),
(:PF1_ID, :ACC1_ID, 'KNRI11', 30,  140.50, 'fii'),
(:PF1_ID, :ACC1_ID, 'MXRF11', 200, 10.50,  'fii'),
(:PF1_ID, :ACC1_ID, 'IVVB11', 25,  320.50, 'etf'),
(:PF1_ID, :ACC1_ID, 'SMAL11', 60,  95.20,  'etf'),
(:PF2_ID, :ACC2_ID, 'BBAS3',  100, 28.90,  'stock'),
(:PF2_ID, :ACC2_ID, 'BOVA11', 50,  130.00, 'etf'),
(:PF2_ID, :ACC2_ID, 'DIVO11', 100, 110.40, 'etf'),
(:PF2_ID, :ACC2_ID, 'HGLG11', 50,  165.30, 'fii');

-- ── Crypto (BTC) na conta XP ─────────────────────────────────────────────────
INSERT INTO crypto_holdings (id, user_id, investment_account_id, portfolio_id, symbol, quantity, average_price_brl, average_price_usd, exchange, note, updated_at) VALUES
('c-001', :USER_ID, :ACC1_ID, :PF1_ID, 'BTC', 0.025, 280000.00, 56000.00, 'Binance', 'Compras DCA', NOW());

-- ── rf_portfolios (espelho legacy do sistema unificado) ─────────────────────
-- /api/v1/fixed-income/portfolio/{id} consulta esta tabela. Sem entry, retorna
-- 404 e /overview não consegue listar holdings RF nos cards.
INSERT INTO rf_portfolios (portfolio_id, user_id, name, created_at) VALUES
(:PF1_ID, :USER_ID, 'Portfolio', '2026-04-26'),
(:PF2_ID, :USER_ID, 'Portfolio', '2026-04-26');

-- ── Renda Fixa (BTG) — CDB + LCI + Tesouro ───────────────────────────────────
INSERT INTO rf_holdings (holding_id, portfolio_id, investment_account_id, bond_id, bond_name, bond_type, indexer, issuer, invested, rate_annual, rate_pct_indexer, purchase_date, maturity_date, ir_exempt, note, liquidity_days) VALUES
('rf-001', :PF2_ID, :ACC2_ID, 'cdb-btg-2027', 'CDB BTG 110% CDI', 'CDB', 'CDI', 'BTG Pactual', 10000.00, 110.0, true, '2026-01-15', '2027-01-15', false, 'CDB pos pra emergência', 1),
('rf-002', :PF2_ID, :ACC2_ID, 'lci-btg-2028', 'LCI BTG 95% CDI',  'LCI', 'CDI', 'BTG Pactual', 8000.00,  95.0,  true, '2026-02-10', '2028-02-10', true,  'LCI isenta IR',         30),
('rf-003', :PF2_ID, :ACC2_ID, 'tesouro-ipca-2030', 'Tesouro IPCA+ 2030', 'TESOURO', 'IPCA', 'Tesouro Nacional', 5000.00, 6.5, false, '2026-01-05', '2030-08-15', false, 'NTN-B principal', 1);

-- ── Other (FII físico hipotético) na conta XP ────────────────────────────────
INSERT INTO other_assets (id, user_id, investment_account_id, portfolio_id, name, asset_type, current_value, invested_value, currency, acquisition_date, ir_exempt, note, created_at, updated_at) VALUES
('o-001', :USER_ID, :ACC1_ID, :PF1_ID, 'Apartamento SP', 'imovel', 450000.00, 380000.00, 'BRL', '2024-06-01', false, 'Imóvel próprio para renda', NOW(), NOW());

-- ── account_transactions: depósitos iniciais + compras + vendas ──────────────
-- Conta XP: depósito inicial 100k → cash_balance 50k após 6 trades net
INSERT INTO account_transactions (id, user_id, account_id, tx_type, amount, currency, status, reference_date, note, created_at) VALUES
('tx-d01', :USER_ID, :ACC1_ID, 'deposit', 100000.00, 'BRL', 'settled', '2026-03-01', 'Depósito inicial XP', NOW()),
-- Trades XP (negativos = saídas pra compras, positivo pra venda)
('tx-001', :USER_ID, :ACC1_ID, 'trade_buy',  -3555.00, 'BRL', 'settled', '2026-03-15', 'Compra PETR4 100x35.50',  NOW()),
('tx-002', :USER_ID, :ACC1_ID, 'trade_buy',  -3415.00, 'BRL', 'settled', '2026-03-20', 'Compra VALE3 50x68.20',   NOW()),
('tx-003', :USER_ID, :ACC1_ID, 'trade_buy',  -6425.00, 'BRL', 'settled', '2026-04-01', 'Compra ITUB4 200x32.10',  NOW()),
('tx-004', :USER_ID, :ACC1_ID, 'trade_buy',  -3429.00, 'BRL', 'settled', '2026-04-05', 'Compra WEGE3 80x42.80',   NOW()),
('tx-005', :USER_ID, :ACC1_ID, 'trade_buy',  -5765.00, 'BRL', 'settled', '2026-04-10', 'Compra BBSE3 150x38.40',  NOW()),
('tx-006', :USER_ID, :ACC1_ID, 'trade_buy',  -4220.00, 'BRL', 'settled', '2026-04-15', 'Compra KNRI11 30x140.50', NOW()),
('tx-007', :USER_ID, :ACC1_ID, 'trade_sell',  1171.00, 'BRL', 'settled', '2026-04-22', 'Venda parcial PETR4',     NOW()),
-- Crypto compra
('tx-c01', :USER_ID, :ACC1_ID, 'crypto_buy',  -7000.00, 'BRL', 'settled', '2026-02-01', 'Compra 0.025 BTC',     NOW()),
-- Dividendos recebidos
('tx-div1', :USER_ID, :ACC1_ID, 'dividend',     180.00, 'BRL', 'settled', '2026-04-05', 'DIVIDENDOS PETR4',     NOW()),
('tx-div2', :USER_ID, :ACC1_ID, 'dividend',     520.00, 'BRL', 'settled', '2026-04-10', 'DIVIDENDOS ITUB4',     NOW()),
('tx-jcp1', :USER_ID, :ACC1_ID, 'dividend',     310.00, 'BRL', 'settled', '2026-04-15', 'JCP BBSE3',            NOW()),
('tx-rd1',  :USER_ID, :ACC1_ID, 'dividend',     108.00, 'BRL', 'settled', '2026-04-18', 'RENDIMENTO KNRI11',    NOW());

-- Conta BTG: depósito 50k → 30k após RF 23k + trades 9.4k
INSERT INTO account_transactions (id, user_id, account_id, tx_type, amount, currency, status, reference_date, note, created_at) VALUES
('tx-d02', :USER_ID, :ACC2_ID, 'deposit', 50000.00, 'BRL', 'settled', '2026-01-02', 'Depósito inicial BTG', NOW()),
('tx-rf1', :USER_ID, :ACC2_ID, 'rf_apply',  -10000.00, 'BRL', 'settled', '2026-01-15', 'CDB BTG 110% CDI',     NOW()),
('tx-rf2', :USER_ID, :ACC2_ID, 'rf_apply',   -8000.00, 'BRL', 'settled', '2026-02-10', 'LCI BTG 95% CDI',      NOW()),
('tx-rf3', :USER_ID, :ACC2_ID, 'rf_apply',   -5000.00, 'BRL', 'settled', '2026-01-05', 'Tesouro IPCA+ 2030',   NOW()),
('tx-008', :USER_ID, :ACC2_ID, 'trade_buy',  -2894.50, 'BRL', 'settled', '2026-03-25', 'Compra BBAS3',         NOW()),
('tx-009', :USER_ID, :ACC2_ID, 'trade_buy',  -6504.50, 'BRL', 'settled', '2026-04-08', 'Compra BOVA11',        NOW()),
('tx-divr', :USER_ID, :ACC2_ID, 'dividend',    340.00, 'BRL', 'settled', '2026-04-20', 'DIVIDENDOS BBAS3',     NOW());

-- Verificação
SELECT 'investment_accounts' AS tbl, count(*) FROM investment_accounts
UNION ALL SELECT 'portfolios', count(*) FROM portfolios
UNION ALL SELECT 'trades', count(*) FROM trades
UNION ALL SELECT 'crypto_holdings', count(*) FROM crypto_holdings
UNION ALL SELECT 'rf_holdings', count(*) FROM rf_holdings
UNION ALL SELECT 'other_assets', count(*) FROM other_assets
UNION ALL SELECT 'account_transactions', count(*) FROM account_transactions;

COMMIT;
