"""
core/config.py — settings del proceso, leidos del entorno.

Aqui vive el interruptor mas importante del repo:

    LIVE_EXECUTION = false

Mientras este en false, ninguna ruta de codigo puede enviar una orden real.
Cambiarlo NO es una decision de ingenieria: requiere aprobacion explicita de
Mark mas los gates de la fase 6 del HYPE_TRADING_RESEARCH_PLAN (SHADOW
superado). El default vive en el codigo, no solo en un .env, para que un
fichero de entorno ausente o mal copiado falle hacia el lado seguro.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import FrozenSet

from ..strategies.hype.common import Config, production_config, tiny_config

MODE_RESEARCH = "RESEARCH"
MODE_PAPER = "PAPER"
MODE_SHADOW = "SHADOW"
MODE_TINY = "TINY"
MODE_LIVE = "LIVE"

VALID_MODES = (MODE_RESEARCH, MODE_PAPER, MODE_SHADOW, MODE_TINY, MODE_LIVE)

# Modos en los que existe la POSIBILIDAD de tocar dinero real. Que un modo
# este aqui no autoriza nada por si solo: ademas hace falta LIVE_EXECUTION.
REAL_MONEY_MODES: FrozenSet[str] = frozenset({MODE_TINY, MODE_LIVE})


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return default if raw is None or raw.strip() == "" else float(raw)


class UnsafeConfiguration(RuntimeError):
    """La configuracion pedida permitiria algo que la politica prohibe."""


@dataclass(frozen=True)
class Settings:
    mode: str = MODE_RESEARCH
    live_execution: bool = False
    allow_short: bool = False
    database_url: str = "sqlite:///./hype_copilot.sqlite3"
    hyperliquid_api_url: str = "https://api.hyperliquid.xyz"
    max_data_age_seconds: float = 120.0
    tradingview_webhook_secret: str = ""
    equity_usd: float = 1000.0
    risk_usd_production: float = 125.0

    @property
    def can_send_real_orders(self) -> bool:
        """Doble condicion, deliberadamente redundante: el modo debe permitir
        dinero real Y el interruptor global debe estar encendido."""
        return self.live_execution and self.mode in REAL_MONEY_MODES

    def strategy_config(self) -> Config:
        """Config de estrategia correspondiente al modo activo."""
        if self.mode == MODE_TINY:
            return tiny_config(allow_short=self.allow_short)
        if self.mode == MODE_LIVE:
            return production_config(allow_short=self.allow_short,
                                     risk_usd=self.risk_usd_production)
        return Config(allow_short=self.allow_short, risk_mode="pct_equity")

    def validate(self) -> None:
        if self.mode not in VALID_MODES:
            raise UnsafeConfiguration(
                f"HYPE_MODE={self.mode!r} invalido. Validos: {VALID_MODES}"
            )
        if self.live_execution and self.mode not in REAL_MONEY_MODES:
            raise UnsafeConfiguration(
                f"LIVE_EXECUTION=true con HYPE_MODE={self.mode}: incoherente. "
                "La ejecucion real solo tiene sentido en TINY o LIVE."
            )
        if self.mode == MODE_LIVE and not (100.0 <= self.risk_usd_production <= 150.0):
            raise UnsafeConfiguration(
                f"risk_usd={self.risk_usd_production} fuera del rango "
                "de produccion $100-$150 (PRD 9.3)."
            )


def load_settings() -> Settings:
    s = Settings(
        mode=os.getenv("HYPE_MODE", MODE_RESEARCH).strip().upper(),
        # El default es False en el codigo, no solo en .env.example.
        live_execution=_env_bool("LIVE_EXECUTION", False),
        allow_short=_env_bool("HYPE_ALLOW_SHORT", False),
        database_url=os.getenv("DATABASE_URL", "sqlite:///./hype_copilot.sqlite3"),
        hyperliquid_api_url=os.getenv("HYPERLIQUID_API_URL",
                                      "https://api.hyperliquid.xyz"),
        max_data_age_seconds=_env_float("HYPE_MAX_DATA_AGE_SECONDS", 120.0),
        tradingview_webhook_secret=os.getenv("TRADINGVIEW_WEBHOOK_SECRET", ""),
        equity_usd=_env_float("HYPE_EQUITY_USD", 1000.0),
        risk_usd_production=_env_float("HYPE_RISK_USD_PRODUCTION", 125.0),
    )
    s.validate()
    return s
