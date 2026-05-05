"""Testes para validators puros de profit_agent.

Não depende de ctypes — roda em CI Linux normalmente.
"""

from __future__ import annotations

from types import SimpleNamespace

from finanalytics_ai.workers.profit_agent_validators import (
    compute_trading_result_match,
    infer_lot_size,
    message_has_blip_pattern,
    parse_order_details,
    resolve_subscribe_list,
    should_retry_rejection,
    trail_should_immediate_trigger,
    validate_attach_oco_params,
    validate_order_quantity,
)

# ── parent_order_id ──────────────────────────────────────────────────────────


def test_attach_oco_missing_parent() -> None:
    out = validate_attach_oco_params({"levels": [{"qty": 100, "tp_price": 50.0}]})
    assert out is not None
    assert "parent_order_id obrigatorio" in out["error"]
    assert out["ok"] is False


def test_attach_oco_zero_parent_id() -> None:
    out = validate_attach_oco_params(
        {"parent_order_id": 0, "levels": [{"qty": 100, "tp_price": 50.0}]}
    )
    assert out is not None
    assert "parent_order_id obrigatorio" in out["error"]


# ── levels ───────────────────────────────────────────────────────────────────


def test_attach_oco_missing_levels() -> None:
    out = validate_attach_oco_params({"parent_order_id": 12345})
    assert out is not None
    assert "levels[] vazio" in out["error"]


def test_attach_oco_empty_levels() -> None:
    out = validate_attach_oco_params({"parent_order_id": 12345, "levels": []})
    assert out is not None
    assert "levels[] vazio" in out["error"]


# ── trail no top-level (bug 30/abr) ──────────────────────────────────────────


def test_attach_oco_rejects_is_trailing_top_level() -> None:
    out = validate_attach_oco_params(
        {
            "parent_order_id": 12345,
            "is_trailing": True,
            "levels": [{"qty": 100, "tp_price": 50.0, "sl_trigger": 48.0}],
        }
    )
    assert out is not None
    assert "is_trailing" in out["error"]
    assert "top-level" in out["error"]


def test_attach_oco_rejects_trail_distance_top_level() -> None:
    out = validate_attach_oco_params(
        {
            "parent_order_id": 12345,
            "trail_distance": 0.05,
            "levels": [{"qty": 100, "sl_trigger": 48.0}],
        }
    )
    assert out is not None
    assert "trail_distance" in out["error"]


def test_attach_oco_rejects_trail_pct_top_level() -> None:
    out = validate_attach_oco_params(
        {
            "parent_order_id": 12345,
            "trail_pct": 0.05,
            "levels": [{"qty": 100, "sl_trigger": 48.0}],
        }
    )
    assert out is not None
    assert "trail_pct" in out["error"]


def test_attach_oco_rejects_multiple_trail_keys_top_level() -> None:
    out = validate_attach_oco_params(
        {
            "parent_order_id": 12345,
            "is_trailing": True,
            "trail_distance": 0.05,
            "levels": [{"qty": 100, "sl_trigger": 48.0}],
        }
    )
    assert out is not None
    assert "is_trailing" in out["error"] and "trail_distance" in out["error"]


# ── happy path ───────────────────────────────────────────────────────────────


def test_attach_oco_valid_minimal() -> None:
    out = validate_attach_oco_params(
        {
            "parent_order_id": 12345,
            "levels": [{"qty": 100, "tp_price": 50.0, "sl_trigger": 48.0}],
        }
    )
    assert out is None


def test_attach_oco_valid_with_trail_per_level() -> None:
    """Trail dentro do level é OK."""
    out = validate_attach_oco_params(
        {
            "parent_order_id": 12345,
            "levels": [
                {
                    "qty": 100,
                    "tp_price": 50.0,
                    "sl_trigger": 48.0,
                    "is_trailing": True,
                    "trail_distance": 0.05,
                }
            ],
        }
    )
    assert out is None


def test_attach_oco_valid_multi_level() -> None:
    out = validate_attach_oco_params(
        {
            "parent_order_id": 12345,
            "levels": [
                {"qty": 60, "tp_price": 50.0, "sl_trigger": 48.0},
                {"qty": 40, "tp_price": 51.0, "sl_trigger": 47.0, "is_trailing": True},
            ],
        }
    )
    assert out is None


# ── trail_should_immediate_trigger (B.10) ────────────────────────────────────
# side: 1=buy short (SL acima do mercado), 2=sell long (SL abaixo do mercado)


def test_immediate_trigger_sell_long_below_trigger() -> None:
    """Long position com SL abaixo: preço caiu abaixo do trigger → disparar."""
    assert trail_should_immediate_trigger(side=2, last_price=47.95, sl_trigger=48.00) is True


def test_immediate_trigger_sell_long_at_trigger() -> None:
    """Long position com last == trigger: cruzou (boundary inclusive)."""
    assert trail_should_immediate_trigger(side=2, last_price=48.00, sl_trigger=48.00) is True


def test_immediate_trigger_sell_long_above_trigger() -> None:
    """Long com preço acima do SL: tudo OK, não disparar."""
    assert trail_should_immediate_trigger(side=2, last_price=48.50, sl_trigger=48.00) is False


def test_immediate_trigger_buy_short_above_trigger() -> None:
    """Short position com SL acima: preço subiu acima do trigger → disparar."""
    assert trail_should_immediate_trigger(side=1, last_price=52.05, sl_trigger=52.00) is True


def test_immediate_trigger_buy_short_at_trigger() -> None:
    """Short com last == trigger: cruzou (boundary inclusive)."""
    assert trail_should_immediate_trigger(side=1, last_price=52.00, sl_trigger=52.00) is True


def test_immediate_trigger_buy_short_below_trigger() -> None:
    """Short com preço abaixo do SL: tudo OK, não disparar."""
    assert trail_should_immediate_trigger(side=1, last_price=51.50, sl_trigger=52.00) is False


def test_immediate_trigger_none_trigger() -> None:
    """sl_trigger=None: short-circuit False (level sem SL configurado)."""
    assert trail_should_immediate_trigger(side=2, last_price=48.00, sl_trigger=None) is False


def test_immediate_trigger_none_last_price() -> None:
    """last_price=None: short-circuit False (sem feed disponível)."""
    assert trail_should_immediate_trigger(side=2, last_price=None, sl_trigger=48.00) is False


def test_immediate_trigger_accepts_int_or_float() -> None:
    """sl_trigger pode chegar como Decimal/int do DB — float() coerce."""
    assert trail_should_immediate_trigger(side=2, last_price=47.99, sl_trigger=48) is True
    assert trail_should_immediate_trigger(side=1, last_price=52.01, sl_trigger=52) is True


# ── compute_trading_result_match (P2-futuros fix 01/mai) ─────────────────────


class TestTradingResultMatch:
    def test_all_empty_returns_none(self) -> None:
        """Sem nenhum identifier — skip (UPDATE sem WHERE = desastre)."""
        assert compute_trading_result_match(0, None, 0) is None
        assert compute_trading_result_match(None, "", None) is None
        assert compute_trading_result_match(0, "  ", 0) is None  # whitespace = vazio

    def test_only_local_id(self) -> None:
        match = compute_trading_result_match(12345, None, 0)
        assert match is not None
        where, params = match
        assert where == "local_order_id = %s"
        assert params == (12345,)

    def test_only_cl_ord_id(self) -> None:
        match = compute_trading_result_match(0, "robot:1:PETR4:BUY:2026-05-01T12:00", None)
        assert match is not None
        where, params = match
        assert where == "cl_ord_id = %s"
        assert params == ("robot:1:PETR4:BUY:2026-05-01T12:00",)

    def test_only_message_id_p2_futuros_case(self) -> None:
        """P2-futuros core case — local_id=0 + cl_ord vazio + msg_id válido.

        Antes do fix: skip + status stuck em 10. Depois: match por message_id.
        """
        match = compute_trading_result_match(0, "", 999_888_777)
        assert match is not None
        where, params = match
        assert where == "message_id = %s"
        assert params == (999_888_777,)

    def test_all_three_identifiers(self) -> None:
        match = compute_trading_result_match(123, "cl_xyz", 456)
        assert match is not None
        where, params = match
        # OR'd na ordem: local, cl, message
        assert where == "local_order_id = %s OR cl_ord_id = %s OR message_id = %s"
        assert params == (123, "cl_xyz", 456)

    def test_local_and_message_no_cl(self) -> None:
        match = compute_trading_result_match(123, None, 456)
        assert match is not None
        where, params = match
        assert where == "local_order_id = %s OR message_id = %s"
        assert params == (123, 456)

    def test_cl_and_message_no_local(self) -> None:
        match = compute_trading_result_match(0, "cl_xyz", 456)
        assert match is not None
        where, params = match
        assert where == "cl_ord_id = %s OR message_id = %s"
        assert params == ("cl_xyz", 456)

    def test_negative_local_id_treated_as_empty(self) -> None:
        """local_id <= 0 nunca deve fazer match (NULL e 0 reservados)."""
        match = compute_trading_result_match(-1, None, 0)
        assert match is None

    def test_negative_message_id_treated_as_empty(self) -> None:
        match = compute_trading_result_match(0, None, -1)
        assert match is None


# ── should_retry_rejection (P1 04/mai) ────────────────────────────────────────


class TestShouldRetryRejection:
    """Decisao do trading_msg_cb: rejeicao broker-blip vs erro de regra.

    True so quando code rejection-like (1,3,5,7,9,24) + msg contem
    blip pattern (Cliente nao logado, timeout, subconex etc).
    """

    def test_rejected_mercury_with_logado_pattern(self) -> None:
        # code=3 RejectedMercury + "Cliente nao esta logado" — caso
        # canonico do log Delphi smoke 04/mai.
        assert should_retry_rejection(3, "Cliente não está logado") is True
        assert should_retry_rejection(3, "Cliente nao esta logado") is True

    def test_rejected_broker_with_timeout_pattern(self) -> None:
        # code=7 RejectedBroker + timeout — outra variante de blip
        assert should_retry_rejection(7, "Timeout aguardando resposta") is True

    def test_not_connected_with_subconex_pattern(self) -> None:
        # code=1 NotConnected + subconex
        assert should_retry_rejection(1, "Subconexao perdida") is True

    def test_rejected_market_with_logado(self) -> None:
        # code=9 RejectedMarket — menos comum mas valido
        assert should_retry_rejection(9, "logado.") is True

    def test_blocked_by_risk_with_blip(self) -> None:
        # code=24 BlockedByRisk + blip — improvavel mas valido
        assert should_retry_rejection(24, "cliente nao conectado") is True

    def test_rejected_hades_invalid_order_no_retry(self) -> None:
        # code=5 RejectedHades + "Ordem invalida" — NAO e' blip,
        # NAO deve retry (qty errada, price fora de circuito etc)
        assert should_retry_rejection(5, "Ordem invalida") is False

    def test_rejected_broker_with_lot_size_no_retry(self) -> None:
        # Bug 04/mai: "multiplo do lote" NAO deve trigger retry —
        # qty errada nao corrige sozinha. Strategy precisa fix.
        assert (
            should_retry_rejection(
                7, "Risco Simulador: Quantidade da ordem deve ser multiplo do lote"
            )
            is False
        )

    def test_starting_code_not_retryable(self) -> None:
        # code=0 Starting — nao e' rejection-like
        assert should_retry_rejection(0, "Cliente nao logado") is False

    def test_sent_to_hades_proxy_not_retryable(self) -> None:
        # code=2 SentToHadesProxy — sucesso, nao e' rejection
        assert should_retry_rejection(2, "Enviando ordem") is False

    def test_sent_to_hades_not_retryable(self) -> None:
        # code=4 SentToHades — sucesso
        assert should_retry_rejection(4, "Enviado ao servidor de ordens") is False

    def test_sent_to_market_not_retryable(self) -> None:
        # code=8 SentToMarket — sucesso
        assert should_retry_rejection(8, "Enviado ao mercado") is False

    def test_accepted_not_retryable(self) -> None:
        # code=10 Accepted (pendente no book) — nao e' rejection
        assert should_retry_rejection(10, "logado") is False

    def test_empty_message_no_retry_even_with_rejection_code(self) -> None:
        # Sem msg, nao da pra distinguir blip de erro real — NAO retry.
        assert should_retry_rejection(3, "") is False
        assert should_retry_rejection(3, None) is False

    def test_case_insensitive_match(self) -> None:
        # Pattern matching deve ser case-insensitive
        assert should_retry_rejection(3, "CLIENTE NAO ESTA LOGADO") is True
        assert should_retry_rejection(3, "TimeOut") is True

    def test_unknown_message_no_retry(self) -> None:
        # code rejection-like mas msg sem blip pattern -> NAO retry
        assert should_retry_rejection(3, "Saldo insuficiente para a operacao") is False
        assert should_retry_rejection(7, "Fora de horario de pregao") is False


# ── message_has_blip_pattern (variante so-msg) ────────────────────────────────


class TestMessageHasBlipPattern:
    """Variante usada em order_cb: status=8 ja confirmado via DLL,
    so precisa decidir se msg indica blip (retry vale) vs regra (nao)."""

    def test_logado_pattern(self) -> None:
        assert message_has_blip_pattern("Cliente nao esta logado") is True

    def test_timeout_pattern(self) -> None:
        assert message_has_blip_pattern("Timeout") is True

    def test_subconex_pattern(self) -> None:
        assert message_has_blip_pattern("Subconexao perdida") is True

    def test_lot_size_msg_not_blip(self) -> None:
        # Bug 04/mai: lot size NAO e' blip
        assert (
            message_has_blip_pattern(
                "Risco Simulador: Quantidade deve ser multiplo do lote"
            )
            is False
        )

    def test_empty_string_not_blip(self) -> None:
        assert message_has_blip_pattern("") is False

    def test_none_not_blip(self) -> None:
        assert message_has_blip_pattern(None) is False

    def test_case_insensitive(self) -> None:
        assert message_has_blip_pattern("LOGADO") is True
        assert message_has_blip_pattern("timeout aguardando") is True


# ── resolve_subscribe_list (P1 04/mai — fix P0 #4 subscribe race) ─────────────


class TestResolveSubscribeList:
    """Resolve final list of (ticker, exchange) to subscribe at boot.

    Bug raiz (smoke 04/mai): logica original `if db: use DB else env`
    deixava 0 subscriptions quando DB conectado mas vazio. Fix: union
    sempre, env como seed garantido.
    """

    def test_empty_db_uses_env(self) -> None:
        """Bug raiz 04/mai: DB conectado mas vazio → fallback pra env."""
        result = resolve_subscribe_list(
            db_tickers=[],
            env_tickers=["PETR4", "VALE3"],
            db_connected=True,
        )
        assert result == [("PETR4", "B"), ("VALE3", "B")]

    def test_db_only_when_env_empty(self) -> None:
        result = resolve_subscribe_list(
            db_tickers=[("WINFUT", "F"), ("PETR4", "B")],
            env_tickers=[],
            db_connected=True,
        )
        assert ("WINFUT", "F") in result
        assert ("PETR4", "B") in result
        assert len(result) == 2

    def test_union_env_and_db(self) -> None:
        """Env primeiro, DB adiciona extras."""
        result = resolve_subscribe_list(
            db_tickers=[("WINFUT", "F"), ("PETR4", "B")],
            env_tickers=["PETR4", "VALE3"],
            db_connected=True,
        )
        # env first: PETR4, VALE3 (deduplica PETR4 do DB)
        # db extra: WINFUT
        assert result[0] == ("PETR4", "B")  # env primeiro
        assert result[1] == ("VALE3", "B")
        assert ("WINFUT", "F") in result
        assert len(result) == 3, f"Esperava 3, got {result}"

    def test_dedup_same_ticker_exchange(self) -> None:
        """PETR4:B em ambos → aparece 1 vez."""
        result = resolve_subscribe_list(
            db_tickers=[("PETR4", "B")],
            env_tickers=["PETR4"],
            db_connected=True,
        )
        assert result == [("PETR4", "B")]

    def test_db_disconnected_uses_env_only(self) -> None:
        """DB indisponivel → ignora db_tickers (mesmo se passado)."""
        result = resolve_subscribe_list(
            db_tickers=[("WINFUT", "F")],  # ignored
            env_tickers=["PETR4", "VALE3"],
            db_connected=False,
        )
        assert result == [("PETR4", "B"), ("VALE3", "B")]
        assert ("WINFUT", "F") not in result

    def test_both_empty_returns_empty(self) -> None:
        result = resolve_subscribe_list(
            db_tickers=[],
            env_tickers=[],
            db_connected=True,
        )
        assert result == []

    def test_uppercase_normalization(self) -> None:
        """Tickers e exchanges sao uppercased."""
        result = resolve_subscribe_list(
            db_tickers=[("petr4", "b"), ("winfut", "f")],
            env_tickers=["vale3"],
            db_connected=True,
        )
        assert ("PETR4", "B") in result
        assert ("WINFUT", "F") in result
        assert ("VALE3", "B") in result

    def test_skip_empty_tickers(self) -> None:
        """Empty/whitespace tickers no env sao ignorados."""
        result = resolve_subscribe_list(
            db_tickers=[],
            env_tickers=["", "PETR4", "  ", "VALE3"],
            db_connected=True,
        )
        assert result == [("PETR4", "B"), ("VALE3", "B")]

    def test_different_exchange_kept_separate(self) -> None:
        """PETR4:B vs PETR4:F sao subscriptions distintas."""
        result = resolve_subscribe_list(
            db_tickers=[("PETR4", "F")],  # imaginario PETR4 em F
            env_tickers=["PETR4"],
            db_connected=True,
        )
        # PETR4:B do env + PETR4:F do DB = 2 entries
        assert len(result) == 2
        assert ("PETR4", "B") in result
        assert ("PETR4", "F") in result

    def test_default_exchange_override(self) -> None:
        """Permite default_exchange custom (ex: F para futures-only seed)."""
        result = resolve_subscribe_list(
            db_tickers=[],
            env_tickers=["WINFUT", "WDOFUT"],
            db_connected=True,
            default_exchange="F",
        )
        assert result == [("WINFUT", "F"), ("WDOFUT", "F")]


# ── parse_order_details (P1 04/mai refactor 3/3) ──────────────────────────────


def _make_mock_order_struct(
    *,
    local_order_id: int = 100,
    cl_ord_id: str = "robot:1:PETR4:BUY:2026-05-04T16:30",
    ticker: str = "PETR4    ",
    exchange: str = "B    ",
    quantity: int = 100,
    traded_qty: int = 100,
    leaves_qty: int = 0,
    price: float = -1.0,
    stop_price: float = -1.0,
    avg_price: float = 49.45,
    order_side: int = 1,
    order_type: int = 1,
    order_status: int = 2,
    validity_type: int = 0,
    text_message: str = "Enviado ao servidor de ordens.    ",
):
    """Constroi mock duck-typed de TConnectorOrderOut.

    SimpleNamespace evita necessidade de ctypes — testavel em CI Linux.
    """
    return SimpleNamespace(
        OrderID=SimpleNamespace(
            LocalOrderID=local_order_id,
            ClOrderID=cl_ord_id,
        ),
        AssetID=SimpleNamespace(
            Ticker=ticker,
            Exchange=exchange,
        ),
        Quantity=quantity,
        TradedQuantity=traded_qty,
        LeavesQuantity=leaves_qty,
        Price=price,
        StopPrice=stop_price,
        AveragePrice=avg_price,
        OrderSide=order_side,
        OrderType=order_type,
        OrderStatus=order_status,
        ValidityType=validity_type,
        TextMessage=text_message,
    )


class TestParseOrderDetails:
    """Pure parsing de TConnectorOrderOut populated → dict.

    Cenarios: filled (status=2), rejected with msg (status=8), null
    string fields (DLL pode retornar NULL), padding strip.
    """

    def test_filled_order(self) -> None:
        struct = _make_mock_order_struct(order_status=2)
        d = parse_order_details(struct)
        assert d["order_status"] == 2
        assert d["local_order_id"] == 100
        assert d["traded_qty"] == 100
        assert d["leaves_qty"] == 0
        assert d["avg_price"] == 49.45

    def test_rejected_order_text_message(self) -> None:
        """Smoke 04/mai canonico: status=8 + msg lot_size."""
        struct = _make_mock_order_struct(
            order_status=8,
            traded_qty=0,
            leaves_qty=10,
            text_message="Risco Simulador: Quantidade da ordem deve ser multiplo do lote",
        )
        d = parse_order_details(struct)
        assert d["order_status"] == 8
        assert d["traded_qty"] == 0
        assert d["leaves_qty"] == 10
        assert "multiplo do lote" in d["text_message"]

    def test_strip_string_padding(self) -> None:
        """Strings vem padded (`' ' * length` antes do 2nd GetOrderDetails)."""
        struct = _make_mock_order_struct(
            ticker="PETR4               ",
            exchange="B    ",
            text_message="Enviando ordem ao HadesProxy        ",
        )
        d = parse_order_details(struct)
        assert d["ticker"] == "PETR4"
        assert d["exchange"] == "B"
        assert d["text_message"] == "Enviando ordem ao HadesProxy"

    def test_null_strings_handled(self) -> None:
        """DLL pode retornar None em c_wchar_p — defensivo."""
        struct = _make_mock_order_struct(
            cl_ord_id=None,
            ticker=None,
            exchange=None,
            text_message=None,
        )
        d = parse_order_details(struct)
        assert d["cl_ord_id"] == ""
        assert d["ticker"] == ""
        assert d["exchange"] == ""
        assert d["text_message"] == ""

    def test_all_required_keys_present(self) -> None:
        """Sentinel: dict tem todas as keys que callers consomem."""
        struct = _make_mock_order_struct()
        d = parse_order_details(struct)
        required = {
            "local_order_id",
            "cl_ord_id",
            "ticker",
            "exchange",
            "quantity",
            "traded_qty",
            "leaves_qty",
            "price",
            "stop_price",
            "avg_price",
            "order_side",
            "order_type",
            "order_status",
            "validity_type",
            "text_message",
        }
        assert set(d.keys()) == required

    def test_market_order_price_minus_one(self) -> None:
        """Market order: price=-1 (sentinel da DLL)."""
        struct = _make_mock_order_struct(order_type=1, price=-1.0)
        d = parse_order_details(struct)
        assert d["order_type"] == 1
        assert d["price"] == -1.0

    def test_limit_order_with_price(self) -> None:
        struct = _make_mock_order_struct(order_type=2, price=49.50)
        d = parse_order_details(struct)
        assert d["order_type"] == 2
        assert d["price"] == 49.50

    def test_stop_order_with_stop_price(self) -> None:
        struct = _make_mock_order_struct(order_type=4, stop_price=48.00)
        d = parse_order_details(struct)
        assert d["order_type"] == 4
        assert d["stop_price"] == 48.00

    def test_partial_fill(self) -> None:
        """status=1 PartialFilled: traded < quantity, leaves > 0."""
        struct = _make_mock_order_struct(
            order_status=1, quantity=200, traded_qty=80, leaves_qty=120
        )
        d = parse_order_details(struct)
        assert d["order_status"] == 1
        assert d["traded_qty"] == 80
        assert d["leaves_qty"] == 120


# ── infer_lot_size (P0 #1 04/mai — defesa em profundidade pre-SendOrder) ─────


class TestInferLotSize:
    """Heuristica B3 conservadora: so retorna inteiro com confianca alta.
    Casos ambiguos retornam None (caller pula validacao, nao bloqueia)."""

    def test_b3_stocks_ordinaria(self) -> None:
        # ON termina em 3
        assert infer_lot_size("PETR3", "B") == 100
        assert infer_lot_size("VALE3", "B") == 100
        assert infer_lot_size("ITUB3", "B") == 100

    def test_b3_stocks_preferencial(self) -> None:
        # PN termina em 4
        assert infer_lot_size("PETR4", "B") == 100
        assert infer_lot_size("ITUB4", "B") == 100
        assert infer_lot_size("BBDC4", "B") == 100

    def test_b3_stocks_pna_pnb(self) -> None:
        # PNA=5, PNB=6
        assert infer_lot_size("USIM5", "B") == 100
        assert infer_lot_size("BRKM5", "B") == 100
        assert infer_lot_size("BRSR6", "B") == 100

    def test_b3_units_eleven_returns_none(self) -> None:
        # Units/BDRs ambiguos (alguns 1, outros 10, outros 100)
        assert infer_lot_size("BPAC11", "B") is None
        assert infer_lot_size("KLBN11", "B") is None
        assert infer_lot_size("ALUP11", "B") is None

    def test_b3_futures_exchange_f(self) -> None:
        # Futuros B3 sao unitarios
        assert infer_lot_size("WINFUT", "F") == 1
        assert infer_lot_size("WDOFUT", "F") == 1
        assert infer_lot_size("WINM26", "F") == 1
        assert infer_lot_size("DOLM26", "F") == 1

    def test_case_insensitive_inputs(self) -> None:
        assert infer_lot_size("petr4", "b") == 100
        assert infer_lot_size("winfut", "f") == 1

    def test_unknown_exchange_returns_none(self) -> None:
        assert infer_lot_size("PETR4", "X") is None
        assert infer_lot_size("PETR4", "") is None
        assert infer_lot_size("PETR4", None) is None

    def test_empty_ticker_returns_none(self) -> None:
        assert infer_lot_size("", "B") is None
        assert infer_lot_size(None, "B") is None

    def test_ticker_ending_in_letter_returns_none(self) -> None:
        # Estrutura nao-canonica — nao infere
        assert infer_lot_size("ABCDEF", "B") is None
        assert infer_lot_size("XPTO", "B") is None

    def test_ticker_too_short_returns_none(self) -> None:
        # Defensivo contra strings curtas
        assert infer_lot_size("3", "B") is None
        assert infer_lot_size("X", "B") is None


# ── validate_order_quantity (P0 #1 04/mai) ────────────────────────────────────


class TestValidateOrderQuantity:
    """Bloqueia qty fora do lote pre-SendOrder. Retorna None se OK,
    string com msg de erro se invalido. lot_size None ou <=0 = skip."""

    def test_valid_multiple(self) -> None:
        assert validate_order_quantity(100, 100) is None
        assert validate_order_quantity(200, 100) is None
        assert validate_order_quantity(1000, 100) is None
        assert validate_order_quantity(5, 1) is None

    def test_invalid_qty_below_lot(self) -> None:
        # Caso canonico do smoke 04/mai
        err = validate_order_quantity(20, 100)
        assert err is not None
        assert "qty=20" in err
        assert "lote=100" in err

    def test_invalid_qty_not_multiple(self) -> None:
        err = validate_order_quantity(150, 100)
        assert err is not None
        assert "150" in err

    def test_error_includes_suggestion(self) -> None:
        # Sugestao deve ser o multiplo inferior (ou lot_size se zero)
        err = validate_order_quantity(250, 100)
        assert err is not None
        assert "200" in err
        # qty < lot: sugestao = lot_size (nao zero)
        err2 = validate_order_quantity(20, 100)
        assert err2 is not None
        assert "100" in err2

    def test_skip_when_lot_size_none(self) -> None:
        # Heuristica nao inferiu — caller skip valida
        assert validate_order_quantity(20, None) is None
        assert validate_order_quantity(123, None) is None

    def test_skip_when_lot_size_zero_or_negative(self) -> None:
        # Defensivo: lot_size invalido = skip (nao bloqueia)
        assert validate_order_quantity(20, 0) is None
        assert validate_order_quantity(20, -1) is None

    def test_qty_zero_or_negative_with_lot(self) -> None:
        # Defensivo: caller normalmente ja bloqueia qty<=0 antes,
        # mas se chegar aqui retorna erro explicito
        err = validate_order_quantity(0, 100)
        assert err is not None
        assert "invalida" in err
        err2 = validate_order_quantity(-10, 100)
        assert err2 is not None

    def test_futures_lot_one_always_valid(self) -> None:
        # WINFUT lot=1: qualquer qty positiva passa
        assert validate_order_quantity(1, 1) is None
        assert validate_order_quantity(5, 1) is None
        assert validate_order_quantity(100, 1) is None
