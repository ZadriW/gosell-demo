"""Seller accounts (seller panel)."""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from .connection import _now_iso, get_conn
from .users import effective_permissions

_USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,40}$")


def normalize_seller_username(raw: str) -> str:
    return (raw or "").strip().lower()


def validate_seller_username(raw: str) -> str:
    username = normalize_seller_username(raw)
    if not username:
        raise ValueError("Usuário do vendedor é obrigatório.")
    if not _USERNAME_RE.fullmatch(username):
        raise ValueError(
            "Informe um usuário válido (3 a 40 caracteres: letras, números, ponto, hífen ou underline)."
        )
    return username


def _seller_login_value(row) -> str:
    return (row["username"] or row["email"] or "").strip()


def ensure_seller_account(
    name: str,
    username: str,
    password_hash: str,
    pin_hash: Optional[str] = None,
) -> Dict:
    """Cria uma conta de vendedor se o usuário ainda não existir."""
    login = validate_seller_username(username)
    seller_name = (name or "").strip() or "Vendedor"
    now = _now_iso()
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT * FROM sellers
             WHERE LOWER(COALESCE(username, '')) = ?
                OR LOWER(email) = ?
            """,
            (login, login),
        ).fetchone()
        if row:
            if pin_hash and not row["pin_hash"]:
                conn.execute(
                    "UPDATE sellers SET pin_hash = ?, updated_at = ? WHERE id = ?",
                    (pin_hash, _now_iso(), int(row["id"])),
                )
                row = conn.execute(
                    "SELECT * FROM sellers WHERE id = ?",
                    (int(row["id"]),),
                ).fetchone()
            return dict(row)
        cur = conn.execute(
            """
            INSERT INTO sellers
                (name, email, username, password_hash, pin_hash, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (seller_name, login, login, password_hash, pin_hash, now, now),
        )
        created = conn.execute(
            "SELECT * FROM sellers WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
    return dict(created)


def normalize_seller_codvend(raw) -> Optional[int]:
    """``CODVEND`` do ERP digitado no admin: vazio = sem código; só dígitos."""
    text = str(raw if raw is not None else "").strip()
    if not text:
        return None
    if not text.isdigit() or int(text) <= 0:
        raise ValueError("O CODVEND deve ser um número inteiro positivo.")
    return int(text)


def _check_codvend_free(conn, codvend: Optional[int], seller_id: Optional[int] = None) -> None:
    if codvend is None:
        return
    other = conn.execute(
        "SELECT id, name FROM sellers WHERE erp_codvend = ? AND id != ?",
        (int(codvend), int(seller_id or 0)),
    ).fetchone()
    if other:
        raise ValueError(
            f"O CODVEND {int(codvend)} já pertence ao vendedor {other['name']}. "
            "Cada vendedor precisa do seu próprio código no ERP."
        )


def create_seller_account(
    name: str,
    username: str,
    password_hash: str,
    pin_hash: Optional[str] = None,
    *,
    erp_codvend: Optional[int] = None,
) -> Dict:
    """Cria uma conta de vendedor, falhando se o usuário já estiver em uso."""
    login = validate_seller_username(username)
    seller_name = (name or "").strip()
    if not seller_name:
        raise ValueError("Nome do vendedor é obrigatório.")
    if not (password_hash or "").strip():
        raise ValueError("Senha do vendedor é obrigatória.")
    ph = (pin_hash or "").strip() or None
    now = _now_iso()
    with get_conn() as conn:
        exists = conn.execute(
            """
            SELECT 1 FROM sellers
             WHERE LOWER(COALESCE(username, '')) = ?
                OR LOWER(email) = ?
            """,
            (login, login),
        ).fetchone()
        if exists:
            raise ValueError("Já existe um vendedor com este usuário.")
        _check_codvend_free(conn, erp_codvend)
        cur = conn.execute(
            """
            INSERT INTO sellers
                (name, email, username, password_hash, pin_hash, active, created_at,
                 updated_at, erp_codvend)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (seller_name, login, login, password_hash, ph, now, now, erp_codvend),
        )
        row = conn.execute(
            "SELECT * FROM sellers WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
    return dict(row)


def list_sellers() -> List[Dict]:
    """Lista vendedores com métricas agregadas de vendas e eventos associados."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                s.*,
                COALESCE(COUNT(t.id), 0) AS transactions_count,
                COALESCE(SUM(t.total), 0) AS total_revenue,
                COALESCE(SUM(t.items_count), 0) AS items_sold,
                MAX(t.created_at) AS last_sale_at
              FROM sellers s
              LEFT JOIN transactions t
                ON t.seller_id = s.id AND t.status = 'confirmado'
             GROUP BY s.id
             ORDER BY s.active DESC, LOWER(s.name), LOWER(COALESCE(s.username, s.email))
            """
        ).fetchall()
        sellers = [dict(r) for r in rows]
        if not sellers:
            return sellers
        seller_ids = [int(s["id"]) for s in sellers]
        placeholders = ",".join("?" * len(seller_ids))
        ev_sql = (
            f"""
            SELECT es.seller_id AS seller_id,
                   e.id AS event_id,
                   e.name AS event_name,
                   e.badge_color AS badge_color,
                   e.active AS event_active
              FROM event_sellers es
              JOIN events e ON e.id = es.event_id
             WHERE es.seller_id IN ({placeholders})
             ORDER BY e.active DESC, LOWER(e.name)
            """
        )
        ev_rows = conn.execute(ev_sql, seller_ids).fetchall()
        by_seller: Dict[int, List[Dict]] = {}
        for er in ev_rows:
            d = dict(er)
            sid = int(d["seller_id"])
            bucket = by_seller.setdefault(sid, [])
            bucket.append(
                {
                    "event_id": int(d["event_id"]),
                    "name": d["event_name"],
                    "badge_color": d["badge_color"],
                    "active": bool(d["event_active"]),
                }
            )
        for s in sellers:
            s["assigned_events"] = by_seller.get(int(s["id"]), [])
            s["username"] = _seller_login_value(s)
    return sellers


def list_seller_pin_hashes() -> List[Dict]:
    """Retorna hashes de PIN para validação/autenticação por PIN."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, name, email, username, active, pin_hash
              FROM sellers
             WHERE pin_hash IS NOT NULL AND pin_hash <> ''
             ORDER BY id
            """
        ).fetchall()
    return [dict(r) for r in rows]


def get_seller_by_username(username: str) -> Optional[Dict]:
    login = normalize_seller_username(username)
    if not login:
        return None
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT * FROM sellers
             WHERE LOWER(COALESCE(username, '')) = ?
                OR LOWER(email) = ?
             LIMIT 1
            """,
            (login, login),
        ).fetchone()
    if not row:
        return None
    data = dict(row)
    data["username"] = _seller_login_value(data)
    return data


def get_seller_by_email(email: str) -> Optional[Dict]:
    """Compatível com cadastros antigos: busca por usuário ou e-mail legado."""
    return get_seller_by_username(email)


def list_sellers_without_codvend() -> List[Dict]:
    """Vendedores ativos sem ``CODVEND``: as vendas deles ficam presas na fila do ERP.

    Só contas com a permissão de vender (Marketing/Administrador não vendem).
    """
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT s.id, s.name, s.username, s.role, s.permissions,
                   (SELECT COUNT(*) FROM transactions t
                     WHERE t.seller_id = s.id AND t.status = 'confirmado') AS confirmed
              FROM sellers s
             WHERE s.active = 1 AND s.erp_codvend IS NULL
             ORDER BY LOWER(s.name)
            """
        ).fetchall()
    return [dict(r) for r in rows if "vender" in effective_permissions(dict(r))]


def get_seller(seller_id: int) -> Optional[Dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM sellers WHERE id = ?",
            (int(seller_id),),
        ).fetchone()
    if not row:
        return None
    data = dict(row)
    data["username"] = _seller_login_value(data)
    return data


def delete_seller(seller_id: int) -> Dict:
    """Remove o cadastro do vendedor. Transações ligadas ficam com ``seller_id`` nulo (FK)."""
    sid = int(seller_id)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, name FROM sellers WHERE id = ?",
            (sid,),
        ).fetchone()
        if row is None:
            raise ValueError("Vendedor não encontrado.")
        name = str(row["name"] or "")
        conn.execute("DELETE FROM sellers WHERE id = ?", (sid,))
    return {"id": sid, "name": name}


def update_seller_last_login(seller_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE sellers SET last_login_at = ?, updated_at = ? WHERE id = ?",
            (_now_iso(), _now_iso(), int(seller_id)),
        )


def update_seller_account(
    seller_id: int,
    *,
    name: str,
    username: str = "",
    active: bool,
    password_hash: Optional[str] = None,
    pin_hash: Optional[str] = None,
    clear_pin_hash: bool = False,
    email: Optional[str] = None,
    erp_codvend: Optional[int] = None,
) -> Dict:
    """Atualiza dados principais do vendedor e opcionalmente redefine a senha."""
    login = validate_seller_username(username or email or "")
    seller_name = (name or "").strip()
    if not seller_name:
        raise ValueError("Nome do vendedor é obrigatório.")
    now = _now_iso()
    with get_conn() as conn:
        exists = conn.execute(
            """
            SELECT id FROM sellers
             WHERE (LOWER(COALESCE(username, '')) = ? OR LOWER(email) = ?)
               AND id <> ?
            """,
            (login, login, int(seller_id)),
        ).fetchone()
        if exists:
            raise ValueError("Já existe outro vendedor com este usuário.")
        _check_codvend_free(conn, erp_codvend, int(seller_id))
        updates = [
            "name = ?",
            "email = ?",
            "username = ?",
            "active = ?",
            "erp_codvend = ?",
        ]
        params: List = [seller_name, login, login, 1 if active else 0, erp_codvend]
        if password_hash:
            updates.append("password_hash = ?")
            params.append(password_hash)
        if clear_pin_hash:
            updates.append("pin_hash = NULL")
        elif pin_hash:
            updates.append("pin_hash = ?")
            params.append(pin_hash)
        updates.append("updated_at = ?")
        params.extend([now, int(seller_id)])
        conn.execute(
            f"""
            UPDATE sellers
               SET {", ".join(updates)}
             WHERE id = ?
            """,
            params,
        )
        row = conn.execute(
            "SELECT * FROM sellers WHERE id = ?",
            (int(seller_id),),
        ).fetchone()
    if row is None:
        raise ValueError("Vendedor não encontrado.")
    data = dict(row)
    data["username"] = _seller_login_value(data)
    return data
