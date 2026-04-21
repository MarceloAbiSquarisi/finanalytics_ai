"""
Testes unitarios para AlertService.

Estrategia: AlertService instancia SQLAlertRepository internamente via session.
Usamos unittest.mock.patch para interceptar a instanciacao do repo.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from finanalytics_ai.application.services.alert_service import AlertService
from finanalytics_ai.domain.entities.alert import Alert, AlertStatus, AlertType


def _make_alert(
    alert_id: str = "alert-001",
    alert_type: AlertType = AlertType.STOP_LOSS,
    threshold: str = "30.00",
    reference_price: str = "40.00",
) -> Alert:
    return Alert(
        alert_id=alert_id,
        ticker="PETR4",
        alert_type=alert_type,
        threshold=Decimal(threshold),
        reference_price=Decimal(reference_price),
        user_id="user-001",
        status=AlertStatus.ACTIVE,
    )


def _make_session_factory() -> MagicMock:
    session = AsyncMock()
    session.begin = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=cm)
    return factory


@pytest.fixture
def mock_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.find_active_by_ticker.return_value = []
    repo.mark_triggered.return_value = None
    repo.cancel.return_value = True
    repo.find_by_user.return_value = []
    repo.save.return_value = None
    return repo


@pytest.fixture
def mock_bus() -> MagicMock:
    bus = MagicMock()
    bus.broadcast = AsyncMock()
    return bus


@pytest.fixture
def service(mock_bus: MagicMock) -> AlertService:
    return AlertService(session_factory=_make_session_factory(), notification_bus=mock_bus)


REPO_PATH = "finanalytics_ai.application.services.alert_service.SQLAlertRepository"


class TestEvaluatePrice:
    @pytest.mark.asyncio
    async def test_no_active_alerts_returns_zero(self, service, mock_repo, mock_bus):
        mock_repo.find_active_by_ticker.return_value = []
        with patch(REPO_PATH, return_value=mock_repo):
            count = await service.evaluate_price("PETR4", 25.0)
        assert count == 0
        mock_bus.broadcast.assert_not_called()

    @pytest.mark.asyncio
    async def test_triggered_alert_increments_count(self, service, mock_repo):
        alert = _make_alert(alert_type=AlertType.STOP_LOSS, threshold="30.00")
        mock_repo.find_active_by_ticker.return_value = [alert]
        with patch(REPO_PATH, return_value=mock_repo):
            count = await service.evaluate_price("PETR4", 25.0)
        assert count == 1

    @pytest.mark.asyncio
    async def test_triggered_alert_calls_mark_triggered(self, service, mock_repo):
        alert = _make_alert(alert_type=AlertType.STOP_LOSS, threshold="30.00")
        mock_repo.find_active_by_ticker.return_value = [alert]
        with patch(REPO_PATH, return_value=mock_repo):
            await service.evaluate_price("PETR4", 25.0)
        mock_repo.mark_triggered.assert_called_once_with("alert-001")

    @pytest.mark.asyncio
    async def test_triggered_alert_broadcasts(self, service, mock_repo, mock_bus):
        alert = _make_alert(alert_type=AlertType.STOP_LOSS, threshold="30.00")
        mock_repo.find_active_by_ticker.return_value = [alert]
        with patch(REPO_PATH, return_value=mock_repo):
            await service.evaluate_price("PETR4", 25.0)
        mock_bus.broadcast.assert_called_once()

    @pytest.mark.asyncio
    async def test_non_triggered_no_side_effects(self, service, mock_repo, mock_bus):
        alert = _make_alert(alert_type=AlertType.STOP_LOSS, threshold="30.00")
        mock_repo.find_active_by_ticker.return_value = [alert]
        with patch(REPO_PATH, return_value=mock_repo):
            count = await service.evaluate_price("PETR4", 35.0)
        assert count == 0
        mock_repo.mark_triggered.assert_not_called()
        mock_bus.broadcast.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_alerts_counts_triggered_only(self, service, mock_repo, mock_bus):
        stop = _make_alert("a1", AlertType.STOP_LOSS, threshold="30.00")
        tp = Alert(
            alert_id="a2",
            ticker="PETR4",
            alert_type=AlertType.TAKE_PROFIT,
            threshold=Decimal("60.00"),
            user_id="user-001",
            status=AlertStatus.ACTIVE,
        )
        mock_repo.find_active_by_ticker.return_value = [stop, tp]
        with patch(REPO_PATH, return_value=mock_repo):
            count = await service.evaluate_price("PETR4", 25.0)
        assert count == 1
        mock_repo.mark_triggered.assert_called_once_with("a1")

    @pytest.mark.asyncio
    async def test_two_triggered_alerts(self, service, mock_repo, mock_bus):
        a1 = _make_alert("a1", AlertType.STOP_LOSS, threshold="30.00")
        a2 = _make_alert("a2", AlertType.STOP_LOSS, threshold="35.00")
        mock_repo.find_active_by_ticker.return_value = [a1, a2]
        with patch(REPO_PATH, return_value=mock_repo):
            count = await service.evaluate_price("PETR4", 20.0)
        assert count == 2
        assert mock_bus.broadcast.call_count == 2

    @pytest.mark.asyncio
    async def test_pct_drop_triggers(self, service, mock_repo, mock_bus):
        alert = _make_alert(
            alert_type=AlertType.PCT_DROP, threshold="10.0", reference_price="100.00"
        )
        mock_repo.find_active_by_ticker.return_value = [alert]
        with patch(REPO_PATH, return_value=mock_repo):
            count = await service.evaluate_price("PETR4", 88.0)
        assert count == 1

    @pytest.mark.asyncio
    async def test_pct_rise_triggers(self, service, mock_repo, mock_bus):
        alert = _make_alert(
            alert_type=AlertType.PCT_RISE, threshold="10.0", reference_price="100.00"
        )
        mock_repo.find_active_by_ticker.return_value = [alert]
        with patch(REPO_PATH, return_value=mock_repo):
            count = await service.evaluate_price("PETR4", 115.0)
        assert count == 1


class TestCreateAlert:
    @pytest.mark.asyncio
    async def test_returns_active_alert(self, service, mock_repo):
        with patch(REPO_PATH, return_value=mock_repo):
            alert = await service.create_alert(
                user_id="user-001",
                ticker="PETR4",
                alert_type="stop_loss",
                threshold=30.0,
                reference_price=40.0,
            )
        assert alert.status == AlertStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_ticker_uppercased(self, service, mock_repo):
        with patch(REPO_PATH, return_value=mock_repo):
            alert = await service.create_alert(
                user_id="user-001",
                ticker="petr4",
                alert_type="stop_loss",
                threshold=30.0,
            )
        assert alert.ticker == "PETR4"

    @pytest.mark.asyncio
    async def test_persists_to_repo(self, service, mock_repo):
        with patch(REPO_PATH, return_value=mock_repo):
            await service.create_alert(
                user_id="user-001",
                ticker="PETR4",
                alert_type="take_profit",
                threshold=60.0,
            )
        mock_repo.save.assert_called_once()


class TestCancelAlert:
    @pytest.mark.asyncio
    async def test_cancel_returns_true(self, service, mock_repo):
        mock_repo.cancel.return_value = True
        with patch(REPO_PATH, return_value=mock_repo):
            result = await service.cancel_alert("alert-001", "user-001")
        assert result is True

    @pytest.mark.asyncio
    async def test_cancel_returns_false_when_not_found(self, service, mock_repo):
        mock_repo.cancel.return_value = False
        with patch(REPO_PATH, return_value=mock_repo):
            result = await service.cancel_alert("nonexistent", "user-001")
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_passes_correct_ids(self, service, mock_repo):
        with patch(REPO_PATH, return_value=mock_repo):
            await service.cancel_alert("alert-xyz", "user-abc")
        mock_repo.cancel.assert_called_once_with("alert-xyz", "user-abc")


class TestListAlerts:
    @pytest.mark.asyncio
    async def test_returns_alerts_from_repo(self, service, mock_repo):
        expected = [_make_alert("a1"), _make_alert("a2")]
        mock_repo.find_by_user.return_value = expected
        with patch(REPO_PATH, return_value=mock_repo):
            result = await service.list_alerts("user-001")
        assert result == expected

    @pytest.mark.asyncio
    async def test_empty_list_when_no_alerts(self, service, mock_repo):
        mock_repo.find_by_user.return_value = []
        with patch(REPO_PATH, return_value=mock_repo):
            result = await service.list_alerts("user-999")
        assert result == []
