"""Per-event stock (event_products) and scoped movements."""
from __future__ import annotations

import sqlite3
from typing import Dict, List, Optional

from .connection import _now_iso, get_conn
from .sku_helpers import _product_sku_label
from .stock import (
    _order_or_client_search_sql,
    _stock_movements_product_search_sql,
    ACTIVE_MOVEMENT_TYPES,
    normalize_movement_type_filter,
)

# ---------------------------------------------------------------------------
# Movimentações escopadas ao evento
# ---------------------------------------------------------------------------

def _insert_event_stock_movement_row(
    conn: sqlite3.Connection,
    *,
    event_id: int,
    product_id: int,
    movement_type: str,
    quantity: int,
    delta: int,
    balance_after: int,
    reason: Optional[str] = None,
    reference: Optional[str] = None,
    created_by: Optional[str] = None,
    unit_cost: Optional[float] = None,
    transaction_id: Optional[int] = None,
) -> Dict:
    """Insere uma linha em ``stock_movements`` com ``event_id`` sem alterar ``event_products``.

    Usado para auditoria com delta 0 (ex.: produto associado ao evento sem estoque inicial).
    """
    if movement_type not in ACTIVE_MOVEMENT_TYPES:
        raise ValueError(f"Tipo de movimentação inválido: {movement_type}")
    now = _now_iso()
    cur = conn.execute(
        """
        INSERT INTO stock_movements
            (product_id, event_id, movement_type, quantity, delta, balance_after,
             unit_cost, reason, reference, transaction_id, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(product_id),
            int(event_id),
            movement_type,
            int(quantity),
            int(delta),
            int(balance_after),
            float(unit_cost) if unit_cost is not None else None,
            reason,
            reference,
            int(transaction_id) if transaction_id is not None else None,
            created_by,
            now,
        ),
    )
    return {
        "id": cur.lastrowid,
        "event_id": int(event_id),
        "product_id": int(product_id),
        "movement_type": movement_type,
        "delta": int(delta),
        "balance_after": int(balance_after),
        "created_at": now,
    }


def _apply_event_movement(
    conn: sqlite3.Connection,
    *,
    event_id: int,
    product_id: int,
    movement_type: str,
    delta: int,
    reason: Optional[str] = None,
    created_by: Optional[str] = None,
    unit_cost: Optional[float] = None,
    reference: Optional[str] = None,
    transaction_id: Optional[int] = None,
) -> Dict:
    """Atomicamente: atualiza event_products.stock e insere stock_movements com event_id."""
    if movement_type not in ACTIVE_MOVEMENT_TYPES:
        raise ValueError(f"Tipo de movimentação inválido: {movement_type}")
    if delta == 0:
        raise ValueError("Movimentação com quantidade zero.")

    ep = conn.execute(
        """
        SELECT ep.stock, p.name
          FROM event_products ep
          JOIN products p ON p.id = ep.product_id
         WHERE ep.event_id = ? AND ep.product_id = ?
        """,
        (int(event_id), int(product_id)),
    ).fetchone()
    if ep is None:
        sku = _product_sku_label(product_id, conn=conn)
        raise ValueError(f"Produto {sku} não pertence ao evento {event_id}.")

    current = int(ep["stock"] or 0)
    new_stock = current + int(delta)
    if new_stock < 0:
        raise ValueError(
            f"Estoque insuficiente para '{ep['name']}' no evento: "
            f"disponível {current}, necessário {abs(delta)}."
        )

    now = _now_iso()
    conn.execute(
        "UPDATE event_products SET stock = ?, updated_at = ? WHERE event_id = ? AND product_id = ?",
        (new_stock, now, int(event_id), int(product_id)),
    )
    cur = conn.execute(
        """
        INSERT INTO stock_movements
            (product_id, event_id, movement_type, quantity, delta, balance_after,
             unit_cost, reason, reference, transaction_id, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(product_id),
            int(event_id),
            movement_type,
            abs(int(delta)),
            int(delta),
            new_stock,
            float(unit_cost) if unit_cost is not None else None,
            reason,
            reference,
            int(transaction_id) if transaction_id is not None else None,
            created_by,
            now,
        ),
    )
    return {
        "id": cur.lastrowid,
        "event_id": int(event_id),
        "product_id": int(product_id),
        "movement_type": movement_type,
        "delta": int(delta),
        "balance_after": new_stock,
        "created_at": now,
    }


def register_event_stock_entry(
    event_id: int,
    product_id: int,
    quantity: int,
    *,
    unit_cost: Optional[float] = None,
    reason: Optional[str] = None,
    created_by: Optional[str] = None,
) -> Dict:
    """Entrada de estoque no evento."""
    qty = int(quantity or 0)
    if qty <= 0:
        raise ValueError("Quantidade da entrada deve ser maior que zero.")
    with get_conn() as conn:
        return _apply_event_movement(
            conn, event_id=event_id, product_id=product_id,
            movement_type="entrada", delta=qty,
            unit_cost=unit_cost,
            reason=reason or None, created_by=created_by,
        )


def register_event_stock_exit(
    event_id: int,
    product_id: int,
    quantity: int,
    *,
    reason: str,
    created_by: Optional[str] = None,
) -> Dict:
    """Saída manual de estoque no evento."""
    qty = int(quantity or 0)
    if qty <= 0:
        raise ValueError("Quantidade da saída deve ser maior que zero.")
    if not (reason or "").strip():
        raise ValueError("Informe o motivo da saída.")
    with get_conn() as conn:
        return _apply_event_movement(
            conn, event_id=event_id, product_id=product_id,
            movement_type="saida", delta=-qty,
            reason=reason.strip(), created_by=created_by,
        )


def register_event_stock_adjustment(
    event_id: int,
    product_id: int,
    new_stock: int,
    *,
    reason: str,
    created_by: Optional[str] = None,
) -> Dict:
    """Corrige o estoque de um produto no evento para um valor absoluto.

    Registra **entrada** ou **saída** conforme o delta necessário.
    """
    target = int(new_stock)
    if target < 0:
        raise ValueError("O estoque final não pode ser negativo.")
    if not (reason or "").strip():
        raise ValueError("Informe o motivo da correção.")
    with get_conn() as conn:
        ep = conn.execute(
            "SELECT stock FROM event_products WHERE event_id = ? AND product_id = ?",
            (int(event_id), int(product_id)),
        ).fetchone()
        if ep is None:
            sku = _product_sku_label(product_id, conn=conn)
            raise ValueError(f"Produto {sku} não pertence ao evento {event_id}.")
        delta = target - int(ep["stock"] or 0)
        if delta == 0:
            raise ValueError("O estoque informado é igual ao atual.")
        movement_type = "entrada" if delta > 0 else "saida"
        return _apply_event_movement(
            conn, event_id=event_id, product_id=product_id,
            movement_type=movement_type, delta=delta,
            reason=reason.strip(), created_by=created_by,
        )


def list_event_stock_movements(
    event_id: int,
    *,
    product_id: Optional[int] = None,
    product_search: Optional[str] = None,
    movement_type: Optional[str] = None,
    reference: Optional[str] = None,
    seller_id: Optional[int] = None,
    limit: int = 300,
) -> List[Dict]:
    """Lista movimentações de estoque de um evento específico.

    ``product_search`` filtra por nome, descrição, SKU ou ID do produto (mesma
    semântica que em ``list_stock_movements``).

    ``reference`` filtra pelo código do pedido (``m.reference``) **ou** pelo
    nome do cliente na transação, como em ``list_stock_movements``.

    ``seller_id`` (quando > 0): apenas **vendas** do vendedor indicado
    (``movement_type = 'venda'`` e ``transactions.seller_id`` coincidente).

    """
    sql = (
        "SELECT m.*, p.name AS product_name, p.category AS product_category, "
        "p.sku AS product_sku, "
        "evt.name AS event_name, "
        "evt.badge_color AS event_badge_color, "
        "t.client_name, t.client_cpf, t.client_zipcode, t.client_address, "
        "t.client_number, t.client_complement, t.client_city, t.client_state, "
        "t.payment_method, t.card_installments, t.aut, "
        "t.client_cro_uf, t.client_cro_numero "
        "FROM stock_movements m "
        "LEFT JOIN products p ON p.id = m.product_id "
        "LEFT JOIN events evt ON evt.id = m.event_id "
        "LEFT JOIN transactions t ON t.id = m.transaction_id "
        "WHERE m.event_id = ?"
    )
    params: List = [int(event_id)]
    if product_id is not None:
        sql += " AND m.product_id = ?"
        params.append(int(product_id))
    if movement_type:
        movement_type = normalize_movement_type_filter(movement_type)
    if movement_type and movement_type in ACTIVE_MOVEMENT_TYPES:
        sql += " AND m.movement_type = ?"
        params.append(movement_type)
    frag, extra = _stock_movements_product_search_sql(product_search)
    sql += frag
    params.extend(extra)
    frag_ref, extra_ref = _order_or_client_search_sql(
        reference, order_column="m.reference"
    )
    sql += frag_ref
    params.extend(extra_ref)
    if seller_id is not None:
        sql += (
            " AND m.movement_type = 'venda' "
            "AND COALESCE(t.seller_id, -1) = ?"
        )
        params.append(int(seller_id))
    sql += " ORDER BY datetime(m.created_at) DESC, m.id DESC LIMIT ?"
    params.append(int(limit))
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]
