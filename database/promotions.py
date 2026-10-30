"""Promoções por evento (descontos e regras de precificação em produtos selecionados)."""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Dict, List, Optional

from .connection import _now_iso, get_conn
from .gifts import AVULSO_ID_PREFIX, apply_gift_rules_in_conn, is_gift_line

_VALID_RULE_TYPES = {"percent", "fixed", "bogo", "min_bundle", "exact_bundle", "combo_bundle"}

RULE_TYPE_LABELS = {
    "percent": "Desconto (%)",
    "fixed": "Desconto fixo (R$)",
    "bogo": "Compre X, Leve Y",
    "min_bundle": "A partir de N un. (preço por unidade)",
    "exact_bundle": "Na compra de (pacote exato)",
    "combo_bundle": "Combo de itens (combinação)",
}


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _pack_groups_and_extra(qty: int, pack_qty: int) -> tuple[int, int]:
    """Quantos pacotes completos e quantas unidades avulsas.

    Ex.: pack_qty=5, qty=6 → (1 pacote, 1 avulsa). qty=10 → (2, 0).
    """
    pack = max(2, int(pack_qty))
    q = max(0, int(qty))
    return q // pack, q % pack


def _pack_subtotal(qty: int, pack_qty: int, pack_total: float, list_price: float) -> float:
    """Pacotes completos pelo valor da promoção; o resto pelo preço de lista.

    Ex.: 5 un. a R$ 15 (lista R$ 75) com kit de 5 por R$ 50 → R$ 50.
    6 un. → R$ 50 + 1×R$ 15 = R$ 65. 10 un. → 2 kits = R$ 100.
    """
    groups, extra = _pack_groups_and_extra(qty, pack_qty)
    return round(groups * max(0.0, float(pack_total)) + extra * float(list_price), 2)


def _promo_rule_applies(rule_type: str, min_qty: int, free_qty: int, qty: int) -> bool:
    """A regra da promoção se cumpre nesta quantidade?

    Só olha a regra (quantidade/tipo), nunca o preço: uma promoção vale mesmo
    quando o valor do pacote não fica abaixo do preço de lista.
    """
    if qty <= 0:
        return False
    if rule_type in ("percent", "fixed"):
        return True
    if rule_type == "bogo":
        return int(free_qty) > 0 and qty >= max(1, int(min_qty))
    if rule_type in ("min_bundle", "exact_bundle"):
        return qty >= max(2, int(min_qty))
    return False


def _compute_effective_subtotal(
    rule_type: str,
    rule_value: float,
    min_qty: int,
    free_qty: int,
    list_price: float,
    qty: int,
) -> float:
    """Calcula o subtotal efetivo (o valor que o cliente paga) para um item com uma promo.

    O valor do pacote prevalece mesmo quando não gera economia: um kit de 5 por
    R$ 349,50 vale para 5 un. de um item de R$ 69,90, ainda que o preço de lista
    dê o mesmo total.
    """
    if rule_type == "percent":
        pct = max(0.0, min(100.0, float(rule_value)))
        return round(list_price * qty * (1.0 - pct / 100.0), 2)
    if rule_type == "fixed":
        discount = max(0.0, float(rule_value))
        return round(max(0.0, list_price - discount) * qty, 2)
    if rule_type == "bogo":
        min_q = max(1, int(min_qty))
        free_q = max(0, int(free_qty))
        if free_q == 0:
            return round(list_price * qty, 2)
        group = min_q + free_q
        groups = qty // group
        rem = qty % group
        paid = groups * min_q + min(rem, min_q)
        return round(list_price * paid, 2)
    if rule_type == "min_bundle":
        # "A partir de min_qty un.": ``rule_value`` é o preço de CADA unidade.
        # O mínimo conta a soma de todos os produtos da promoção no pedido, então
        # quem chama já conferiu que ele foi atingido (``_min_bundle_totals``).
        # Nunca encarece: produto mais barato que o preço da promoção fica como está.
        unit = min(float(list_price), max(0.0, float(rule_value)))
        return round(unit * qty, 2)
    if rule_type == "exact_bundle":
        # "Na compra de min_qty": só pacotes completos usam o valor da promoção.
        # Unidades além do múltiplo (ex.: 6ª de um kit de 5) pagam preço de lista.
        min_q = max(2, int(min_qty))
        groups, _extra = _pack_groups_and_extra(qty, min_q)
        if groups <= 0:
            return round(list_price * qty, 2)
        return _pack_subtotal(qty, min_q, rule_value, list_price)
    return round(list_price * qty, 2)


#: Promoções de pacote (grupos completos pelo valor do kit). "A partir de N un."
#: não é pacote: atingido o mínimo, toda unidade sai pelo preço da promoção.
_PACK_RULE_TYPES = ("exact_bundle",)


def _min_bundle_totals(
    promo_defs: Dict[int, dict],
    promo_pids: Dict[int, set],
    items: List[Dict],
) -> Dict[int, int]:
    """Unidades pagas de cada promoção "A partir de N un." somando todos os
    produtos dela no pedido (cores/variantes diferentes contam juntas)."""
    totals: Dict[int, int] = {}
    for promo_id, promo in promo_defs.items():
        if promo["rule_type"] != "min_bundle":
            continue
        pids = promo_pids.get(promo_id) or set()
        totals[promo_id] = sum(
            int(it.get("quantity") or 0)
            for it in items
            if it.get("product_id") is not None
            and int(it["product_id"]) in pids
            and not _is_bogo_gift_line(it)
        )
    return totals


def _pack_options_from_promos(promos: List[dict]) -> List[tuple]:
    """``(qtd do pacote, valor do pacote, promo)`` de cada promoção de pacote."""
    options: List[tuple] = []
    for promo in promos:
        if promo.get("rule_type") not in _PACK_RULE_TYPES:
            continue
        options.append((
            max(2, int(promo.get("min_qty") or 2)),
            max(0.0, float(promo.get("rule_value") or 0.0)),
            promo,
        ))
    return options


def _plan_beats(candidate: tuple, current: Optional[tuple]) -> bool:
    """Cobrir mais unidades vence; o total menor só desempata.

    ``candidate``/``current`` começam com ``(unidades_cobertas, total, ...)``.
    """
    if current is None:
        return True
    if candidate[0] != current[0]:
        return candidate[0] > current[0]
    return candidate[1] < current[1] - 0.0001


def _best_pack_plan(
    pack_options: List[tuple],
    list_price: float,
    qty: int,
) -> Optional[tuple]:
    """Melhor combinação de pacotes para ``qty`` unidades do mesmo produto.

    Cada promoção entra pela sua própria regra — pacotes completos pelo valor do
    pacote, sobra a preço de lista — e vence a combinação que cobre mais
    unidades; o total menor só desempata. Ex.: 5 un. de um item de R$ 69,90 com
    kits de 3/R$ 190,00 e 5/R$ 349,50 → 1 kit de 5 (cobre as 5 un.), e não
    1 kit de 3 + 2 avulsas, que sai mais barato mas deixa 2 un. fora da regra.
    Com 8 un., os dois kits se somam: 5 + 3 = R$ 539,50.

    Retorna ``(cobertas, total, {promo_id: nº de pacotes})`` ou ``None`` quando
    nenhum pacote cabe na quantidade.
    """
    qty = max(0, int(qty))
    if qty <= 0 or not pack_options:
        return None
    unit = float(list_price)
    # dp[n]: melhor plano para n unidades → (cobertas, total, pacotes usados)
    dp: List[tuple] = [(0, 0.0, ())]
    for n in range(1, qty + 1):
        prev = dp[n - 1]
        best = (prev[0], round(prev[1] + unit, 2), prev[2])
        for pack_qty, pack_total, promo in pack_options:
            if pack_qty > n:
                continue
            base = dp[n - pack_qty]
            cand = (
                base[0] + pack_qty,
                round(base[1] + pack_total, 2),
                base[2] + (int(promo["id"]),),
            )
            if _plan_beats(cand, best):
                best = cand
        dp.append(best)
    covered, total, used = dp[qty]
    if not used:
        return None
    counts: Dict[int, int] = {}
    for promo_id in used:
        counts[promo_id] = counts.get(promo_id, 0) + 1
    return covered, round(total, 2), counts


def _pack_plan_details(counts: Dict[int, int], pack_options: List[tuple]) -> tuple:
    """``(promoção dominante, linhas do plano)`` para o plano escolhido.

    A dominante é a que cobre mais unidades: a linha da venda guarda uma única
    promoção em ``promotion_id``, mesmo quando o plano soma kits diferentes.
    """
    by_id = {
        int(promo["id"]): (pack_qty, pack_total)
        for pack_qty, pack_total, promo in pack_options
    }
    rows: List[Dict] = []
    for promo_id, groups in counts.items():
        pack_qty, pack_total = by_id[promo_id]
        rows.append({
            "promotion_id": promo_id,
            "min_qty": pack_qty,
            "rule_value": pack_total,
            "groups": groups,
        })
    rows.sort(key=lambda r: (-r["min_qty"], r["promotion_id"]))
    dominant = max(rows, key=lambda r: (r["min_qty"] * r["groups"], r["min_qty"]))
    return dominant["promotion_id"], rows


def _int_or_none(value) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n else None


# Tipos de regra onde "qualquer unidade destes produtos conta" — faz sentido
# estender ao produto-pai de uma variante vinculada. ``bogo`` (par comprar/levar
# específico) e ``combo_bundle`` (produtos diferentes, não variantes de um
# mesmo item) têm semântica própria de SKU exato e ficam de fora.
_PARENT_INHERIT_RULE_TYPES = {"percent", "fixed", "min_bundle", "exact_bundle"}


def _variant_parent_by_child(conn: sqlite3.Connection, product_ids) -> Dict[int, int]:
    """``{product_id da variante-filha: id do produto-pai}`` via ``family_id``.

    O produto-pai (a SKU "cabeça" de um grupo de Variantes) é ele mesmo um item
    comprável do catálogo — uma promoção vinculada só às variantes-filhas deve
    valer também para ele.
    """
    ids = {int(p) for p in product_ids if p}
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT id, family_id FROM products WHERE id IN ({placeholders})",
        list(ids),
    ).fetchall()
    out: Dict[int, int] = {}
    for r in rows:
        pid = int(r["id"])
        parent_id = _int_or_none(r["family_id"])
        if parent_id and parent_id != pid:
            out[pid] = parent_id
    return out


def _promo_covered_product_ids(conn: sqlite3.Connection, promo: Dict) -> List[int]:
    """IDs vinculados à promoção + o produto-pai de cada variante-filha entre eles."""
    pids = [int(p) for p in (promo.get("product_ids") or [])]
    if promo.get("rule_type") not in _PARENT_INHERIT_RULE_TYPES:
        return pids
    parent_by_child = _variant_parent_by_child(conn, pids)
    covered = list(pids)
    for pid in pids:
        parent_id = parent_by_child.get(pid)
        if parent_id is not None and parent_id not in covered:
            covered.append(parent_id)
    return covered


def _fetch_promo_products(conn: sqlite3.Connection, promo_id: int):
    return conn.execute(
        """
        SELECT pp.product_id, p.name, p.sku
          FROM promotion_products pp
          JOIN products p ON p.id = pp.product_id
         WHERE pp.promotion_id = ?
         ORDER BY p.name COLLATE NOCASE
        """,
        (int(promo_id),),
    ).fetchall()


def _sku_by_product_id(products: List) -> Dict[int, str]:
    out: Dict[int, str] = {}
    for r in products or []:
        d = dict(r) if not isinstance(r, dict) else r
        pid = int(d.get("product_id") or 0)
        if pid:
            out[pid] = str(d.get("sku") or "").strip()
    return out


def _normalize_bogo_product_ids(
    rule_type: str,
    product_ids: List[int],
    bogo_buy_product_id: Optional[int],
    bogo_free_product_id: Optional[int],
) -> tuple[Optional[int], Optional[int]]:
    if rule_type != "bogo":
        return None, None
    buy_id = _int_or_none(bogo_buy_product_id)
    free_id = _int_or_none(bogo_free_product_id)
    if not buy_id or not free_id:
        raise ValueError("Selecione o SKU a comprar e o SKU que o cliente leva grátis.")
    id_set = {int(p) for p in product_ids}
    if buy_id not in id_set:
        raise ValueError("O SKU a comprar deve estar na lista de produtos participantes.")
    if free_id not in id_set:
        raise ValueError("O SKU grátis deve estar na lista de produtos participantes.")
    return buy_id, free_id


def _rows_to_promo(rows, product_rows) -> Dict:
    """Converte row de promoção + linhas de produtos em dict rico."""
    promo = dict(rows)
    promo["rule_label"] = RULE_TYPE_LABELS.get(promo.get("rule_type", ""), "")
    promo["product_ids"] = [int(r["product_id"]) for r in product_rows]
    promo["products"] = [dict(r) for r in product_rows]
    promo["bogo_buy_product_id"] = _int_or_none(promo.get("bogo_buy_product_id"))
    promo["bogo_free_product_id"] = _int_or_none(promo.get("bogo_free_product_id"))
    sku_map = _sku_by_product_id(promo["products"])
    buy_id = promo["bogo_buy_product_id"]
    free_id = promo["bogo_free_product_id"]
    promo["bogo_buy_sku"] = sku_map.get(buy_id, "") if buy_id else ""
    promo["bogo_free_sku"] = sku_map.get(free_id, "") if free_id else ""
    return promo


# ---------------------------------------------------------------------------
# CRUD de promoções
# ---------------------------------------------------------------------------

def create_promotion(
    event_id: int,
    name: str,
    rule_type: str,
    *,
    rule_value: float = 0.0,
    min_qty: int = 1,
    free_qty: int = 0,
    product_ids: List[int],
    bogo_buy_product_id: Optional[int] = None,
    bogo_free_product_id: Optional[int] = None,
) -> Dict:
    """Cria uma promoção para o evento e associa os produtos. Retorna o dict criado."""
    name_s = (name or "").strip()
    if not name_s:
        raise ValueError("Informe um nome para a promoção.")
    if rule_type not in _VALID_RULE_TYPES:
        raise ValueError(f"Tipo de regra inválido: {rule_type}")
    if rule_type == "percent":
        if not (0 < rule_value <= 100):
            raise ValueError("O percentual de desconto deve ser entre 1 e 100.")
    elif rule_type == "fixed":
        if rule_value <= 0:
            raise ValueError("O desconto fixo deve ser maior que zero.")
    elif rule_type == "bogo":
        if min_qty < 1:
            raise ValueError("Quantidade mínima deve ser pelo menos 1.")
        if free_qty < 1:
            raise ValueError("Quantidade grátis deve ser pelo menos 1.")
    elif rule_type == "min_bundle":
        if min_qty < 2:
            raise ValueError("A quantidade mínima deve ser pelo menos 2.")
        if rule_value <= 0:
            raise ValueError("O preço por unidade deve ser maior que zero.")
    elif rule_type == "exact_bundle":
        if min_qty < 2:
            raise ValueError("A quantidade do pacote deve ser pelo menos 2.")
        if rule_value <= 0:
            raise ValueError("O valor do pacote deve ser maior que zero.")
    elif rule_type == "combo_bundle":
        if rule_value <= 0:
            raise ValueError("O valor do combo deve ser maior que zero.")
    if not product_ids:
        raise ValueError("Selecione ao menos um produto para a promoção.")
    if rule_type == "combo_bundle" and len(product_ids) < 2:
        raise ValueError("O combo precisa de pelo menos 2 produtos diferentes.")
    buy_id, free_id = _normalize_bogo_product_ids(
        rule_type, product_ids, bogo_buy_product_id, bogo_free_product_id,
    )

    now = _now_iso()
    with get_conn() as conn:
        ev = conn.execute("SELECT 1 FROM events WHERE id = ?", (int(event_id),)).fetchone()
        if ev is None:
            raise ValueError("Evento não encontrado.")
        cur = conn.execute(
            """
            INSERT INTO promotions
                (event_id, name, rule_type, rule_value, min_qty, free_qty,
                 bogo_buy_product_id, bogo_free_product_id, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (int(event_id), name_s, rule_type, float(rule_value),
             int(min_qty), int(free_qty), buy_id, free_id, now, now),
        )
        promo_id = int(cur.lastrowid)
        _sync_promo_products(conn, promo_id, product_ids)
        row = conn.execute("SELECT * FROM promotions WHERE id = ?", (promo_id,)).fetchone()
        p_rows = _fetch_promo_products(conn, promo_id)
    return _rows_to_promo(row, p_rows)


def update_promotion(
    promo_id: int,
    name: str,
    rule_type: str,
    *,
    rule_value: float,
    min_qty: int,
    free_qty: int,
    active: bool,
    product_ids: List[int],
    bogo_buy_product_id: Optional[int] = None,
    bogo_free_product_id: Optional[int] = None,
) -> Dict:
    """Atualiza nome, regra, produtos e status de uma promoção existente."""
    name_s = (name or "").strip()
    if not name_s:
        raise ValueError("Informe um nome para a promoção.")
    if rule_type not in _VALID_RULE_TYPES:
        raise ValueError(f"Tipo de regra inválido: {rule_type}")
    if rule_type == "percent":
        if not (0 < rule_value <= 100):
            raise ValueError("O percentual de desconto deve ser entre 1 e 100.")
    elif rule_type == "fixed":
        if rule_value <= 0:
            raise ValueError("O desconto fixo deve ser maior que zero.")
    elif rule_type == "bogo":
        if min_qty < 1:
            raise ValueError("Quantidade mínima deve ser pelo menos 1.")
        if free_qty < 1:
            raise ValueError("Quantidade grátis deve ser pelo menos 1.")
    elif rule_type == "min_bundle":
        if min_qty < 2:
            raise ValueError("A quantidade mínima deve ser pelo menos 2.")
        if rule_value <= 0:
            raise ValueError("O preço por unidade deve ser maior que zero.")
    elif rule_type == "exact_bundle":
        if min_qty < 2:
            raise ValueError("A quantidade do pacote deve ser pelo menos 2.")
        if rule_value <= 0:
            raise ValueError("O valor do pacote deve ser maior que zero.")
    elif rule_type == "combo_bundle":
        if rule_value <= 0:
            raise ValueError("O valor do combo deve ser maior que zero.")
    if not product_ids:
        raise ValueError("Selecione ao menos um produto para a promoção.")
    if rule_type == "combo_bundle" and len(product_ids) < 2:
        raise ValueError("O combo precisa de pelo menos 2 produtos diferentes.")
    buy_id, free_id = _normalize_bogo_product_ids(
        rule_type, product_ids, bogo_buy_product_id, bogo_free_product_id,
    )

    now = _now_iso()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM promotions WHERE id = ?", (int(promo_id),)).fetchone()
        if row is None:
            raise ValueError("Promoção não encontrada.")
        conn.execute(
            """
            UPDATE promotions
               SET name = ?, rule_type = ?, rule_value = ?, min_qty = ?,
                   free_qty = ?, bogo_buy_product_id = ?, bogo_free_product_id = ?,
                   active = ?, updated_at = ?
             WHERE id = ?
            """,
            (name_s, rule_type, float(rule_value), int(min_qty),
             int(free_qty), buy_id, free_id, 1 if active else 0, now, int(promo_id)),
        )
        _sync_promo_products(conn, int(promo_id), product_ids)
        row = conn.execute("SELECT * FROM promotions WHERE id = ?", (int(promo_id),)).fetchone()
        p_rows = _fetch_promo_products(conn, int(promo_id))
    return _rows_to_promo(row, p_rows)


def toggle_promotion_active(promo_id: int) -> Dict:
    """Inverte o campo ``active`` de uma promoção. Retorna o dict atualizado."""
    now = _now_iso()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM promotions WHERE id = ?", (int(promo_id),)).fetchone()
        if row is None:
            raise ValueError("Promoção não encontrada.")
        new_active = 0 if int(row["active"]) else 1
        conn.execute(
            "UPDATE promotions SET active = ?, updated_at = ? WHERE id = ?",
            (new_active, now, int(promo_id)),
        )
        row = conn.execute("SELECT * FROM promotions WHERE id = ?", (int(promo_id),)).fetchone()
        p_rows = _fetch_promo_products(conn, int(promo_id))
    return _rows_to_promo(row, p_rows)


def delete_promotion(promo_id: int) -> None:
    """Remove uma promoção e seus vínculos de produto (CASCADE cuida do FK)."""
    with get_conn() as conn:
        row = conn.execute("SELECT 1 FROM promotions WHERE id = ?", (int(promo_id),)).fetchone()
        if row is None:
            raise ValueError("Promoção não encontrada.")
        conn.execute("DELETE FROM promotions WHERE id = ?", (int(promo_id),))


def get_promotion(promo_id: int) -> Optional[Dict]:
    """Retorna uma promoção com a lista de produtos, ou None."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM promotions WHERE id = ?", (int(promo_id),)).fetchone()
        if row is None:
            return None
        p_rows = _fetch_promo_products(conn, int(promo_id))
    return _rows_to_promo(row, p_rows)


def list_promotions_for_event(event_id: int) -> List[Dict]:
    """Lista todas as promoções do evento com produtos associados."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM promotions WHERE event_id = ? ORDER BY active DESC, created_at DESC",
            (int(event_id),),
        ).fetchall()
        result = []
        for row in rows:
            pid = int(row["id"])
            p_rows = _fetch_promo_products(conn, pid)
            result.append(_rows_to_promo(row, p_rows))
    return result


def get_active_promotions_for_event(event_id: int) -> List[Dict]:
    """Promoções ativas do evento com lista de product_ids (usado no catálogo)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM promotions WHERE event_id = ? AND active = 1",
            (int(event_id),),
        ).fetchall()
        if not rows:
            return []
        promo_ids = [int(r["id"]) for r in rows]
        placeholders = ",".join("?" * len(promo_ids))
        product_rows = conn.execute(
            f"""
            SELECT pp.promotion_id, pp.product_id, p.name, p.sku
              FROM promotion_products pp
              JOIN products p ON p.id = pp.product_id
             WHERE pp.promotion_id IN ({placeholders})
             ORDER BY p.name COLLATE NOCASE
            """,
            promo_ids,
        ).fetchall()
        by_promo: Dict[int, List] = defaultdict(list)
        for r in product_rows:
            by_promo[int(r["promotion_id"])].append(r)

        result = []
        for row in rows:
            pid = int(row["id"])
            p_rows = by_promo.get(pid, [])
            d = dict(row)
            d["product_ids"] = [int(r["product_id"]) for r in p_rows]
            d["products"] = [dict(r) for r in p_rows]
            d["rule_label"] = RULE_TYPE_LABELS.get(d.get("rule_type", ""), "")
            d["bogo_buy_product_id"] = _int_or_none(d.get("bogo_buy_product_id"))
            d["bogo_free_product_id"] = _int_or_none(d.get("bogo_free_product_id"))
            sku_map = _sku_by_product_id(d["products"])
            d["bogo_buy_sku"] = sku_map.get(d["bogo_buy_product_id"], "") if d["bogo_buy_product_id"] else ""
            d["bogo_free_sku"] = sku_map.get(d["bogo_free_product_id"], "") if d["bogo_free_product_id"] else ""
            result.append(d)
    return result


def list_promotions_for_event_export(event_id: int) -> List[Dict]:
    """Promoções do evento com dados completos de cada produto (SKU, preço, categoria).

    Cada item de ``products`` traz ``product_id``, ``name``, ``sku``, ``category``,
    ``library_price`` e ``event_price`` (preço efetivo no evento).
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM promotions WHERE event_id = ? ORDER BY active DESC, created_at DESC",
            (int(event_id),),
        ).fetchall()
        result = []
        for row in rows:
            pid = int(row["id"])
            p_rows = conn.execute(
                """
                SELECT pp.product_id,
                       p.name,
                       p.sku,
                       p.category,
                       p.price                         AS library_price,
                       COALESCE(ep.price, p.price)     AS event_price
                  FROM promotion_products pp
                  JOIN products p ON p.id = pp.product_id
                  LEFT JOIN event_products ep
                    ON ep.product_id = pp.product_id AND ep.event_id = ?
                 WHERE pp.promotion_id = ?
                 ORDER BY p.name COLLATE NOCASE
                """,
                (int(event_id), pid),
            ).fetchall()
            promo = dict(row)
            promo["rule_label"] = RULE_TYPE_LABELS.get(promo.get("rule_type", ""), "")
            promo["products"] = [dict(r) for r in p_rows]
            result.append(promo)
    return result


def product_ids_with_active_promotions_for_event(event_id: int) -> set[int]:
    """Conjunto de ``product_id`` com pelo menos uma promoção **ativa** no evento.

    Inclui o produto-pai de uma variante-filha vinculada: ele também é um item
    comprável do catálogo.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT pp.product_id, pr.rule_type
              FROM promotions pr
              JOIN promotion_products pp ON pp.promotion_id = pr.id
             WHERE pr.event_id = ? AND pr.active = 1
            """,
            (int(event_id),),
        ).fetchall()
        pids = {int(r["product_id"]) for r in rows}
        inheritable = {int(r["product_id"]) for r in rows if r["rule_type"] in _PARENT_INHERIT_RULE_TYPES}
        parent_by_child = _variant_parent_by_child(conn, inheritable)
    return pids | set(parent_by_child.values())


def _format_promo_tooltip_line(promo: Dict) -> str:
    """Uma linha curta para tooltip/hover (nome + regra)."""
    name = (promo.get("name") or "").strip() or "Promoção"
    rt = promo.get("rule_type") or ""
    rv = float(promo.get("rule_value") or 0)
    min_q = max(1, int(promo.get("min_qty") or 1))
    free_q = max(0, int(promo.get("free_qty") or 0))
    if rt == "percent":
        pct = min(100.0, max(0.0, rv))
        pct_txt = str(int(pct)) if abs(pct - int(pct)) < 1e-9 else f"{pct:.1f}".replace(".", ",")
        return f"{name}: {pct_txt}% de desconto"
    if rt == "fixed":
        brv = f"{rv:.2f}".replace(".", ",")
        return f"{name}: R$ {brv} de desconto no preço unitário"
    if rt == "bogo":
        total = min_q + free_q if free_q > 0 else min_q
        buy_sku = (promo.get("bogo_buy_sku") or "").strip()
        free_sku = (promo.get("bogo_free_sku") or "").strip()
        if buy_sku and free_sku and buy_sku != free_sku:
            return (
                f"{name}: compre {min_q} un. do SKU {buy_sku}, "
                f"leve {free_q} un. do SKU {free_sku} grátis"
            )
        return f"{name}: compre {min_q}, leve {total}"
    if rt == "min_bundle":
        brv = f"{rv:.2f}".replace(".", ",")
        return f"{name}: a partir de {min_q} un. (somando os produtos da promoção), R$ {brv} cada"
    if rt == "exact_bundle":
        brv = f"{rv:.2f}".replace(".", ",")
        return f"{name}: kit de {min_q} un. por R$ {brv} (extras no preço normal)"
    if rt == "combo_bundle":
        brv = f"{rv:.2f}".replace(".", ",")
        return f"{name}: combo de itens por R$ {brv}"
    return name


def active_promotion_tooltip_by_product_id(event_id: int) -> Dict[int, str]:
    """Por produto, texto único listando todas as promoções ativas que o cobrem (separador · )."""
    promos = get_active_promotions_for_event(event_id)
    promos_sorted = sorted(promos, key=lambda p: int(p.get("id") or 0))
    lines_by_pid: Dict[int, List[str]] = defaultdict(list)
    with get_conn() as conn:
        for pr in promos_sorted:
            line = _format_promo_tooltip_line(pr)
            for pid in _promo_covered_product_ids(conn, pr):
                lines_by_pid[int(pid)].append(line)
    return {pid: " · ".join(lines) for pid, lines in lines_by_pid.items()}


def active_promotion_names_by_product_id(event_id: int) -> Dict[int, str]:
    """Por produto, nomes das promoções ativas que o cobrem (separador · )."""
    promos = get_active_promotions_for_event(event_id)
    promos_sorted = sorted(promos, key=lambda p: int(p.get("id") or 0))
    names_by_pid: Dict[int, List[str]] = defaultdict(list)
    with get_conn() as conn:
        for pr in promos_sorted:
            name = (pr.get("name") or "").strip() or "Promoção"
            for pid in _promo_covered_product_ids(conn, pr):
                names_by_pid[int(pid)].append(name)
    return {pid: " · ".join(names) for pid, names in names_by_pid.items()}


# ---------------------------------------------------------------------------
# Aplicação de promoções aos itens da transação
# ---------------------------------------------------------------------------

def _balance_rounding(shares: Dict[int, float], target: float) -> None:
    """Corrige, na maior linha, o centavo que o rateio perde ao arredondar."""
    diff = round(float(target) - round(sum(shares.values()), 2), 2)
    if abs(diff) < 0.005:
        return
    idx = max(shares, key=lambda k: shares[k])
    shares[idx] = round(max(0.0, shares[idx] + diff), 2)


def _line_subtotal(item: Dict) -> float:
    if item.get("subtotal") is not None:
        return float(item["subtotal"])
    qty = int(item.get("quantity") or 0)
    return round(
        float(item.get("original_price") or item.get("unit_price") or 0) * qty,
        2,
    )


def _apply_exact_bundle_cross_product(
    promo: dict,
    item_indices: List[int],
    result: List[Dict],
) -> None:
    """Aplica exact_bundle somando quantidades de todos os produtos da promoção.

    Distribui o desconto proporcionalmente entre os itens participantes.
    Ex.: kit de 3 por R$100, itens A(1 un. R$50) + B(1 un. R$40) + C(1 un. R$30)
    → subtotal original R$120, subtotal promo R$100, rateio proporcional.
    """
    pack_qty = max(2, int(promo["min_qty"]))
    pack_total = float(promo["rule_value"])
    promo_id = int(promo["id"])

    total_qty = sum(int(result[i].get("quantity") or 0) for i in item_indices)
    groups, extra = _pack_groups_and_extra(total_qty, pack_qty)
    if groups <= 0:
        return

    original_subtotal = round(
        sum(float(result[i].get("original_price") or result[i].get("unit_price") or 0)
            * int(result[i].get("quantity") or 0) for i in item_indices), 2,
    )
    if original_subtotal <= 0:
        return

    bundle_subtotal = round(groups * pack_total, 2)
    extra_remaining = extra

    extra_subtotal = 0.0
    item_extras: Dict[int, int] = {}
    for idx in reversed(item_indices):
        qty_i = int(result[idx].get("quantity") or 0)
        take = min(qty_i, extra_remaining)
        item_extras[idx] = take
        extra_remaining -= take
        list_p = float(result[idx].get("original_price") or result[idx].get("unit_price") or 0)
        extra_subtotal += round(take * list_p, 2)
        if extra_remaining <= 0:
            break

    promo_total = round(bundle_subtotal + extra_subtotal, 2)
    current_subtotal = round(sum(_line_subtotal(result[i]) for i in item_indices), 2)
    # Cobertura de PACOTE já aplicada (min_bundle/exact_bundle — inclusive de um
    # cross anterior). percent/fixed/bogo sempre "cobrem" 100% da própria
    # quantidade por definição, então não contam aqui: não é uma regra de
    # pacote sendo cumprida, não há "unidade deixada de fora" a proteger.
    current_pack_covered = sum(
        int(result[i].get("promo_covered_qty") or 0)
        for i in item_indices
        if result[i].get("promo_plan")
    )
    cross_covered = groups * pack_qty
    if cross_covered < current_pack_covered:
        # Já havia um pacote (individual ou cross anterior) cobrindo mais
        # unidades destes itens — não troca por um cruzamento que cobre menos.
        return
    already_promoted = any(result[i].get("promotion_id") is not None for i in item_indices)
    if already_promoted and promo_total >= current_subtotal - 0.001:
        # Já valia alguma promoção nestes itens (pacote ou não) — só troca se
        # o cruzamento sair mais barato. "Cobrir mais unidades" só dispensa
        # essa comparação de preço na primeira formação do pacote (nada
        # aplicado ainda): não deixa "cobrir mais" piorar o preço de um item
        # que já tinha uma promoção — de pacote ou não — mais vantajosa.
        return

    shares: Dict[int, float] = {}
    for idx in item_indices:
        qty_i = int(result[idx].get("quantity") or 0)
        if qty_i <= 0:
            continue
        list_p = float(result[idx].get("original_price") or result[idx].get("unit_price") or 0)
        item_original = round(list_p * qty_i, 2)
        share = item_original / original_subtotal if original_subtotal else 0
        shares[idx] = round(promo_total * share, 2)

    if shares:
        # O rateio arredondado não pode mudar o valor do kit: o resto de
        # centavos vai para a maior linha.
        _balance_rounding(shares, promo_total)

    for idx, item_promo_subtotal in shares.items():
        qty_i = int(result[idx].get("quantity") or 0)
        eff_unit = round(item_promo_subtotal / qty_i, 6) if qty_i else 0.0
        result[idx]["unit_price"] = eff_unit
        result[idx]["subtotal"] = item_promo_subtotal
        result[idx]["promotion_id"] = promo_id
        result[idx]["promo_covered_qty"] = max(0, qty_i - item_extras.get(idx, 0))
        result[idx]["promo_plan"] = []


def _apply_combo_bundle(
    promo: dict,
    item_indices: List[int],
    result: List[Dict],
    promo_pids: set,
) -> None:
    """Aplica combo_bundle: exige ao menos 1 un. de CADA produto vinculado à promoção.

    Número de combos = min(qtd de cada produto participante presente no carrinho).
    Cada combo completo custa rule_value; unidades excedentes pagam preço de lista.
    """
    combo_total = float(promo["rule_value"])
    promo_id = int(promo["id"])

    pids_in_cart = {int(result[i]["product_id"]) for i in item_indices}
    if not promo_pids.issubset(pids_in_cart):
        return

    qty_per_pid: Dict[int, List[int]] = {}
    for i in item_indices:
        pid = int(result[i]["product_id"])
        if pid in promo_pids:
            qty_per_pid.setdefault(pid, []).append(i)

    min_combos = min(
        sum(int(result[i].get("quantity") or 0) for i in idxs)
        for idxs in qty_per_pid.values()
    )
    if min_combos <= 0:
        return

    num_products = len(promo_pids)
    original_combo_subtotal = 0.0
    for i in item_indices:
        pid = int(result[i]["product_id"])
        if pid not in promo_pids:
            continue
        qty_i = int(result[i].get("quantity") or 0)
        lp = float(result[i].get("original_price") or result[i].get("unit_price") or 0)
        in_combo = min(qty_i, min_combos)
        original_combo_subtotal += round(in_combo * lp, 2)

    promo_subtotal = round(min_combos * combo_total, 2)
    if promo_subtotal >= original_combo_subtotal:
        # Ao contrário de exact_bundle/min_bundle (mesmo produto, risco de
        # "quebrar" o pacote em sub-combinação mais barata), o combo junta
        # produtos diferentes — não há regra de quantidade a proteger. Sem
        # desconto real, mantém o preço de lista de cada item (deixa a
        # promoção individual de cada um, se houver, decidir no loop principal).
        return

    combo_shares: Dict[int, float] = {}
    for i in item_indices:
        pid = int(result[i]["product_id"])
        if pid not in promo_pids:
            continue
        qty_i = int(result[i].get("quantity") or 0)
        lp = float(result[i].get("original_price") or result[i].get("unit_price") or 0)
        in_combo = min(qty_i, min_combos)
        share = (round(in_combo * lp, 2) / original_combo_subtotal) if original_combo_subtotal else 0
        combo_shares[i] = round(promo_subtotal * share, 2)

    if combo_shares:
        # O rateio arredondado não pode mudar o valor do combo.
        _balance_rounding(combo_shares, promo_subtotal)

    for i in item_indices:
        pid = int(result[i]["product_id"])
        if pid not in promo_pids:
            continue
        qty_i = int(result[i].get("quantity") or 0)
        lp = float(result[i].get("original_price") or result[i].get("unit_price") or 0)
        in_combo = min(qty_i, min_combos)
        extra = qty_i - in_combo

        item_promo_subtotal = combo_shares.get(i, 0.0)
        item_total = round(item_promo_subtotal + extra * lp, 2)

        eff_unit = round(item_total / qty_i, 6) if qty_i else 0.0
        result[i]["unit_price"] = eff_unit
        result[i]["subtotal"] = item_total
        result[i]["promotion_id"] = promo_id
        result[i]["promo_covered_qty"] = in_combo
        result[i]["promo_plan"] = []


def _is_cross_bogo(promo: dict) -> bool:
    buy_id = _int_or_none(promo.get("bogo_buy_product_id"))
    free_id = _int_or_none(promo.get("bogo_free_product_id"))
    return bool(buy_id and free_id and buy_id != free_id)


def _is_same_sku_bogo(promo: dict) -> bool:
    buy_id = _int_or_none(promo.get("bogo_buy_product_id"))
    free_id = _int_or_none(promo.get("bogo_free_product_id"))
    return bool(buy_id and free_id and buy_id == free_id)


def _is_bogo_pair(promo: dict) -> bool:
    buy_id = _int_or_none(promo.get("bogo_buy_product_id"))
    free_id = _int_or_none(promo.get("bogo_free_product_id"))
    return bool(buy_id and free_id)


def _is_bogo_gift_line(item: Dict) -> bool:
    return bool(item.get("bogo_auto_free"))


def _apply_bogo_cross_product(promo: dict, result: List[Dict]) -> List[int]:
    """Compre X do SKU A, leve Y do SKU B grátis. Retorna índices com desconto.

    Também cobre o mesmo SKU (compre 1, leve 2): a linha paga não entra
    em ``free_indices``; só as linhas ``bogo_auto_free``.
    """
    buy_id = _int_or_none(promo.get("bogo_buy_product_id"))
    free_id = _int_or_none(promo.get("bogo_free_product_id"))
    if not buy_id or not free_id:
        return []
    same_sku = buy_id == free_id
    min_q = max(1, int(promo.get("min_qty") or 1))
    free_q = max(0, int(promo.get("free_qty") or 0))
    if free_q <= 0:
        return []

    buy_qty = 0
    free_indices: List[int] = []
    for i, it in enumerate(result):
        pid = it.get("product_id")
        qty = int(it.get("quantity") or 0)
        if pid is None or qty <= 0:
            continue
        pid_i = int(pid)
        is_gift = _is_bogo_gift_line(it)
        if pid_i == buy_id and not is_gift:
            buy_qty += qty
        if pid_i == free_id and (is_gift if same_sku else True):
            free_indices.append(i)
    if buy_qty < min_q or not free_indices:
        return []

    granted = (buy_qty // min_q) * free_q
    remaining = granted
    applied: List[int] = []
    promo_id = int(promo["id"])
    for i in free_indices:
        if remaining <= 0:
            break
        qty_i = int(result[i].get("quantity") or 0)
        take = min(qty_i, remaining)
        if take <= 0:
            continue
        lp = float(result[i].get("original_price") or result[i].get("unit_price") or 0)
        orig = round(lp * qty_i, 2)
        paid_qty = qty_i - take
        new_sub = round(lp * paid_qty, 2)
        if new_sub < orig:
            result[i]["subtotal"] = new_sub
            result[i]["unit_price"] = round(new_sub / qty_i, 6) if qty_i else 0.0
            result[i]["promotion_id"] = promo_id
            applied.append(i)
        remaining -= take
    return applied


def _inject_bogo_gift_items(
    conn: sqlite3.Connection,
    event_id: int,
    promo: dict,
    result: List[Dict],
) -> None:
    """Inclui o SKU grátis (Y) quando o SKU pago (X) atinge o mínimo da promoção.

    No mesmo SKU, a linha paga nunca é reaproveitada: cria/atualiza uma
    linha ``bogo_auto_free`` à parte.
    """
    buy_id = _int_or_none(promo.get("bogo_buy_product_id"))
    free_id = _int_or_none(promo.get("bogo_free_product_id"))
    if not buy_id or not free_id:
        return
    same_sku = buy_id == free_id
    min_q = max(1, int(promo.get("min_qty") or 1))
    free_q = max(0, int(promo.get("free_qty") or 0))
    if free_q <= 0:
        return

    buy_qty = 0
    free_item = None
    for it in result:
        pid = it.get("product_id")
        qty = int(it.get("quantity") or 0)
        if pid is None or qty <= 0:
            continue
        pid_i = int(pid)
        is_gift = _is_bogo_gift_line(it)
        if pid_i == buy_id and not is_gift:
            buy_qty += qty
        if pid_i == free_id and (is_gift if same_sku else True):
            free_item = it

    granted = (buy_qty // min_q) * free_q
    if granted <= 0:
        return

    if free_item is not None:
        if same_sku:
            lp = float(free_item.get("original_price") or free_item.get("unit_price") or 0)
            free_item["quantity"] = granted
            free_item["bogo_auto_free"] = True
            free_item["subtotal"] = 0.0
            free_item["unit_price"] = 0.0
            return
        current = int(free_item.get("quantity") or 0)
        if current < granted:
            lp = float(free_item.get("original_price") or free_item.get("unit_price") or 0)
            free_item["quantity"] = granted
            free_item["subtotal"] = round(lp * granted, 2)
        return

    row = conn.execute(
        """
        SELECT p.id, p.name, p.sku, p.category,
               COALESCE(ep.price, p.price) AS price
          FROM products p
          LEFT JOIN event_products ep
            ON ep.product_id = p.id AND ep.event_id = ?
         WHERE p.id = ?
        """,
        (int(event_id), int(free_id)),
    ).fetchone()
    if not row:
        return
    lp = float(row["price"] or 0)
    result.append(
        {
            "product_id": int(row["id"]),
            "product_id_str": str(row["id"]),
            "product_name": str(row["name"] or "Produto"),
            "product_sku": row["sku"],
            "category": row["category"],
            "unit_price": lp,
            "original_price": lp,
            "quantity": granted,
            "subtotal": round(lp * granted, 2),
            "promotion_id": None,
            "bogo_auto_free": True,
        }
    )


def apply_promotions_to_items_in_conn(
    conn: sqlite3.Connection,
    event_id: int,
    items: List[Dict],
) -> List[Dict]:
    """Promoções de preço do evento + brindes (linhas a R$ 0,00 no fim do pedido).

    Linhas de brinde recebidas são descartadas e recalculadas pelo servidor
    (``gifts.apply_gift_rules_in_conn``); o brinde não muda o preço de nada.
    """
    paid = [it for it in items if not is_gift_line(it)]
    priced = _apply_price_promotions_in_conn(conn, event_id, paid)
    return apply_gift_rules_in_conn(conn, event_id, priced)


def _apply_price_promotions_in_conn(
    conn: sqlite3.Connection,
    event_id: int,
    items: List[Dict],
) -> List[Dict]:
    """Aplica promoções ativas do evento sobre ``items`` normalizados.

    Para ``exact_bundle``: soma as quantidades de **todos** os produtos da
    mesma promoção para formar os pacotes (cross-product). Ex.: kit de 3 com
    produtos A, B e C — 1 de cada ativa o pacote, assim como 3 de um mesmo.

    Para ``combo_bundle``: exige ao menos 1 un. de cada produto vinculado;
    cada combo completo (1 de cada) custa rule_value.

    Para ``min_bundle`` ("A partir de N un."): soma as unidades de todos os
    produtos da promoção; atingido N, cada unidade deles sai por rule_value
    (preço unitário). Compete por preço com percent/fixed/bogo do mesmo item.

    Para os demais tipos (incluindo kits ``exact_bundle`` no
    mesmo produto): avalia cada item isoladamente entre as promoções cuja
    regra se cumpre na quantidade comprada. Os kits se combinam — 8 un. com
    kits de 3 e de 5 saem como 5 + 3 — e vence o arranjo que cobre mais
    unidades; o total menor só desempata. Assim 5 un. com kits de 3/R$ 190,00
    e 5/R$ 349,50 fecham no kit de 5, e não em 1 kit de 3 + 2 un. avulsas.

    Uma promoção cuja regra se cumpre é sempre aplicada, mesmo que o valor
    dela não fique abaixo do preço de lista. A comparação de preço nunca
    encolhe a regra de uma promoção: serve só para desempatar arranjos que
    cobrem as mesmas unidades.
    """
    promo_rows = conn.execute(
        """
        SELECT pr.id, pr.rule_type, pr.rule_value, pr.min_qty, pr.free_qty,
               pr.bogo_buy_product_id, pr.bogo_free_product_id,
               pp.product_id
          FROM promotions pr
          JOIN promotion_products pp ON pp.promotion_id = pr.id
         WHERE pr.event_id = ? AND pr.active = 1
        """,
        (int(event_id),),
    ).fetchall()

    if not promo_rows:
        return items

    # O produto-pai de uma variante (a SKU "cabeça" de um grupo de Variantes)
    # também é comprável no catálogo — herda a promoção de qualquer filha
    # vinculada (exceto bogo/combo_bundle, que têm semântica de SKU exato).
    parent_by_child = _variant_parent_by_child(
        conn, (r["product_id"] for r in promo_rows),
    )

    product_promos: dict = {}
    promo_defs: Dict[int, dict] = {}
    promo_pids: Dict[int, set] = {}
    for r in promo_rows:
        pid = int(r["product_id"])
        pr = {
            "id": int(r["id"]),
            "rule_type": r["rule_type"],
            "rule_value": float(r["rule_value"]),
            "min_qty": int(r["min_qty"]),
            "free_qty": int(r["free_qty"]),
            "bogo_buy_product_id": _int_or_none(r["bogo_buy_product_id"]),
            "bogo_free_product_id": _int_or_none(r["bogo_free_product_id"]),
        }
        promo_defs[pr["id"]] = pr
        for target_pid in {pid, parent_by_child.get(pid)} if pr["rule_type"] in _PARENT_INHERIT_RULE_TYPES else {pid}:
            if target_pid is None:
                continue
            promo_pids.setdefault(pr["id"], set())
            if target_pid in promo_pids[pr["id"]]:
                continue
            product_promos.setdefault(target_pid, []).append(pr)
            promo_pids[pr["id"]].add(target_pid)

    result = []
    for item in items:
        new_item = dict(item)
        list_price = float(item.get("unit_price") or 0.0)
        qty = int(new_item.get("quantity") or 0)
        new_item["original_price"] = list_price
        new_item["promotion_id"] = None
        new_item["subtotal"] = round(list_price * qty, 2)
        new_item["promo_covered_qty"] = 0
        new_item["promo_plan"] = []
        result.append(new_item)

    handled_by_cross: set = set()
    min_bundle_qty = _min_bundle_totals(promo_defs, promo_pids, result)

    # --- combo_bundle: exige 1 de cada produto vinculado ---
    for promo_id, promo in promo_defs.items():
        if promo["rule_type"] != "combo_bundle":
            continue
        indices = [
            i for i, it in enumerate(result)
            if it.get("product_id") is not None
            and int(it["product_id"]) in promo_pids[promo_id]
            and int(it.get("quantity") or 0) > 0
        ]
        if not indices:
            continue
        _apply_combo_bundle(promo, indices, result, promo_pids[promo_id])
        for i in indices:
            if result[i].get("promotion_id") is not None:
                handled_by_cross.add(i)

    # --- bogo: compre SKU A, leve SKU B grátis (incluindo o mesmo SKU) ---
    for promo_id, promo in promo_defs.items():
        if promo["rule_type"] != "bogo" or not _is_bogo_pair(promo):
            continue
        _inject_bogo_gift_items(conn, event_id, promo, result)
        applied = _apply_bogo_cross_product(promo, result)
        handled_by_cross.update(applied)

    for i, new_item in enumerate(result):
        if i in handled_by_cross:
            continue
        if _is_bogo_gift_line(new_item):
            continue
        pid = new_item.get("product_id")
        list_price = float(new_item.get("original_price") or new_item.get("unit_price") or 0.0)
        qty = int(new_item.get("quantity") or 0)

        if pid is None or pid not in product_promos or qty <= 0:
            continue

        applicable: List[dict] = []
        for promo in product_promos[pid]:
            if promo["rule_type"] == "combo_bundle":
                continue
            if promo["rule_type"] == "bogo" and _is_bogo_pair(promo):
                continue
            if promo["rule_type"] == "bogo":
                buy_id = _int_or_none(promo.get("bogo_buy_product_id"))
                free_id = _int_or_none(promo.get("bogo_free_product_id"))
                if buy_id and free_id and int(pid) not in (buy_id, free_id):
                    continue
            # "A partir de N un." conta as unidades de todos os produtos da promoção.
            rule_qty = min_bundle_qty.get(promo["id"], qty) if promo["rule_type"] == "min_bundle" else qty
            if not _promo_rule_applies(
                promo["rule_type"], promo["min_qty"], promo["free_qty"], rule_qty,
            ):
                continue
            applicable.append(promo)

        if not applicable:
            continue

        # (unidades cobertas, total, promoção dominante, linhas do plano)
        best: Optional[tuple] = None
        pack_options = _pack_options_from_promos(applicable)
        plan = _best_pack_plan(pack_options, list_price, qty)
        if plan is not None:
            covered, total, counts = plan
            dominant_id, plan_rows = _pack_plan_details(counts, pack_options)
            best = (covered, total, dominant_id, plan_rows)

        # percent/fixed/bogo/min_bundle cobrem 100% da quantidade por definição — não há
        # "unidade deixada de fora da regra" a proteger como há entre pacotes
        # de tamanhos diferentes. Por isso, aqui a comparação é só de preço
        # (nunca `_plan_beats`): um pacote parcial mais barato que sobra
        # unidades avulsas não pode perder para um desconto percentual mais
        # caro só por "cobrir mais" — cobertura de pacote nunca é maior que a
        # quantidade comprada, então "cobrir mais" seria sempre o percent/
        # fixed/bogo, tornando a promoção de pacote nunca escolhida.
        for promo in applicable:
            if promo["rule_type"] in _PACK_RULE_TYPES:
                continue
            eff = _compute_effective_subtotal(
                promo["rule_type"], promo["rule_value"],
                promo["min_qty"], promo["free_qty"],
                list_price, qty,
            )
            if best is None or eff < best[1] - 0.0001:
                best = (qty, eff, int(promo["id"]), [])

        if best is not None:
            covered, total, promo_id, plan_rows = best
            eff_unit = round(total / qty, 6) if qty else 0.0
            new_item["unit_price"] = eff_unit
            new_item["subtotal"] = round(total, 2)
            new_item["promotion_id"] = promo_id
            new_item["promo_covered_qty"] = min(qty, int(covered))
            new_item["promo_plan"] = plan_rows

    # --- exact_bundle cruzado: só se for melhor que o preço já aplicado ---
    for promo_id, promo in promo_defs.items():
        if promo["rule_type"] != "exact_bundle":
            continue
        indices = [
            i for i, it in enumerate(result)
            if it.get("product_id") is not None
            and int(it["product_id"]) in promo_pids[promo_id]
            and int(it.get("quantity") or 0) > 0
            and i not in handled_by_cross
        ]
        if len(indices) < 2:
            continue
        _apply_exact_bundle_cross_product(promo, indices, result)

    return result


def apply_list_prices_to_normalized_items(
    conn: sqlite3.Connection,
    items: List[Dict],
    event_id: Optional[int] = None,
) -> None:
    """Substitui ``unit_price`` pelo preço de lista (evento, senão biblioteca) antes das promoções."""
    pids = {int(i["product_id"]) for i in items if i.get("product_id") is not None}
    if not pids:
        return
    placeholders = ",".join("?" * len(pids))
    pid_list = list(pids)
    if event_id is not None:
        rows = conn.execute(
            f"""
            SELECT p.id, COALESCE(ep.price, p.price) AS price
              FROM products p
              LEFT JOIN event_products ep
                ON ep.product_id = p.id AND ep.event_id = ?
             WHERE p.id IN ({placeholders})
            """,
            [int(event_id), *pid_list],
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT id, price FROM products WHERE id IN ({placeholders})",
            pid_list,
        ).fetchall()
    prices = {int(r["id"]): float(r["price"] or 0) for r in rows}
    for item in items:
        pid = item.get("product_id")
        if pid is None:
            continue
        list_p = prices.get(int(pid))
        if list_p is None:
            continue
        qty = int(item.get("quantity") or 0)
        item["unit_price"] = list_p
        item["subtotal"] = round(list_p * qty, 2)


def quote_cart_items_for_event(event_id: int, cart_items: List[Dict]) -> Dict:
    """Calcula preços promocionais para itens do carrinho (mesma lógica da venda).

    ``cart_items``: lista com ``id``/``product_id`` e ``quantidade``/``quantity``.
    Retorna ``{items, total, subtotal_lista, economia_total}``.
    """
    normalized: List[Dict] = []
    for raw in cart_items or []:
        if is_gift_line(raw):
            continue  # o brinde é recalculado aqui, não vem do navegador
        pid_raw = raw.get("id") if raw.get("id") is not None else raw.get("product_id")
        try:
            product_id = int(pid_raw) if pid_raw is not None else None
        except (TypeError, ValueError):
            product_id = None
        try:
            qty = int(raw.get("quantidade") or raw.get("quantity") or 0)
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0 or product_id is None:
            continue
        list_p = float(raw.get("preco_lista") or raw.get("preco_original") or raw.get("preco") or 0)
        normalized.append(
            {
                "product_id": product_id,
                "product_id_str": str(product_id),
                "product_name": str(raw.get("nome") or raw.get("product_name") or "Produto"),
                "product_sku": raw.get("sku") or raw.get("product_sku"),
                "category": raw.get("categoria") or raw.get("category"),
                "unit_price": list_p,
                "quantity": qty,
                "subtotal": round(list_p * qty, 2),
                "bogo_auto_free": bool(raw.get("bogo_auto_free")),
            }
        )

    if not normalized:
        return {"items": [], "total": 0.0, "subtotal_lista": 0.0, "economia_total": 0.0}

    promo_names: Dict[int, str] = {}
    promo_types: Dict[int, str] = {}
    promo_values: Dict[int, float] = {}
    promo_min_qtys: Dict[int, int] = {}
    promo_free_qtys: Dict[int, int] = {}
    with get_conn() as conn:
        apply_list_prices_to_normalized_items(conn, normalized, event_id=int(event_id))
        subtotal_lista = round(sum(i["subtotal"] for i in normalized), 2)
        priced = apply_promotions_to_items_in_conn(conn, int(event_id), normalized)
        promo_ids = {int(i["promotion_id"]) for i in priced if i.get("promotion_id")}
        for i in priced:
            for row in (i.get("promo_plan") or []):
                promo_ids.add(int(row["promotion_id"]))
        if promo_ids:
            placeholders = ",".join("?" * len(promo_ids))
            for r in conn.execute(
                f"SELECT id, name, rule_type, rule_value, min_qty, free_qty "
                f"FROM promotions WHERE id IN ({placeholders})",
                list(promo_ids),
            ).fetchall():
                promo_names[int(r["id"])] = str(r["name"] or "")
                promo_types[int(r["id"])] = str(r["rule_type"] or "")
                promo_values[int(r["id"])] = float(r["rule_value"] or 0)
                promo_min_qtys[int(r["id"])] = int(r["min_qty"] or 1)
                promo_free_qtys[int(r["id"])] = int(r["free_qty"] or 0)

    out_items: List[Dict] = []
    for row in priced:
        # Brinde avulso não é produto: o carrinho o identifica por "avulso:<id>".
        pid = (
            int(row["product_id"]) if row.get("product_id") is not None
            else f"{AVULSO_ID_PREFIX}{int(row['gift_item_id'])}"
        )
        qty = int(row["quantity"])
        list_p = float(row.get("original_price") or row.get("unit_price") or 0)
        eff_unit = float(row.get("unit_price") or 0)
        subtotal = float(row.get("subtotal") or 0)
        promo_id = row.get("promotion_id")
        row_plan = row.get("promo_plan") or []
        is_gift = bool(row.get("bogo_auto_free"))
        # Promoção aplicada conta como promoção mesmo com economia zero.
        has_promo = is_gift or promo_id is not None
        out_items.append(
            {
                "id": pid,
                "quantidade": qty,
                "preco_lista": list_p,
                "preco": 0.0 if is_gift else eff_unit,
                "subtotal": 0.0 if is_gift else subtotal,
                "em_promocao": has_promo,
                "promotion_id": int(promo_id) if promo_id is not None else None,
                "promo_nome": promo_names.get(int(promo_id), "") if promo_id else "",
                "promo_tipo": promo_types.get(int(promo_id), "") if promo_id else "",
                "promo_rule_value": promo_values.get(int(promo_id), 0) if promo_id else 0,
                "promo_min_qty": promo_min_qtys.get(int(promo_id), 1) if promo_id else 0,
                "promo_free_qty": promo_free_qtys.get(int(promo_id), 0) if promo_id else 0,
                "economia": round(max(0.0, list_p * qty - (0.0 if is_gift else subtotal)), 2),
                "bogo_auto_free": is_gift,
                # Plano de pacotes da linha: um kit por entrada, na ordem em que
                # o carrinho mostra ("1 pacote de 5 + 1 pacote de 3 + 1 avulsa").
                "promo_plan": [
                    {
                        "promotion_id": int(row["promotion_id"]),
                        "min_qty": int(row["min_qty"]),
                        "rule_value": float(row["rule_value"]),
                        "groups": int(row["groups"]),
                        "promo_nome": promo_names.get(int(row["promotion_id"]), ""),
                    }
                    for row in (row_plan or [])
                ],
                "promo_covered_qty": int(row.get("promo_covered_qty") or 0),
                "brinde": bool(row.get("brinde")),
                "gift_rule_id": row.get("gift_rule_id"),
                "brinde_nome": row.get("gift_rule_name") or "",
                "gift_item_id": row.get("gift_item_id"),
                "brinde_avulso": bool(row.get("gift_item_id")),
            }
        )

    total = round(sum(i["subtotal"] for i in out_items), 2)
    economia_total = round(max(0.0, subtotal_lista - total), 2)
    return {
        "items": out_items,
        "total": total,
        "subtotal_lista": subtotal_lista,
        "economia_total": economia_total,
    }


# ---------------------------------------------------------------------------
# Helper de exibição no catálogo
# ---------------------------------------------------------------------------

def _promo_display_entry(promo: Dict) -> Dict:
    return {
        "promo_id": int(promo.get("id") or 0),
        "promo_nome": promo.get("name") or "",
        "promo_tipo": promo.get("rule_type") or "",
        "promo_label": promo.get("rule_label", ""),
        "rule_value": float(promo.get("rule_value") or 0),
        "min_qty": int(promo.get("min_qty") or 1),
        "free_qty": int(promo.get("free_qty") or 0),
        "bogo_buy_product_id": _int_or_none(promo.get("bogo_buy_product_id")),
        "bogo_free_product_id": _int_or_none(promo.get("bogo_free_product_id")),
        "bogo_buy_sku": str(promo.get("bogo_buy_sku") or ""),
        "bogo_free_sku": str(promo.get("bogo_free_sku") or ""),
    }


def _promo_badge_text(entry: Dict, *, pid: int = 0) -> str:
    rule = entry.get("promo_tipo") or ""
    val = float(entry.get("rule_value") or 0)
    min_q = int(entry.get("min_qty") or 1)
    free_q = int(entry.get("free_qty") or 0)
    if rule == "percent":
        pct = min(100.0, max(0.0, val))
        return f"{int(pct) if pct == int(pct) else pct}% OFF"
    if rule == "fixed":
        return f"- R$ {val:.2f}".replace(".", ",")
    if rule == "bogo":
        buy_sku = str(entry.get("bogo_buy_sku") or "").strip()
        free_sku = str(entry.get("bogo_free_sku") or "").strip()
        if buy_sku and free_sku and buy_sku != free_sku:
            if pid == int(entry.get("bogo_free_product_id") or 0):
                return f"Grátis na compra de {min_q} un. do SKU {buy_sku}"
            return f"Compre {min_q} leve {free_q} SKU {free_sku}"
        return f"Compre {min_q} Leve {min_q + free_q}"
    if rule == "min_bundle":
        if min_q >= 2 and val > 0:
            price = f"{val:.2f}".replace(".", ",")
            return f"A partir de {min_q} un.: R$ {price} cada"
        return ""
    if rule == "exact_bundle":
        if min_q >= 2 and val > 0:
            return f"Kit de {min_q}: R$ {val:.2f}".replace(".", ",")
        return ""
    if rule == "combo_bundle":
        if val > 0:
            return f"Combo: R$ {val:.2f}".replace(".", ",")
        return ""
    return ""


def _map_entries(raw: Dict) -> List[Dict]:
    entries = list(raw.get("promos") or [])
    if entries:
        return entries
    return [raw]


def _stamp_primary_promo(product: Dict, entry: Dict, list_price: float) -> None:
    product["promo_id"] = int(entry.get("promo_id") or 0)
    product["promo_tipo"] = entry.get("promo_tipo") or ""
    product["promo_label"] = entry.get("promo_label") or ""
    product["promo_min_qty"] = int(entry.get("promo_min_qty") or entry.get("min_qty") or 1)
    product["promo_rule_value"] = float(entry.get("promo_rule_value") or entry.get("rule_value") or 0)
    product["promo_free_qty"] = int(entry.get("promo_free_qty") or entry.get("free_qty") or 0)
    product["promo_bogo_buy_id"] = int(entry.get("promo_bogo_buy_id") or entry.get("bogo_buy_product_id") or 0)
    product["promo_bogo_free_id"] = int(entry.get("promo_bogo_free_id") or entry.get("bogo_free_product_id") or 0)
    product["promo_bogo_buy_sku"] = str(entry.get("promo_bogo_buy_sku") or entry.get("bogo_buy_sku") or "")
    product["promo_bogo_free_sku"] = str(entry.get("promo_bogo_free_sku") or entry.get("bogo_free_sku") or "")
    rule = product["promo_tipo"]
    val = product["promo_rule_value"]
    if rule == "percent":
        pct = min(100.0, max(0.0, val))
        product["preco"] = round(list_price * (1.0 - pct / 100.0), 2)
    elif rule == "fixed":
        product["preco"] = round(max(0.0, list_price - val), 2)
    else:
        product["preco"] = list_price


def build_promo_display_map(promotions: List[Dict]) -> Dict[int, Dict]:
    """Constrói {product_id -> promo_display} para enriquecer o catálogo do vendedor.

    Um produto pode participar de várias promoções. ``promos`` lista todas;
    os demais campos repetem a primeira (compatibilidade com leitores antigos).
    """
    grouped: Dict[int, List[Dict]] = defaultdict(list)
    with get_conn() as conn:
        for promo in promotions:
            entry = _promo_display_entry(promo)
            buy_id = entry["bogo_buy_product_id"]
            free_id = entry["bogo_free_product_id"]
            # Inclui o produto-pai de qualquer variante-filha vinculada: ele
            # também é um item comprável do catálogo.
            for pid in _promo_covered_product_ids(conn, promo):
                if promo.get("rule_type") == "bogo" and buy_id and free_id and int(pid) not in (buy_id, free_id):
                    continue
                grouped[int(pid)].append(entry)

    best: Dict[int, Dict] = {}
    for pid, entries in grouped.items():
        primary = dict(entries[0])
        primary["promos"] = entries
        best[pid] = primary
    return best


def enrich_product_with_promo(product: Dict, promo_map: Dict[int, Dict]) -> Dict:
    """Adiciona campos de promoção a um produto do catálogo (formato cliente).

    Campos adicionados:
    - ``em_promocao`` (bool)
    - ``promos`` (lista de regras elegíveis)
    - ``promo_nome`` (str) — nomes unidos por ·
    - ``promo_tipo`` (str) — regra principal (percent/fixed, senão a primeira)
    - ``promo_badge`` (str) — badges unidos por ·
    - ``preco_original`` / ``preco``
    """
    pid = int(product.get("id") or 0)
    p = dict(product)
    p["em_promocao"] = False
    p["preco_original"] = float(p.get("preco") or 0)
    p["promo_nome"] = ""
    p["promo_tipo"] = ""
    p["promo_label"] = ""
    p["promo_badge"] = ""
    p["promo_min_qty"] = 0
    p["promos"] = []

    if pid not in promo_map:
        return p

    list_price = float(p.get("preco") or 0)
    promos_out: List[Dict] = []
    for entry in _map_entries(promo_map[pid]):
        rule = entry.get("promo_tipo") or ""
        val = float(entry.get("rule_value") or 0)
        min_q = int(entry.get("min_qty") or 1)
        free_q = int(entry.get("free_qty") or 0)
        promos_out.append({
            "promo_id": int(entry.get("promo_id") or 0),
            "promo_nome": entry.get("promo_nome") or "",
            "promo_tipo": rule,
            "promo_label": entry.get("promo_label") or "",
            "promo_rule_value": val,
            "promo_min_qty": min_q,
            "promo_free_qty": free_q,
            "promo_badge": _promo_badge_text(entry, pid=pid),
            "promo_bogo_buy_id": int(entry.get("bogo_buy_product_id") or 0),
            "promo_bogo_free_id": int(entry.get("bogo_free_product_id") or 0),
            "promo_bogo_buy_sku": str(entry.get("bogo_buy_sku") or ""),
            "promo_bogo_free_sku": str(entry.get("bogo_free_sku") or ""),
        })

    if not promos_out:
        return p

    names = []
    badges = []
    for item in promos_out:
        name = (item.get("promo_nome") or "").strip()
        if name and name not in names:
            names.append(name)
        badge = (item.get("promo_badge") or "").strip()
        if badge and badge not in badges:
            badges.append(badge)

    immediate = [
        item for item in promos_out if item.get("promo_tipo") in ("percent", "fixed")
    ]
    primary = None
    if immediate:
        best_price = None
        for item in immediate:
            rule = item["promo_tipo"]
            val = float(item.get("promo_rule_value") or 0)
            if rule == "percent":
                price = round(list_price * (1.0 - min(100.0, max(0.0, val)) / 100.0), 2)
            else:
                price = round(max(0.0, list_price - val), 2)
            if best_price is None or price < best_price:
                best_price = price
                primary = item
    if primary is None:
        primary = promos_out[0]

    p["em_promocao"] = True
    p["promos"] = promos_out
    p["promo_nome"] = " · ".join(names)
    p["promo_badge"] = " · ".join(badges)
    _stamp_primary_promo(p, primary, list_price)
    return p


# ---------------------------------------------------------------------------
# Sync de produtos (interno)
# ---------------------------------------------------------------------------

def _sync_promo_products(
    conn: sqlite3.Connection,
    promo_id: int,
    product_ids: List[int],
) -> None:
    """Substitui a lista de produtos de uma promoção (DELETE + INSERT)."""
    conn.execute("DELETE FROM promotion_products WHERE promotion_id = ?", (promo_id,))
    unique_ids = list(dict.fromkeys(int(p) for p in product_ids if p))
    if unique_ids:
        conn.executemany(
            "INSERT OR IGNORE INTO promotion_products (promotion_id, product_id) VALUES (?, ?)",
            [(promo_id, pid) for pid in unique_ids],
        )