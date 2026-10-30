"""Reservas temporárias de carrinho entre vendedores do mesmo evento.

O carrinho vive no sessionStorage; o estoque só baixa na confirmação do AUT.
A reserva começa quando o vendedor entra em /pagamento e permanece enquanto o
item estiver no carrinho (catálogo ou pagamento). Só some se o item for
removido na gaveta ou na tela de pagamento — voltar ao catálogo não libera.
Quem reservou primeiro não vê o modal; os caixas seguintes veem o aviso.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple

import product_images

from .connection import _now_iso, get_conn
from .sqlutil import qmarks

# Heartbeat do front ~4s; folga para Wi-Fi instável no evento.
HOLD_TTL_SECONDS = 40
MAX_HOLD_ITEMS = 200
MAX_HOLD_QTY = 9999


def _cutoff_iso() -> str:
    return (datetime.now() - timedelta(seconds=HOLD_TTL_SECONDS)).isoformat(
        timespec="seconds"
    )


def _purge_expired_holds_conn(conn) -> None:
    conn.execute(
        "DELETE FROM checkout_holds WHERE updated_at < ?",
        (_cutoff_iso(),),
    )


def _seller_hold_name(seller_name: str) -> str:
    name = (seller_name or "Vendedor").strip()[:80]
    return name or "Vendedor"


def format_holder_names(holders: List[Dict]) -> str:
    """Junta nomes de vendedores em português (A, B e C)."""
    names: List[str] = []
    seen = set()
    for row in holders or []:
        label = _seller_hold_name(str(row.get("seller_name") or "Vendedor"))
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(label)
    if not names:
        return "Outro vendedor"
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} e {names[1]}"
    return f"{', '.join(names[:-1])} e {names[-1]}"


def normalize_hold_cart_items(raw: Any) -> List[Tuple[int, int]]:
    """Extrai ``(product_id, quantidade)`` de um payload de carrinho."""
    if not isinstance(raw, list):
        return []
    merged: Dict[int, int] = {}
    for row in raw:
        if not isinstance(row, dict):
            continue
        if row.get("bogo_auto_free"):
            continue
        try:
            pid = int(row.get("id") or row.get("product_id") or 0)
            qty = int(row.get("quantidade") or row.get("qty") or 0)
        except (TypeError, ValueError):
            continue
        if pid <= 0 or qty <= 0:
            continue
        qty = min(qty, MAX_HOLD_QTY)
        merged[pid] = min(MAX_HOLD_QTY, merged.get(pid, 0) + qty)
        if len(merged) >= MAX_HOLD_ITEMS:
            break
    return [(pid, qty) for pid, qty in merged.items()]


def _row_val(row, key, default=None):
    try:
        val = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if val is None else val


def _hold_rank(row) -> Tuple[str, int]:
    """Ordem de chegada da reserva: created_at e, em empate, id menor ganha."""
    created = str(_row_val(row, "created_at") or _row_val(row, "updated_at") or "")
    try:
        hid = int(_row_val(row, "id", 0) or 0)
    except (TypeError, ValueError):
        hid = 0
    return (created, hid)


def _replace_seller_holds_conn(
    conn,
    event_id: int,
    seller_id: int,
    seller_name: str,
    normalized: List[Tuple[int, int]],
) -> None:
    eid = int(event_id)
    sid = int(seller_id)
    name = _seller_hold_name(seller_name)
    now = _now_iso()
    existing_ids = {
        int(r["product_id"])
        for r in conn.execute(
            "SELECT product_id FROM checkout_holds WHERE event_id = ? AND seller_id = ?",
            (eid, sid),
        ).fetchall()
    }
    desired_ids = {int(pid) for pid, _qty in normalized}
    stale = [pid for pid in existing_ids if pid not in desired_ids]
    if stale:
        conn.execute(
            f"""
            DELETE FROM checkout_holds
             WHERE event_id = ? AND seller_id = ? AND product_id IN ({qmarks(len(stale))})
            """,
            (eid, sid, *stale),
        )
    for pid, qty in normalized:
        conn.execute(
            """
            INSERT INTO checkout_holds (
                event_id, seller_id, seller_name, product_id, quantity,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id, seller_id, product_id) DO UPDATE SET
                seller_name = excluded.seller_name,
                quantity = excluded.quantity,
                updated_at = excluded.updated_at
            """,
            (eid, sid, name, int(pid), int(qty), now, now),
        )


def _ensure_hold_sync_table_conn(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS checkout_hold_sync (
            event_id  INTEGER NOT NULL,
            seller_id INTEGER NOT NULL,
            sync_seq  INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (event_id, seller_id)
        )
        """
    )


def _hold_sync_seq_conn(conn, event_id: int, seller_id: int) -> int:
    _ensure_hold_sync_table_conn(conn)
    row = conn.execute(
        "SELECT sync_seq FROM checkout_hold_sync WHERE event_id = ? AND seller_id = ?",
        (int(event_id), int(seller_id)),
    ).fetchone()
    if row is None:
        return 0
    try:
        return max(0, int(row["sync_seq"] or 0))
    except (TypeError, ValueError):
        return 0


def _set_hold_sync_seq_conn(conn, event_id: int, seller_id: int, seq: int) -> None:
    _ensure_hold_sync_table_conn(conn)
    conn.execute(
        """
        INSERT INTO checkout_hold_sync (event_id, seller_id, sync_seq)
        VALUES (?, ?, ?)
        ON CONFLICT(event_id, seller_id) DO UPDATE SET
            sync_seq = excluded.sync_seq
        """,
        (int(event_id), int(seller_id), int(seq)),
    )


def _accept_hold_sync_seq_conn(conn, event_id: int, seller_id: int, seq: int) -> bool:
    """Aceita o sync se não houver versão mais nova já aplicada."""
    try:
        incoming = int(seq or 0)
    except (TypeError, ValueError):
        incoming = 0
    if incoming <= 0:
        return True
    last = _hold_sync_seq_conn(conn, event_id, seller_id)
    if incoming < last:
        return False
    _set_hold_sync_seq_conn(conn, event_id, seller_id, incoming)
    return True


def upsert_seller_checkout_holds(
    event_id: int,
    seller_id: int,
    seller_name: str,
    items: Any,
) -> None:
    """Substitui as reservas do vendedor no evento pelo carrinho atual."""
    normalized = normalize_hold_cart_items(items)
    with get_conn() as conn:
        _purge_expired_holds_conn(conn)
        _replace_seller_holds_conn(
            conn, int(event_id), int(seller_id), seller_name, normalized,
        )


def release_seller_checkout_holds(
    event_id: int,
    seller_id: int,
    *,
    seq: int = 0,
) -> bool:
    """Libera todas as reservas do vendedor neste evento.

    Retorna ``False`` se ``seq`` for mais antigo que o último sync aceito
    (requisição atrasada após uma atualização mais nova do carrinho).
    """
    with get_conn() as conn:
        if not _accept_hold_sync_seq_conn(conn, int(event_id), int(seller_id), seq):
            return False
        conn.execute(
            "DELETE FROM checkout_holds WHERE event_id = ? AND seller_id = ?",
            (int(event_id), int(seller_id)),
        )
        return True


def _conflicts_from_rows(
    mine: Dict[int, int],
    hold_rows,
    stock_rows,
    my_hold_rows=None,
) -> List[Dict]:
    my_rank_by_pid: Dict[int, Tuple[str, int]] = {}
    for row in my_hold_rows or []:
        pid = int(row["product_id"])
        my_rank_by_pid[pid] = _hold_rank(row)

    holders_by_pid: Dict[int, List[Dict]] = {}
    other_qty_by_pid: Dict[int, int] = {}
    earliest_other_rank: Dict[int, Tuple[str, int]] = {}
    for row in hold_rows:
        pid = int(row["product_id"])
        if pid not in mine:
            continue
        qty = max(0, int(row["quantity"] or 0))
        if qty <= 0:
            continue
        other_qty_by_pid[pid] = other_qty_by_pid.get(pid, 0) + qty
        rank = _hold_rank(row)
        prev = earliest_other_rank.get(pid)
        if prev is None or rank < prev:
            earliest_other_rank[pid] = rank
        holders_by_pid.setdefault(pid, []).append(
            {
                "seller_id": int(row["seller_id"]),
                "seller_name": _seller_hold_name(str(row["seller_name"] or "Vendedor")),
                "quantity": qty,
            }
        )

    stock_by_pid = {int(r["product_id"]): r for r in stock_rows}

    conflicts: List[Dict] = []
    for pid, my_qty in mine.items():
        other_qty = other_qty_by_pid.get(pid, 0)
        if other_qty <= 0:
            continue
        my_rank = my_rank_by_pid.get(pid)
        other_rank = earliest_other_rank.get(pid)
        # Quem reservou primeiro mantém a unidade; não vê o modal.
        if my_rank is not None and other_rank is not None and my_rank <= other_rank:
            continue
        info = stock_by_pid.get(pid)
        if info is None:
            continue
        stock = max(0, int(info["stock"] or 0))
        remaining = stock - other_qty
        available_after_others = max(0, remaining)
        sellable_now = min(my_qty, available_after_others)
        pending_qty = max(0, my_qty - sellable_now)
        if pending_qty <= 0:
            continue
        bl = int(info["backorder_limit"] if info["backorder_limit"] is not None else -1)
        holders = holders_by_pid.get(pid, [])
        nome = info["name"] or ""
        variante = (info["variant_name"] or "").strip()
        conflicts.append(
            {
                "product_id": pid,
                "id": pid,
                "nome": nome,
                "variante": variante,
                "sku": (info["sku"] or "").strip(),
                "imagem": product_images.resolve_image_url(pid, info["image"]),
                "estoque": stock,
                "my_qty": my_qty,
                "other_qty": other_qty,
                "available_after_others": available_after_others,
                "sellable_now": sellable_now,
                "pending_qty": pending_qty,
                "backorder_limit": bl,
                "pending_allowed": bl != 0,
                "holders": holders,
                "holders_label": format_holder_names(holders),
                "holders_count": len({int(h["seller_id"]) for h in holders}),
            }
        )
    conflicts.sort(key=lambda c: (c["nome"] or "").lower())
    return conflicts


def _list_checkout_stock_conflicts_conn(
    conn,
    event_id: int,
    viewer_seller_id: int,
    mine: Dict[int, int],
) -> List[Dict]:
    if not mine:
        return []
    eid = int(event_id)
    viewer = int(viewer_seller_id)
    pids = list(mine.keys())
    hold_rows = conn.execute(
        f"""
        SELECT id, product_id, seller_id, seller_name, quantity, created_at, updated_at
          FROM checkout_holds
         WHERE event_id = ? AND seller_id != ? AND updated_at >= ?
           AND product_id IN ({qmarks(len(pids))})
        """,
        (eid, viewer, _cutoff_iso(), *pids),
    ).fetchall()
    my_hold_rows = conn.execute(
        f"""
        SELECT id, product_id, created_at, updated_at
          FROM checkout_holds
         WHERE event_id = ? AND seller_id = ? AND updated_at >= ?
           AND product_id IN ({qmarks(len(pids))})
        """,
        (eid, viewer, _cutoff_iso(), *pids),
    ).fetchall()
    stock_rows = conn.execute(
        f"""
        SELECT ep.product_id, ep.stock, ep.backorder_limit,
               p.name, p.sku, p.variant_name, p.image
          FROM event_products ep
          JOIN products p ON p.id = ep.product_id
         WHERE ep.event_id = ? AND ep.product_id IN ({qmarks(len(pids))})
        """,
        (eid, *pids),
    ).fetchall()
    return _conflicts_from_rows(mine, hold_rows, stock_rows, my_hold_rows)


def list_checkout_stock_conflicts(
    event_id: int,
    viewer_seller_id: int,
    cart_items: Any,
) -> List[Dict]:
    """Conflitos: outras reservas de carrinho esgotariam o estoque do viewer."""
    mine = dict(normalize_hold_cart_items(cart_items))
    if not mine:
        return []
    with get_conn() as conn:
        _purge_expired_holds_conn(conn)
        return _list_checkout_stock_conflicts_conn(
            conn, int(event_id), int(viewer_seller_id), mine,
        )


def other_sellers_hold_qty_by_product_conn(
    conn,
    event_id: int,
    viewer_seller_id: int,
    product_ids: List[int],
) -> Dict[int, int]:
    """Reservas de outros caixas que chegaram *antes* deste vendedor.

    Quem reservou primeiro confirma contra o estoque real. Os caixas
    seguintes só recebem o que restar depois dessas reservas anteriores.
    """
    pids: List[int] = []
    for raw in product_ids or []:
        try:
            pid = int(raw)
        except (TypeError, ValueError):
            continue
        if pid > 0:
            pids.append(pid)
    if not pids:
        return {}
    _purge_expired_holds_conn(conn)
    eid = int(event_id)
    viewer = int(viewer_seller_id)
    cutoff = _cutoff_iso()
    my_rows = conn.execute(
        f"""
        SELECT id, product_id, created_at, updated_at
          FROM checkout_holds
         WHERE event_id = ? AND seller_id = ? AND updated_at >= ?
           AND product_id IN ({qmarks(len(pids))})
        """,
        (eid, viewer, cutoff, *pids),
    ).fetchall()
    my_rank_by_pid: Dict[int, Tuple[str, int]] = {}
    for row in my_rows:
        my_rank_by_pid[int(row["product_id"])] = _hold_rank(row)

    other_rows = conn.execute(
        f"""
        SELECT id, product_id, quantity, created_at, updated_at
          FROM checkout_holds
         WHERE event_id = ? AND seller_id != ? AND updated_at >= ?
           AND product_id IN ({qmarks(len(pids))})
        """,
        (eid, viewer, cutoff, *pids),
    ).fetchall()
    held: Dict[int, int] = {}
    for row in other_rows:
        pid = int(row["product_id"])
        qty = max(0, int(row["quantity"] or 0))
        if qty <= 0:
            continue
        my_rank = my_rank_by_pid.get(pid)
        other_rank = _hold_rank(row)
        # Sem reserva própria, trata como último da fila.
        if my_rank is not None and other_rank >= my_rank:
            continue
        held[pid] = held.get(pid, 0) + qty
    return held


def sync_seller_checkout_holds(
    event_id: int,
    seller_id: int,
    seller_name: str,
    items: Any,
    *,
    seq: int = 0,
) -> List[Dict]:
    """Publica o carrinho atual e devolve os conflitos visíveis para este caixa.

    ``seq`` crescente ignora heartbeats atrasados depois de uma remoção.
    """
    eid = int(event_id)
    sid = int(seller_id)
    normalized = normalize_hold_cart_items(items)
    mine = dict(normalized)
    with get_conn() as conn:
        _purge_expired_holds_conn(conn)
        if _accept_hold_sync_seq_conn(conn, eid, sid, seq):
            _replace_seller_holds_conn(conn, eid, sid, seller_name, normalized)
        return _list_checkout_stock_conflicts_conn(conn, eid, sid, mine)
