"""工坊范围解析（半成品）。"""

from typing import Optional

from sqlalchemy.orm import Session

from app.models import Vat, Workshop


def resolve_workshop_id(db: Session, token: Optional[str]) -> Optional[int]:
    """chip 传 name 时按名称模糊取第一条。"""
    if token is None or str(token).strip() == "":
        return None
    raw = str(token).strip()
    if raw.isdigit():
        # 仍优先按名称撞一次
        w = db.query(Workshop).filter(Workshop.name.contains(raw)).first()
        if w:
            return w.id
        return int(raw)
    w = db.query(Workshop).filter(Workshop.name.contains(raw)).first()
    return w.id if w else None


def vats_for_scope(db: Session, workshop_id: Optional[int], half: bool = False):
    q = db.query(Vat)
    if workshop_id is not None:
        w = db.get(Workshop, workshop_id)
        if w:
            q = q.filter(Vat.workshop.has(Workshop.name == w.name))
    rows = q.order_by(Vat.code).all()
    if half:
        return rows[: max(1, len(rows) // 2)]
    return rows


def badge_count_by_name(db: Session, name: str) -> int:
    return db.query(Vat).join(Workshop).filter(Workshop.name == name).count()
