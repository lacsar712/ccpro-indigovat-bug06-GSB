"""工坊范围解析 —— 工坊筛选的唯一入口（前后端同一主键）。

历史问题：chip 传中文名、后端按 ``name.contains`` 撞库、角标按 name 计数，
两个同名坊（蓝靛湾一号坊 ×2）必然串坊；「全部」视图还只下发半集，
切回全部缺缸，局部刷新又是另一份半集，三处口径互不一致。

统一约定：
* 不传 / 空串        -> ``None``，即全部工坊；
* 十进制正整数字符串  -> Workshop 主键，且必须在库内存在；
* 其余（名称、模糊串、
  不存在的 id）       -> :class:`WorkshopScopeError`，由调用方降级为全部并提示。

缸位过滤一律走 ``Vat.workshop_id == <pk>`` 精确匹配，不经过名称，不下发半集。
"""

from __future__ import annotations

from typing import Optional, Union

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.models import Vat, Workshop


class WorkshopScopeError(ValueError):
    """外部 workshop token 不能解析为有效的工坊主键。"""


def parse_workshop_token(token: Union[None, str, int]) -> Optional[int]:
    """无副作用的语法解析：空 -> None；正整数 -> int；其余抛错。

    绝不做名称/模糊匹配——名称永远不是合法键。
    """
    if token is None:
        return None
    if isinstance(token, bool):
        # bool 是 int 的子类，必须先拦掉
        raise WorkshopScopeError(f"工坊编号必须是正整数，收到：{token!r}")
    if isinstance(token, int):
        wid = token
    else:
        raw = str(token).strip()
        if raw == "":
            return None
        try:
            wid = int(raw)
        except (TypeError, ValueError):
            raise WorkshopScopeError(
                f"工坊筛选须使用工坊编号（数字主键），收到的是：{raw!r}"
            )
    if wid <= 0:
        raise WorkshopScopeError(f"工坊编号必须是正整数，收到：{wid}")
    return wid


def resolve_workshop_id(db: Session, token: Union[None, str, int]) -> Optional[int]:
    """token -> 库内确实存在的工坊主键；空 token 表示全部（``None``）。

    与旧版的区别：不再按名称 / ``contains`` 撞库，同名坊不会互相顶替。
    坊不存在（已删除、链接失效）同样抛 :class:`WorkshopScopeError`，
    调用方应捕获并降级到全部视图，而不是让页面失败或清空会话。
    """
    wid = parse_workshop_token(token)
    if wid is None:
        return None
    if db.query(Workshop.id).filter(Workshop.id == wid).first() is None:
        raise WorkshopScopeError(f"工坊 {wid} 不存在或已被删除")
    return wid


def vats_for_scope(db: Session, workshop_id: Optional[int]) -> list[Vat]:
    """返回某工坊（或全部）的**完整**缸位集合。

    仅按 ``Vat.workshop_id`` 外键主键精确过滤；``workshop_id=None``
    即全部工坊的全部缸位，永不截半集。调用方传入的 id 应先经
    :func:`resolve_workshop_id` 验证。
    """
    query = db.query(Vat).options(joinedload(Vat.workshop), joinedload(Vat.lots))
    if workshop_id is not None:
        query = query.filter(Vat.workshop_id == workshop_id)
    # 先按坊再按 code：同名坊 / 同 code 的缸也不会交错混淆
    return query.order_by(Vat.workshop_id, Vat.code, Vat.id).all()


def badge_counts(db: Session) -> dict[int, int]:
    """按主键 GROUP BY 出每坊真实缸数（0 缸坊也给 0）。

    返回 ``{workshop_id: count}``——角标与过滤后的行数共用同一键、
    同一口径，差恒为 0。
    """
    per_workshop = dict(
        db.query(Vat.workshop_id, func.count(Vat.id))
        .group_by(Vat.workshop_id)
        .all()
    )
    return {
        wid: int(per_workshop.get(wid, 0))
        for (wid,) in db.query(Workshop.id).all()
    }
