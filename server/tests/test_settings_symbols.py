import pytest
from pydantic import ValidationError

from app.config.settings import LIVE_CONFIRMATION_PHRASE, Settings, TradingMode
from app.config.symbols import SymbolResolver
from app.database.database import normalize_database_url
from app.services.signal_service import effective_mode


def make(**kw):
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def test_defaults_are_paper_and_live_disabled():
    s = make()
    assert s.trading_mode is TradingMode.PAPER
    assert s.enable_live_trading is False
    assert s.effective_trading_mode is TradingMode.PAPER


def test_live_requires_all_safeguards():
    assert make(trading_mode="LIVE").effective_trading_mode is TradingMode.PAPER
    assert make(trading_mode="LIVE", enable_live_trading=True).effective_trading_mode is TradingMode.PAPER
    s = make(trading_mode="LIVE", enable_live_trading=True, live_trading_confirmation=LIVE_CONFIRMATION_PHRASE)
    assert s.effective_trading_mode is TradingMode.LIVE


def test_most_conservative_mode_wins():
    assert effective_mode(TradingMode.DEMO, "PAPER") is TradingMode.PAPER
    assert effective_mode(TradingMode.PAPER, "LIVE") is TradingMode.PAPER
    assert effective_mode(TradingMode.LIVE, "DEMO") is TradingMode.DEMO


@pytest.mark.parametrize("kw", [
    dict(risk_per_trade=-0.01), dict(risk_per_trade=1.5), dict(max_daily_loss=0),
    dict(risk_per_trade=0.02, max_symbol_risk=0.015), dict(max_symbol_risk=0.05, max_portfolio_risk=0.03),
    dict(risk_multiplier_max=3.0), dict(confidence_threshold=0.4), dict(max_positions=0),
])
def test_invalid_risk_settings_rejected(kw):
    with pytest.raises(ValidationError):
        make(**kw)


def test_symbol_mapping_is_configured_not_assumed():
    s = make(xauusd_symbol="GOLD", oil_symbol="USOIL.cash", us30_symbol="US30.cash", extra_symbols="EURUSD=EURUSD.r")
    r = SymbolResolver(s.symbol_map())
    assert r.to_broker("XAUUSD") == "GOLD"
    assert r.to_logical("USOIL.cash") == "OIL"
    assert r.to_logical("EURUSD.r") == "EURUSD"
    assert r.to_logical("WTI") is None
    assert make().symbol_map() == {}


def test_render_database_url_normalised():
    assert normalize_database_url("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalize_database_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
