"""Server-only client; the browser cannot choose an upstream or see its token."""
import os
from dataclasses import dataclass
from urllib.parse import urlsplit

import requests

from app.vehicle_checks.models import Report


class CheckError(Exception):
    def __init__(self, code, message, status=503):
        self.code, self.public_message, self.status = code, message, status
        super().__init__(message)


def integer(name, default, minimum, maximum):
    try:
        return max(minimum, min(int(os.getenv(name, str(default))), maximum))
    except (ValueError, TypeError):
        return default


@dataclass(frozen=True)
class CheckSettings:
    base_url: str
    token: str
    allow_paid: bool = False
    demo_only: bool = False
    daily_limit: int = 10
    timeout: int = 75

    @classmethod
    def from_env(cls):
        return cls(os.getenv("CHECK_CARS_BASE_URL", "").strip().rstrip("/"),
                   os.getenv("CHECK_CARS_INTEGRATION_TOKEN", "").strip(),
                   os.getenv("CHECK_CARS_ALLOW_PAID", "0") == "1",
                   os.getenv("CHECK_CARS_DEMO_ONLY", "0") == "1",
                   integer("CHECK_CARS_DAILY_LIMIT", 10, 1, 100),
                   integer("CHECK_CARS_TIMEOUT_SECONDS", 75, 5, 90))

    def validate(self):
        try:
            url = urlsplit(self.base_url)
            valid = (url.scheme in ("http", "https") and url.hostname and not url.username and not url.password
                     and not url.query and not url.fragment and url.path in ("", "/"))
            if url.scheme == "http" and url.hostname not in ("127.0.0.1", "localhost", "::1"):
                valid = False
            _ = url.port
        except ValueError:
            valid = False
        if not valid or len(self.token) < 32:
            raise CheckError("not_configured", "Проверка истории не подключена. Настройте связку с ШАССИ по docs/vehicle-checks.md")

    def public_status(self):
        try:
            self.validate()
            configured = True
        except CheckError:
            configured = False
        return {"configured": configured, "allow_paid": self.allow_paid, "demo_only": self.demo_only,
                "default_mode": "demo" if self.demo_only else "live", "daily_limit": self.daily_limit,
                "message": "Связка с ШАССИ настроена" if configured else "Проверка истории не подключена; см. docs/vehicle-checks.md"}


ERRORS = {
    401: ("upstream_auth", "ШАССИ отклонил интеграционный токен. Проверьте настройки двух сервисов"),
    402: ("paid_consent_required", "В ШАССИ подключены платные источники. Нужны разрешение владельца в настройках и отдельная галочка согласия"),
    409: ("upstream_busy", "ШАССИ уже выполняет проверку. Автоматического повтора нет"),
    422: ("upstream_input", "ШАССИ не принял VIN/госномер или не смог определить VIN. Проверьте ввод и доступность источников"),
    429: ("upstream_limit", "Достигнут лимит ШАССИ. Автоматического повтора нет"),
}


def fetch_report(config: CheckSettings, payload: dict) -> tuple[Report, bool]:
    config.validate()
    try:
        with requests.post(config.base_url + "/api/integrations/car-lot-pilot", json=payload,
                           headers={"Authorization": "Bearer " + config.token, "Accept": "application/json"},
                           timeout=(5, config.timeout), allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                code, message = ERRORS.get(response.status_code, ("upstream_failed", "ШАССИ недоступен или проверка не завершилась. Автоматического повтора нет"))
                raise CheckError(code, message, 402 if response.status_code == 402 else 503)
            chunks, size = [], 0
            for chunk in response.iter_content(16384):
                size += len(chunk)
                if size > 256_000:
                    raise CheckError("invalid_report", "ШАССИ вернул слишком большой отчёт", 502)
                chunks.append(chunk)
        import json
        envelope = json.loads(b"".join(chunks))
        if envelope.get("ok") is not True or not isinstance(envelope.get("cached"), bool):
            raise ValueError("Invalid envelope")
        report = Report.model_validate(envelope["report"])
        if report.mode != payload["mode"]:
            raise ValueError("Wrong mode")
        return report, envelope["cached"]
    except CheckError:
        raise
    except requests.RequestException:
        raise CheckError("upstream_unreachable", "Нет ответа от ШАССИ. Проверка могла завершиться на его стороне; автоматического повтора нет") from None
    except (ValueError, TypeError, KeyError, AttributeError):
        raise CheckError("invalid_report", "Ответ ШАССИ не прошёл проверку формата или режима данных", 502) from None
