"""Global catalog stock and aggregated movements."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .connection import _now_iso, get_conn
from .products import _EVT_PRODUCTS_JOIN
from .sku_helpers import _product_sku_label

ACTIVE_MOVEMENT_TYPES = frozenset({"entrada", "saida", "venda"})
_LEGACY_MOVEMENT_TYPES = frozenset({"ajuste", "inicial"})
_VALID_TYPES = ACTIVE_MOVEMENT_TYPES | _LEGACY_MOVEMENT_TYPES


def normalize_movement_type_filter(movement_type: Optional[str]) -> Optional[str]:
    """Normaliza filtro de listagem; tipos legados ``ajuste``/``inicial`` foram unificados."""
    mt = (movement_type or "").strip().lower()
    if not mt or mt == "todos":
        return None
    if mt == "inicial":
        return "entrada"
    if mt == "ajuste":
        return None
    if mt in ACTIVE_MOVEMENT_TYPES:
        return mt
    return None


def _apply_movement(
    conn: sqlite3.Connection,
    *,
    product_id: int,
    movement_type: str,
    delta: int,
    reason: Optional[str] = None,
    unit_cost: Optional[float] = None,
    reference: Optional[str] = None,
    transaction_id: Optional[int] = None,
    created_by: Optional[str] = None,
) -> Dict:
    """Aplica uma movimentação e atualiza o saldo do produto.

    - ``delta`` é **sinalizado** (positivo para entrada,
      negativo para saída/venda).
    - Levanta ``ValueError`` se o saldo resultante ficaria negativo.
    - Deve ser chamado dentro de uma conexão já aberta (transação SQLite).
    """
    if movement_type not in ACTIVE_MOVEMENT_TYPES:
        raise ValueError(f"Tipo de movimentação inválido: {movement_type}")
    if delta == 0:
        raise ValueError("Movimentação com quantidade zero.")

    row = conn.execute(
        "SELECT id, name, stock FROM products WHERE id = ?", (int(product_id),)
    ).fetchone()
    if row is None:
        sku = _product_sku_label(product_id, conn=conn)
        raise ValueError(f"Produto {sku} não encontrado.")

    current = int(row["stock"] or 0)
    new_stock = current + int(delta)
    if new_stock < 0:
        raise ValueError(
            f"Estoque insuficiente para '{row['name']}': "
            f"disponível {current}, necessário {abs(delta)}."
        )

    now = _now_iso()
    conn.execute(
        "UPDATE products SET stock = ?, updated_at = ? WHERE id = ?",
        (new_stock, now, int(product_id)),
    )
    cur = conn.execute(
        """
        INSERT INTO stock_movements
            (product_id, movement_type, quantity, delta, balance_after,
             unit_cost, reason, reference, transaction_id, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(product_id),
            movement_type,
            abs(int(delta)),
            int(delta),
            new_stock,
            float(unit_cost) if unit_cost is not None else None,
            reason,
            reference,
            transaction_id,
            created_by,
            now,
        ),
    )
    return {
        "id": cur.lastrowid,
        "product_id": int(product_id),
        "movement_type": movement_type,
        "delta": int(delta),
        "balance_after": new_stock,
        "created_at": now,
    }


# ---------------------------------------------------------------------------
# Entrada, saída e correção de inventário (API de alto nível do painel admin)
# ---------------------------------------------------------------------------

def register_stock_entry(
    product_id: int,
    quantity: int,
    *,
    unit_cost: Optional[float] = None,
    reason: Optional[str] = None,
    created_by: Optional[str] = None,
) -> Dict:
    """Entrada de estoque (compra/reposição)."""
    qty = int(quantity or 0)
    if qty <= 0:
        raise ValueError("Quantidade da entrada deve ser maior que zero.")
    with get_conn() as conn:
        return _apply_movement(
            conn,
            product_id=product_id,
            movement_type="entrada",
            delta=qty,
            unit_cost=unit_cost,
            reason=reason or None,
            created_by=created_by,
        )


def register_stock_exit(
    product_id: int,
    quantity: int,
    *,
    reason: str,
    created_by: Optional[str] = None,
) -> Dict:
    """Saída manual (perda, quebra, vencimento, devolução ao fornecedor...)."""
    qty = int(quantity or 0)
    if qty <= 0:
        raise ValueError("Quantidade da saída deve ser maior que zero.")
    if not (reason or "").strip():
        raise ValueError("Informe o motivo da saída.")
    with get_conn() as conn:
        return _apply_movement(
            conn,
            product_id=product_id,
            movement_type="saida",
            delta=-qty,
            reason=reason.strip(),
            created_by=created_by,
        )


def register_stock_adjustment(
    product_id: int,
    new_stock: int,
    *,
    reason: str,
    created_by: Optional[str] = None,
) -> Dict:
    """Corrige o estoque para um valor absoluto (conferência/inventário).

    Registra **entrada** ou **saída** conforme o delta necessário.
    """
    target = int(new_stock)
    if target < 0:
        raise ValueError("O estoque final não pode ser negativo.")
    if not (reason or "").strip():
        raise ValueError("Informe o motivo da correção.")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT stock FROM products WHERE id = ?", (int(product_id),)
        ).fetchone()
        if row is None:
            sku = _product_sku_label(product_id, conn=conn)
            raise ValueError(f"Produto {sku} não encontrado.")
        delta = target - int(row["stock"] or 0)
        if delta == 0:
            raise ValueError("O estoque informado é igual ao atual.")
        movement_type = "entrada" if delta > 0 else "saida"
        return _apply_movement(
            conn,
            product_id=product_id,
            movement_type=movement_type,
            delta=delta,
            reason=reason.strip(),
            created_by=created_by,
        )


# ---------------------------------------------------------------------------
# Movimentações (leitura para o painel)
# ---------------------------------------------------------------------------

def _normalize_order_reference(value: Optional[str]) -> str:
    """Remove espaços e ``#`` inicial do código de pedido (ex.: ``#OM...``)."""
    s = (value or "").strip()
    if s.startswith("#"):
        s = s[1:].strip()
    return s


_ORDER_OR_CLIENT_COLUMNS = frozenset(
    {"m.reference", "t.order_number", "t.client_name"}
)


def _order_or_client_search_sql(
    value: Optional[str],
    *,
    order_column: str,
    client_column: str = "t.client_name",
) -> Tuple[str, List]:
    """``AND (...)`` + parâmetros: código do pedido **ou** nome do cliente (subtexto).

    Mesma semântica do filtro ``order_search`` das transações (admin/vendedor).
    ``order_column`` / ``client_column`` são identificadores internos, nunca input.
    """
    ref = _normalize_order_reference(value)
    if not ref:
        return "", []
    if (
        order_column not in _ORDER_OR_CLIENT_COLUMNS
        or client_column not in _ORDER_OR_CLIENT_COLUMNS
    ):
        raise ValueError("coluna inválida para busca de pedido/cliente")
    sql = (
        " AND ("
        f"({order_column} IS NOT NULL AND INSTR(LOWER({order_column}), LOWER(?)) > 0)"
        " OR "
        f"({client_column} IS NOT NULL AND INSTR(LOWER({client_column}), LOWER(?)) > 0)"
        ")"
    )
    return sql, [ref, ref]


def _stock_movements_product_search_sql(product_search: Optional[str]) -> Tuple[str, List]:
    """Trecho ``AND (...)`` + parâmetros: mesma busca por tokens de ``_product_catalog_like_clause``."""
    from .products import _product_catalog_like_clause

    clause, extra = _product_catalog_like_clause(product_search, alias="p")
    if not clause:
        return "", []
    return f" AND {clause}", extra


def _normalize_iso_date(value: Optional[str]) -> Optional[str]:
    """``YYYY-MM-DD`` válido ou ``None``."""
    s = (value or "").strip()
    if len(s) != 10:
        return None
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return None
    return s


def _stock_movements_filter_sql(
    *,
    product_id: Optional[int] = None,
    product_search: Optional[str] = None,
    movement_type: Optional[str] = None,
    reference: Optional[str] = None,
    seller_id: Optional[int] = None,
    event_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Tuple[str, List]:
    """Trecho ``AND ...`` + parâmetros compartilhado por list/count/max das movimentações."""
    sql = ""
    params: List = []
    if product_id is not None:
        sql += " AND m.product_id = ?"
        params.append(int(product_id))
    frag, extra = _stock_movements_product_search_sql(product_search)
    sql += frag
    params.extend(extra)
    if movement_type:
        movement_type = normalize_movement_type_filter(movement_type)
    if movement_type and movement_type in ACTIVE_MOVEMENT_TYPES:
        sql += " AND m.movement_type = ?"
        params.append(movement_type)
    frag_ref, extra_ref = _order_or_client_search_sql(
        reference, order_column="m.reference"
    )
    sql += frag_ref
    params.extend(extra_ref)
    if seller_id is not None and int(seller_id) > 0:
        sql += (
            " AND m.movement_type = 'venda' "
            "AND COALESCE(t.seller_id, -1) = ?"
        )
        params.append(int(seller_id))
    if event_id is not None and int(event_id) > 0:
        sql += " AND m.event_id = ?"
        params.append(int(event_id))
    date_from_n = _normalize_iso_date(date_from)
    date_to_n = _normalize_iso_date(date_to)
    if date_from_n:
        sql += " AND date(m.created_at) >= date(?)"
        params.append(date_from_n)
    if date_to_n:
        sql += " AND date(m.created_at) <= date(?)"
        params.append(date_to_n)
    return sql, params


def count_stock_movements(
    *,
    product_id: Optional[int] = None,
    product_search: Optional[str] = None,
    movement_type: Optional[str] = None,
    reference: Optional[str] = None,
    seller_id: Optional[int] = None,
    event_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> int:
    filt_sql, filt_params = _stock_movements_filter_sql(
        product_id=product_id,
        product_search=product_search,
        movement_type=movement_type,
        reference=reference,
        seller_id=seller_id,
        event_id=event_id,
        date_from=date_from,
        date_to=date_to,
    )
    sql = (
        "SELECT COUNT(*) AS c FROM stock_movements m "
        "LEFT JOIN products p ON p.id = m.product_id "
        "LEFT JOIN transactions t ON t.id = m.transaction_id "
        "WHERE 1=1" + filt_sql
    )
    with get_conn() as conn:
        row = conn.execute(sql, filt_params).fetchone()
    return int(row["c"] if row else 0)


def max_stock_movement_id_filtered(
    *,
    product_id: Optional[int] = None,
    product_search: Optional[str] = None,
    movement_type: Optional[str] = None,
    reference: Optional[str] = None,
    seller_id: Optional[int] = None,
    event_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> int:
    """Maior ``m.id`` entre movimentações que passam pelos mesmos filtros da listagem."""
    filt_sql, filt_params = _stock_movements_filter_sql(
        product_id=product_id,
        product_search=product_search,
        movement_type=movement_type,
        reference=reference,
        seller_id=seller_id,
        event_id=event_id,
        date_from=date_from,
        date_to=date_to,
    )
    sql = (
        "SELECT MAX(m.id) AS mx FROM stock_movements m "
        "LEFT JOIN products p ON p.id = m.product_id "
        "LEFT JOIN transactions t ON t.id = m.transaction_id "
        "WHERE 1=1" + filt_sql
    )
    with get_conn() as conn:
        row = conn.execute(sql, filt_params).fetchone()
    return int(row["mx"] if row and row["mx"] is not None else 0)


def list_stock_movements(
    *,
    product_id: Optional[int] = None,
    product_search: Optional[str] = None,
    movement_type: Optional[str] = None,
    reference: Optional[str] = None,
    seller_id: Optional[int] = None,
    event_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
) -> List[Dict]:
    """Lista movimentações. ``reference`` filtra pelo código do pedido **ou**
    nome do cliente (subtexto, case-insensitive; vendas ligadas a transação).

    ``product_search`` restringe pela mesma busca de produtos do catálogo
    (tokens no nome/variante/SKU; letras curtas só como palavra no título).

    ``seller_id`` (quando > 0): apenas linhas de **venda** (`movement_type = 'venda'`)
    cuja transação tem ``seller_id`` igual ao informado (via JOIN ``transactions``).
    Demais tipos de movimentação ficam de fora da lista enquanto o filtro estiver ativo.

    ``event_id`` (quando > 0): apenas linhas com ``stock_movements.event_id`` igual ao informado.

    ``date_from`` / ``date_to`` (``YYYY-MM-DD``): restringe pelo dia de ``created_at``.

    ``offset``: deslocamento para paginação (ordenado por data decrescente).
    """
    filt_sql, filt_params = _stock_movements_filter_sql(
        product_id=product_id,
        product_search=product_search,
        movement_type=movement_type,
        reference=reference,
        seller_id=seller_id,
        event_id=event_id,
        date_from=date_from,
        date_to=date_to,
    )
    sql = (
        "SELECT m.*, p.name AS product_name, p.category AS product_category, "
        "p.sku AS product_sku, "
        "p.variant_name AS product_variant, "
        "evt.name AS event_name, "
        "evt.badge_color AS event_badge_color, "
        "t.seller_id, t.seller_name, t.order_number, "
        "t.client_name, t.client_cpf, t.client_zipcode, t.client_address, "
        "t.client_number, t.client_complement, t.client_city, t.client_state, "
        "t.payment_method, t.card_installments, t.aut, "
        "t.client_cro_uf, t.client_cro_numero "
        "FROM stock_movements m "
        "LEFT JOIN products p ON p.id = m.product_id "
        "LEFT JOIN events evt ON evt.id = m.event_id "
        "LEFT JOIN transactions t ON t.id = m.transaction_id "
        "WHERE 1=1" + filt_sql
        + " ORDER BY datetime(m.created_at) DESC, m.id DESC LIMIT ? OFFSET ?"
    )
    params = filt_params + [int(limit), max(0, int(offset))]

    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_stock_stats() -> Dict:
    """Métricas agregadas de catálogo/estoque para o dashboard."""
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS products_count,
                COALESCE(SUM(CASE WHEN active = 1 THEN 1 ELSE 0 END), 0) AS products_active,
                COALESCE(SUM(stock), 0) AS units_in_stock,
                COALESCE(SUM(stock * price), 0) AS stock_value,
                COALESCE(SUM(CASE WHEN stock < min_stock THEN 1 ELSE 0 END), 0) AS below_min,
                COALESCE(SUM(CASE WHEN stock <= 0 THEN 1 ELSE 0 END), 0) AS out_of_stock
              FROM products
            """
        ).fetchone()
    return {
        "products_count": int(row["products_count"] or 0),
        "products_active": int(row["products_active"] or 0),
        "units_in_stock": int(row["units_in_stock"] or 0),
        "stock_value": float(row["stock_value"] or 0.0),
        "below_min": int(row["below_min"] or 0),
        "out_of_stock": int(row["out_of_stock"] or 0),
    }


def get_product_events_stock_total(product_id: int) -> int:
    """Soma das quantidades deste produto em todos os eventos."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(stock), 0) AS t FROM event_products WHERE product_id = ?",
            (int(product_id),),
        ).fetchone()
    return int(row["t"] if row else 0)


def get_products_library_stats() -> Dict:
    """Catálogo (cadastro) + inventário somado em todos os eventos (para dashboard/listagens)."""
    with get_conn() as conn:
        row_products = conn.execute(
            """
            SELECT
                COUNT(*) AS products_count,
                COALESCE(SUM(CASE WHEN active = 1 THEN 1 ELSE 0 END), 0) AS products_active
              FROM products
            """
        ).fetchone()
        row_agg = conn.execute(
            """
            SELECT
                COALESCE(SUM(ep.stock), 0) AS units_in_stock,
                COALESCE(SUM(ep.stock * pr.price), 0) AS stock_value
              FROM event_products ep
              JOIN products pr ON pr.id = ep.product_id
            """
        ).fetchone()
        row_below = conn.execute(
            f"""
            SELECT COUNT(*) AS c
                  {_EVT_PRODUCTS_JOIN}
             WHERE COALESCE(ev_agg.ev_stock_total, 0) > 0
               AND COALESCE(ev_agg.ev_stock_total, 0) < p.min_stock
            """
        ).fetchone()
        row_out = conn.execute(
            f"""
            SELECT COUNT(*) AS c
                  {_EVT_PRODUCTS_JOIN}
             WHERE COALESCE(ev_agg.ev_stock_total, 0) <= 0
            """
        ).fetchone()
    return {
        "products_count": int(row_products["products_count"] or 0),
        "products_active": int(row_products["products_active"] or 0),
        "units_in_stock": int(row_agg["units_in_stock"] or 0),
        "stock_value": float(row_agg["stock_value"] or 0.0),
        "below_min": int(row_below["c"] if row_below else 0),
        "out_of_stock": int(row_out["c"] if row_out else 0),
    }
