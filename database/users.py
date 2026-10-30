"""Usuários do Go Sell: tipo (pré-definição) e permissões.

As contas ficam na tabela ``sellers`` (vendas, metas, entregas e o envio ao
ERP já apontam para ela). Cada conta tem:

- ``role``: ``administrador``, ``vendedor``, ``marketing`` ou ``personalizado``;
- ``permissions``: lista (JSON) usada só pelo tipo ``personalizado`` — os
  outros tipos valem pelas permissões da pré-definição (``PRESETS``), de modo
  que mudar uma pré-definição aqui vale para todas as contas daquele tipo;
- eventos do painel: ``all_events`` = 1 (todos) ou a lista em ``user_events``.
  O evento em que a pessoa **vende** continua em ``event_sellers`` (um só).

A conta mestre do ``.env`` (``TOTEM_ADMIN_USER``) não fica aqui: tem todas as
permissões do painel e serve para nunca perder o acesso.
"""
from __future__ import annotations

import json
from typing import Dict, Iterable, List, Optional, Set

from .connection import _now_iso, get_conn

#: Permissões, na ordem em que aparecem no cadastro:
#: (chave, rótulo, grupo, descrição).
PERMISSIONS: List[tuple] = [
    ("vender", "Vender", "Vendas", "Catálogo, carrinho e painel do vendedor (Minhas vendas, Estoque)."),
    ("transacoes_ver", "Ver transações", "Evento", "Todas as transações do evento e a planilha de vendas."),
    ("transacoes_gerir", "Gerir transações", "Evento",
     "Estornar, confirmar entrega, trocar item, apagar carrinho e editar a observação da nota."),
    ("promocoes", "Promoções e brindes", "Evento", "Criar, editar, ativar e excluir promoções e brindes."),
    ("estoque", "Estoque", "Evento", "Estoque do evento, movimentações, importação por planilha e preços."),
    ("dashboard", "Dashboard e metas", "Evento", "Dashboard do evento e metas de venda."),
    ("clientes", "Clientes", "Evento", "Clientes do evento e relatórios."),
    ("eventos", "Eventos", "Painel", "Criar, editar, encerrar, arquivar e excluir eventos."),
    ("produtos", "Produtos", "Painel", "Biblioteca de produtos."),
    ("usuarios", "Usuários", "Painel", "Criar e editar usuários e permissões."),
    ("financeiro", "Financeiro", "Painel", "Relatório financeiro."),
    ("erp", "Integração ERP", "Painel", "Integração com o ERP: fila, configuração e sincronização."),
    ("reiniciar", "Reiniciar sistema", "Painel", "Restaurar a demonstração ao estado inicial."),
]
PERMISSION_KEYS = [p[0] for p in PERMISSIONS]
PERMISSION_LABELS = {p[0]: p[1] for p in PERMISSIONS}
#: Permissões do painel administrativo (todas menos ``vender``).
PANEL_PERMISSIONS = [k for k in PERMISSION_KEYS if k != "vender"]

#: Pré-definições. ``all_events``: acessa todos os eventos no painel.
PRESETS: Dict[str, Dict] = {
    "administrador": {
        "label": "Administrador",
        "permissions": list(PANEL_PERMISSIONS),
        "all_events": True,
        "hint": "Todo o painel administrativo, em todos os eventos.",
    },
    "vendedor": {
        "label": "Vendedor",
        "permissions": ["vender"],
        "all_events": False,
        "hint": "Vende no catálogo do evento em que estiver associado.",
    },
    "marketing": {
        "label": "Marketing",
        "permissions": ["transacoes_ver", "promocoes", "dashboard"],
        "all_events": False,
        "hint": "Dashboard e metas, todas as transações (só consulta) e as promoções e "
                "brindes dos eventos escolhidos.",
    },
    "personalizado": {
        "label": "Personalizado",
        "permissions": [],
        "all_events": False,
        "hint": "Permissões marcadas uma a uma.",
    },
}
ROLE_KEYS = list(PRESETS)
DEFAULT_ROLE = "vendedor"


def normalize_role(raw: Optional[str]) -> str:
    role = (raw or "").strip().lower()
    return role if role in PRESETS else DEFAULT_ROLE


def clean_permissions(raw: Iterable[str]) -> List[str]:
    """Só chaves conhecidas, sem repetir, na ordem do catálogo."""
    wanted = {str(p).strip() for p in raw or []}
    return [k for k in PERMISSION_KEYS if k in wanted]


def _stored_permissions(row: Dict) -> List[str]:
    raw = row.get("permissions")
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return clean_permissions(data if isinstance(data, list) else [])


def effective_permissions(row: Optional[Dict]) -> Set[str]:
    """Permissões que valem para a conta: as da pré-definição ou as marcadas."""
    if not row:
        return set()
    role = normalize_role(row.get("role"))
    if role == "personalizado":
        return set(_stored_permissions(row))
    return set(PRESETS[role]["permissions"])


def has_all_events(row: Optional[Dict]) -> bool:
    if not row:
        return False
    role = normalize_role(row.get("role"))
    if role == "administrador":
        return True
    return bool(row.get("all_events"))


def panel_event_ids(user_id: int) -> List[int]:
    """Eventos escolhidos para o painel (quando a conta não tem todos)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT event_id FROM user_events WHERE user_id = ? ORDER BY added_at, event_id",
            (int(user_id),),
        ).fetchall()
    return [int(r["event_id"]) for r in rows]


def describe_user(row: Optional[Dict]) -> Optional[Dict]:
    """Acrescenta tipo, permissões efetivas e eventos do painel à conta."""
    if row is None:
        return None
    user = dict(row)
    role = normalize_role(user.get("role"))
    perms = effective_permissions(user)
    user["role"] = role
    user["role_label"] = PRESETS[role]["label"]
    user["permission_list"] = [k for k in PERMISSION_KEYS if k in perms]
    user["permission_labels"] = [PERMISSION_LABELS[k] for k in user["permission_list"]]
    user["can_sell"] = "vender" in perms
    user["has_panel"] = bool(perms & set(PANEL_PERMISSIONS))
    user["all_events"] = has_all_events(user)
    user["panel_event_ids"] = [] if user["all_events"] else panel_event_ids(int(user["id"]))
    return user


def set_user_access(
    user_id: int,
    *,
    role: str,
    permissions: Iterable[str] = (),
    all_events: bool = False,
    event_ids: Iterable[int] = (),
) -> None:
    """Grava tipo, permissões (só no personalizado) e eventos do painel."""
    role_n = normalize_role(role)
    perms = clean_permissions(permissions) if role_n == "personalizado" else []
    all_ev = has_all_events({"role": role_n, "all_events": all_events})
    wanted = [] if all_ev else list(dict.fromkeys(int(e) for e in event_ids if e))
    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM sellers WHERE id = ?", (int(user_id),)).fetchone() is None:
            raise ValueError("Usuário não encontrado.")
        for eid in wanted:
            if conn.execute("SELECT 1 FROM events WHERE id = ?", (eid,)).fetchone() is None:
                raise ValueError("Evento não encontrado.")
        conn.execute(
            "UPDATE sellers SET role = ?, permissions = ?, all_events = ?, updated_at = ? WHERE id = ?",
            (role_n, json.dumps(perms) if perms else None, 1 if all_ev else 0, _now_iso(), int(user_id)),
        )
        current = {
            int(r["event_id"])
            for r in conn.execute("SELECT event_id FROM user_events WHERE user_id = ?", (int(user_id),)).fetchall()
        }
        for eid in current - set(wanted):
            conn.execute("DELETE FROM user_events WHERE user_id = ? AND event_id = ?", (int(user_id), eid))
        now = _now_iso()
        for eid in wanted:
            if eid not in current:
                conn.execute(
                    "INSERT INTO user_events (user_id, event_id, added_at) VALUES (?, ?, ?)",
                    (int(user_id), eid, now),
                )


def user_can_access_event(user: Optional[Dict], event_id: int) -> bool:
    """A conta pode abrir este evento no painel."""
    if not user:
        return False
    if user.get("all_events"):
        return True
    return int(event_id) in set(user.get("panel_event_ids") or [])


def list_panel_events_for_user(user: Optional[Dict], *, include_archived: bool = True) -> List[Dict]:
    """Eventos do seletor do painel para a conta (todos ou os escolhidos)."""
    if not user:
        return []
    with get_conn() as conn:
        if user.get("all_events"):
            where = "" if include_archived else "WHERE active = 1"
            rows = conn.execute(f"SELECT * FROM events {where} ORDER BY active DESC, id DESC").fetchall()
        else:
            ids = list(user.get("panel_event_ids") or [])
            if not ids:
                return []
            placeholders = ",".join("?" * len(ids))
            rows = conn.execute(
                f"SELECT * FROM events WHERE id IN ({placeholders}) ORDER BY active DESC, id DESC", ids,
            ).fetchall()
    return [dict(r) for r in rows]
