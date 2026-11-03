import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.config import settings
from app.db import aggregate, get_lot, list_lots, list_photos, get_price_history
from app.photos.search import find_photos_for_lot
from app.tasks import (
    scrape_job, scrape_platforms, scrape_status,
    scrape_torgi_job, email_send_job, email_check_job,
)

router = APIRouter()

# ---------------------------------------------------------------------------
# Авторизация администратора (простая, для локального интерфейса)
# ---------------------------------------------------------------------------
_admin_tokens: set[str] = set()


def require_admin(
    x_admin_token: str = Header(default=""),
    token: str = Query(default=""),
):
    if (x_admin_token or token) not in _admin_tokens:
        raise HTTPException(401, "Требуется вход администратора")
    return True


class LoginRequest(BaseModel):
    password: str = ""


@router.post("/admin/login")
def admin_login(req: LoginRequest):
    if not settings.admin_password or req.password != settings.admin_password:
        raise HTTPException(401, "Неверный пароль")
    token = secrets.token_urlsafe(18)
    _admin_tokens.add(token)
    return {"ok": True, "token": token}


@router.get("/admin/check")
def admin_check(_: bool = Depends(require_admin)):
    return {"ok": True}


@router.post("/admin/logout")
def admin_logout(x_admin_token: str = Header(default="")):
    _admin_tokens.discard(x_admin_token)
    return {"ok": True}


# Analysis is opt-in and admin-only, including saved reports and offline arithmetic.
from app.analysis.schemas import AnalysisRequest, Inputs
from app.analysis.settings import AnalysisSettings
from app.analysis.provider import ProviderError
from app.analysis import service as analysis_service, store as analysis_store


def analysis_lot(lot_id):
    lot = get_lot(lot_id)
    if not lot:
        raise HTTPException(404, "Лот не найден")
    return lot


from app.vehicle_checks.models import CheckRequest
from app.vehicle_checks.client import CheckError, CheckSettings
from app.vehicle_checks import service as checks_service, store as checks_store


@router.get("/vehicle-checks/status", dependencies=[Depends(require_admin)])
def vehicle_checks_status():
    return CheckSettings.from_env().public_status()


@router.get("/lots/{lot_id}/vehicle-check", dependencies=[Depends(require_admin)])
def vehicle_check_latest(lot_id: int):
    lot = analysis_lot(lot_id)
    return {"check": checks_service.mark_identity(checks_store.latest(lot_id), lot)}


@router.post("/lots/{lot_id}/vehicle-check", dependencies=[Depends(require_admin)])
def vehicle_check_start(lot_id: int, req: CheckRequest):
    try:
        return checks_service.run(analysis_lot(lot_id), req, CheckSettings.from_env())
    except CheckError as error:
        raise HTTPException(error.status, error.public_message) from None


@router.get("/ai/status", dependencies=[Depends(require_admin)])
def ai_status():
    return AnalysisSettings.from_env().public_status()


@router.post("/lots/{lot_id}/analysis/calculate", dependencies=[Depends(require_admin)])
def analysis_calculate(lot_id: int, req: Inputs):
    try:
        return analysis_service.offline(analysis_lot(lot_id), req)
    except ValueError:
        raise HTTPException(422, "Не удалось определить автомобиль. Уточните марку, модель и год") from None


@router.post("/lots/{lot_id}/analysis", status_code=202, dependencies=[Depends(require_admin)])
def analysis_start(lot_id: int, req: AnalysisRequest):
    try:
        return analysis_service.submit(analysis_lot(lot_id), req, AnalysisSettings.from_env())
    except analysis_store.RunConflict as error:
        raise HTTPException(429 if error.code == "daily_limit" else 409, error.public_message) from None
    except ProviderError as error:
        raise HTTPException(503, error.public_message) from None
    except ValueError as error:
        raise HTTPException(422, str(error)) from None


@router.get("/lots/{lot_id}/analysis", dependencies=[Depends(require_admin)])
def analysis_history(lot_id: int):
    lot = analysis_lot(lot_id)
    data = analysis_store.history(lot_id)
    for key in ("latest", "last_success"):
        analysis_service.mark_staleness(data[key], lot)
    return data


@router.get("/lots/{lot_id}/analysis/runs/{run_id}", dependencies=[Depends(require_admin)])
def analysis_run(lot_id: int, run_id: str):
    lot = analysis_lot(lot_id)
    run = analysis_store.get_run(lot_id, run_id)
    if not run:
        raise HTTPException(404, "Отчёт не найден")
    return analysis_service.mark_staleness(run, lot)


class ScrapeRequest(BaseModel):
    date_from: str = ""
    date_to: str = ""
    max_pages: int | None = None
    find_photos: bool = True
    all_active: bool = False


class ScrapePlatformsRequest(BaseModel):
    platforms: list[str] | None = None
    find_photos: bool = True


@router.get("/stats")
def stats():
    lots, total, agg = list_lots(page_size=1, page=1)
    cats = aggregate("category", has_photo_only=False)
    with_photo_cats = aggregate("category", has_photo_only=True)
    return {
        "total_lots": total,
        "with_photo": agg.get("with_photo") or 0,
        "categories": {c["value"] or "?" : c["cnt"] for c in cats},
        "categories_with_photo": {c["value"] or "?" : c["cnt"] for c in with_photo_cats},
    }


@router.get("/events", dependencies=[Depends(require_admin)])
def events(limit: int = 200, kind: str = "", today: bool = False):
    from app.db import list_events, count_events
    return {
        "items": list_events(limit=min(limit, 1000), kind=kind, today=today),
        "today_count": count_events(today=True),
        "total_count": count_events(today=False),
    }


@router.get("/events/log", response_class=PlainTextResponse, dependencies=[Depends(require_admin)])
def events_log(tail: int = 1000):
    from app.db import read_log_tail
    return read_log_tail(tail)


@router.get("/lots")
def lots_view(
    search: str = "",
    category: str = "",
    debtor: str = "",
    region: str = "",
    sale_method: str = "",
    vin: str = "",
    has_photo: bool | None = None,
    sort: str = "published_desc",
    page: int = 1,
    page_size: int = 24,
    source: str = "",
    email_status: str = "",
    make: str = "",
    model: str = "",
    year_from: int | None = None,
    year_to: int | None = None,
    trade_kind: str = "",
    trade_type: str = "",
    status: str = "",
):
    rows, total, _ = list_lots(
        search=search, category=category, debtor=debtor, region=region,
        sale_method=sale_method, vin=vin, has_photo=has_photo, sort=sort,
        page=page, page_size=page_size, source=source, email_status=email_status,
        make=make, model=model, year_from=year_from, year_to=year_to,
        trade_kind=trade_kind, trade_type=trade_type, status=status,
    )
    return {"items": rows, "total": total, "page": page, "page_size": page_size, "pages": max(1, (total + page_size - 1) // page_size)}


@router.get("/lots/{lot_id}")
def lot_detail(lot_id: int):
    lot = get_lot(lot_id)
    if not lot:
        raise HTTPException(404, "лот не найден")
    lot["photos"] = list_photos(lot_id, status="ok")
    lot["photos_all"] = list_photos(lot_id)
    lot["price_history"] = get_price_history(lot_id)
    # Декодируем condition_info и documents из raw
    import json
    if lot.get("condition_info") and isinstance(lot.get("condition_info"), str):
        try:
            lot["condition_data"] = json.loads(lot["condition_info"])
        except json.JSONDecodeError:
            pass
    if lot.get("specs") and isinstance(lot.get("specs"), str):
        try:
            lot["specs_data"] = json.loads(lot["specs"])
        except json.JSONDecodeError:
            pass
    raw = None
    if lot.get("raw"):
        try:
            raw = json.loads(lot["raw"]) if isinstance(lot["raw"], str) else lot["raw"]
        except json.JSONDecodeError:
            raw = None
    if raw:
        lot["documents"] = raw.get("documents") or []
        lot["source_links"] = raw.get("source_links") or []
        lot["trade_type_detail"] = raw.get("trade_type") or {}
        lot["marketplace_detail"] = raw.get("marketplace") or {}
        lot["pictures"] = raw.get("pictures") or []
    return lot


@router.get("/lots/{lot_id}/photos")
def lot_photos(lot_id: int):
    return {"items": list_photos(lot_id)}


@router.post("/lots/{lot_id}/photos/search", dependencies=[Depends(require_admin)])
def search_photos(lot_id: int):
    if not get_lot(lot_id):
        raise HTTPException(404, "лот не найден")
    return find_photos_for_lot(lot_id, settings)


@router.get("/categories")
def categories():
    rows = aggregate("category", has_photo_only=False)
    return {"items": rows}


@router.get("/sale_methods")
def sale_methods():
    rows = aggregate("sale_method", has_photo_only=False)
    return {"items": rows}


@router.get("/regions")
def regions():
    rows = aggregate("region", has_photo_only=False)
    return {"items": [r for r in rows if r["value"]]}


@router.get("/makes")
def makes():
    from app.db import q
    rows = q(
        """SELECT make value, COUNT(*) c FROM lots
           WHERE make IS NOT NULL AND make != ''
           GROUP BY make ORDER BY c DESC, make"""
    )
    return {"items": [{**row, "cnt": row["c"]} for row in rows]}


@router.get("/models")
def models(make: str = ""):
    from app.db import q
    if not make:
        return {"items": []}
    rows = q(
        """SELECT model, COUNT(*) c FROM lots
           WHERE make = ? AND model IS NOT NULL AND model != ''
           GROUP BY model ORDER BY c DESC, model""",
        (make,),
    )
    return {"items": [{"value": r["model"], "cnt": r["c"]} for r in rows]}


@router.get("/years")
def years():
    from app.db import q
    rows = q(
        "SELECT year v, COUNT(*) c FROM lots WHERE year IS NOT NULL GROUP BY year ORDER BY year DESC"
    )
    return {"items": [{"value": r["v"], "cnt": r["c"]} for r in rows]}


@router.get("/trade_kinds")
def trade_kinds():
    from app.db import q, TRADE_KINDS
    items = []
    for key, label, sql in TRADE_KINDS:
        c = q(f"SELECT COUNT(*) c FROM lots WHERE {sql}")[0]["c"]
        if c:
            items.append({"value": key, "label": label, "cnt": c})
    return {"items": items}


@router.get("/trade_types")
def trade_types():
    rows = aggregate("trade_type", has_photo_only=False)
    return {"items": [r for r in rows if r["value"]]}


@router.get("/statuses")
def statuses():
    rows = aggregate("status", has_photo_only=False)
    return {"items": [r for r in rows if r["value"]]}


# ---------------------------------------------------------------------------
# Задачи
# ---------------------------------------------------------------------------
@router.post("/scrape/run", dependencies=[Depends(require_admin)])
def run_scrape(req: ScrapeRequest):
    return scrape_job(
        date_from=req.date_from,
        date_to=req.date_to,
        max_pages=req.max_pages,
        find_photos=req.find_photos,
        all_active=req.all_active,
        s=settings,
    )


@router.post("/scrape/platforms", dependencies=[Depends(require_admin)])
def run_platform_scrape(req: ScrapePlatformsRequest):
    return scrape_platforms(
        platforms=req.platforms,
        find_photos=req.find_photos,
        s=settings,
    )


@router.get("/scrape/status", dependencies=[Depends(require_admin)])
def run_status():
    return scrape_status()


# ---------------------------------------------------------------------------
# Торги-россии.рф
# ---------------------------------------------------------------------------
class ScrapeTorgiRequest(BaseModel):
    max_pages: int = 20
    fetch_details: bool = True
    find_photos: bool = True


@router.post("/scrape/torgi", dependencies=[Depends(require_admin)])
def run_torgi_scrape(req: ScrapeTorgiRequest):
    return scrape_torgi_job(
        max_pages=req.max_pages,
        fetch_details=req.fetch_details,
        find_photos=req.find_photos,
        s=settings,
    )


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
@router.post("/email/send", dependencies=[Depends(require_admin)])
def run_email_send():
    return email_send_job(s=settings)


@router.post("/email/check", dependencies=[Depends(require_admin)])
def run_email_check():
    return email_check_job(s=settings)


@router.get("/email/status", dependencies=[Depends(require_admin)])
def email_status():
    from app.db import q, ACTIVE_SALE_SQL
    from app.config import settings
    from app.emailer import get_email_service
    from app.email_worker import _count_sent_today, _lots_for_reminder, daily_cap, warmup_day

    emailer = get_email_service("primary")
    secondary = get_email_service("secondary")
    counts = {
        r["email_status"] or "NULL": r["c"]
        for r in q("SELECT email_status, COUNT(*) c FROM lots GROUP BY email_status")
    }
    no_photo_with_email = q(
        f"""SELECT COUNT(*) c FROM lots
           WHERE (email_status IS NULL OR email_status='not_sent')
           AND contact_email IS NOT NULL AND contact_email != ''
           AND NOT EXISTS (SELECT 1 FROM photos WHERE photos.lot_id=lots.id AND photos.status='ok')
           AND {ACTIVE_SALE_SQL}""",
    )[0]["c"]
    reminder = len(_lots_for_reminder(settings, settings.email_follow_up_days))
    return {
        "smtp_configured": emailer.is_configured(),
        "imap_configured": emailer.is_imap_configured(),
        "from_email": emailer.from_email,
        "secondary_enabled": settings.email2_enabled,
        "secondary_smtp_configured": secondary.is_configured(),
        "secondary_imap_configured": secondary.is_imap_configured(),
        "secondary_from": secondary.from_email,
        "auto_send": settings.email_auto_send,
        "auto_check": settings.email_auto_check,
        "send_interval_min": settings.email_send_interval_min,
        "check_interval_min": settings.email_check_interval_min,
        "daily_limit": settings.email_send_daily_limit,
        "daily_cap_today": daily_cap(settings),
        "warmup_day": warmup_day(),
        "send_delay_sec": settings.email_send_delay_sec,
        "sent_today": _count_sent_today(),
        "lots_waiting_no_photo": no_photo_with_email,
        "lots_reminder": reminder,
        "lots_not_sent": counts.get("not_sent", 0),
        "lots_sent": counts.get("sent", 0),
        "lots_responded": counts.get("responded", 0),
        "lots_bounced": counts.get("bounced", 0),
    }


@router.post("/lots/{lot_id}/email/send", dependencies=[Depends(require_admin)])
def send_email_for_lot(lot_id: int):
    lot = get_lot(lot_id)
    if not lot:
        raise HTTPException(404, "лот не найден")
    if not lot.get("contact_email"):
        raise HTTPException(400, "нет email управляющего")
    from app.emailer import get_email_service
    from app.db import set_email_status, utcnow
    emailer = get_email_service()
    if not emailer.is_configured():
        raise HTTPException(500, "Email не сконфигурирован")
    ok = emailer.send_inquiry(lot)
    if ok:
        set_email_status(lot_id, "sent", email_sent_at=utcnow())
    return {"sent": ok, "email": lot.get("contact_email")}

