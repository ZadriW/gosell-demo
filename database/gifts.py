"""Brindes por evento: regras que acrescentam um item a R$ 0,00 ao pedido.

Diferente das promoções (``promotions.py``), um brinde nunca muda o preço dos
itens pagos: quando a regra se cumpre, o pedido ganha uma linha a mais,
marcada com ``gift_rule_id``.

O brinde pode ter um ou mais produtos do estoque (``gift_rule_gift_products``),
um ou mais brindes avulsos (``gift_rule_gift_items``), ou produtos e avulsos
juntos (a regra entrega ``gift_qty`` unidades de cada um):

- **produtos** do estoque do evento (``gift_rule_gift_products``): saem do mesmo saldo
  e vai ao ERP como item com 100% de desconto. Sem saldo, vira retirada
  pendente, exceto quando o produto não aceita venda futura
  (``backorder_limit`` >= 0): aí o brinde é limitado ao saldo, para que ele
  nunca trave a venda;
- **brindes avulsos** (``gift_rule_gift_items``, tabela ``gift_items``): cadastrados
  só no Go Sell (caneca, squeeze...), sem CODPROD. A linha da venda não tem
  ``product_id``, não mexe no estoque de produtos e não vai como item ao
  ERP (só citada na observação interna). ``gift_items.stock`` é o total
  disponível no evento (vazio = sem limite); o saldo é esse total menos o que
  saiu em vendas confirmadas, então estorno e cancelamento devolvem sozinhos.

Tipos de regra:

- ``min_qty`` — "A partir de N unidades": soma as unidades de todos os
  produtos participantes; ao chegar em N, o pedido ganha o brinde uma vez.
- ``kit`` — "A cada kit de N unidades do mesmo produto": cada produto
  participante forma os próprios kits (8 un. com kit de 5 = 1 kit); cada kit
  completo dá o brinde. ``max_per_order`` limita as unidades de brinde.
- ``min_total`` — "Pedidos a partir de R$ X": valor dos itens pagos (com as
  promoções e o desconto manual do vendedor); com produtos participantes, só o
  valor deles conta. O pedido ganha o brinde uma vez.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Dict, Iterable, List, Optional

from .connection import _now_iso, get_conn

GIFT_RULE_TYPES = ("min_qty", "kit", "min_total")

GIFT_RULE_LABELS = {
    "min_qty": "A partir de N unidades",
    "kit": "A cada kit de N unidades",
    "min_total": "Pedidos a partir de R$ X",
}

#: Prefixo do ``id`` da linha de brinde avulso no carrinho (não há produto).
AVULSO_ID_PREFIX = "avulso:"


# ---------------------------------------------------------------------------
# Brindes avulsos (gift_items)
# ---------------------------------------------------------------------------

def _items_used(conn: sqlite3.Connection, item_ids: Iterable[int]) -> Dict[int, int]:
    """Unidades de cada brinde avulso que saíram em vendas confirmadas."""
    ids = [int(i) for i in item_ids]
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"""
        SELECT ti.gift_item_id, COALESCE(SUM(ti.quantity), 0) AS used
          FROM transaction_items ti
          JOIN transactions t ON t.id = ti.transaction_id
         WHERE ti.gift_item_id IN ({placeholders})
           AND LOWER(TRIM(COALESCE(t.status, ''))) = 'confirmado'
         GROUP BY ti.gift_item_id
        """,
        ids,
    ).fetchall()
    return {int(r["gift_item_id"]): int(r["used"] or 0) for r in rows}


def _item_available(item: Dict, used: int) -> Optional[int]:
    """Saldo do brinde avulso; ``None`` = sem limite."""
    if item.get("stock") is None:
        return None
    return max(0, int(item["stock"]) - int(used))


def _decorate_items(conn: sqlite3.Connection, rows) -> List[Dict]:
    items = [dict(r) for r in rows]
    used = _items_used(conn, [i["id"] for i in items])
    rule_counts = {}
    if items:
        placeholders = ",".join("?" * len(items))
        rule_counts = {
            int(r["gift_item_id"]): int(r["n"])
            for r in conn.execute(
                f"SELECT gift_item_id, COUNT(*) AS n FROM gift_rule_gift_items "
                f"WHERE gift_item_id IN ({placeholders}) GROUP BY gift_item_id",
                [i["id"] for i in items],
            ).fetchall()
        }
    for item in items:
        item["used"] = used.get(int(item["id"]), 0)
        item["available"] = _item_available(item, item["used"])
        item["rules_count"] = rule_counts.get(int(item["id"]), 0)
    return items


def list_gift_items_for_event(event_id: int) -> List[Dict]:
    """Brindes avulsos do evento com saldo, unidades entregues e regras que os usam."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM gift_items WHERE event_id = ? ORDER BY name COLLATE NOCASE",
            (int(event_id),),
        ).fetchall()
        return _decorate_items(conn, rows)


def get_gift_item(item_id: int) -> Optional[Dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM gift_items WHERE id = ?", (int(item_id),)).fetchall()
        items = _decorate_items(conn, rows)
    return items[0] if items else None


def _clean_item_fields(name: str, stock: Optional[int], description: Optional[str]) -> tuple:
    name_s = (name or "").strip()
    if not name_s:
        raise ValueError("Informe o nome do brinde avulso.")
    if len(name_s) > 120:
        raise ValueError("O nome do brinde avulso deve ter no máximo 120 caracteres.")
    if stock is not None and int(stock) < 0:
        raise ValueError("A quantidade disponível não pode ser negativa.")
    desc = (description or "").strip() or None
    return name_s, (int(stock) if stock is not None else None), desc


def create_gift_item_in_conn(
    conn: sqlite3.Connection,
    event_id: int,
    name: str,
    stock: Optional[int] = None,
    description: Optional[str] = None,
) -> int:
    name_s, stock_v, desc = _clean_item_fields(name, stock, description)
    if conn.execute("SELECT 1 FROM events WHERE id = ?", (int(event_id),)).fetchone() is None:
        raise ValueError("Evento não encontrado.")
    dup = conn.execute(
        "SELECT 1 FROM gift_items WHERE event_id = ? AND LOWER(name) = LOWER(?)",
        (int(event_id), name_s),
    ).fetchone()
    if dup:
        raise ValueError(f"Já existe um brinde avulso «{name_s}» neste evento.")
    now = _now_iso()
    cur = conn.execute(
        """
        INSERT INTO gift_items (event_id, name, description, stock, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (int(event_id), name_s, desc, stock_v, now, now),
    )
    return int(cur.lastrowid)


def create_gift_item(
    event_id: int,
    name: str,
    stock: Optional[int] = None,
    description: Optional[str] = None,
) -> Dict:
    """Cadastra um brinde avulso (não é produto, não vai ao ERP)."""
    with get_conn() as conn:
        item_id = create_gift_item_in_conn(conn, event_id, name, stock, description)
    return get_gift_item(item_id)


def update_gift_item(
    item_id: int,
    name: str,
    stock: Optional[int] = None,
    description: Optional[str] = None,
) -> Dict:
    """Atualiza nome, total disponível e descrição. Vendas antigas mantêm o nome que tinham."""
    name_s, stock_v, desc = _clean_item_fields(name, stock, description)
    with get_conn() as conn:
        row = conn.execute("SELECT event_id FROM gift_items WHERE id = ?", (int(item_id),)).fetchone()
        if row is None:
            raise ValueError("Brinde avulso não encontrado.")
        dup = conn.execute(
            "SELECT 1 FROM gift_items WHERE event_id = ? AND LOWER(name) = LOWER(?) AND id <> ?",
            (int(row["event_id"]), name_s, int(item_id)),
        ).fetchone()
        if dup:
            raise ValueError(f"Já existe um brinde avulso «{name_s}» neste evento.")
        conn.execute(
            "UPDATE gift_items SET name = ?, description = ?, stock = ?, updated_at = ? WHERE id = ?",
            (name_s, desc, stock_v, _now_iso(), int(item_id)),
        )
    return get_gift_item(item_id)


def delete_gift_item(item_id: int) -> None:
    """Exclui o brinde avulso. Recusa se alguma regra de brinde ainda o usa."""
    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM gift_items WHERE id = ?", (int(item_id),)).fetchone() is None:
            raise ValueError("Brinde avulso não encontrado.")
        names = [
            r["name"] for r in conn.execute(
                """
                SELECT g.name FROM gift_rules g
                  JOIN gift_rule_gift_items gi ON gi.gift_rule_id = g.id
                 WHERE gi.gift_item_id = ?
                 ORDER BY g.name
                """,
                (int(item_id),),
            ).fetchall()
        ]
        if names:
            raise ValueError(
                "Este brinde avulso está em uso nas regras: " + ", ".join(f"«{n}»" for n in names)
                + ". Troque o brinde dessas regras ou exclua-as antes."
            )
        conn.execute("DELETE FROM gift_items WHERE id = ?", (int(item_id),))


# ---------------------------------------------------------------------------
# Regras: leitura
# ---------------------------------------------------------------------------

def _rule_products(conn: sqlite3.Connection, rule_ids: List[int]) -> Dict[int, List[Dict]]:
    if not rule_ids:
        return {}
    placeholders = ",".join("?" * len(rule_ids))
    rows = conn.execute(
        f"""
        SELECT gp.gift_rule_id, gp.product_id, p.name, p.sku
          FROM gift_rule_products gp
          JOIN products p ON p.id = gp.product_id
         WHERE gp.gift_rule_id IN ({placeholders})
         ORDER BY p.name COLLATE NOCASE
        """,
        list(rule_ids),
    ).fetchall()
    out: Dict[int, List[Dict]] = defaultdict(list)
    for r in rows:
        out[int(r["gift_rule_id"])].append(
            {"product_id": int(r["product_id"]), "name": r["name"], "sku": r["sku"] or ""}
        )
    return out


def _rule_gift_products(conn: sqlite3.Connection, rule_ids: List[int]) -> Dict[int, List[Dict]]:
    """Produtos do estoque que cada regra entrega, na ordem em que foram escolhidos."""
    if not rule_ids:
        return {}
    placeholders = ",".join("?" * len(rule_ids))
    rows = conn.execute(
        f"""
        SELECT gg.gift_rule_id, gg.product_id, p.name, p.sku, p.image
          FROM gift_rule_gift_products gg
          LEFT JOIN products p ON p.id = gg.product_id
         WHERE gg.gift_rule_id IN ({placeholders})
         ORDER BY gg.position, gg.rowid
        """,
        list(rule_ids),
    ).fetchall()
    out: Dict[int, List[Dict]] = defaultdict(list)
    for r in rows:
        out[int(r["gift_rule_id"])].append({
            "product_id": int(r["product_id"]),
            "name": r["name"] or "Produto removido",
            "sku": r["sku"] or "",
            "image": r["image"] or "",
        })
    return out


def _sync_gift_products(conn: sqlite3.Connection, rule_id: int, product_ids: List[int]) -> None:
    conn.execute("DELETE FROM gift_rule_gift_products WHERE gift_rule_id = ?", (int(rule_id),))
    conn.executemany(
        "INSERT OR IGNORE INTO gift_rule_gift_products (gift_rule_id, product_id, position) VALUES (?, ?, ?)",
        [(int(rule_id), int(pid), pos) for pos, pid in enumerate(product_ids)],
    )


def _rule_gift_items(conn: sqlite3.Connection, rule_ids: List[int]) -> Dict[int, List[Dict]]:
    """Brindes avulsos que cada regra entrega (com saldo), na ordem em que foram escolhidos."""
    if not rule_ids:
        return {}
    placeholders = ",".join("?" * len(rule_ids))
    links = conn.execute(
        f"""
        SELECT gi.gift_rule_id, gi.gift_item_id
          FROM gift_rule_gift_items gi
         WHERE gi.gift_rule_id IN ({placeholders})
         ORDER BY gi.position, gi.rowid
        """,
        list(rule_ids),
    ).fetchall()
    item_ids = list(dict.fromkeys(int(r["gift_item_id"]) for r in links))
    items: Dict[int, Dict] = {}
    if item_ids:
        marks = ",".join("?" * len(item_ids))
        items = {
            int(i["id"]): i
            for i in _decorate_items(conn, conn.execute(
                f"SELECT * FROM gift_items WHERE id IN ({marks})", item_ids,
            ).fetchall())
        }
    out: Dict[int, List[Dict]] = defaultdict(list)
    for r in links:
        item = items.get(int(r["gift_item_id"]))
        if item is not None:
            out[int(r["gift_rule_id"])].append(item)
    return out


def _sync_gift_items(conn: sqlite3.Connection, rule_id: int, item_ids: List[int]) -> None:
    conn.execute("DELETE FROM gift_rule_gift_items WHERE gift_rule_id = ?", (int(rule_id),))
    conn.executemany(
        "INSERT OR IGNORE INTO gift_rule_gift_items (gift_rule_id, gift_item_id, position) VALUES (?, ?, ?)",
        [(int(rule_id), int(iid), pos) for pos, iid in enumerate(item_ids)],
    )


def _gift_item_ids(args: Dict) -> List[int]:
    """``gift_item_ids`` (lista) ou o ``gift_item_id`` de antes, sem repetidos."""
    raw = args.get("gift_item_ids")
    if raw is None:
        raw = [args["gift_item_id"]] if args.get("gift_item_id") else []
    return list(dict.fromkeys(int(i) for i in raw if i))


def _gift_product_ids(args: Dict) -> List[int]:
    """``gift_product_ids`` (lista) ou o ``gift_product_id`` de antes, sem repetidos."""
    raw = args.get("gift_product_ids")
    if raw is None:
        raw = [args["gift_product_id"]] if args.get("gift_product_id") else []
    return list(dict.fromkeys(int(p) for p in raw if p))


def _select_rules(conn: sqlite3.Connection, where: str, params: Iterable) -> List[Dict]:
    rows = conn.execute(
        f"""
        SELECT g.*
          FROM gift_rules g
         WHERE {where}
         ORDER BY g.active DESC, g.created_at DESC, g.id DESC
        """,
        list(params),
    ).fetchall()
    rules = [dict(r) for r in rows]
    products = _rule_products(conn, [int(r["id"]) for r in rules])
    gift_products = _rule_gift_products(conn, [int(r["id"]) for r in rules])
    gift_items = _rule_gift_items(conn, [int(r["id"]) for r in rules])
    for rule in rules:
        rule["products"] = products.get(int(rule["id"]), [])
        rule["product_ids"] = [p["product_id"] for p in rule["products"]]
        rule["rule_label"] = GIFT_RULE_LABELS.get(rule.get("rule_type") or "", "")
        rule["max_per_order"] = int(rule["max_per_order"]) if rule.get("max_per_order") else None
        rule["min_value"] = float(rule["min_value"]) if rule.get("min_value") is not None else None
        rule["gift_items"] = gift_items.get(int(rule["id"]), [])
        rule["gift_item_ids"] = [int(i["id"]) for i in rule["gift_items"]]
        # Campos de um avulso só (leitores antigos): o primeiro da lista.
        rule["gift_item"] = rule["gift_items"][0] if rule["gift_items"] else None
        rule["gift_item_name"] = rule["gift_item"]["name"] if rule["gift_item"] else None
        rule["gift_products"] = gift_products.get(int(rule["id"]), [])
        rule["gift_product_ids"] = [p["product_id"] for p in rule["gift_products"]]
        _describe_gifts(rule)
    return rules


def _describe_gifts(rule: Dict) -> None:
    """``gifts``: o que a regra entrega (produtos primeiro, depois os avulsos).

    Também preenche os campos resumidos de antes (``gift_name``, ``gift_sku``,
    ``gift_image`` do 1º produto, ``gift_kind`` = produto/avulso/ambos) para a
    lista e os textos.
    """
    gifts: List[Dict] = [
        {
            "kind": "produto",
            "product_id": p["product_id"],
            "name": p["name"],
            "sku": p["sku"],
            "image": p["image"],
            "available": None,
        }
        for p in rule.get("gift_products") or []
    ]
    for item in rule.get("gift_items") or []:
        gifts.append({
            "kind": "avulso",
            "gift_item_id": int(item["id"]),
            "name": item.get("name") or "Brinde avulso",
            "sku": "",
            "image": "",
            "available": item.get("available"),
        })
    rule["gifts"] = gifts
    rule["gift_name"] = " + ".join(g["name"] for g in gifts)
    first = (rule.get("gift_products") or [{}])[0]
    rule["gift_sku"] = first.get("sku") or ""
    rule["gift_image"] = first.get("image") or ""
    kinds = {g["kind"] for g in gifts}
    rule["gift_kind"] = "ambos" if len(kinds) > 1 else (kinds.pop() if kinds else "")


def list_gift_rules_for_event(event_id: int) -> List[Dict]:
    """Todas as regras de brinde do evento (ativas primeiro)."""
    with get_conn() as conn:
        return _select_rules(conn, "g.event_id = ?", (int(event_id),))


def get_gift_rule(rule_id: int) -> Optional[Dict]:
    with get_conn() as conn:
        rules = _select_rules(conn, "g.id = ?", (int(rule_id),))
    return rules[0] if rules else None


def active_gift_rules_for_cart(event_id: int) -> List[Dict]:
    """Regras ativas no formato que o carrinho (promo-pricing.js) usa.

    Traz os dados de cada brinde (nome, imagem, preço e saldo) para o carrinho
    montar as linhas sem consultar o catálogo. Produto de brinde que saiu do
    evento não é entregue; regra sem nenhum brinde entregável fica de fora.
    """
    with get_conn() as conn:
        rules = _select_rules(conn, "g.event_id = ? AND g.active = 1", (int(event_id),))
        if not rules:
            return []
        gift_ids = {pid for r in rules for pid in r["gift_product_ids"]}
        info = {}
        if gift_ids:
            placeholders = ",".join("?" * len(gift_ids))
            info = {
                int(r["id"]): r
                for r in conn.execute(
                    f"""
                    SELECT p.id, p.name, p.sku, p.category, p.image,
                           COALESCE(ep.price, p.price) AS price,
                           ep.stock, ep.backorder_limit
                      FROM products p
                      JOIN event_products ep ON ep.product_id = p.id AND ep.event_id = ?
                     WHERE p.id IN ({placeholders})
                    """,
                    [int(event_id), *gift_ids],
                ).fetchall()
            }
        covered = {int(r["id"]): _covered_product_ids(conn, r) for r in rules}
    out: List[Dict] = []
    for rule in rules:
        if not rule["product_ids"] and rule["rule_type"] != "min_total":
            continue
        brindes: List[Dict] = []
        for gid in rule["gift_product_ids"]:
            gift = info.get(gid)
            if gift is None:  # produto que saiu do evento não é entregue
                continue
            brindes.append({
                "id": int(gift["id"]),
                "avulso": False,
                "nome": gift["name"],
                "sku": gift["sku"] or "",
                "categoria": gift["category"] or "",
                "imagem": gift["image"] or "",
                "preco": float(gift["price"] or 0),
                "estoque": int(gift["stock"] or 0),
                "backorder_limit": int(gift["backorder_limit"]) if gift["backorder_limit"] is not None else -1,
            })
        for item in rule["gift_items"]:
            brindes.append({
                "id": f"{AVULSO_ID_PREFIX}{int(item['id'])}",
                "avulso": True,
                "gift_item_id": int(item["id"]),
                "nome": item["name"],
                "sku": "",
                "categoria": "Brinde",
                "imagem": "",
                "preco": 0.0,
                "estoque": item["available"],  # None = sem limite
                "backorder_limit": -1,
            })
        if not brindes:
            continue
        out.append({
            "id": int(rule["id"]),
            "nome": rule["name"],
            "tipo": rule["rule_type"],
            "min_qty": int(rule["min_qty"] or 1),
            "min_value": rule["min_value"],
            "gift_qty": int(rule["gift_qty"]),
            "max_per_order": rule["max_per_order"],
            "product_ids": [str(pid) for pid in covered[int(rule["id"])]],
            "brindes": brindes,
            "brinde": brindes[0],  # leitores antigos
        })
    return out


# ---------------------------------------------------------------------------
# Regras: cadastro
# ---------------------------------------------------------------------------

def _validate(
    name: str,
    rule_type: str,
    min_qty: int,
    min_value: Optional[float],
    gift_product_ids: List[int],
    gift_item_ids: List[int],
    gift_qty: int,
    max_per_order: Optional[int],
    product_ids: List[int],
) -> str:
    name_s = (name or "").strip()
    if not name_s:
        raise ValueError("Informe um nome para o brinde.")
    if rule_type not in GIFT_RULE_TYPES:
        raise ValueError(f"Tipo de regra inválido: {rule_type}")
    if not gift_product_ids and not gift_item_ids:
        raise ValueError("Escolha o brinde: produtos do estoque, brindes avulsos ou os dois.")
    if int(gift_qty or 0) < 1:
        raise ValueError("A quantidade do brinde deve ser pelo menos 1.")
    if rule_type == "min_total":
        if min_value is None or float(min_value) <= 0:
            raise ValueError("Informe o valor mínimo do pedido.")
    else:
        if int(min_qty or 0) < 1:
            raise ValueError("A quantidade da regra deve ser pelo menos 1.")
        if rule_type == "kit" and int(min_qty) < 2:
            raise ValueError("O kit precisa de pelo menos 2 unidades.")
        if not product_ids:
            raise ValueError("Selecione ao menos um produto participante.")
    if max_per_order is not None and int(max_per_order) < 1:
        raise ValueError("O limite por pedido deve ser pelo menos 1 (ou deixe em branco).")
    return name_s


def _check_gift_source(
    conn: sqlite3.Connection,
    event_id: int,
    gift_product_ids: List[int],
    gift_item_ids: List[int],
) -> None:
    if conn.execute("SELECT 1 FROM events WHERE id = ?", (int(event_id),)).fetchone() is None:
        raise ValueError("Evento não encontrado.")
    for iid in gift_item_ids:
        row = conn.execute("SELECT event_id FROM gift_items WHERE id = ?", (int(iid),)).fetchone()
        if row is None or int(row["event_id"]) != int(event_id):
            raise ValueError("Brinde avulso não encontrado neste evento.")
    for pid in gift_product_ids:
        in_event = conn.execute(
            "SELECT 1 FROM event_products WHERE event_id = ? AND product_id = ?",
            (int(event_id), int(pid)),
        ).fetchone()
        if in_event is None:
            name = conn.execute("SELECT name FROM products WHERE id = ?", (int(pid),)).fetchone()
            raise ValueError(
                f"O produto de brinde «{name['name'] if name else pid}» precisa estar no estoque deste evento."
            )


def _sync_products(conn: sqlite3.Connection, rule_id: int, product_ids: List[int]) -> None:
    conn.execute("DELETE FROM gift_rule_products WHERE gift_rule_id = ?", (int(rule_id),))
    unique_ids = list(dict.fromkeys(int(p) for p in product_ids if p))
    conn.executemany(
        "INSERT OR IGNORE INTO gift_rule_products (gift_rule_id, product_id) VALUES (?, ?)",
        [(int(rule_id), pid) for pid in unique_ids],
    )


def _normalize_rule_args(rule_type: str, args: Dict) -> Dict:
    """Zera os campos que não se aplicam à regra escolhida."""
    out = dict(args)
    if rule_type != "kit":
        out["max_per_order"] = None  # "a partir de" e "valor" dão o brinde uma vez só
    if rule_type == "min_total":
        out["min_qty"] = 1
    else:
        out["min_value"] = None
    # Produtos do brinde: lista; a coluna gift_product_id guarda o primeiro.
    out["gift_product_ids"] = _gift_product_ids(out)
    out["gift_product_id"] = out["gift_product_ids"][0] if out["gift_product_ids"] else None
    # Brindes avulsos: lista; a coluna gift_item_id guarda o primeiro.
    out["gift_item_ids"] = _gift_item_ids(out)
    out["gift_item_id"] = out["gift_item_ids"][0] if out["gift_item_ids"] else None
    return out


def _resolve_new_item(conn: sqlite3.Connection, event_id: int, args: Dict) -> Dict:
    """Cria o brinde avulso informado no próprio formulário da regra, se houver,
    e o acrescenta aos avulsos escolhidos."""
    new_item = args.pop("new_item", None)
    if new_item:
        new_id = create_gift_item_in_conn(
            conn, event_id, new_item.get("name") or "", new_item.get("stock"),
        )
        args["gift_item_ids"] = [*_gift_item_ids(args), new_id]
    return args


def create_gift_rule(event_id: int, name: str, rule_type: str, **kwargs) -> Dict:
    """Cria uma regra de brinde ativa.

    ``kwargs``: ``min_qty``, ``min_value``, ``gift_product_ids`` (um ou mais
    produtos do estoque; aceita também ``gift_product_id``) e/ou
    ``gift_item_ids`` (um ou mais brindes avulsos; aceita também
    ``gift_item_id``; ``new_item={'name', 'stock'}`` cadastra mais um avulso
    junto), ``gift_qty``, ``max_per_order``, ``product_ids``.
    """
    now = _now_iso()
    with get_conn() as conn:
        args = _resolve_new_item(conn, event_id, dict(kwargs))
        args = _normalize_rule_args(rule_type, args)
        name_s = _validate(
            name, rule_type, args.get("min_qty") or 0, args.get("min_value"),
            args["gift_product_ids"], args["gift_item_ids"],
            args.get("gift_qty") or 0, args.get("max_per_order"), args.get("product_ids") or [],
        )
        _check_gift_source(conn, event_id, args["gift_product_ids"], args["gift_item_ids"])
        cur = conn.execute(
            """
            INSERT INTO gift_rules
                (event_id, name, rule_type, min_qty, min_value, gift_product_id, gift_item_id,
                 gift_qty, max_per_order, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (int(event_id), name_s, rule_type, int(args.get("min_qty") or 1), args.get("min_value"),
             args.get("gift_product_id"), args.get("gift_item_id"), int(args["gift_qty"]),
             args.get("max_per_order"), now, now),
        )
        rule_id = int(cur.lastrowid)
        _sync_products(conn, rule_id, args.get("product_ids") or [])
        _sync_gift_products(conn, rule_id, args["gift_product_ids"])
        _sync_gift_items(conn, rule_id, args["gift_item_ids"])
        return _select_rules(conn, "g.id = ?", (rule_id,))[0]


def update_gift_rule(rule_id: int, name: str, rule_type: str, **kwargs) -> Dict:
    """Atualiza nome, regra, brinde e produtos. Não mexe no ativo/inativo."""
    with get_conn() as conn:
        row = conn.execute("SELECT event_id FROM gift_rules WHERE id = ?", (int(rule_id),)).fetchone()
        if row is None:
            raise ValueError("Brinde não encontrado.")
        event_id = int(row["event_id"])
        args = _resolve_new_item(conn, event_id, dict(kwargs))
        args = _normalize_rule_args(rule_type, args)
        name_s = _validate(
            name, rule_type, args.get("min_qty") or 0, args.get("min_value"),
            args["gift_product_ids"], args["gift_item_ids"],
            args.get("gift_qty") or 0, args.get("max_per_order"), args.get("product_ids") or [],
        )
        _check_gift_source(conn, event_id, args["gift_product_ids"], args["gift_item_ids"])
        conn.execute(
            """
            UPDATE gift_rules
               SET name = ?, rule_type = ?, min_qty = ?, min_value = ?, gift_product_id = ?,
                   gift_item_id = ?, gift_qty = ?, max_per_order = ?, updated_at = ?
             WHERE id = ?
            """,
            (name_s, rule_type, int(args.get("min_qty") or 1), args.get("min_value"),
             args.get("gift_product_id"), args.get("gift_item_id"), int(args["gift_qty"]),
             args.get("max_per_order"), _now_iso(), int(rule_id)),
        )
        _sync_products(conn, int(rule_id), args.get("product_ids") or [])
        _sync_gift_products(conn, int(rule_id), args["gift_product_ids"])
        _sync_gift_items(conn, int(rule_id), args["gift_item_ids"])
        return _select_rules(conn, "g.id = ?", (int(rule_id),))[0]


def toggle_gift_rule_active(rule_id: int) -> Dict:
    with get_conn() as conn:
        row = conn.execute("SELECT active FROM gift_rules WHERE id = ?", (int(rule_id),)).fetchone()
        if row is None:
            raise ValueError("Brinde não encontrado.")
        conn.execute(
            "UPDATE gift_rules SET active = ?, updated_at = ? WHERE id = ?",
            (0 if int(row["active"]) else 1, _now_iso(), int(rule_id)),
        )
        return _select_rules(conn, "g.id = ?", (int(rule_id),))[0]


def delete_gift_rule(rule_id: int) -> None:
    """Exclui a regra. Vendas antigas mantêm a linha do brinde (``gift_rule_id`` fica órfão)."""
    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM gift_rules WHERE id = ?", (int(rule_id),)).fetchone() is None:
            raise ValueError("Brinde não encontrado.")
        conn.execute("DELETE FROM gift_rules WHERE id = ?", (int(rule_id),))


# ---------------------------------------------------------------------------
# Aplicação no pedido
# ---------------------------------------------------------------------------

def _covered_product_ids(conn: sqlite3.Connection, rule: Dict) -> List[int]:
    """Participantes + o produto-pai de cada variante-filha entre eles.

    Como em "A partir de"/"Kit" das promoções, qualquer unidade do grupo de
    variantes conta: o produto-pai também é comprável no catálogo.
    """
    from .promotions import _variant_parent_by_child  # evita import circular

    pids = [int(p) for p in (rule.get("product_ids") or [])]
    parents = _variant_parent_by_child(conn, pids)
    covered = list(pids)
    for pid in pids:
        parent = parents.get(pid)
        if parent is not None and parent not in covered:
            covered.append(parent)
    return covered


def gift_units_for_rule(
    rule: Dict,
    qty_by_pid: Dict[int, int],
    covered: Iterable[int],
    value_by_pid: Optional[Dict[int, float]] = None,
    order_value: float = 0.0,
) -> int:
    """Quantas unidades de brinde a regra dá para este pedido.

    ``value_by_pid``/``order_value``: valor pago por produto e do pedido todo
    (para ``min_total``). Espelhada em ``giftUnitsForRule`` (static/js/promo-pricing.js).
    """
    gift_q = max(1, int(rule.get("gift_qty") or 1))
    covered = [int(p) for p in covered]
    rule_type = rule.get("rule_type")
    if rule_type == "min_total":
        target = float(rule.get("min_value") or 0)
        if target <= 0:
            return 0
        value = (
            sum((value_by_pid or {}).get(pid, 0.0) for pid in set(covered))
            if covered else float(order_value)
        )
        return gift_q if value >= target - 0.004 else 0
    min_q = max(1, int(rule.get("min_qty") or 1))
    if rule_type == "kit":
        kits = sum(qty_by_pid.get(pid, 0) // max(2, min_q) for pid in set(covered))
        units = kits * gift_q
        limit = rule.get("max_per_order")
        if limit:
            units = min(units, int(limit))
        return max(0, units)
    total = sum(qty_by_pid.get(pid, 0) for pid in set(covered))
    return gift_q if total >= min_q else 0


def is_gift_line(item: Dict) -> bool:
    return bool(item.get("brinde") or item.get("gift_rule_id"))


def apply_gift_rules_in_conn(
    conn: sqlite3.Connection,
    event_id: int,
    items: List[Dict],
    *,
    order_value: Optional[float] = None,
) -> List[Dict]:
    """Acrescenta a ``items`` (já precificados) as linhas de brinde do pedido.

    Linhas de brinde que vierem em ``items`` são descartadas e recalculadas: o
    servidor decide o brinde, nunca o navegador. Só contam as linhas pagas
    (linhas grátis do "compre e leve" não somam para o brinde).

    ``order_value``: valor efetivamente cobrado, quando o vendedor deu desconto
    manual. As regras por valor usam esse valor (com participantes, o desconto
    é rateado pelo peso de cada item).
    """
    paid = [it for it in items if not is_gift_line(it)]
    rows = conn.execute(
        """
        SELECT g.id, g.name, g.rule_type, g.min_qty, g.min_value, g.gift_product_id,
               g.gift_item_id, g.gift_qty, g.max_per_order
          FROM gift_rules g
         WHERE g.event_id = ? AND g.active = 1
         ORDER BY g.id
        """,
        (int(event_id),),
    ).fetchall()
    if not rows:
        return paid

    qty_by_pid: Dict[int, int] = defaultdict(int)
    value_by_pid: Dict[int, float] = defaultdict(float)
    paid_value = 0.0
    for it in paid:
        if it.get("bogo_auto_free") or it.get("product_id") is None:
            continue
        pid = int(it["product_id"])
        qty_by_pid[pid] += max(0, int(it.get("quantity") or 0))
        sub = float(it.get("subtotal") or 0)
        value_by_pid[pid] += sub
        paid_value += sub
    if not qty_by_pid:
        return paid
    paid_value = round(paid_value, 2)
    if order_value is not None and paid_value > 0 and order_value < paid_value:
        factor = max(0.0, float(order_value)) / paid_value
        value_by_pid = defaultdict(float, {k: v * factor for k, v in value_by_pid.items()})
        paid_value = round(float(order_value), 2)

    rules = [dict(r) for r in rows]
    products = _rule_products(conn, [int(r["id"]) for r in rules])
    gift_products = _rule_gift_products(conn, [int(r["id"]) for r in rules])
    gift_items = _rule_gift_items(conn, [int(r["id"]) for r in rules])
    gifts: List[tuple] = []  # (regra, unidades)
    for rule in rules:
        rule["product_ids"] = [p["product_id"] for p in products.get(int(rule["id"]), [])]
        rule["gift_product_ids"] = [p["product_id"] for p in gift_products.get(int(rule["id"]), [])]
        rule["gift_items"] = gift_items.get(int(rule["id"]), [])
        if not rule["product_ids"] and rule["rule_type"] != "min_total":
            continue
        covered = _covered_product_ids(conn, rule)
        units = gift_units_for_rule(rule, qty_by_pid, covered, value_by_pid, paid_value)
        if units > 0:
            gifts.append((rule, units))
    if not gifts:
        return paid

    gift_ids = {pid for rule, _ in gifts for pid in rule["gift_product_ids"]}
    info = {}
    if gift_ids:
        placeholders = ",".join("?" * len(gift_ids))
        info = {
            int(r["id"]): r
            for r in conn.execute(
                f"""
                SELECT p.id, p.name, p.sku, p.category,
                       COALESCE(ep.price, p.price) AS price,
                       ep.stock, ep.backorder_limit
                  FROM products p
                  JOIN event_products ep ON ep.product_id = p.id AND ep.event_id = ?
                 WHERE p.id IN ({placeholders})
                """,
                [int(event_id), *gift_ids],
            ).fetchall()
        }

    # Saldo que sobra para brinde: estoque menos o que o pedido já leva pago.
    used: Dict[int, int] = defaultdict(int)
    for it in paid:
        if it.get("product_id") is not None:
            used[int(it["product_id"])] += max(0, int(it.get("quantity") or 0))
    used_avulso: Dict[int, int] = defaultdict(int)

    out = list(paid)
    for rule, units in gifts:
        base = {
            "unit_price": 0.0,
            "subtotal": 0.0,
            "promotion_id": None,
            "promo_covered_qty": 0,
            "promo_plan": [],
            # ``bogo_auto_free`` reaproveita o tratamento de linha grátis
            # (fora da cotação de preço, não removível no carrinho).
            "bogo_auto_free": True,
            "brinde": True,
            "gift_rule_id": int(rule["id"]),
            "gift_rule_name": str(rule["name"] or ""),
        }
        # A regra entrega todos os seus brindes (produtos e avulsos), cada um
        # com o próprio saldo: faltar um não impede os outros.
        for gid in rule["gift_product_ids"]:
            gift = info.get(gid)
            if gift is None:  # produto fora do evento não é entregue
                continue
            p_units = units
            limit = int(gift["backorder_limit"]) if gift["backorder_limit"] is not None else -1
            if limit >= 0:
                p_units = min(p_units, max(0, int(gift["stock"] or 0) - used[gid]))
            if p_units <= 0:
                continue
            used[gid] += p_units
            out.append({
                **base,
                "product_id": gid,
                "product_id_str": str(gid),
                "product_name": str(gift["name"] or "Brinde"),
                "product_sku": gift["sku"],
                "category": gift["category"],
                "original_price": float(gift["price"] or 0),
                "quantity": p_units,
            })
        for item in rule["gift_items"]:
            iid = int(item["id"])
            a_units = units
            if item["available"] is not None:
                a_units = min(a_units, max(0, item["available"] - used_avulso[iid]))
            if a_units > 0:
                used_avulso[iid] += a_units
                out.append({
                    **base,
                    "product_id": None,
                    "product_id_str": None,
                    "product_name": str(item["name"]),
                    "product_sku": None,
                    "category": "Brinde",
                    "original_price": 0.0,
                    "quantity": a_units,
                    "gift_item_id": iid,
                    "avulso": True,
                })
    return out


def reapply_gifts_for_charged_total(
    conn: sqlite3.Connection,
    event_id: int,
    items: List[Dict],
    charged_total: float,
) -> List[Dict]:
    """Recalcula os brindes quando o vendedor cobrou menos que o valor dos itens.

    Só as regras por valor dependem disso; sem desconto manual, devolve ``items``.
    """
    paid_value = round(sum(float(i.get("subtotal") or 0) for i in items if not is_gift_line(i)), 2)
    if charged_total >= paid_value - 0.004:
        return items
    return apply_gift_rules_in_conn(conn, event_id, items, order_value=charged_total)
