"""Events, event–product links, CSV export helpers and event sellers."""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from datetime import date as _date, timedelta as _timedelta
from typing import Dict, List, Optional, Tuple

from .connection import DEFAULT_MIN_STOCK, _now_iso, get_conn
from .event_stock import _apply_event_movement
from .products import (
    _product_catalog_like_clause,
    _product_row_to_client,
    _product_search_order_clause,
    prepare_catalog_variant_groups,
)
from .sku_helpers import _default_sku_for_id
import product_images

# Classificação operacional do cadastro (não altera o fluxo de vendas).
EVENT_OPERATION_TYPES: Tuple[Tuple[str, str], ...] = (
    ("evento", "Evento"),
    ("congresso", "Congresso"),
    ("stand", "Stand"),
)
EVENT_OPERATION_TYPE_DEFAULT = "evento"
_EVENT_OPERATION_TYPE_KEYS = {key for key, _ in EVENT_OPERATION_TYPES}
_EVENT_OPERATION_TYPE_LABELS = dict(EVENT_OPERATION_TYPES)


def normalize_event_operation_type(raw: Optional[str]) -> Optional[str]:
    """Devolve o slug válido (evento|congresso|stand) ou None se inválido."""
    key = (raw or "").strip().lower()
    if key in _EVENT_OPERATION_TYPE_KEYS:
        return key
    return None


def event_operation_type_label(raw: Optional[str]) -> str:
    """Rótulo de exibição; valores ausentes ou antigos caem em Evento."""
    key = normalize_event_operation_type(raw) or EVENT_OPERATION_TYPE_DEFAULT
    return _EVENT_OPERATION_TYPE_LABELS[key]


def event_operation_noun(source=None, *, plural: bool = False) -> str:
    """Substantivo da operação (Evento/Congresso/Stand), no singular ou plural."""
    if isinstance(source, dict):
        raw = source.get("operation_type")
    else:
        raw = source
    label = event_operation_type_label(raw)
    if not plural:
        return label
    if label == "Stand":
        return "Stands"
    return f"{label}s"


def create_event(
    name: str,
    description: str = "",
    *,
    badge_color: Optional[str] = None,
    operation_type: Optional[str] = None,
) -> int:
    """Cria um novo evento e retorna o id gerado."""
    now = _now_iso()
    op_type = normalize_event_operation_type(operation_type) or EVENT_OPERATION_TYPE_DEFAULT
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO events (
                name, description, badge_color, operation_type, active, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 1, ?, ?)
            """,
            (name.strip(), (description or "").strip(), badge_color, op_type, now, now),
        )
        return int(cur.lastrowid)


def event_ops_open(event) -> bool:
    """True se o evento está ativo e com operações abertas (vendas e edições).

    Aceita o dict do evento ou um id numérico. Sem evento / id inválido → False.
    """
    if isinstance(event, (int, float)):
        event = get_event(int(event))
    elif isinstance(event, str) and event.strip().isdigit():
        event = get_event(int(event.strip()))
    if not event:
        return False
    try:
        if int(event.get("active") or 0) != 1:
            return False
    except (TypeError, ValueError, AttributeError):
        return False
    try:
        return int(event.get("operations_closed") or 0) == 0
    except (TypeError, ValueError, AttributeError):
        return True


def list_events(include_archived: bool = False) -> List[Dict]:
    """Lista eventos com contagem de produtos e de vendedores associados."""
    where = "" if include_archived else "WHERE e.active = 1"
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT
                e.id, e.name, e.description, e.badge_color, e.operation_type,
                e.active, e.operations_closed, e.operations_closed_at,
                e.created_at, e.updated_at,
                (SELECT COUNT(*) FROM event_products ep WHERE ep.event_id = e.id) AS products_count,
                (SELECT COUNT(*) FROM event_sellers es WHERE es.event_id = e.id) AS sellers_count
            FROM events e
            {where}
            ORDER BY e.active DESC, e.created_at DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def get_event(event_id: int) -> Optional[Dict]:
    """Retorna dados de um evento pelo id, ou None se não existir."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM events WHERE id = ?", (event_id,)
        ).fetchone()
        return dict(row) if row else None


def update_event(
    event_id: int,
    name: str,
    description: str = "",
    *,
    badge_color: Optional[str] = None,
    operation_type: Optional[str] = None,
) -> None:
    """Atualiza nome, descrição, tipo de operação e cor opcional do badge."""
    now = _now_iso()
    op_type = normalize_event_operation_type(operation_type) or EVENT_OPERATION_TYPE_DEFAULT
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE events
               SET name = ?, description = ?, badge_color = ?, operation_type = ?, updated_at = ?
             WHERE id = ?
            """,
            (name.strip(), (description or "").strip(), badge_color, op_type, now, event_id),
        )


def set_event_operations_closed(event_id: int, closed: bool) -> None:
    """Encerra ou reabre as operações do evento (sem arquivar)."""
    now = _now_iso()
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE events
               SET operations_closed = ?,
                   operations_closed_at = ?,
                   updated_at = ?
             WHERE id = ?
            """,
            (
                1 if closed else 0,
                now if closed else None,
                now,
                int(event_id),
            ),
        )


def update_event_goals(
    event_id: int,
    *,
    revenue_goal: Optional[float] = None,
    volume_goal: Optional[int] = None,
) -> bool:
    """Define ou limpa as metas de faturamento e volume do evento.

    ``None`` remove a meta correspondente. Valores negativos são rejeitados.
    """
    stored_revenue: Optional[float] = None
    if revenue_goal is not None:
        stored_revenue = round(float(revenue_goal), 2)
        if stored_revenue < 0:
            return False
        if stored_revenue == 0:
            stored_revenue = None
    stored_volume: Optional[int] = None
    if volume_goal is not None:
        stored_volume = int(volume_goal)
        if stored_volume < 0:
            return False
        if stored_volume == 0:
            stored_volume = None
    now = _now_iso()
    with get_conn() as conn:
        cur = conn.execute(
            """
            UPDATE events
               SET revenue_goal = ?, volume_goal = ?, updated_at = ?
             WHERE id = ?
            """,
            (stored_revenue, stored_volume, now, int(event_id)),
        )
        return cur.rowcount > 0


def _goal_metric(actual: float, goal) -> Optional[Dict]:
    """Compara realizado × meta. ``None`` se a meta não estiver definida."""
    try:
        goal_val = float(goal) if goal is not None else 0.0
    except (TypeError, ValueError):
        goal_val = 0.0
    if goal_val <= 0:
        return None
    actual_val = float(actual or 0)
    pct = (actual_val / goal_val) * 100.0 if goal_val else 0.0
    return {
        "goal": goal_val,
        "actual": actual_val,
        "remaining": max(0.0, goal_val - actual_val),
        "percent": round(pct, 1),
        "bar_percent": min(100.0, max(0.0, pct)),
        "reached": actual_val + 1e-9 >= goal_val,
    }


def get_event_goal_progress(event_id: int) -> Dict:
    """Metas do evento vs. vendas confirmadas do evento (sem filtro de período)."""
    ev = get_event(event_id) or {}
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS orders,
                   COALESCE(SUM(t.total), 0) AS revenue,
                   COALESCE(SUM(t.items_count), 0) AS items_sold
              FROM transactions t
             WHERE t.status = 'confirmado' AND t.event_id = ?
            """,
            (int(event_id),),
        ).fetchone()
    revenue = float(row["revenue"] or 0) if row else 0.0
    items_sold = int(row["items_sold"] or 0) if row else 0
    revenue_prog = _goal_metric(revenue, ev.get("revenue_goal"))
    volume_prog = _goal_metric(items_sold, ev.get("volume_goal"))
    return {
        "revenue": revenue_prog,
        "volume": volume_prog,
        "has_any": revenue_prog is not None or volume_prog is not None,
        "revenue_goal": ev.get("revenue_goal"),
        "volume_goal": ev.get("volume_goal"),
        "actual_revenue": revenue,
        "actual_volume": items_sold,
    }


def archive_event(event_id: int) -> None:
    """Arquiva (desativa) um evento. Não apaga os dados."""
    now = _now_iso()
    with get_conn() as conn:
        conn.execute(
            "UPDATE events SET active = 0, updated_at = ? WHERE id = ?",
            (now, event_id),
        )


def restore_event(event_id: int) -> None:
    """Reativa um evento arquivado."""
    now = _now_iso()
    with get_conn() as conn:
        conn.execute(
            "UPDATE events SET active = 1, updated_at = ? WHERE id = ?",
            (now, event_id),
        )


def delete_event(event_id: int) -> Dict:
    """Remove o evento e todos os dados ligados a ele (irreversível).

    Apaga transações do evento (e itens), movimentações de estoque do evento,
    produtos/promoções/vínculos com vendedores (CASCADE ao remover ``events``).
    """
    eid = int(event_id)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, name FROM events WHERE id = ?", (eid,)
        ).fetchone()
        if row is None:
            raise ValueError("Evento não encontrado.")

        summary = {
            "id": eid,
            "name": row["name"],
            "products": int(
                conn.execute(
                    "SELECT COUNT(*) FROM event_products WHERE event_id = ?", (eid,)
                ).fetchone()[0]
            ),
            "promotions": int(
                conn.execute(
                    "SELECT COUNT(*) FROM promotions WHERE event_id = ?", (eid,)
                ).fetchone()[0]
            ),
            "sellers": int(
                conn.execute(
                    "SELECT COUNT(*) FROM event_sellers WHERE event_id = ?", (eid,)
                ).fetchone()[0]
            ),
            "transactions": int(
                conn.execute(
                    "SELECT COUNT(*) FROM transactions WHERE event_id = ?", (eid,)
                ).fetchone()[0]
            ),
            "stock_movements": int(
                conn.execute(
                    "SELECT COUNT(*) FROM stock_movements WHERE event_id = ?", (eid,)
                ).fetchone()[0]
            ),
        }

        tx_ids = [
            int(r[0])
            for r in conn.execute(
                "SELECT id FROM transactions WHERE event_id = ?", (eid,)
            ).fetchall()
        ]

        if tx_ids:
            placeholders = ",".join("?" * len(tx_ids))
            conn.execute(
                f"DELETE FROM stock_movements WHERE transaction_id IN ({placeholders})",
                tx_ids,
            )

        conn.execute("DELETE FROM stock_movements WHERE event_id = ?", (eid,))
        conn.execute("DELETE FROM transactions WHERE event_id = ?", (eid,))
        conn.execute("DELETE FROM events WHERE id = ?", (eid,))

    return summary


def find_product_by_sku_or_id(q: str) -> Optional[Dict]:
    """Busca produto ativo por SKU, ``CODPROD`` do ERP, SKU alternativo ou ID local.

    O SKU e o ``CODPROD`` vêm antes do ID: as faixas de ``CODPROD`` e dos ids
    locais se sobrepõem, e quem digita um código no admin quase sempre digita o
    do ERP.
    """
    q = (q or "").strip().lstrip("#").strip()
    if not q:
        return None
    try:
        num: Optional[int] = int(q)
    except ValueError:
        num = None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM products WHERE sku = ? AND active = 1", (q,)
        ).fetchone()
        if row is None and num is not None:
            row = conn.execute(
                "SELECT * FROM products WHERE erp_codprod = ? AND active = 1", (num,)
            ).fetchone()
        if row is None:
            row = conn.execute(
                """
                SELECT p.* FROM product_sku_aliases a
                  JOIN products p ON p.id = a.product_id
                 WHERE a.sku = ? AND p.active = 1
                """,
                (q,),
            ).fetchone()
        if row is None and num is not None:
            row = conn.execute(
                "SELECT * FROM products WHERE id = ? AND active = 1", (num,)
            ).fetchone()
        return dict(row) if row else None


def add_product_to_event(
    event_id: int,
    product_id: int,
    stock: int = 0,
    min_stock: int = DEFAULT_MIN_STOCK,
    *,
    link_audit_reason: Optional[str] = None,
    link_audit_reference: Optional[str] = None,
    created_by: Optional[str] = None,
) -> None:
    """Adiciona um produto ao evento com estoque inicial.

    ``link_audit_reason`` quando informado ativa o fluxo da biblioteca geral: grava
    inclusão como movimentação tipo ``entrada`` (``stock_movements`` com ``event_id``),
    visível nas movimentações globais e do evento. Estoque inicial > 0 vira uma entrada;
    com estoque 0 apenas associa o produto ao evento (sem linha de movimentação).

    Sem ``link_audit_reason``, mantém o comportamento legado (apenas ``INSERT`` em
    ``event_products``).

    Lança ``ValueError`` se o produto já pertence ao evento ou motivo vazio no fluxo com auditoria.
    """
    now = _now_iso()
    stock_i = max(0, int(stock))
    min_i = max(0, int(min_stock))

    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM event_products WHERE event_id = ? AND product_id = ?",
            (event_id, product_id),
        ).fetchone()
        if existing:
            raise ValueError("Produto já adicionado a este evento.")

        prod_row = conn.execute(
            "SELECT active FROM products WHERE id = ?", (int(product_id),)
        ).fetchone()
        if prod_row is None:
            raise ValueError("Produto não encontrado.")
        if int(prod_row["active"] or 0) != 1:
            raise ValueError("Este produto está inativo no catálogo.")

        if link_audit_reason is None:
            conn.execute(
                """
                INSERT INTO event_products
                    (event_id, product_id, stock, min_stock, backorder_limit, created_at, updated_at)
                VALUES (?, ?, ?, ?, -1, ?, ?)
                """,
                (event_id, product_id, stock_i, min_i, now, now),
            )
            return

        note = (link_audit_reason or "").strip()
        if not note:
            raise ValueError("Informe Motivo / Ref.")
        ref_note = (link_audit_reference or "").strip() or None

        ev_ok = conn.execute(
            "SELECT 1 FROM events WHERE id = ?", (int(event_id),)
        ).fetchone()
        if ev_ok is None:
            raise ValueError("Evento não encontrado.")

        conn.execute(
            """
            INSERT INTO event_products
                (event_id, product_id, stock, min_stock, backorder_limit, created_at, updated_at)
            VALUES (?, ?, 0, ?, -1, ?, ?)
            """,
            (event_id, product_id, min_i, now, now),
        )

        if stock_i > 0:
            _apply_event_movement(
                conn,
                event_id=int(event_id),
                product_id=int(product_id),
                movement_type="entrada",
                delta=stock_i,
                reason=note,
                reference=ref_note,
                created_by=created_by,
            )


def remove_product_from_event(event_id: int, product_id: int) -> None:
    """Remove um produto do evento."""
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM event_products WHERE event_id = ? AND product_id = ?",
            (event_id, product_id),
        )


def update_event_product_stock(
    event_id: int,
    product_id: int,
    stock: int,
    min_stock: int = DEFAULT_MIN_STOCK,
) -> None:
    """Atualiza o estoque e mínimo de um produto dentro de um evento."""
    now = _now_iso()
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE event_products
               SET stock = ?, min_stock = ?, updated_at = ?
             WHERE event_id = ? AND product_id = ?
            """,
            (max(0, int(stock)), max(0, int(min_stock)), now, event_id, product_id),
        )


def update_event_product_backorder_limit(
    event_id: int,
    product_id: int,
    backorder_limit: int,
) -> None:
    """Define o limite de unidades vendáveis como entrega pendente.

    ``-1`` remove o limite (entrega pendente livre); ``0`` bloqueia qualquer
    entrega pendente; ``> 0`` é o total de unidades pendentes permitidas.
    """
    now = _now_iso()
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE event_products
               SET backorder_limit = ?, updated_at = ?
             WHERE event_id = ? AND product_id = ?
            """,
            (max(-1, int(backorder_limit)), now, event_id, product_id),
        )


def update_event_product_price(
    event_id: int,
    product_id: int,
    price: Optional[float],
) -> bool:
    """Define o preço de venda no evento. ``None`` herda o preço-base da biblioteca."""
    now = _now_iso()
    stored: Optional[float]
    if price is None:
        stored = None
    else:
        stored = round(float(price), 2)
        if stored < 0:
            return False
    with get_conn() as conn:
        cur = conn.execute(
            """
            UPDATE event_products
               SET price = ?, updated_at = ?
             WHERE event_id = ? AND product_id = ?
            """,
            (stored, now, int(event_id), int(product_id)),
        )
        return cur.rowcount > 0


def _with_resolved_product_image(row: Dict) -> Dict:
    """Prefere a cópia em ``/static/product-images`` quando o arquivo existir."""
    pid = row.get("product_id")
    if pid is None:
        pid = row.get("id")
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return row
    row["image"] = product_images.resolve_image_url(pid, row.get("image"))
    return row


def list_event_products(event_id: int) -> List[Dict]:
    """Lista produtos de um evento com dados do catálogo (JOIN com products)."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                ep.id            AS ep_id,
                ep.event_id,
                ep.product_id,
                ep.stock,
                ep.min_stock,
                ep.created_at    AS ep_created_at,
                ep.updated_at    AS ep_updated_at,
                p.name,
                p.sku,
                p.category,
                p.brand,
                p.image,
                p.price          AS library_price,
                ep.price         AS event_price,
                COALESCE(ep.price, p.price) AS price,
                p.active         AS product_active
            FROM event_products ep
            JOIN products p ON p.id = ep.product_id
            WHERE ep.event_id = ?
            ORDER BY p.name COLLATE NOCASE
            """,
            (event_id,),
        ).fetchall()
        return [_with_resolved_product_image(dict(r)) for r in rows]


_EVENT_PRODUCTS_ADMIN_FROM = """
FROM event_products ep
JOIN products p ON p.id = ep.product_id
WHERE ep.event_id = ?
"""


def _event_products_admin_filter_clause(
    q: Optional[str],
    categoria: str,
    status: str,
    entrega: str = "todos",
) -> Tuple[str, List]:
    """Cláusula AND … para filtros da grade de estoque do evento (admin)."""
    parts: List[str] = []
    params: List = []
    search_sql, search_params = _product_catalog_like_clause(q, include_sku_aliases=True)
    if search_sql:
        parts.append(search_sql)
        params.extend(search_params)
    cat = (categoria or "todos").strip().lower()
    if cat != "todos":
        parts.append("LOWER(p.category) = LOWER(?)")
        params.append(categoria)
    st = (status or "todos").strip().lower()
    if st == "ok":
        parts.append(
            "p.active = 1 AND ep.stock > 0 AND "
            "(ep.min_stock <= 0 OR ep.stock >= ep.min_stock)"
        )
    elif st == "baixo":
        parts.append(
            "p.active = 1 AND ep.min_stock > 0 AND ep.stock > 0 AND ep.stock < ep.min_stock"
        )
    elif st == "sem_estoque":
        parts.append("p.active = 1 AND ep.stock <= 0")
    elif st == "inativo":
        parts.append("p.active = 0")
    else:
        parts.append("p.active = 1")
    ent = (entrega or "todos").strip().lower()
    if ent == "pendente":
        parts.append(
            """EXISTS (
                SELECT 1
                  FROM transaction_items ti
                  JOIN transactions t ON t.id = ti.transaction_id
                 WHERE t.event_id = ep.event_id
                   AND LOWER(TRIM(COALESCE(t.status, ''))) = 'confirmado'
                   AND CAST(ti.product_id AS INTEGER) = ep.product_id
                   AND (ti.quantity - COALESCE(ti.quantity_delivered, 0)) > 0
            )"""
        )
    extra = f" AND {' AND '.join(parts)}" if parts else ""
    return extra, params


def count_event_products_filtered(
    event_id: int,
    q: Optional[str],
    categoria: str = "todos",
    status: str = "todos",
    entrega: str = "todos",
) -> int:
    """Quantidade de vínculos evento–produto após filtros (lista admin)."""
    extra, params = _event_products_admin_filter_clause(q, categoria, status, entrega)
    sql = f"SELECT COUNT(*) AS c {_EVENT_PRODUCTS_ADMIN_FROM}{extra}"
    with get_conn() as conn:
        row = conn.execute(sql, (event_id, *params)).fetchone()
    return int(row["c"] if row else 0)


#: Colunas ordenáveis da grade de estoque do evento (seta ▲/▼ no cabeçalho).
_EVENT_STOCK_SORT_COLUMNS = {
    "preco": "COALESCE(ep.price, p.price)",
    "estoque": "ep.stock",
    "minimo": "ep.min_stock",
}

#: "vendas" soma pelo mesmo critério de ``units_sold_by_product_for_event``
#: (pedidos confirmados), via subconsulta correlacionada — necessário para
#: ordenar e paginar no SQL, já que a métrica não é uma coluna de ``event_products``.
#: Com ``sort_seller_id``, restringe à mesma seleção do vendedor (vendas dele, não do evento todo).
_EVENT_STOCK_VENDAS_SORT_SQL = """(
        SELECT COALESCE(SUM(ti.quantity), 0)
          FROM transaction_items ti
          JOIN transactions t ON t.id = ti.transaction_id
         WHERE t.event_id = ep.event_id
           AND LOWER(TRIM(COALESCE(t.status, ''))) = 'confirmado'
           AND CAST(ti.product_id AS INTEGER) = ep.product_id
           {seller_filter}
    )"""


def list_event_products_slice(
    event_id: int,
    q: Optional[str],
    categoria: str = "todos",
    status: str = "todos",
    *,
    limit: int,
    offset: int,
    entrega: str = "todos",
    sort: Optional[str] = None,
    direction: str = "desc",
    sort_seller_id: Optional[int] = None,
) -> List[Dict]:
    """Página da grade de estoque do evento com os mesmos filtros da biblioteca geral.

    ``sort`` escolhe a coluna (chave de ``_EVENT_STOCK_SORT_COLUMNS``, ou
    ``"vendas"``); sem correspondência, mantém a ordenação por relevância de
    busca/nome. ``sort_seller_id`` restringe a métrica de vendas usada na
    ordenação às vendas desse vendedor (painel do vendedor).
    """
    extra, params = _event_products_admin_filter_clause(q, categoria, status, entrega)
    sort_key = (sort or "").strip().lower()
    order_params: List = []
    if sort_key == "vendas":
        if sort_seller_id is not None:
            seller_filter = "AND t.seller_id = ?"
            order_params.append(int(sort_seller_id))
        else:
            seller_filter = ""
        sort_col = _EVENT_STOCK_VENDAS_SORT_SQL.format(seller_filter=seller_filter)
    else:
        sort_col = _EVENT_STOCK_SORT_COLUMNS.get(sort_key)

    if sort_col:
        dir_sql = "ASC" if (direction or "").strip().lower() == "asc" else "DESC"
        order_sql = f"{sort_col} {dir_sql}, p.name COLLATE NOCASE ASC"
    else:
        order_sql, order_params = _product_search_order_clause(
            q, alias="p", fallback="p.name COLLATE NOCASE"
        )
    sql = f"""
            SELECT
                ep.id            AS ep_id,
                ep.event_id,
                ep.product_id,
                ep.stock,
                ep.min_stock,
                ep.created_at    AS ep_created_at,
                ep.updated_at    AS ep_updated_at,
                p.name,
                p.sku,
                p.category,
                p.description,
                p.image,
                p.price          AS library_price,
                ep.price         AS event_price,
                COALESCE(ep.price, p.price) AS price,
                p.active         AS product_active,
                p.variant_name,
                p.subtitle
            {_EVENT_PRODUCTS_ADMIN_FROM}
            {extra}
            ORDER BY {order_sql}
            LIMIT ? OFFSET ?
            """
    with get_conn() as conn:
        rows = conn.execute(
            sql,
            (event_id, *params, *order_params, limit, offset),
        ).fetchall()
        return [_with_resolved_product_image(dict(r)) for r in rows]


def _event_products_slice_row_to_client(row: Dict) -> Dict:
    """Converte linha de ``list_event_products_slice`` para o formato do catálogo/vendedor."""
    pid = int(row["product_id"])
    sku_val = row.get("sku")
    sku = (sku_val or "").strip() if sku_val is not None else ""
    if not sku:
        sku = _default_sku_for_id(pid)
    estoque = int(row["stock"] or 0)
    estoque_minimo = int(row["min_stock"] or 0)
    return {
        "id": pid,
        "sku": sku,
        "nome": row["name"],
        "variante": (row.get("variant_name") or "").strip(),
        "categoria": row["category"],
        "descricao": (row.get("description") or ""),
        "preco": float(row["price"] or 0),
        "preco_biblioteca": float(row.get("library_price") or row["price"] or 0),
        "imagem": product_images.resolve_image_url(pid, row["image"]),
        "estoque": estoque,
        "estoque_minimo": estoque_minimo,
        "ativo": bool(row["product_active"]),
        "abaixo_minimo": estoque_minimo > 0 and estoque < estoque_minimo,
        "sem_estoque": estoque <= 0,
    }


def list_event_products_filtered_for_client(
    event_id: int,
    q: Optional[str],
    status: str,
    *,
    limit: int,
    offset: int,
    sort: Optional[str] = None,
    direction: str = "desc",
    sort_seller_id: Optional[int] = None,
) -> List[Dict]:
    """Página de produtos do evento no formato cliente (painel vendedor)."""
    rows = list_event_products_slice(
        event_id,
        q,
        "todos",
        status,
        limit=int(limit),
        offset=int(offset),
        sort=sort,
        direction=direction,
        sort_seller_id=sort_seller_id,
    )
    return [_event_products_slice_row_to_client(r) for r in rows]


def get_event_stats(event_id: int) -> Dict:
    """Retorna estatísticas de um evento: produtos, unidades e abaixo do mínimo."""
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*)                                                                    AS products_count,
                COALESCE(SUM(ep.stock), 0)                                                 AS units_in_stock,
                COALESCE(SUM(CASE WHEN ep.stock = 0 THEN 1 ELSE 0 END), 0)                AS sem_estoque,
                COALESCE(SUM(CASE WHEN ep.stock > 0 AND ep.stock < ep.min_stock THEN 1 ELSE 0 END), 0) AS below_min
            FROM event_products ep
            WHERE ep.event_id = ?
            """,
            (event_id,),
        ).fetchone()
        return dict(row) if row else {"products_count": 0, "units_in_stock": 0, "sem_estoque": 0, "below_min": 0}


def get_event_stock_stats(event_id: int) -> Dict:
    """Estatísticas expandidas de estoque do evento, incluindo valor monetário."""
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(ep.id)                                                                           AS products_count,
                COALESCE(SUM(ep.stock), 0)                                                             AS units_in_stock,
                COALESCE(SUM(ep.stock * COALESCE(ep.price, p.price)), 0)                               AS stock_value,
                COALESCE(SUM(CASE WHEN ep.stock = 0 THEN 1 ELSE 0 END), 0)                            AS sem_estoque,
                COALESCE(SUM(CASE WHEN ep.stock > 0 AND ep.stock < ep.min_stock THEN 1 ELSE 0 END), 0) AS below_min
            FROM event_products ep
            JOIN products p ON p.id = ep.product_id
            WHERE ep.event_id = ?
            """,
            (event_id,),
        ).fetchone()
        return dict(row) if row else {
            "products_count": 0, "units_in_stock": 0, "stock_value": 0.0,
            "sem_estoque": 0, "below_min": 0,
        }


def get_event_sales_dashboard(
    event_id: int,
    *,
    sales_days_limit: int = 120,
    sales_days_page: int = 1,
    sales_days_per_page: int = 10,
) -> Dict:
    """Vendas do evento: pedidos confirmados com movimento ``venda`` neste ``event_id``.

    Retorna receita total, ticket médio, dias distintos com vendas paginados
    (``sales_days_per_page`` por página, mais recentes primeiro, até ``sales_days_limit`` dias)
    e os 5 produtos mais vendidos por quantidade de unidades.
    """
    eid = int(event_id)
    lim_days = max(1, min(int(sales_days_limit), 366))
    per_page = max(1, min(int(sales_days_per_page), 50))
    page = max(1, int(sales_days_page))
    tx_filter = (
        "FROM transactions t "
        "WHERE t.status = 'confirmado' "
        "AND EXISTS ("
        " SELECT 1 FROM stock_movements m "
        " WHERE m.transaction_id = t.id AND m.movement_type = 'venda' "
        " AND m.event_id = ?)"
    )

    with get_conn() as conn:
        agg = conn.execute(
            f"SELECT COUNT(*) AS orders_count, COALESCE(SUM(t.total), 0) AS revenue_total {tx_filter}",
            (eid,),
        ).fetchone()
        orders_count = int(agg["orders_count"] or 0)
        revenue_total = float(agg["revenue_total"] or 0.0)
        avg_ticket = (revenue_total / orders_count) if orders_count else 0.0

        count_row = conn.execute(
            f"""
            SELECT COUNT(*) AS c FROM (
                SELECT date(t.created_at) AS day
                {tx_filter}
                GROUP BY date(t.created_at)
                LIMIT ?
            )
            """,
            (eid, lim_days),
        ).fetchone()
        days_total = int(count_row["c"] or 0)
        total_pages = max(1, (days_total + per_page - 1) // per_page) if days_total > 0 else 1
        page = min(page, total_pages)
        offset = (page - 1) * per_page

        day_rows = conn.execute(
            f"""
            SELECT date(t.created_at) AS day, COUNT(*) AS orders_count
            {tx_filter}
            GROUP BY date(t.created_at)
            ORDER BY day DESC
            LIMIT ? OFFSET ?
            """,
            (eid, per_page, offset),
        ).fetchall()
        sales_by_day = [
            {"day": str(r["day"]), "orders_count": int(r["orders_count"] or 0)}
            for r in day_rows
        ]

        showing_from = offset + 1 if days_total > 0 else 0
        showing_to = min(offset + len(sales_by_day), days_total) if days_total > 0 else 0
        sales_by_day_pagination = {
            "page": page,
            "per_page": per_page,
            "total": days_total,
            "total_pages": total_pages,
            "has_prev": page > 1,
            "has_next": page < total_pages,
            "showing_from": showing_from,
            "showing_to": showing_to,
        }

        top_rows = conn.execute(
            """
            SELECT
                ti.product_id AS product_id_raw,
                MAX(ti.product_name) AS product_name,
                SUM(ti.quantity) AS units_sold,
                SUM(ti.subtotal) AS revenue_subtotal,
                COALESCE(
                    MAX(NULLIF(TRIM(ti.product_sku), '')),
                    MAX(p.sku),
                    ''
                ) AS sku_display
            FROM transaction_items ti
            JOIN transactions t ON t.id = ti.transaction_id
            LEFT JOIN products p ON p.id = CAST(ti.product_id AS INTEGER)
            WHERE t.status = 'confirmado'
              AND EXISTS (
                SELECT 1 FROM stock_movements m
                WHERE m.transaction_id = t.id AND m.movement_type = 'venda'
                  AND m.event_id = ?
              )
              AND ti.gift_item_id IS NULL
            GROUP BY ti.product_id
            ORDER BY units_sold DESC
            LIMIT 5
            """,
            (eid,),
        ).fetchall()

    top_products: List[Dict] = []
    for r in top_rows:
        raw_pid = r["product_id_raw"]
        pid_int: Optional[int] = None
        if raw_pid is not None and str(raw_pid).strip().isdigit():
            pid_int = int(str(raw_pid).strip())
        top_products.append(
            {
                "product_id": pid_int,
                "sku": (r["sku_display"] or "").strip() or "—",
                "product_name": (r["product_name"] or "").strip() or "—",
                "units_sold": int(r["units_sold"] or 0),
                "revenue": float(r["revenue_subtotal"] or 0.0),
            }
        )

    return {
        "orders_count": orders_count,
        "revenue_total": revenue_total,
        "avg_ticket": avg_ticket,
        "sales_by_day": sales_by_day,
        "sales_by_day_pagination": sales_by_day_pagination,
        "top_products": top_products,
        "sales_days_limit": lim_days,
        "goals": get_event_goal_progress(eid),
    }
# Limites das séries entregues aos gráficos do dashboard (d3).
ANALYTICS_MAX_DAYS = 180
ANALYTICS_TOP_PRODUCTS = 8
ANALYTICS_TOP_SELLERS = 8


def get_event_sales_analytics(
    event_id: int,
    *,
    max_days: int = ANALYTICS_MAX_DAYS,
    top_products: int = ANALYTICS_TOP_PRODUCTS,
    top_sellers: int = ANALYTICS_TOP_SELLERS,
) -> Dict:
    """Séries prontas para os gráficos d3 do dashboard do evento.

    Usa exatamente o mesmo recorte de :func:`get_event_sales_dashboard` — pedidos
    ``confirmado`` com movimentação ``venda`` neste ``event_id`` — para que os
    gráficos fechem com os KPIs exibidos logo acima deles.

    Retorna um dict serializável em JSON:

    - ``by_day``          : série diária contínua (dias sem venda entram zerados)
                            {day, label, orders, revenue, items, avg_ticket,
                            cumulative_revenue}
    - ``by_hour``         : 24 posições fixas {hour, label, orders, revenue}
    - ``top_products``    : {sku, product_name, product_id, units_sold, revenue}
    - ``payment_methods`` : {method, orders, revenue}
    - ``sellers``         : {name, orders, revenue}
    - ``totals``          : {orders, revenue, items, avg_ticket}
    - ``goals``           : {revenue_goal, volume_goal} do cadastro do evento
    - ``truncated_days``  : True quando a série diária foi cortada em ``max_days``
    """
    eid = int(event_id)
    lim_days = max(1, min(int(max_days), 366))
    lim_products = max(1, min(int(top_products), 25))
    lim_sellers = max(1, min(int(top_sellers), 25))

    tx_filter = (
        "FROM transactions t "
        "WHERE t.status = 'confirmado' "
        "AND EXISTS ("
        " SELECT 1 FROM stock_movements m "
        " WHERE m.transaction_id = t.id AND m.movement_type = 'venda' "
        " AND m.event_id = ?)"
    )

    with get_conn() as conn:
        totals_row = conn.execute(
            f"SELECT COUNT(*) AS orders, COALESCE(SUM(t.total), 0) AS revenue, "
            f"COALESCE(SUM(t.items_count), 0) AS items {tx_filter}",
            (eid,),
        ).fetchone()

        day_rows = conn.execute(
            f"""
            SELECT date(t.created_at) AS day,
                   COUNT(*) AS orders,
                   COALESCE(SUM(t.total), 0) AS revenue,
                   COALESCE(SUM(t.items_count), 0) AS items
            {tx_filter}
            GROUP BY date(t.created_at)
            ORDER BY day DESC
            LIMIT ?
            """,
            (eid, lim_days + 1),
        ).fetchall()

        hour_rows = conn.execute(
            f"""
            SELECT CAST(strftime('%H', t.created_at) AS INTEGER) AS hour,
                   COUNT(*) AS orders,
                   COALESCE(SUM(t.total), 0) AS revenue
            {tx_filter}
            GROUP BY hour
            """,
            (eid,),
        ).fetchall()

        pay_rows = conn.execute(
            f"""
            SELECT COALESCE(NULLIF(TRIM(t.payment_method), ''), '') AS method,
                   COUNT(*) AS orders,
                   COALESCE(SUM(t.total), 0) AS revenue
            {tx_filter}
            GROUP BY method
            ORDER BY revenue DESC
            """,
            (eid,),
        ).fetchall()

        seller_rows = conn.execute(
            f"""
            SELECT COALESCE(NULLIF(TRIM(t.seller_name), ''), '(sem vendedor)') AS name,
                   COUNT(*) AS orders,
                   COALESCE(SUM(t.total), 0) AS revenue
            {tx_filter}
            GROUP BY name
            ORDER BY revenue DESC
            LIMIT ?
            """,
            (eid, lim_sellers),
        ).fetchall()

        product_rows = conn.execute(
            """
            SELECT ti.product_id AS product_id_raw,
                   MAX(ti.product_name) AS product_name,
                   SUM(ti.quantity) AS units_sold,
                   SUM(ti.subtotal) AS revenue,
                   COALESCE(MAX(NULLIF(TRIM(ti.product_sku), '')), MAX(p.sku), '') AS sku_display
            FROM transaction_items ti
            JOIN transactions t ON t.id = ti.transaction_id
            LEFT JOIN products p ON p.id = CAST(ti.product_id AS INTEGER)
            WHERE t.status = 'confirmado'
              AND EXISTS (
                SELECT 1 FROM stock_movements m
                WHERE m.transaction_id = t.id AND m.movement_type = 'venda'
                  AND m.event_id = ?
              )
              AND ti.gift_item_id IS NULL
            GROUP BY ti.product_id
            ORDER BY units_sold DESC
            LIMIT ?
            """,
            (eid, lim_products),
        ).fetchall()

        goal_row = conn.execute(
            "SELECT revenue_goal, volume_goal FROM events WHERE id = ?", (eid,)
        ).fetchone()

    orders_total = int(totals_row["orders"] or 0) if totals_row else 0
    revenue_total = float(totals_row["revenue"] or 0.0) if totals_row else 0.0
    items_total = int(totals_row["items"] or 0) if totals_row else 0

    # ---------- série diária contínua (lacunas viram zero) -------------------
    truncated_days = len(day_rows) > lim_days
    day_rows = list(reversed(day_rows[:lim_days]))  # ordem crescente, já limitada
    by_day: List[Dict] = []
    by_date = {}
    for r in day_rows:
        try:
            by_date[_date.fromisoformat(str(r["day"])[:10])] = r
        except ValueError:
            continue
    if by_date:
        cursor = min(by_date)
        last = max(by_date)
        running = 0.0
        # Série com lacunas mente sobre o ritmo de vendas: dias sem pedido
        # entram zerados, até o teto de pontos.
        while cursor <= last and len(by_day) < lim_days:
            row = by_date.get(cursor)
            orders = int(row["orders"] or 0) if row else 0
            revenue = float(row["revenue"] or 0.0) if row else 0.0
            items = int(row["items"] or 0) if row else 0
            running += revenue
            by_day.append(
                {
                    "day": cursor.isoformat(),
                    "label": cursor.strftime("%d/%m"),
                    "orders": orders,
                    "revenue": revenue,
                    "items": items,
                    "avg_ticket": (revenue / orders) if orders else 0.0,
                    "cumulative_revenue": running,
                }
            )
            cursor += _timedelta(days=1)

    # ---------- distribuição por hora (24 posições fixas) --------------------
    hour_map = {int(r["hour"]): r for r in hour_rows if r["hour"] is not None}
    by_hour = [
        {
            "hour": h,
            "label": f"{h:02d}h",
            "orders": int(hour_map[h]["orders"] or 0) if h in hour_map else 0,
            "revenue": float(hour_map[h]["revenue"] or 0.0) if h in hour_map else 0.0,
        }
        for h in range(24)
    ]

    products: List[Dict] = []
    for r in product_rows:
        raw_pid = r["product_id_raw"]
        pid_int: Optional[int] = None
        if raw_pid is not None and str(raw_pid).strip().isdigit():
            pid_int = int(str(raw_pid).strip())
        products.append(
            {
                "product_id": pid_int,
                "sku": (r["sku_display"] or "").strip() or "—",
                "product_name": (r["product_name"] or "").strip() or "—",
                "units_sold": int(r["units_sold"] or 0),
                "revenue": float(r["revenue"] or 0.0),
            }
        )

    return {
        "totals": {
            "orders": orders_total,
            "revenue": revenue_total,
            "items": items_total,
            "avg_ticket": (revenue_total / orders_total) if orders_total else 0.0,
        },
        "by_day": by_day,
        "by_hour": by_hour,
        "top_products": products,
        "payment_methods": [
            {
                "method": (r["method"] or "").strip(),
                "orders": int(r["orders"] or 0),
                "revenue": float(r["revenue"] or 0.0),
            }
            for r in pay_rows
        ],
        "sellers": [
            {
                "name": r["name"],
                "orders": int(r["orders"] or 0),
                "revenue": float(r["revenue"] or 0.0),
            }
            for r in seller_rows
        ],
        "goals": {
            "revenue_goal": (
                float(goal_row["revenue_goal"])
                if goal_row and goal_row["revenue_goal"]
                else None
            ),
            "volume_goal": (
                int(goal_row["volume_goal"])
                if goal_row and goal_row["volume_goal"]
                else None
            ),
        },
        "max_days": lim_days,
        "truncated_days": truncated_days,
    }


# Limites para exportações CSV (painel admin).
EXPORT_MOVEMENTS_CSV_CAP = 100_000
EXPORT_SALES_SUMMARY_CSV_CAP = 50_000
EXPORT_SALES_ITEMS_CSV_CAP = 200_000
EXPORT_STOCK_CSV_CAP = 50_000


def list_transactions_summary_for_event_period(
    event_id: int,
    *,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    seller_id: Optional[int] = None,
    limit: int = EXPORT_SALES_SUMMARY_CSV_CAP,
) -> List[Dict]:
    """Pedidos com venda registrada em ``stock_movements`` para o evento (``movement_type='venda'``).

    ``date_from`` / ``date_to``: ``YYYY-MM-DD``, comparados com ``date(transactions.created_at)`` (inclusive).
    ``seller_id``: restringe a um vendedor; ``None`` exporta todos.
    """
    cap = max(1, min(int(limit), EXPORT_SALES_SUMMARY_CSV_CAP))
    sql = (
        "SELECT t.id, t.order_number, t.created_at, t.total, t.items_count, t.status, "
        "t.client_name, t.client_cpf, t.client_email, t.client_phone, t.client_zipcode, t.client_address, "
        "t.client_number, t.client_complement, t.client_neighborhood, t.client_city, t.client_state, "
        "t.seller_id, t.seller_name, t.payment_method, t.card_installments, t.aut, "
        "t.client_cro_uf, t.client_cro_numero "
        "FROM transactions t "
        "WHERE EXISTS ("
        " SELECT 1 FROM stock_movements m "
        " WHERE m.transaction_id = t.id AND m.movement_type = 'venda' "
        " AND m.event_id = ?)"
        " AND t.status = 'confirmado'"
    )
    params: List = [int(event_id)]
    if seller_id is not None:
        sql += " AND t.seller_id = ?"
        params.append(int(seller_id))
    if date_from:
        sql += " AND date(t.created_at) >= date(?)"
        params.append(date_from)
    if date_to:
        sql += " AND date(t.created_at) <= date(?)"
        params.append(date_to)
    sql += " ORDER BY datetime(t.created_at) DESC, t.id DESC LIMIT ?"
    params.append(cap)
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def list_transaction_items_for_event_period(
    event_id: int,
    *,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    seller_id: Optional[int] = None,
    limit: int = EXPORT_SALES_ITEMS_CSV_CAP,
) -> List[Dict]:
    """Itens de pedidos cuja venda está ligada ao evento (via ``stock_movements``).

    Inclui dados do pedido/cliente para a exportação unificada (``nivel=completo``).
    """
    cap = max(1, min(int(limit), EXPORT_SALES_ITEMS_CSV_CAP))
    sql = (
        "SELECT ti.id AS item_id, t.id AS transaction_id, t.order_number, t.created_at, "
        "t.status, t.total, t.items_count, "
        "t.client_name, t.client_cpf, t.client_email, t.client_phone, t.client_zipcode, t.client_address, "
        "t.client_number, t.client_complement, t.client_city, t.client_state, "
        "t.client_cro_uf, t.client_cro_numero, "
        "t.seller_id, t.seller_name, t.payment_method, t.card_installments, t.aut, "
        "ti.product_id, ti.product_name, ti.category, ti.product_sku, "
        "ti.quantity, ti.unit_price, ti.subtotal "
        "FROM transaction_items ti "
        "JOIN transactions t ON t.id = ti.transaction_id "
        "WHERE EXISTS ("
        " SELECT 1 FROM stock_movements m "
        " WHERE m.transaction_id = t.id AND m.movement_type = 'venda' "
        " AND m.event_id = ?)"
        " AND t.status = 'confirmado'"
    )
    params: List = [int(event_id)]
    if seller_id is not None:
        sql += " AND t.seller_id = ?"
        params.append(int(seller_id))
    if date_from:
        sql += " AND date(t.created_at) >= date(?)"
        params.append(date_from)
    if date_to:
        sql += " AND date(t.created_at) <= date(?)"
        params.append(date_to)
    sql += " ORDER BY datetime(t.created_at) DESC, ti.id ASC LIMIT ?"
    params.append(cap)
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]

# ---------------------------------------------------------------------------
# Relatório Financeiro do Evento
# ---------------------------------------------------------------------------

#: A marca vem do campo ``MARCA`` do ERP (``products.brand``). Produtos ainda
#: não sincronizados caem no fim do nome, herdado da Wake ("Resina X - FGM").
_BRAND_SEPARATOR = re.compile(r"\s[-–—]\s")
UNKNOWN_BRAND_LABEL = "Sem marca identificada"


def _brand_from_product_name(name: Optional[str]) -> Optional[str]:
    """Marca = trecho após o último separador " - " do nome; ``None`` se não houver."""
    parts = _BRAND_SEPARATOR.split((name or "").strip())
    if len(parts) < 2:
        return None
    brand = " ".join(parts[-1].split()).upper()
    if not brand or len(brand) > 40 or not any(ch.isalpha() for ch in brand):
        return None
    return brand


def _brand_group_key(brand: str) -> str:
    """Agrupa grafias do mesmo fabricante ("MK LIFE"/"MKLIFE", "SWANN-MORTON")."""
    folded = unicodedata.normalize("NFKD", brand)
    return "".join(ch for ch in folded if ch.isalnum())


def _revenue_by_brand(rows) -> List[Dict]:
    """Agrega itens vendidos por marca, do maior faturamento para o menor."""
    groups: Dict[str, Dict] = {}
    for r in rows:
        erp_brand = " ".join(str(r["brand"] or "").split()).upper() if "brand" in r.keys() else ""
        brand = erp_brand or _brand_from_product_name(r["name"])
        key = _brand_group_key(brand) if brand else ""
        g = groups.setdefault(key, {
            "spellings": {}, "revenue": 0.0, "units": 0, "orders": set(), "products": set(),
            "known": brand is not None,
        })
        label = brand or UNKNOWN_BRAND_LABEL
        g["spellings"][label] = g["spellings"].get(label, 0) + int(r["qty"] or 0)
        g["revenue"] += float(r["subtotal"] or 0.0)
        g["units"] += int(r["qty"] or 0)
        g["orders"].add(int(r["tx_id"]))
        g["products"].add(str(r["pid_raw"] or r["name"] or ""))
    brands = [
        {
            "brand": max(g["spellings"], key=g["spellings"].get),
            "revenue": g["revenue"],
            "units": g["units"],
            "orders": len(g["orders"]),
            "products": len(g["products"]),
            "known": g["known"],
        }
        for g in groups.values()
    ]
    brands.sort(key=lambda b: b["revenue"], reverse=True)
    return brands


def get_event_financial_report(
    event_id: int,
    *,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Dict:
    """Relatório financeiro completo de um evento para a seção Financeiro.

    ``date_from`` / ``date_to``: ``YYYY-MM-DD`` (ambos inclusive). Se ambos
    forem ``None`` retorna o período integral do evento.

    Retorna um dict com:
    - ``event``          : dados do evento (id, name, active, created_at)
    - ``period``         : {date_from, date_to, label}
    - ``kpis``           : {orders, revenue, avg_ticket, refunds_count,
                            refunds_value, items_sold}
    - ``payment_methods``: lista {method, orders, revenue}
    - ``stock_summary``  : {entries, exits_manual, sold_units,
                            refunded_units, final_units,
                            products_count, sem_estoque, below_min, stock_value}
    - ``top_skus``       : lista (top 10) {rank, sku, product_name, product_id,
                            units_sold, revenue, refunded_units}
    - ``top_products``   : alias de ``top_skus`` no formato lido pelos gráficos d3
    - ``sales_by_day``   : lista {day, orders, revenue}
    - ``chart_by_day``   : série diária contínua p/ gráficos (lacunas viram zero)
                            {day, label, orders, revenue, items, avg_ticket,
                            cumulative_revenue}
    - ``chart_by_hour``  : 24 posições fixas {hour, label, orders, revenue}
    - ``sellers``        : lista {name, orders, revenue}
    - ``brands``         : lista {brand, revenue, units, orders, products, known}, maior receita
                            primeiro; ``known=False`` agrupa itens sem marca no nome
    - ``stock_health``   : {ok, below_min, out_of_stock} — composição atual do estoque
    """
    eid = int(event_id)

    # ------------------------------------------------------------------
    # Predicado de período (usado em WHERE de cada query)
    # ------------------------------------------------------------------
    date_params: List = []
    date_clause = ""
    if date_from:
        date_clause += " AND date(t.created_at) >= date(?)"
        date_params.append(date_from)
    if date_to:
        date_clause += " AND date(t.created_at) <= date(?)"
        date_params.append(date_to)

    # Mesmo predicado para movimentações de estoque
    mov_date_params: List = []
    mov_date_clause = ""
    if date_from:
        mov_date_clause += " AND date(m.created_at) >= date(?)"
        mov_date_params.append(date_from)
    if date_to:
        mov_date_clause += " AND date(m.created_at) <= date(?)"
        mov_date_params.append(date_to)

    with get_conn() as conn:
        # ---------- dados do evento ----------------------------------
        ev_row = conn.execute(
            "SELECT id, name, active, operations_closed, created_at, description FROM events WHERE id = ?",
            (eid,),
        ).fetchone()
        event_data = dict(ev_row) if ev_row else {}

        # ---------- KPIs de vendas (confirmadas) ---------------------
        confirmed_filter = (
            "FROM transactions t "
            "WHERE t.status = 'confirmado' AND t.event_id = ?"
        )
        agg = conn.execute(
            f"SELECT COUNT(*) AS orders, COALESCE(SUM(t.total),0) AS revenue, "
            f"COALESCE(SUM(t.items_count),0) AS items_sold "
            f"{confirmed_filter}{date_clause}",
            [eid] + date_params,
        ).fetchone()
        orders = int(agg["orders"] or 0)
        revenue = float(agg["revenue"] or 0.0)
        items_sold = int(agg["items_sold"] or 0)
        avg_ticket = (revenue / orders) if orders else 0.0

        # ---------- Estornos -----------------------------------------
        refund_agg = conn.execute(
            f"SELECT COUNT(*) AS rc, COALESCE(SUM(t.total),0) AS rv "
            f"FROM transactions t "
            f"WHERE t.status = 'estornado' AND t.event_id = ?{date_clause}",
            [eid] + date_params,
        ).fetchone()
        refunds_count = int(refund_agg["rc"] or 0)
        refunds_value = float(refund_agg["rv"] or 0.0)

        # ---------- Formas de pagamento ------------------------------
        pm_rows = conn.execute(
            f"SELECT COALESCE(t.payment_method,'—') AS method, "
            f"COUNT(*) AS orders, COALESCE(SUM(t.total),0) AS revenue "
            f"{confirmed_filter}{date_clause} "
            f"GROUP BY t.payment_method ORDER BY revenue DESC",
            [eid] + date_params,
        ).fetchall()
        payment_methods = [dict(r) for r in pm_rows]

        # ---------- Resumo de estoque via movimentações --------------
        def _sum_mov(types_tuple, extra_clause="") -> int:
            placeholders = ",".join("?" * len(types_tuple))
            row = conn.execute(
                f"SELECT COALESCE(SUM(ABS(m.delta)),0) AS total "
                f"FROM stock_movements m "
                f"WHERE m.event_id = ? AND m.movement_type IN ({placeholders})"
                f"{extra_clause}{mov_date_clause}",
                [eid] + list(types_tuple) + mov_date_params,
            ).fetchone()
            return int(row["total"] or 0) if row else 0

        entries = _sum_mov(("entrada",))
        exits_manual = _sum_mov(("saida",))
        sold_units = _sum_mov(("venda",), " AND m.delta < 0")
        refunded_units = _sum_mov(("venda",), " AND m.delta > 0")

        # Estoque final (situação atual dos produtos no evento)
        final_row = conn.execute(
            "SELECT COALESCE(SUM(ep.stock),0) AS total "
            "FROM event_products ep WHERE ep.event_id = ?",
            (eid,),
        ).fetchone()
        final_units = int(final_row["total"] or 0) if final_row else 0

        # Valor do estoque e alertas (sempre estado atual, sem filtro de data)
        sv_row = conn.execute(
            "SELECT COUNT(ep.id) AS products_count, "
            "COALESCE(SUM(ep.stock * COALESCE(ep.price, p.price)),0) AS stock_value, "
            "COALESCE(SUM(CASE WHEN ep.stock=0 THEN 1 ELSE 0 END),0) AS sem_estoque, "
            "COALESCE(SUM(CASE WHEN ep.stock>0 AND ep.stock<ep.min_stock THEN 1 ELSE 0 END),0) AS below_min "
            "FROM event_products ep JOIN products p ON p.id = ep.product_id "
            "WHERE ep.event_id = ?",
            (eid,),
        ).fetchone()

        stock_summary = {
            "entries": entries,
            "exits_manual": exits_manual,
            "sold_units": sold_units,
            "refunded_units": refunded_units,
            "final_units": final_units,
            "products_count": int(sv_row["products_count"] or 0) if sv_row else 0,
            "sem_estoque": int(sv_row["sem_estoque"] or 0) if sv_row else 0,
            "below_min": int(sv_row["below_min"] or 0) if sv_row else 0,
            "stock_value": float(sv_row["stock_value"] or 0.0) if sv_row else 0.0,
        }

        # ---------- Top 10 SKUs (vendas confirmadas) -----------------
        top_rows = conn.execute(
            f"SELECT ti.product_id AS pid_raw, "
            f"COALESCE(MAX(NULLIF(TRIM(ti.product_sku),'')),MAX(p.sku),'') AS sku, "
            f"MAX(ti.product_name) AS product_name, "
            f"SUM(ti.quantity) AS units_sold, "
            f"SUM(ti.subtotal) AS revenue "
            f"FROM transaction_items ti "
            f"JOIN transactions t ON t.id = ti.transaction_id "
            f"LEFT JOIN products p ON p.id = CAST(ti.product_id AS INTEGER) "
            f"WHERE t.status = 'confirmado' AND t.event_id = ?{date_clause} "
            f"AND ti.gift_item_id IS NULL "
            f"GROUP BY ti.product_id "
            f"ORDER BY units_sold DESC LIMIT 10",
            [eid] + date_params,
        ).fetchall()

        # estornos por produto (via movimentação delta > 0)
        refund_by_pid = {}
        refund_rows = conn.execute(
            "SELECT ti.product_id AS pid_raw, SUM(ti.quantity) AS qty "
            "FROM transaction_items ti "
            "JOIN transactions t ON t.id = ti.transaction_id "
            "WHERE t.status = 'estornado' AND t.event_id = ? AND ti.gift_item_id IS NULL "
            "GROUP BY ti.product_id",
            (eid,),
        ).fetchall()
        for r in refund_rows:
            refund_by_pid[str(r["pid_raw"] or "")] = int(r["qty"] or 0)

        top_skus = []
        for i, r in enumerate(top_rows, 1):
            raw = str(r["pid_raw"] or "")
            pid_int = int(raw) if raw.isdigit() else None
            top_skus.append({
                "rank": i,
                "sku": (r["sku"] or "").strip() or "—",
                "product_name": (r["product_name"] or "").strip() or "—",
                "product_id": pid_int,
                "units_sold": int(r["units_sold"] or 0),
                "revenue": float(r["revenue"] or 0.0),
                "refunded_units": refund_by_pid.get(raw, 0),
            })

        # ---------- Vendas por dia ------------------------------------
        day_rows = conn.execute(
            f"SELECT date(t.created_at) AS day, COUNT(*) AS orders, "
            f"COALESCE(SUM(t.total),0) AS revenue, "
            f"COALESCE(SUM(t.items_count),0) AS items "
            f"{confirmed_filter}{date_clause} "
            f"GROUP BY date(t.created_at) ORDER BY day",
            [eid] + date_params,
        ).fetchall()
        sales_by_day = [
            {"day": str(r["day"]), "orders": int(r["orders"] or 0),
             "revenue": float(r["revenue"] or 0.0)}
            for r in day_rows
        ]

        # ---------- Série diária contínua p/ gráficos (lacunas viram zero) ---
        chart_by_day: List[Dict] = []
        by_date = {}
        for r in day_rows:
            try:
                by_date[_date.fromisoformat(str(r["day"])[:10])] = r
            except ValueError:
                continue
        if by_date:
            cursor = min(by_date)
            last_day = max(by_date)
            running = 0.0
            while cursor <= last_day:
                if len(chart_by_day) >= ANALYTICS_MAX_DAYS:
                    cursor += _timedelta(days=1)
                    continue
                row = by_date.get(cursor)
                d_orders = int(row["orders"] or 0) if row else 0
                d_revenue = float(row["revenue"] or 0.0) if row else 0.0
                d_items = int(row["items"] or 0) if row else 0
                running += d_revenue
                chart_by_day.append({
                    "day": cursor.isoformat(),
                    "label": cursor.strftime("%d/%m"),
                    "orders": d_orders,
                    "revenue": d_revenue,
                    "items": d_items,
                    "avg_ticket": (d_revenue / d_orders) if d_orders else 0.0,
                    "cumulative_revenue": running,
                })
                cursor += _timedelta(days=1)

        # ---------- Distribuição por hora (24 posições fixas) ----------------
        hour_rows = conn.execute(
            f"SELECT CAST(strftime('%H', t.created_at) AS INTEGER) AS hour, "
            f"COUNT(*) AS orders, COALESCE(SUM(t.total),0) AS revenue "
            f"{confirmed_filter}{date_clause} "
            f"GROUP BY hour",
            [eid] + date_params,
        ).fetchall()
        hour_map = {int(r["hour"]): r for r in hour_rows if r["hour"] is not None}
        chart_by_hour = [
            {
                "hour": h,
                "label": f"{h:02d}h",
                "orders": int(hour_map[h]["orders"] or 0) if h in hour_map else 0,
                "revenue": float(hour_map[h]["revenue"] or 0.0) if h in hour_map else 0.0,
            }
            for h in range(24)
        ]

        # ---------- Por vendedor --------------------------------------
        seller_rows = conn.execute(
            f"SELECT COALESCE(t.seller_name,'(sem vendedor)') AS name, "
            f"COUNT(*) AS orders, COALESCE(SUM(t.total),0) AS revenue "
            f"{confirmed_filter}{date_clause} "
            f"GROUP BY t.seller_name ORDER BY revenue DESC",
            [eid] + date_params,
        ).fetchall()
        sellers = [
            {"name": r["name"], "orders": int(r["orders"] or 0),
             "revenue": float(r["revenue"] or 0.0)}
            for r in seller_rows
        ]

        # ---------- Por marca (MARCA do ERP; sem ela, fim do nome) -----
        # O total do pedido (já com descontos de pedido) é rateado entre os
        # itens pelo subtotal: a soma por marca bate com a receita dos KPIs.
        brand_rows = conn.execute(
            f"SELECT t.id AS tx_id, ti.product_id AS pid_raw, "
            f"COALESCE(NULLIF(TRIM(ti.product_name),''), p.name) AS name, "
            f"p.brand AS brand, "
            f"ti.quantity AS qty, "
            f"ti.subtotal * t.total / NULLIF(("
            f"  SELECT SUM(x.subtotal) FROM transaction_items x WHERE x.transaction_id = t.id"
            f"), 0) AS subtotal "
            f"FROM transaction_items ti "
            f"JOIN transactions t ON t.id = ti.transaction_id "
            f"LEFT JOIN products p ON p.id = CAST(ti.product_id AS INTEGER) "
            f"WHERE t.status = 'confirmado' AND t.event_id = ?{date_clause}",
            [eid] + date_params,
        ).fetchall()
        brands = _revenue_by_brand(brand_rows)

    # ---------- label do período ------------------------------------
    if date_from and date_to:
        period_label = f"{_fmt_date_label(date_from)} – {_fmt_date_label(date_to)}"
    elif date_from:
        period_label = f"A partir de {_fmt_date_label(date_from)}"
    elif date_to:
        period_label = f"Até {_fmt_date_label(date_to)}"
    else:
        period_label = "Todo o evento"

    return {
        "event": event_data,
        "period": {
            "date_from": date_from or "",
            "date_to": date_to or "",
            "label": period_label,
        },
        "kpis": {
            "orders": orders,
            "revenue": revenue,
            "avg_ticket": avg_ticket,
            "items_sold": items_sold,
            "refunds_count": refunds_count,
            "refunds_value": refunds_value,
        },
        "goals": get_event_goal_progress(eid),
        "payment_methods": payment_methods,
        "stock_summary": stock_summary,
        "stock_health": {
            "ok": max(
                0,
                stock_summary["products_count"]
                - stock_summary["below_min"]
                - stock_summary["sem_estoque"],
            ),
            "below_min": stock_summary["below_min"],
            "out_of_stock": stock_summary["sem_estoque"],
        },
        "top_skus": top_skus,
        "top_products": top_skus,
        "sales_by_day": sales_by_day,
        "chart_by_day": chart_by_day,
        "chart_by_hour": chart_by_hour,
        "sellers": sellers,
        "brands": brands,
    }


def _fmt_date_label(iso: str) -> str:
    """Converte YYYY-MM-DD para DD/MM/YYYY."""
    try:
        d = _date.fromisoformat(iso[:10])
        return d.strftime("%d/%m/%Y")
    except (ValueError, AttributeError):
        return iso


# ---------------------------------------------------------------------------
# Vendedores do evento
# ---------------------------------------------------------------------------

def list_event_sellers(event_id: int) -> List[Dict]:
    """Vendedores associados ao evento, com métricas de vendas (mesmo critério que ``list_sellers``)."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                s.id,
                s.name,
                s.email,
                s.active,
                s.pin_hash,
                s.last_login_at,
                es.added_at,
                COALESCE(COUNT(t.id), 0) AS transactions_count,
                COALESCE(SUM(t.total), 0) AS total_revenue
              FROM event_sellers es
              JOIN sellers s ON s.id = es.seller_id
              LEFT JOIN transactions t
                ON t.seller_id = s.id AND t.status = 'confirmado'
             WHERE es.event_id = ?
             GROUP BY s.id, es.added_at
             ORDER BY s.active DESC, LOWER(s.name), LOWER(s.email)
            """,
            (int(event_id),),
        ).fetchall()
        return [dict(r) for r in rows]


def add_seller_to_event(event_id: int, seller_id: int) -> None:
    """Associa um vendedor ao evento. Falha se o vendedor já estiver em outro evento."""
    now = _now_iso()
    eid = int(event_id)
    sid = int(seller_id)
    with get_conn() as conn:
        existing_same = conn.execute(
            "SELECT 1 FROM event_sellers WHERE event_id = ? AND seller_id = ?",
            (eid, sid),
        ).fetchone()
        if existing_same:
            return
        other = conn.execute(
            "SELECT event_id FROM event_sellers WHERE seller_id = ? LIMIT 1",
            (sid,),
        ).fetchone()
        if other is not None and int(other["event_id"]) != eid:
            raise ValueError(
                "Este vendedor já está associado a outro evento. "
                "Remova-o desse evento ou altere a designação na ficha do vendedor."
            )
        try:
            conn.execute(
                "INSERT INTO event_sellers (event_id, seller_id, added_at) VALUES (?, ?, ?)",
                (eid, sid, now),
            )
        except sqlite3.IntegrityError as exc:
            err = str(exc).lower()
            if "unique" in err and "seller_id" in err:
                raise ValueError(
                    "Este vendedor já está associado a outro evento. "
                    "Remova-o desse evento ou altere a designação na ficha do vendedor."
                ) from exc
            raise


def get_seller_admin_event_selection_id(seller_id: int) -> Optional[int]:
    """Id do evento para pré-selecionar no admin: ativo mais recente; senão qualquer vínculo mais recente."""
    sid = int(seller_id)
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT es.event_id
              FROM event_sellers es
              JOIN events e ON e.id = es.event_id
             WHERE es.seller_id = ? AND e.active = 1
             ORDER BY es.added_at DESC
             LIMIT 1
            """,
            (sid,),
        ).fetchone()
        if row:
            return int(row["event_id"])
        row = conn.execute(
            """
            SELECT es.event_id
              FROM event_sellers es
             WHERE es.seller_id = ?
             ORDER BY es.added_at DESC
             LIMIT 1
            """,
            (sid,),
        ).fetchone()
        return int(row["event_id"]) if row else None


def replace_seller_event_assignment(seller_id: int, event_id: Optional[int]) -> None:
    """Remove todos os vínculos evento×vendedor e associa no máximo a um evento (substituição exclusiva)."""
    sid = int(seller_id)
    with get_conn() as conn:
        conn.execute("DELETE FROM event_sellers WHERE seller_id = ?", (sid,))
        if event_id is None or int(event_id) <= 0:
            return
        eid = int(event_id)
        ev = conn.execute("SELECT id FROM events WHERE id = ?", (eid,)).fetchone()
        if ev is None:
            raise ValueError("Evento não encontrado.")
        now = _now_iso()
        conn.execute(
            "INSERT INTO event_sellers (event_id, seller_id, added_at) VALUES (?, ?, ?)",
            (eid, sid, now),
        )


def get_active_event_for_seller(seller_id: int) -> Optional[Dict]:
    """Retorna o evento ativo mais recente ao qual o vendedor está associado, ou None."""
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT e.*
              FROM event_sellers es
              JOIN events e ON e.id = es.event_id
             WHERE es.seller_id = ? AND e.active = 1
             ORDER BY es.added_at DESC
             LIMIT 1
            """,
            (int(seller_id),),
        ).fetchone()
        return dict(row) if row else None


def list_event_products_for_client(event_id: int) -> List[Dict]:
    """Produtos do evento no formato cliente/catálogo, com estoque do evento."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT p.*, ep.stock AS event_stock, ep.min_stock AS event_min_stock,
                   ep.backorder_limit AS event_backorder_limit,
                   p.price AS library_price,
                   COALESCE(ep.price, p.price) AS event_unit_price
              FROM event_products ep
              JOIN products p ON p.id = ep.product_id
             WHERE ep.event_id = ? AND p.active = 1
             ORDER BY p.category, p.name
            """,
            (int(event_id),),
        ).fetchall()
    result = []
    for r in rows:
        d = _product_row_to_client(r)
        d["preco_biblioteca"] = float(r["library_price"] or 0)
        d["preco"] = float(r["event_unit_price"] if r["event_unit_price"] is not None else d["preco"])
        d["estoque"] = int(r["event_stock"] or 0)
        d["estoque_minimo"] = int(r["event_min_stock"] or 0)
        d["backorder_limit"] = int(r["event_backorder_limit"] if r["event_backorder_limit"] is not None else -1)
        d["abaixo_minimo"] = d["estoque_minimo"] > 0 and d["estoque"] < d["estoque_minimo"]
        d["sem_estoque"] = d["estoque"] <= 0
        result.append(d)
    return prepare_catalog_variant_groups(result)


def list_active_event_product_stocks(event_id: int) -> List[Dict]:
    """Id e estoque dos produtos ativos no evento (polling do catálogo)."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT ep.product_id AS id, ep.stock AS estoque,
                   ep.backorder_limit
              FROM event_products ep
              JOIN products p ON p.id = ep.product_id
             WHERE ep.event_id = ? AND p.active = 1
            """,
            (int(event_id),),
        ).fetchall()
    return [
        {
            "id": int(r["id"]),
            "estoque": int(r["estoque"] or 0),
            "backorder_limit": int(r["backorder_limit"] if r["backorder_limit"] is not None else -1),
        }
        for r in rows
    ]
