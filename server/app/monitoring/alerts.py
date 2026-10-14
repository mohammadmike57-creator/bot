"""Notification architecture: Telegram, email and generic webhook channels.

Channels are enabled only when their credentials are configured. Delivery runs
on a small thread pool so a slow notification provider never blocks trading
decisions. Every alert is also written to the structured log.
"""

from __future__ import annotations

import smtplib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import EmailMessage
from enum import Enum
from typing import Any, Protocol

import httpx

from app.config.settings import Settings
from app.monitoring.logger import get_logger

log = get_logger(__name__)


class AlertType(str, Enum):
    TRADE_OPENED = "TRADE_OPENED"
    TRADE_CLOSED = "TRADE_CLOSED"
    LARGE_PROFIT = "LARGE_PROFIT"
    LARGE_LOSS = "LARGE_LOSS"
    DAILY_LOSS_REACHED = "DAILY_LOSS_REACHED"
    DRAWDOWN_REACHED = "DRAWDOWN_REACHED"
    EA_OFFLINE = "EA_OFFLINE"
    BACKEND_OFFLINE = "BACKEND_OFFLINE"
    NEWS_BLACKOUT = "NEWS_BLACKOUT"
    NEWS_SERVICE_DOWN = "NEWS_SERVICE_DOWN"
    KILL_SWITCH = "KILL_SWITCH"
    MODEL_FAILURE = "MODEL_FAILURE"
    SAFE_MODE = "SAFE_MODE"
    SLIPPAGE_ABNORMAL = "SLIPPAGE_ABNORMAL"
    LOSS_STREAK_COOLDOWN = "LOSS_STREAK_COOLDOWN"


class Severity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass
class Alert:
    type: AlertType
    message: str
    severity: Severity = Severity.INFO
    data: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def text(self) -> str:
        return f"[{self.severity.value}] {self.type.value}: {self.message}"


class Channel(Protocol):
    name: str

    def send(self, alert: Alert) -> None: ...


class TelegramChannel:
    name = "telegram"

    def __init__(self, token: str, chat_id: str):
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"
        self._chat_id = chat_id

    def send(self, alert: Alert) -> None:
        httpx.post(self._url, json={"chat_id": self._chat_id, "text": alert.text()}, timeout=10).raise_for_status()


class WebhookChannel:
    name = "webhook"

    def __init__(self, url: str):
        self._url = url

    def send(self, alert: Alert) -> None:
        payload = {
            "type": alert.type.value,
            "severity": alert.severity.value,
            "message": alert.message,
            "data": alert.data,
            "created_at": alert.created_at.isoformat(),
        }
        httpx.post(self._url, json=payload, timeout=10).raise_for_status()


class EmailChannel:
    name = "email"

    def __init__(self, host: str, port: int, username: str, password: str, sender: str, recipient: str):
        self._host, self._port = host, port
        self._username, self._password = username, password
        self._sender, self._recipient = sender, recipient

    def send(self, alert: Alert) -> None:
        msg = EmailMessage()
        msg["Subject"] = f"Trading alert: {alert.type.value}"
        msg["From"] = self._sender
        msg["To"] = self._recipient
        msg.set_content(alert.text())
        with smtplib.SMTP(self._host, self._port, timeout=15) as smtp:
            smtp.starttls()
            if self._username:
                smtp.login(self._username, self._password)
            smtp.send_message(msg)


class Notifier:
    def __init__(self, channels: list[Channel] | None = None, synchronous: bool = False):
        self.channels = channels or []
        self._synchronous = synchronous
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="alerts")
        self.history: list[Alert] = []

    @classmethod
    def from_settings(cls, settings: Settings) -> Notifier:
        channels: list[Channel] = []
        token = settings.telegram_bot_token.get_secret_value()
        if token and settings.telegram_chat_id:
            channels.append(TelegramChannel(token, settings.telegram_chat_id))
        if settings.alert_webhook_url:
            channels.append(WebhookChannel(settings.alert_webhook_url))
        if settings.smtp_host and settings.alert_email_to and settings.alert_email_from:
            channels.append(
                EmailChannel(
                    settings.smtp_host,
                    settings.smtp_port,
                    settings.smtp_username,
                    settings.smtp_password.get_secret_value(),
                    settings.alert_email_from,
                    settings.alert_email_to,
                )
            )
        return cls(channels, synchronous=settings.environment == "test")

    def notify(self, alert: Alert) -> None:
        self.history.append(alert)
        del self.history[:-200]
        log.log(
            40 if alert.severity == Severity.CRITICAL else 30 if alert.severity == Severity.WARNING else 20,
            alert.message,
            extra={"alert_type": alert.type.value, "alert_data": alert.data},
        )
        for channel in self.channels:
            if self._synchronous:
                self._deliver(channel, alert)
            else:
                self._pool.submit(self._deliver, channel, alert)

    @staticmethod
    def _deliver(channel: Channel, alert: Alert) -> None:
        try:
            channel.send(alert)
        except Exception as exc:  # delivery failures must never crash trading logic
            log.warning("alert delivery failed", extra={"channel": channel.name, "error": str(exc)})
