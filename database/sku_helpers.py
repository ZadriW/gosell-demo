"""SKU helpers shared by ERP sync, migrations and catalog."""
from __future__ import annotations

import sqlite3
from typing import Dict, Iterable, Optional

def _default_sku_for_id(product_id: int) -> str:
    """SKU de fallback quando o cadastro não possui código (formato ``OM-`` + id)."""
    pid = int(product_id)
    if pid <= 0:
        return f"OM-invalid-{abs(pid)}"
    return f"OM-{pid:05d}"


def _is_placeholder_product_name(name: str) -> bool:
    n = (name or "").strip()
    return not n or n == "Produto"


def _is_generated_fallback_sku(sku: str, product_id: int) -> bool:
    return (sku or "").strip() == _default_sku_for_id(int(product_id))


def _resolve_product_sku(conn: sqlite3.Connection, product_id: int) -> str:
    """Retorna o SKU cadastrado ou o fallback ``OM-`` + id."""
    row = conn.execute(
        "SELECT sku FROM products WHERE id = ?", (int(product_id),)
    ).fetchone()
    if row is None:
        return _default_sku_for_id(int(product_id))
    sku = (row["sku"] or "").strip()
    return sku or _default_sku_for_id(int(product_id))


def _product_sku_label(
    product_id: int,
    *,
    conn: Optional[sqlite3.Connection] = None,
    sku: Optional[str] = None,
) -> str:
    """Rótulo legível do produto (SKU) para mensagens de erro ao usuário."""
    if sku and str(sku).strip():
        return str(sku).strip()
    if conn is not None:
        return _resolve_product_sku(conn, int(product_id))
    return _default_sku_for_id(int(product_id))


def _build_sku_by_product_id(
    conn: sqlite3.Connection, product_ids: Iterable[int]
) -> Dict[int, str]:
    """Mapa ``product_id -> SKU`` (com fallback para ids sem cadastro)."""
    pids = {int(p) for p in product_ids if p is not None}
    out: Dict[int, str] = {}
    if not pids:
        return out
    placeholders = ",".join("?" * len(pids))
    for r in conn.execute(
        f"SELECT id, sku FROM products WHERE id IN ({placeholders})",
        list(pids),
    ).fetchall():
        pid = int(r["id"])
        out[pid] = (r["sku"] or "").strip() or _default_sku_for_id(pid)
    for pid in pids:
        out.setdefault(pid, _default_sku_for_id(pid))
    return out


def _ensure_distinct_sku(conn: sqlite3.Connection, pid: int, sku: str) -> str:
    """Garante ``sku`` único na tabela (integrações podem repetir SKU entre produtos)."""
    base = (sku or "").strip() or _default_sku_for_id(pid)
    clash = conn.execute(
        "SELECT id FROM products WHERE sku = ? AND id != ?",
        (base, pid),
    ).fetchone()
    if not clash:
        return base
    return _default_sku_for_id(pid)

