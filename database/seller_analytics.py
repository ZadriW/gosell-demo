"""Análise de desempenho de um vendedor a partir das vendas pagas.

Mesma convenção do resto do painel: só ``status = 'confirmado'`` conta.

A periodicidade mede o tempo entre vendas consecutivas **do mesmo evento no
mesmo dia** — o expediente de balcão. Sem esse recorte, a noite entre dois
dias de evento (ou o mês entre dois eventos) entraria como "intervalo" e
dominaria a média.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .connection import get_conn

# Faixas do histograma de intervalos: (limite superior exclusivo em minutos, rótulo).
_INTERVAL_BUCKETS: Tuple[Tuple[Optional[float], str], ...] = (
    (5, "até 5 min"),
    (15, "5–15 min"),
    (30, "15–30 min"),
    (60, "30–60 min"),
    (120, "1–2 h"),
    (None, "mais de 2 h"),
)


def _parse_dt(value: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def _interval_summary(gaps_days: List[float]) -> Dict[str, Any]:
    buckets = [{"label": label, "count": 0} for _, label in _INTERVAL_BUCKETS]
    for gap in gaps_days:
        minutes = gap * 1440
        for i, (upper, _) in enumerate(_INTERVAL_BUCKETS):
            if upper is None or minutes < upper:
                buckets[i]["count"] += 1
                break
    if not gaps_days:
        return {
            "count": 0, "avg_days": None, "median_days": None,
            "min_days": None, "max_days": None, "buckets": buckets,
        }
    return {
        "count": len(gaps_days),
        "avg_days": round(sum(gaps_days) / len(gaps_days), 6),
        "median_days": round(_median(gaps_days), 6),
        "min_days": round(min(gaps_days), 6),
        "max_days": round(max(gaps_days), 6),
        "buckets": buckets,
    }


def list_seller_events(seller_id: int) -> List[Dict[str, Any]]:
    """Eventos em que o vendedor tem vendas (qualquer status), do mais recente ao antigo.

    Alimenta o filtro de evento da página do vendedor.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT e.id, e.name, e.active, e.operations_closed, e.badge_color,
                   COUNT(*) AS orders, MAX(t.created_at) AS last_sale_at
              FROM transactions t
              JOIN events e ON e.id = t.event_id
             WHERE t.seller_id = ?
             GROUP BY e.id
             ORDER BY last_sale_at DESC
            """,
            (int(seller_id),),
        ).fetchall()
    return [dict(r) for r in rows]


def get_seller_analytics(seller_id: int, event_id: Optional[int] = None) -> Dict[str, Any]:
    """Séries da página do vendedor (``event_id`` restringe a um evento).

    - ``by_day``          : uma linha por dia de evento {key, day, label, event_id,
                            event_name, orders, revenue, items, avg_ticket,
                            first_sale_at, last_sale_at, avg_interval_days}
    - ``intervals``       : {count, avg_days, median_days, min_days, max_days, buckets}
    - ``payment_methods`` : [{method, orders, revenue, items}] por receita
    - ``totals``          : {orders, revenue, items, days, events}
    """
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT t.created_at, t.total, t.items_count, t.event_id,
                   COALESCE(NULLIF(LOWER(TRIM(t.payment_method)), ''), 'cartao') AS method,
                   e.name AS event_name
              FROM transactions t
              LEFT JOIN events e ON e.id = t.event_id
             WHERE t.seller_id = ?
               AND LOWER(TRIM(COALESCE(t.status, ''))) = 'confirmado'{" AND t.event_id = ?" if event_id else ""}
             ORDER BY t.created_at ASC, t.id ASC
            """,
            (int(seller_id), *([int(event_id)] if event_id else [])),
        ).fetchall()

    days: Dict[Tuple[Any, str], Dict[str, Any]] = {}
    methods: Dict[str, Dict[str, Any]] = {}
    all_gaps: List[float] = []

    for r in rows:
        at = _parse_dt(r["created_at"])
        day = str(r["created_at"] or "")[:10]
        total = float(r["total"] or 0)
        items = int(r["items_count"] or 0)

        slot = days.get((r["event_id"], day))
        if slot is None:
            slot = days[(r["event_id"], day)] = {
                "event_id": r["event_id"],
                "event_name": r["event_name"] or "",
                "day": day,
                "orders": 0, "revenue": 0.0, "items": 0,
                "first": at, "last": None, "gaps": [],
            }
        elif at and slot["last"]:
            gap = (at - slot["last"]).total_seconds() / 86400.0
            slot["gaps"].append(gap)
            all_gaps.append(gap)
        slot["orders"] += 1
        slot["revenue"] += total
        slot["items"] += items
        if at:
            slot["last"] = at

        m = methods.setdefault(r["method"], {"method": r["method"], "orders": 0, "revenue": 0.0, "items": 0})
        m["orders"] += 1
        m["revenue"] += total
        m["items"] += items

    by_day = []
    for slot in sorted(days.values(), key=lambda s: (s["day"], s["first"] or datetime.min)):
        try:
            label = datetime.fromisoformat(slot["day"]).strftime("%d/%m")
        except ValueError:
            label = slot["day"]
        gaps = slot["gaps"]
        by_day.append({
            "key": f"{slot['event_id'] or 0}:{slot['day']}",
            "day": slot["day"],
            "label": label,
            "event_id": slot["event_id"],
            "event_name": slot["event_name"],
            "orders": slot["orders"],
            "revenue": round(slot["revenue"], 2),
            "items": slot["items"],
            "avg_ticket": round(slot["revenue"] / slot["orders"], 2) if slot["orders"] else 0.0,
            "first_sale_at": slot["first"].isoformat(timespec="seconds") if slot["first"] else None,
            "last_sale_at": slot["last"].isoformat(timespec="seconds") if slot["last"] else None,
            "avg_interval_days": round(sum(gaps) / len(gaps), 6) if gaps else None,
        })

    payment_methods = sorted(
        ({**m, "revenue": round(m["revenue"], 2)} for m in methods.values()),
        key=lambda m: -m["revenue"],
    )
    return {
        "by_day": by_day,
        "intervals": _interval_summary(all_gaps),
        "payment_methods": payment_methods,
        "totals": {
            "orders": len(rows),
            "revenue": round(sum(float(r["total"] or 0) for r in rows), 2),
            "items": sum(int(r["items_count"] or 0) for r in rows),
            "days": len(by_day),
            "events": len({d["event_id"] for d in by_day if d["event_id"]}),
        },
    }
