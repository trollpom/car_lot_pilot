"""Bind a validated report to one lot; do not alter auction data or call the LLM."""
import hashlib
import json
import re
from datetime import timedelta

from app.vehicle_checks import store
from app.vehicle_checks.client import CheckError, CheckSettings, fetch_report
from app.vehicle_checks.models import CheckRequest

VIN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
PLATE = re.compile(r"^[АВЕКМНОРСТУХ]\d{3}[АВЕКМНОРСТУХ]{2}\d{2,3}$")
PLATE_LETTERS = str.maketrans("ABEKMHOPCTYX", "АВЕКМНОРСТУХ")


def normalized(raw):
    value = re.sub(r"[\s_-]", "", str(raw or "").strip().upper())
    if VIN.fullmatch(value):
        return value, "vin"
    plate = value.translate(PLATE_LETTERS)
    if PLATE.fullmatch(plate):
        return plate, "plate"
    raise CheckError("invalid_query", "Введите VIN из 17 символов без I/O/Q или госномер в формате А123ВС77", 422)


def prepare(lot, req):
    query, kind = normalized(req.query or lot.get("vin"))
    lot_vin = re.sub(r"[\s_-]", "", str(lot.get("vin") or "").upper())
    # If the lot has a valid VIN, it is the identifier for the check; a plate is additional context.
    if VIN.fullmatch(lot_vin) and query != lot_vin:
        raise CheckError("lot_identity", "VIN запроса должен совпадать с VIN лота; госномер укажите отдельным полем", 422)
    plate = ""
    if req.plate:
        plate, plate_kind = normalized(req.plate)
        if plate_kind != "plate":
            raise CheckError("invalid_plate", "Проверьте дополнительный госномер", 422)
    return query, kind, plate


def run(lot: dict, req: CheckRequest, config: CheckSettings):
    config.validate()
    if config.demo_only and req.mode != "demo":
        raise CheckError("demo_only", "Изолированный стенд разрешает только синтетический demo-режим", 422)
    if req.mode == "live" and not req.consent_to_external:
        raise CheckError("consent_required", "Подтвердите передачу VIN/госномера источникам проверки", 422)
    if req.mode == "live" and req.allow_paid and not config.allow_paid:
        raise CheckError("paid_disabled", "Платные источники выключены владельцем сервиса", 422)
    query, kind, plate = prepare(lot, req)
    key = hashlib.sha256(json.dumps([config.base_url, query, plate, req.mode], ensure_ascii=False).encode()).hexdigest()
    record, reuse = store.reserve(lot["id"], req.request_id, key, req.mode, config.daily_limit)
    if reuse != "new":
        return {"check": mark_identity(record, lot), "reuse": reuse}
    try:
        report, cached = fetch_report(config, {
            "query": query, "plate": plate, "mode": req.mode,
            "consent_to_external": req.consent_to_external,
            "allow_paid": req.mode == "live" and config.allow_paid and req.allow_paid,
        })
        now = store.utcnow()
        local_expiry = report.created_at+timedelta(hours=24 if report.demo else 6)
        if (kind == "vin" and report.vehicle.vin != query) or min(report.expires_at, local_expiry) <= now or report.created_at > now+timedelta(minutes=5):
            raise CheckError("report_identity", "Отчёт относится к другому VIN или имеет недопустимый срок действия", 502)
        result = store.finish(record["id"], report)
        from app.db import add_event
        try:
            add_event("vehicle_check", "Проверка истории завершена: " + req.mode + ("; кеш ШАССИ" if cached else ""), lot_id=lot["id"])
        except Exception:
            import logging
            logging.getLogger(__name__).warning("Проверка сохранена, но запись в общий журнал не выполнена")
        return {"check": mark_identity(result, lot), "reuse": "upstream_cache" if cached else "new"}
    except CheckError as error:
        store.fail(record["id"], error)
        raise
    except Exception:
        error = CheckError("check_failed", "Проверка не завершена. Автоматического повтора нет", 502)
        store.fail(record["id"], error)
        raise error from None


def mark_identity(record, lot):
    if record and record.get("report"):
        report = record["report"]
        vin = str(lot.get("vin") or "").strip().upper()
        record["identity_changed"] = bool(VIN.fullmatch(vin) and vin != report["vehicle"]["vin"])
        record["identity_warning"] = ""
        vehicle = report["vehicle"]
        if any(lot.get(k) and vehicle.get(k) and str(lot[k]).casefold() != str(vehicle[k]).casefold() for k in ("make", "model", "year")):
            record["identity_warning"] = "Марка, модель или год отчёта отличаются от карточки лота. Проверьте идентификацию автомобиля"
    return record
