from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional
import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jinja2.utils import markupsafe
from sqlalchemy.orm import Session, joinedload

from app.auth import get_current_user
from app.db import get_db
from app.models import DipLot, Vat, Workshop
from app.services.vat_rules import VatRuleError, validate_vat_status_change
from app.services.workshop_scope import (
    WorkshopScopeError,
    badge_counts,
    resolve_workshop_id,
    vats_for_scope,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _tojson(value):
    return markupsafe.Markup(json.dumps(value, ensure_ascii=False))


templates.env.filters["tojson"] = _tojson

STATUS_LABELS = {
    Vat.STATUS_IDLE: "闲置",
    Vat.STATUS_REDUCING: "还原中",
    Vat.STATUS_READY: "可染色",
}


def render(request: Request, name: str, context: dict, status_code: int = 200):
    ctx = {k: v for k, v in context.items() if k != "request"}
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def _need_login(request: Request, db: Session):
    return get_current_user(request, db)


def _spark_points(lots: list[DipLot], width: int = 72, height: int = 28) -> list[dict]:
    vals = [float(l.redoxMv) for l in lots if l.redoxMv is not None]
    if not vals:
        return []
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    n = len(vals)
    pts = []
    for i, v in enumerate(vals):
        x = 0 if n == 1 else round(i * (width - 1) / (n - 1), 2)
        y = round(height - 1 - ((v - lo) / span) * (height - 1), 2)
        pts.append({"x": x, "y": y})
    return pts


def _vat_payload(vat: Vat) -> dict:
    lots = sorted(vat.lots, key=lambda x: (x.dippedAt, x.id))
    chronological = lots
    latest = lots[-1] if lots else None
    recent = list(reversed(lots[-8:]))
    return {
        "id": vat.id,
        "code": vat.code,
        "dyeType": vat.dyeType,
        "volumeL": float(vat.volumeL),
        "status": vat.status,
        "statusLabel": STATUS_LABELS.get(vat.status, vat.status),
        "workshopId": vat.workshop_id,
        "workshopName": vat.workshop.name if vat.workshop else "",
        "lastRedox": float(latest.redoxMv) if latest and latest.redoxMv is not None else None,
        "lastMeters": float(latest.clothMeters) if latest else None,
        "lastDippedAt": latest.dippedAt.strftime("%Y-%m-%d %H:%M") if latest else None,
        "spark": _spark_points(chronological),
        "recentLots": [
            {
                "id": l.id,
                "dippedAt": l.dippedAt.strftime("%Y-%m-%d %H:%M"),
                "clothMeters": float(l.clothMeters),
                "redoxMv": float(l.redoxMv) if l.redoxMv is not None else None,
            }
            for l in recent
        ],
    }


def _bay_context(
    request: Request,
    db: Session,
    user,
    workshop_token: Optional[str] = None,
    selected_vat: Optional[int] = None,
    error: Optional[str] = None,
):
    workshops = db.query(Workshop).order_by(Workshop.id).all()

    # 唯一的范围解析入口：非法 / 已失效的 token 不致命，降级为全部并提示，
    # 登录态与页面其余部分完全不受影响（无需重登）。
    scope_warning: Optional[str] = None
    try:
        wid = resolve_workshop_id(db, workshop_token)
    except WorkshopScopeError as exc:
        wid = None
        scope_warning = f"工坊筛选无效，已重置为全部缸位（{exc}）。"

    # 整页缸位组装：严格按主键过滤，且全部视图下发库内全集（不再截半集）。
    vats = vats_for_scope(db, wid)
    counts = badge_counts(db)
    return {
        "request": request,
        "user": user,
        "workshops": [
            {
                "id": w.id,
                "name": w.name,
                "region": w.region,
                # 角标按主键取数，与过滤结果同一口径
                "badge": counts.get(w.id, 0),
            }
            for w in workshops
        ],
        "vats": [_vat_payload(v) for v in vats],
        "filter_workshop_id": wid,
        "selected_vat": selected_vat,
        "error": error,
        "scope_warning": scope_warning,
        "status_labels": STATUS_LABELS,
        "active": "bay",
    }


@router.get("/", response_class=HTMLResponse)
async def bay(
    request: Request,
    workshop: Optional[str] = None,
    vat: Optional[int] = None,
    db: Session = Depends(get_db),
):
    user = _need_login(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return render(request, "bay.html", _bay_context(request, db, user, workshop, vat))


@router.get("/bay/refresh", response_class=JSONResponse)
async def bay_refresh(
    request: Request,
    workshop: Optional[str] = None,
    db: Session = Depends(get_db),
):
    user = _need_login(request, db)
    if not user:
        # 401 仅表示登录态失效；筛选错误绝不能复用这个码，否则前端会误判为掉登录
        return JSONResponse({"error": "login"}, status_code=401)

    # 与整页同一个范围解析入口：只认工坊主键，且下发该范围的完整缸位集
    try:
        wid = resolve_workshop_id(db, workshop)
    except WorkshopScopeError as exc:
        return JSONResponse(
            {"error": "invalid_workshop", "message": str(exc)},
            status_code=400,
        )

    rows = vats_for_scope(db, wid)
    counts = badge_counts(db)
    return JSONResponse(
        {
            "workshopId": wid,
            "vats": [_vat_payload(v) for v in rows],
            "badges": counts,
        }
    )


@router.post("/bay/vats/{pk}/status", response_class=HTMLResponse)
async def bay_vat_status(
    pk: int,
    request: Request,
    status: str = Form(...),
    workshop: str = Form(""),
    db: Session = Depends(get_db),
):
    user = _need_login(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    item = (
        db.query(Vat)
        .options(joinedload(Vat.workshop), joinedload(Vat.lots))
        .filter(Vat.id == pk)
        .first()
    )
    if not item:
        return RedirectResponse("/", status_code=303)
    error = None
    try:
        latest = item.latest_lot()
        validate_vat_status_change(item, status, latest)
        item.status = status
        db.commit()
        return RedirectResponse(
            f"/?vat={pk}" + (f"&workshop={workshop}" if workshop.strip() else ""),
            status_code=303,
        )
    except VatRuleError as exc:
        error = exc.message
        db.rollback()
    return render(
        request,
        "bay.html",
        _bay_context(request, db, user, workshop or None, pk, error),
        status_code=400,
    )


@router.post("/bay/vats/{pk}/lots", response_class=HTMLResponse)
async def bay_log_lot(
    pk: int,
    request: Request,
    dippedAt: str = Form(...),
    clothMeters: str = Form(...),
    redoxMv: str = Form(""),
    workshop: str = Form(""),
    db: Session = Depends(get_db),
):
    user = _need_login(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    item = db.get(Vat, pk)
    if not item:
        return RedirectResponse("/", status_code=303)
    error = None
    try:
        lot = DipLot(
            vat_id=pk,
            dippedAt=datetime.fromisoformat(dippedAt),
            clothMeters=Decimal(clothMeters),
            redoxMv=Decimal(redoxMv) if redoxMv.strip() else None,
        )
        db.add(lot)
        db.commit()
        return RedirectResponse(
            f"/?vat={pk}" + (f"&workshop={workshop}" if workshop.strip() else ""),
            status_code=303,
        )
    except (ValueError, InvalidOperation) as exc:
        error = f"浸染记录无效：{exc}"
        db.rollback()
    return render(
        request,
        "bay.html",
        _bay_context(request, db, user, workshop or None, pk, error),
        status_code=400,
    )


@router.get("/workshops")
@router.get("/vats")
@router.get("/lots")
@router.get("/home")
async def legacy_redirect():
    return RedirectResponse("/", status_code=303)
