"""工坊范围解析：整页下发、局部刷新、角标计数三处共用的唯一入口。

全链路只认工坊主键（``workshops.id``）：

- :func:`resolve_workshop_id` 把 query/form 里的 ``workshop`` token 严格解析成
  主键，绝不做名称模糊撞库——同名工坊靠名称无法区分，模糊匹配必然串坊；
- :func:`list_scoped_vats` 直接按外键列 ``Vat.workshop_id`` 过滤，且“全部”
  视图返回全集，不再只下发半集；
- :func:`badge_counts` 一次 ``GROUP BY workshop_id`` 返回 ``{坊主键: 缸数}``，
  角标口径与列表过滤口径完全一致。
"""

from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.models import Vat, Workshop

__all__ = [
    "resolve_workshop_id",
    "list_scoped_vats",
    "badge_counts",
    "workshop_choices",
]


def resolve_workshop_id(token: Optional[str]) -> Optional[int]:
    """把 ``workshop`` token 严格解析为工坊主键。

    - None / 空串 → None，表示“全部工坊”；
    - 正整数字符串（如 ``"3"``）→ 对应 int 主键，不校验存在性，不存在时
      列表自然为空，这是合法的过滤结果；
    - 其他任何内容（工坊名称、残缺数字、脏参数）→ None，按“全部”降级，
      绝不回退到名称匹配。调用方无需抛错，页面始终可打开。
    """
    if token is None:
        return None
    raw = str(token).strip()
    if not raw or not raw.isdigit():
        return None
    workshop_id = int(raw)
    return workshop_id if workshop_id > 0 else None


def list_scoped_vats(
    db: Session,
    workshop_id: Optional[int],
    *,
    with_relations: bool = True,
) -> list[Vat]:
    """返回某工坊（或全部工坊）下的缸位全集。

    过滤条件只使用外键列 ``Vat.workshop_id``，不 join 名称，同名坊互不串缸。
    """
    query = db.query(Vat)
    if workshop_id is not None:
        query = query.filter(Vat.workshop_id == workshop_id)
    if with_relations:
        query = query.options(joinedload(Vat.workshop), joinedload(Vat.lots))
    return query.order_by(Vat.code, Vat.id).all()


def badge_counts(db: Session) -> dict[int, int]:
    """一次 GROUP BY 返回 ``{workshop_id: 缸数}``，无缸的坊缺省为 0。"""
    rows = (
        db.query(Vat.workshop_id, func.count(Vat.id))
        .group_by(Vat.workshop_id)
        .all()
    )
    return {workshop_id: count for workshop_id, count in rows}


def workshop_choices(db: Session) -> list[Workshop]:
    """按主键稳定排序的工坊列表，供整页 chip 使用。"""
    return db.query(Workshop).order_by(Workshop.id).all()
