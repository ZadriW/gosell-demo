"""Product catalog, ERP sync and admin library listings."""
from __future__ import annotations

import logging
import re
import sqlite3
import time
import unicodedata
from bisect import bisect_left
from collections import defaultdict
from functools import lru_cache
from typing import Dict, Iterable, List, Optional, Tuple

from . import product_search
from .connection import DEFAULT_MIN_STOCK, _now_iso, get_conn
from .sku_helpers import _default_sku_for_id
import product_images

log = logging.getLogger(__name__)


def _row_to_product_dict(row: sqlite3.Row) -> Dict:
    """Converte row SQLite de ``products`` para dict interno."""
    return dict(row)


@lru_cache(maxsize=65536)
def _fold_product_name(name: str) -> str:
    """Nome comparável (minúsculas, sem acento) para detectar produto-base vs variante.

    Em cache: o agrupamento do catálogo compara os mesmos nomes muitas vezes.
    """
    text = unicodedata.normalize("NFD", (name or "").strip().lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return " ".join(text.split())


def is_gift_only_product(name: str) -> bool:
    """Produto de brinde ("BRINDE - ..."): só entra no pedido pelas regras de
    brinde (Promoções), nunca pelo catálogo do vendedor. Produtos com o brinde
    no meio do nome ("... + Brinde ...") são vendáveis e não entram aqui."""
    return _fold_product_name(name).startswith("brinde")


def _product_catalog_like_clause(
    q: Optional[str],
    *,
    alias: str = "p",
    include_sku_aliases: bool = False,
    include_family_id: bool = False,
) -> Tuple[str, List]:
    """Filtro de busca por texto (regra em ``product_search``).

    Cada palavra precisa casar com nome, variante ou SKU (e os apelidos de SKU
    com ``include_sku_aliases``); veja o que conta como "casar" no módulo
    ``product_search``. Busca só de dígitos (opc. ``#``) também acha pelo ID
    e, com ``include_family_id``, pela família e pelo CODPROD.
    """
    query = product_search.parse_query(q)
    if not query.tokens:
        return "", []

    prefix = f"{alias}." if alias else ""
    id_col = f"{prefix}id"
    aliases = (
        "(SELECT GROUP_CONCAT(sa.sku, '|') FROM product_sku_aliases sa "
        f"WHERE sa.product_id = {id_col})"
        if include_sku_aliases
        else "NULL"
    )
    clause = (
        f"product_search_score(?, {prefix}name, {prefix}variant_name, "
        f"{prefix}sku, {aliases}) IS NOT NULL"
    )
    params: List = [query.phrase]
    if len(query.tokens) == 1 and query.tokens[0].isdigit():
        id_part = query.tokens[0]
        id_ors = [
            f"{id_col} = ?",
            f"INSTR(CAST({id_col} AS TEXT), ?) > 0",
        ]
        params.extend([int(id_part), id_part])
        if include_family_id:
            id_ors.append(f"{prefix}family_id = ?")
            id_ors.append(f"{prefix}erp_codprod = ?")
            params.extend([int(id_part), int(id_part)])
        clause = f"{clause} OR {' OR '.join(id_ors)}"
    return f"({clause})", params


def _product_search_order_clause(
    q: Optional[str],
    *,
    alias: str = "p",
    fallback: Optional[str] = None,
) -> Tuple[str, List]:
    """``ORDER BY``: relevância da busca e, sem texto, o fallback (categoria/nome)."""
    prefix = f"{alias}." if alias else ""
    query = product_search.parse_query(q)
    if not query.tokens:
        return fallback or f"{prefix}category, {prefix}name", []
    order = ""
    params: List = []
    if len(query.tokens) == 1 and query.tokens[0].isdigit():
        order = f"({prefix}id = ?) DESC, "
        params.append(int(query.tokens[0]))
    order += (
        f"COALESCE(product_search_score(?, {prefix}name, {prefix}variant_name, "
        f"{prefix}sku, NULL), 0) DESC, {prefix}name COLLATE NOCASE"
    )
    params.append(query.phrase)
    return order, params


def _retire_variant_parent_ids(conn: sqlite3.Connection, parent_ids: Iterable[int]) -> int:
    """Não retira mais o produto-base. Mantido só por compatibilidade (no-op)."""
    return 0


def _detect_variant_parent_ids(conn: sqlite3.Connection) -> List[int]:
    """IDs de SKU-base quando já existem variantes do mesmo produto (ativos ou não).

    1. ``id = family_id`` com irmãos (família de variantes cadastrada).
    2. Nome do cadastro é prefixo do nome de outro item, e o ``id`` do base
       é menor que o das variantes.
    """
    found: set[int] = set()
    rows = conn.execute(
        """
        SELECT p.id
          FROM products p
         WHERE p.family_id IS NOT NULL
           AND p.family_id > 0
           AND p.id = p.family_id
           AND EXISTS (
                SELECT 1 FROM products v
                 WHERE v.family_id = p.family_id
                   AND v.id != p.id
           )
        """
    ).fetchall()
    for r in rows:
        found.add(int(r["id"]))

    catalog = conn.execute("SELECT id, name FROM products").fetchall()
    folded = [(int(r["id"]), _fold_product_name(r["name"])) for r in catalog]
    for pid, pname in folded:
        if pid in found or len(pname) < 12:
            continue
        child_ids = [
            cid
            for cid, cname in folded
            if cid != pid and cname.startswith(pname + " ") and len(cname) >= len(pname) + 8
        ]
        if child_ids and pid < min(child_ids):
            found.add(pid)
    return sorted(found)


def detect_unsellable_variant_parent_ids(conn: sqlite3.Connection) -> List[int]:
    """Compatibilidade: a listagem de SKU-base não implica mais bloqueio de venda."""
    return _detect_variant_parent_ids(conn)


def restore_retired_variant_parents_in_conn(conn: sqlite3.Connection) -> Dict[str, int]:
    """Reativa produtos-base desativados e religa-os aos eventos das variantes."""
    ids = _detect_variant_parent_ids(conn)
    if not ids:
        return {"reactivated": 0, "relinked": 0}

    now = _now_iso()
    placeholders = ",".join("?" * len(ids))
    cur = conn.execute(
        f"UPDATE products SET active = 1, updated_at = ? "
        f"WHERE id IN ({placeholders}) AND active = 0",
        (now, *ids),
    )
    reactivated = int(cur.rowcount or 0)

    stock_rows = conn.execute(
        f"""
        SELECT event_id, product_id, COALESCE(SUM(delta), 0) AS stock
          FROM stock_movements
         WHERE event_id IS NOT NULL AND product_id IN ({placeholders})
         GROUP BY event_id, product_id
        """,
        ids,
    ).fetchall()
    tx_rows = conn.execute(
        f"""
        SELECT DISTINCT t.event_id AS event_id, ti.product_id AS product_id
          FROM transaction_items ti
          JOIN transactions t ON t.id = ti.transaction_id
         WHERE t.event_id IS NOT NULL AND ti.product_id IN ({placeholders})
        """,
        ids,
    ).fetchall()

    pairs: Dict[Tuple[int, int], int] = {}
    for r in stock_rows:
        pairs[(int(r["event_id"]), int(r["product_id"]))] = max(0, int(r["stock"] or 0))
    for r in tx_rows:
        pairs.setdefault((int(r["event_id"]), int(r["product_id"])), 0)

    catalog = conn.execute("SELECT id, name, family_id FROM products").fetchall()
    folded_by_id = {int(r["id"]): _fold_product_name(r["name"]) for r in catalog}
    family_by_id = {
        int(r["id"]): int(r["family_id"] or 0) for r in catalog
    }
    for parent_id in ids:
        prefix = folded_by_id.get(parent_id) or ""
        family_ref = family_by_id.get(parent_id) or 0
        child_ids = [
            int(r["id"])
            for r in catalog
            if int(r["id"]) != parent_id
            and (
                (family_ref > 0 and int(r["family_id"] or 0) == family_ref)
                or (
                    prefix
                    and len(prefix) >= 12
                    and folded_by_id.get(int(r["id"]), "").startswith(prefix + " ")
                    and len(folded_by_id.get(int(r["id"]), "")) >= len(prefix) + 8
                )
            )
        ]
        if not child_ids:
            continue
        child_ph = ",".join("?" * len(child_ids))
        ev_rows = conn.execute(
            f"SELECT DISTINCT event_id FROM event_products WHERE product_id IN ({child_ph})",
            child_ids,
        ).fetchall()
        for ev in ev_rows:
            pairs.setdefault((int(ev["event_id"]), parent_id), 0)

    relinked = 0
    for (event_id, product_id), stock in pairs.items():
        ev_ok = conn.execute(
            "SELECT 1 FROM events WHERE id = ?", (event_id,)
        ).fetchone()
        if ev_ok is None:
            continue
        existing = conn.execute(
            "SELECT 1 FROM event_products WHERE event_id = ? AND product_id = ?",
            (event_id, product_id),
        ).fetchone()
        if existing:
            continue
        conn.execute(
            """
            INSERT INTO event_products
                (event_id, product_id, stock, min_stock, backorder_limit, created_at, updated_at)
            VALUES (?, ?, ?, ?, -1, ?, ?)
            """,
            (event_id, product_id, stock, DEFAULT_MIN_STOCK, now, now),
        )
        relinked += 1

    if reactivated or relinked:
        log.info(
            "Produtos-base restaurados: reactivated=%s relinked=%s ids=%s",
            reactivated,
            relinked,
            ids[:20],
        )
    return {"reactivated": reactivated, "relinked": relinked}


def retire_unsellable_variant_parents() -> int:
    """Não desativa mais SKUs-base. Mantido por compatibilidade."""
    return 0


def is_unsellable_variant_parent(product_id: int) -> bool:
    """Não bloqueia mais o SKU-base; sempre False."""
    return False


def variant_children_preview(parent_id: int, limit: int = 5) -> List[Dict]:
    """SKUs/nomes das variantes locais de um produto-base (para mensagem ao admin)."""
    pid = int(parent_id)
    with get_conn() as conn:
        parent = conn.execute(
            "SELECT id, name FROM products WHERE id = ?", (pid,)
        ).fetchone()
        if parent is None:
            return []
        prefix = _fold_product_name(parent["name"])
        if not prefix:
            return []
        rows = conn.execute(
            "SELECT id, sku, name FROM products WHERE id != ? AND active = 1 ORDER BY sku",
            (pid,),
        ).fetchall()
    out: List[Dict] = []
    for r in rows:
        folded = _fold_product_name(r["name"])
        if folded.startswith(prefix + " ") and len(folded) >= len(prefix) + 8:
            out.append({"id": int(r["id"]), "sku": r["sku"] or "", "nome": r["name"] or ""})
            if len(out) >= int(limit):
                break
    return out


# ---------------------------------------------------------------------------
# Catálogo (produtos)
# ---------------------------------------------------------------------------

def _product_row_to_client(row: sqlite3.Row) -> Dict:
    """Converte um row em dict com os nomes usados pelo front (pt-BR)."""
    pid = int(row["id"])
    try:
        sku_val = row["sku"]
    except (KeyError, IndexError):
        sku_val = None
    sku = (sku_val or "").strip() if sku_val is not None else ""
    if not sku:
        sku = _default_sku_for_id(pid)
    try:
        vn = (row["variant_name"] or "").strip()
    except (KeyError, IndexError):
        vn = ""
    try:
        family_pid = int(row["family_id"] or 0)
    except (KeyError, IndexError, TypeError, ValueError):
        family_pid = 0
    try:
        main_variant = bool(int(row["main_variant"] or 0))
    except (KeyError, IndexError, TypeError, ValueError):
        main_variant = False
    try:
        subtitle = (row["subtitle"] or "").strip()
    except (KeyError, IndexError):
        subtitle = ""
    try:
        erp_codprod = int(row["erp_codprod"]) if row["erp_codprod"] is not None else None
    except (KeyError, IndexError, TypeError, ValueError):
        erp_codprod = None
    try:
        brand = (row["brand"] or "").strip()
    except (KeyError, IndexError):
        brand = ""
    try:
        group_code = int(row["erp_group_code"]) if row["erp_group_code"] is not None else None
    except (KeyError, IndexError, TypeError, ValueError):
        group_code = None
    imagem, imagem_card = product_images.resolve_image_urls(pid, row["image"])
    return {
        "id": pid,
        "sku": sku,
        "nome": row["name"],
        "variante": vn,
        "subtitle": subtitle,
        "categoria": row["category"],
        "descricao": row["description"] or "",
        "preco": float(row["price"] or 0),
        "imagem": imagem,
        # Miniatura WebP para o card do catálogo (ou a própria foto, se não houver).
        "imagem_card": imagem_card,
        "estoque": int(row["stock"] or 0),
        "estoque_minimo": int(row["min_stock"] or 0),
        "ativo": bool(row["active"]),
        "family_id": family_pid,
        "erp_codprod": erp_codprod,
        "marca": brand,
        "grupo_erp": group_code,
        "main_variant": main_variant,
        "tem_opcoes": False,
        "catalog_oculto": False,
        "opcoes": [],
    }


def _is_name_variant_child(parent_name: str, child_name: str) -> bool:
    pname = _fold_product_name(parent_name)
    cname = _fold_product_name(child_name)
    return bool(
        pname
        and len(pname) >= 12
        and cname.startswith(pname + " ")
        and len(cname) >= len(pname) + 8
    )


def _catalog_children_of_parent(parent: Dict, products: List[Dict], parent_ids: set) -> List[Dict]:
    pid = int(parent["id"])
    family = int(parent.get("family_id") or 0)
    children: List[Dict] = []
    for cand in products:
        cid = int(cand["id"])
        if cid == pid or cid in parent_ids:
            continue
        cfamily = int(cand.get("family_id") or 0)
        same_family = family > 0 and cfamily == family
        name_child = _is_name_variant_child(parent.get("nome") or "", cand.get("nome") or "")
        if same_family or name_child:
            children.append(cand)
    children.sort(key=lambda c: ((c.get("variante") or c.get("nome") or ""), int(c["id"])))
    return children


def _variant_suffix_from_name(parent_name: str, child_name: str) -> str:
    """Extrai a parte diferenciadora do nome da filha em relação ao pai.

    Corta as palavras iguais no começo e no fim, já que no ERP o detalhe
    costuma ficar no meio: pai ``PONTA ... MF 120UN TANARI`` e filha
    ``PONTA ... FM 28MM 120UN TANARI`` dão ``FM 28MM``. Sem palavra em comum
    com o pai, devolve ``""`` (a etiqueta fica com o nome inteiro).
    """
    p_words = (parent_name or "").split()
    c_words = (child_name or "").split()
    if not p_words or not c_words:
        return ""
    p_fold = [_fold_product_name(w) for w in p_words]
    c_fold = [_fold_product_name(w) for w in c_words]
    shortest = min(len(p_fold), len(c_fold))
    head = 0
    while head < shortest and p_fold[head] == c_fold[head]:
        head += 1
    tail = 0
    while tail < shortest - head and p_fold[-1 - tail] == c_fold[-1 - tail]:
        tail += 1
    if not head and not tail:
        return ""
    return " ".join(c_words[head:len(c_words) - tail]).strip(" -\u2013\u2014/")


def _mark_catalog_family(head: Dict, members: List[Dict], *, include_head: bool) -> None:
    option_ids: List[int] = []
    head_name = head.get("nome") or ""
    if include_head:
        option_ids.append(int(head["id"]))
        if not (head.get("variante") or "").strip():
            sibling = next((m for m in members if int(m["id"]) != int(head["id"])), None)
            head["variante"] = (
                sibling and _variant_suffix_from_name(sibling.get("nome") or "", head_name)
            ) or head_name
    for child in members:
        cid = int(child["id"])
        if cid == int(head["id"]):
            continue
        child["catalog_oculto"] = True
        child["opcao_de"] = int(head["id"])
        if not (child.get("variante") or "").strip():
            suffix = _variant_suffix_from_name(head_name, child.get("nome") or "")
            if suffix:
                child["variante"] = suffix
        option_ids.append(cid)
    if not option_ids:
        return
    head["tem_opcoes"] = True
    head["catalog_oculto"] = False
    head["opcoes"] = option_ids
    by_id = {int(m["id"]): m for m in members}
    by_id[int(head["id"])] = head
    search_bits: List[str] = []
    for oid in option_ids:
        opt = by_id.get(oid)
        if not opt:
            continue
        blob = " ".join(
            bit for bit in (
                opt.get("nome") or "",
                opt.get("variante") or "",
            )
            if (bit or "").strip()
        )
        if blob.strip():
            search_bits.append(blob.strip())
    head["busca_opcoes"] = " | ".join(search_bits)


def summarize_catalog_option_groups(products: List[Dict]) -> None:
    """Atualiza preço/estoque resumidos do card-pai a partir das variantes."""
    by_id = {int(p["id"]): p for p in products}
    for p in products:
        ids = [int(i) for i in (p.get("opcoes") or [])]
        if not ids:
            continue
        children = [by_id[i] for i in ids if i in by_id]
        if not children:
            continue
        prices = [float(c.get("preco") or 0) for c in children]
        stocks = [int(c.get("estoque") or 0) for c in children]
        p["opcoes_count"] = len(children)
        p["estoque_opcoes"] = sum(stocks)
        p["preco_a_partir"] = min(prices) if prices else float(p.get("preco") or 0)
        p["precos_opcoes_variam"] = (max(prices) - min(prices) > 0.001) if prices else False
        pending = sum(int(c.get("pending_delivery_units") or 0) for c in children)
        p["pending_delivery_units"] = max(int(p.get("pending_delivery_units") or 0), pending)
        # Selo do card da família: aparece quando alguma opção tem promoção. Fica
        # em campos próprios e nunca em `em_promocao`/`promo_badge` do card-pai:
        # ele é também uma opção selecionável (o primeiro item de `opcoes`) e, com
        # o selo emprestado, a variante sem promoção apareceria como promovida no
        # modal e no carrinho.
        promo_option = next(
            (c for c in children if c.get("em_promocao") and c.get("promo_badge")), None
        )
        p["opcoes_em_promocao"] = any(c.get("em_promocao") for c in children)
        p["opcoes_promo_badge"] = (promo_option or {}).get("promo_badge") or ""


def _detect_variant_parent_ids_from_products(products: List[Dict]) -> set:
    """Mesma regra de ``_detect_variant_parent_ids``, só sobre a lista já carregada.

    Não abre conexão extra — o catálogo na LAN não pode varrer a tabela
    ``products`` a cada request/polling.
    """
    found: set = set()
    by_family: Dict[int, List[Dict]] = defaultdict(list)
    for p in products:
        family = int(p.get("family_id") or 0)
        if family > 0:
            by_family[family].append(p)
    for family, group in by_family.items():
        if len(group) < 2:
            continue
        for p in group:
            if int(p["id"]) == family:
                found.add(int(p["id"]))

    # Nomes ordenados: os que começam com "<nome> " ficam juntos, entre
    # "<nome> " e "<nome>!" (o "!" vem logo depois do espaço). A busca binária
    # evita comparar cada produto com todos (lento com o catálogo do ERP).
    folded = sorted((_fold_product_name(p.get("nome") or ""), int(p["id"])) for p in products)
    names = [name for name, _pid in folded]
    for pname, pid in folded:
        if pid in found or len(pname) < 12:
            continue
        lo = bisect_left(names, pname + " ")
        hi = bisect_left(names, pname + "!", lo)
        child_ids = [
            cid
            for cname, cid in folded[lo:hi]
            if cid != pid and len(cname) >= len(pname) + 8
        ]
        if child_ids and pid < min(child_ids):
            found.add(pid)
    return found


#: Famílias pelo molde do nome (cadastro do ERP): no máximo tantas palavras
#: variam entre as variantes, e o que fica fixo precisa ter este tamanho.
_TEMPLATE_MAX_SPAN = 2
_TEMPLATE_MIN_HEAD_WORDS = 2
_TEMPLATE_MIN_FIXED_WORDS = 3
_TEMPLATE_CODE_MAX_LETTERS = 5
_TEMPLATE_CODE_PUNCT = set("-/,.+")


def _looks_like_variant_code(word: str) -> bool:
    """Palavra com cara de código de variante: cor (DA1, A1, BL, TRANS-N),
    medida (25MM, 30/04, DA3,5) ou tamanho curto (P, GG, CURTA).

    Palavras longas sem número (ESSENTIAL, TECHNOLOGY) não contam: assim kits
    diferentes da mesma linha não viram "variantes" um do outro.
    """
    return bool(word) and (
        any(ch.isdigit() for ch in word)
        or any(ch in _TEMPLATE_CODE_PUNCT for ch in word)
        or len(word) <= _TEMPLATE_CODE_MAX_LETTERS
    )


def _natural_sort_key(text: str) -> Tuple:
    """DA2 antes de DA10; números comparados como números."""
    parts = re.split(r"(\d+)", _fold_product_name(text))
    return tuple((0, int(part)) if part.isdigit() else (1, part) for part in parts if part)


def _name_template_groups(products: List[Dict]) -> List[Tuple[List[Dict], str, Dict[int, str]]]:
    """Famílias de variantes pelo molde do nome, no padrão do ERP.

    No ERP cada variante é um ``CODPROD`` com o nome completo, e o que muda
    fica no meio: ``RESINA VITTRA APS REFIL DENTINA DA1 1SERX4G FGM`` e
    ``... DENTINA DA2 1SERX4G FGM``. Junta os produtos da mesma marca e do mesmo
    grupo do ERP cujos nomes só diferem num trecho de até ``_TEMPLATE_MAX_SPAN``
    palavras com cara de código (``_looks_like_variant_code``).

    Devolve ``(membros, nome da família, {id: rótulo da variante})`` para cada
    família com 2 ou mais produtos. Cada produto entra em uma família só: os
    moldes com mais membros (e, no empate, mais específicos) escolhem primeiro.
    """
    by_template: Dict[Tuple, Dict[int, Tuple[Dict, int, int]]] = defaultdict(dict)
    for p in products:
        group = p.get("grupo_erp")
        words = (p.get("nome") or "").split()
        if group is None or len(words) < _TEMPLATE_MIN_FIXED_WORDS + 1:
            continue
        folded = [_fold_product_name(w) for w in words]
        bucket = (int(group), _fold_product_name(p.get("marca") or ""))
        for start in range(_TEMPLATE_MIN_HEAD_WORDS, len(words)):
            for span in range(1, _TEMPLATE_MAX_SPAN + 1):
                end = start + span
                if end > len(words) or len(words) - span < _TEMPLATE_MIN_FIXED_WORDS:
                    break
                if not all(_looks_like_variant_code(w) for w in folded[start:end]):
                    break
                key = (bucket, tuple(folded[:start]), tuple(folded[end:]))
                by_template[key].setdefault(int(p["id"]), (p, start, end))

    ranked = sorted(
        (key for key, members in by_template.items() if len(members) >= 2),
        key=lambda key: (-len(by_template[key]), -sum(len(w) for w in key[1] + key[2]), key),
    )
    used: set = set()
    groups: List[Tuple[List[Dict], str, Dict[int, str]]] = []
    for key in ranked:
        free = [m for pid, m in by_template[key].items() if pid not in used]
        if len(free) < 2:
            continue
        labels: Dict[int, str] = {}
        for p, start, end in free:
            labels[int(p["id"])] = " ".join((p.get("nome") or "").split()[start:end])
        # Rótulos repetidos (dois cadastros da mesma cor) não são variantes distintas.
        if len({_fold_product_name(v) for v in labels.values()}) < len(labels):
            continue
        members = sorted((m[0] for m in free), key=lambda m: (_natural_sort_key(labels[int(m["id"])]), int(m["id"])))
        head, head_start, head_end = next(m for m in free if m[0] is members[0])
        head_words = (head.get("nome") or "").split()
        family_name = " ".join(head_words[:head_start] + head_words[head_end:])
        used.update(int(m["id"]) for m in members)
        groups.append((members, family_name, labels))
    return groups


def prepare_catalog_variant_groups(products: List[Dict]) -> List[Dict]:
    """Marca famílias pai/variante: um card no catálogo, variantes só no modal.

    Mutates ``products`` in place and returns the same list.
    """
    if not products:
        return products

    for p in products:
        p["tem_opcoes"] = False
        p["catalog_oculto"] = False
        p["opcoes"] = []
        p["busca_opcoes"] = ""
        p.pop("opcao_de", None)
        p.pop("nome_familia", None)

    # Brindes ficam na lista (o carrinho precisa deles quando a regra os
    # acrescenta), mas sem card e fora das famílias de variantes.
    sellable: List[Dict] = []
    for p in products:
        if is_gift_only_product(p.get("nome") or ""):
            p["catalog_oculto"] = True
        else:
            sellable.append(p)

    parent_ids = _detect_variant_parent_ids_from_products(sellable)
    by_id = {int(p["id"]): p for p in sellable}

    for parent_id in parent_ids:
        parent = by_id.get(int(parent_id))
        if parent is None:
            continue
        children = _catalog_children_of_parent(parent, sellable, parent_ids)
        if not children:
            continue
        _mark_catalog_family(parent, children, include_head=True)

    by_family: Dict[int, List[Dict]] = defaultdict(list)
    for p in sellable:
        if p.get("catalog_oculto") or p.get("tem_opcoes"):
            continue
        family = int(p.get("family_id") or 0)
        if family > 0:
            by_family[family].append(p)
    for _family, group in by_family.items():
        visible = [p for p in group if not p.get("catalog_oculto")]
        if len(visible) < 2:
            continue
        if any(p.get("tem_opcoes") for p in visible):
            continue
        head = min(visible, key=lambda p: (len(p.get("nome") or ""), int(p["id"])))
        _mark_catalog_family(head, visible, include_head=True)

    # Cadastro do ERP: sem família nem produto-base, as variantes só se
    # reconhecem pelo molde do nome (ver ``_name_template_groups``).
    loose = [p for p in sellable if not p.get("catalog_oculto") and not p.get("tem_opcoes")]
    for members, family_name, labels in _name_template_groups(loose):
        for member in members:
            if not (member.get("variante") or "").strip():
                member["variante"] = labels[int(member["id"])]
        head = members[0]
        _mark_catalog_family(head, members, include_head=True)
        # Título do card sem o trecho que varia; o ``nome`` de cada variante
        # fica intacto (é o que vai para o carrinho e o pedido).
        head["nome_familia"] = family_name

    summarize_catalog_option_groups(products)
    return products


def list_products_for_client(
    category: Optional[str] = None,
    query: Optional[str] = None,
    include_out_of_stock: bool = True,
    include_inactive: bool = False,
) -> List[Dict]:
    """Produtos para consumo do front do totem/cliente."""
    sql = "SELECT * FROM products WHERE 1=1"
    params: List = []
    if not include_inactive:
        sql += " AND active = 1"
    if not include_out_of_stock:
        sql += " AND stock > 0"
    if category and category.lower() != "todos":
        sql += " AND LOWER(category) = LOWER(?)"
        params.append(category)
    search_sql, search_params = _product_catalog_like_clause(query, alias="")
    if search_sql:
        sql += f" AND {search_sql}"
        params.extend(search_params)
    order_sql, order_params = _product_search_order_clause(query, alias="")
    sql += f" ORDER BY {order_sql}"
    params.extend(order_params)

    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return prepare_catalog_variant_groups([_product_row_to_client(r) for r in rows])


def list_active_product_stocks() -> List[Dict[str, int]]:
    """Id e estoque dos produtos ativos (mesmo conjunto base do catálogo ao cliente)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, stock FROM products WHERE active = 1 ORDER BY id"
        ).fetchall()
    return [
        {"id": int(r["id"]), "estoque": int(r["stock"] or 0)} for r in rows
    ]


def list_products_admin() -> List[Dict]:
    """Todos os produtos para o painel administrativo (inclui inativos)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM products ORDER BY category, name"
        ).fetchall()
    out: List[Dict] = []
    for r in rows:
        d = _product_row_to_client(r)
        d.update(
            {
                "abaixo_minimo": d["estoque"] < d["estoque_minimo"],
                "sem_estoque": d["estoque"] <= 0,
            }
        )
        out.append(d)
    return out


def list_distinct_product_categories() -> List[str]:
    """Valores distintos de categoria na biblioteca de produtos (filtro admin)."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT TRIM(category)
              FROM products
             WHERE TRIM(COALESCE(category, '')) != ''
             ORDER BY LOWER(TRIM(category))
            """
        ).fetchall()
    # Índice posicional: compatível com todos os sqlite3.Row / builds onde alias falha.
    return [str(row[0]) for row in rows if row[0] is not None and str(row[0]).strip()]


def _admin_products_library_filter_clause(
    q: Optional[str],
    categoria: str,
    status: str,
) -> Tuple[str, List]:
    """Filtros da biblioteca de produtos (saldos agregados em todos os eventos).

    Com texto em ``q``: todas as palavras precisam aparecer em nome, variante
    ou SKU; se o trecho for só dígitos (opc. ``#``), também o ID.
    """
    parts: List[str] = ["1=1"]
    params: List = []
    ev = "COALESCE(ev_agg.ev_stock_total, 0)"
    search_sql, search_params = _product_catalog_like_clause(
        q, include_sku_aliases=True, include_family_id=True
    )
    if search_sql:
        parts.append(search_sql)
        params.extend(search_params)
    if categoria and categoria.lower() != "todos":
        parts.append("LOWER(p.category) = LOWER(?)")
        params.append(categoria)
    st = (status or "todos").strip().lower()
    if st == "ok":
        parts.append(
            f"p.active = 1 AND {ev} > 0 AND "
            f"(p.min_stock <= 0 OR {ev} >= p.min_stock)"
        )
    elif st == "baixo":
        parts.append(f"p.active = 1 AND {ev} > 0 AND {ev} < p.min_stock")
    elif st == "sem_estoque":
        parts.append(f"p.active = 1 AND {ev} <= 0")
    elif st == "inativo":
        parts.append("p.active = 0")
    else:
        parts.append("p.active = 1")
    return " AND ".join(parts), params


_EVT_PRODUCTS_JOIN = """
FROM products p
LEFT JOIN (
    SELECT product_id, COALESCE(SUM(stock), 0) AS ev_stock_total
      FROM event_products
     GROUP BY product_id
) ev_agg ON ev_agg.product_id = p.id
"""


def _admin_products_library_row_to_admin_product(row: sqlite3.Row) -> Dict:
    rd = dict(row)
    ev_total = int(rd.pop("stock_events_total") or 0)
    d = _product_row_to_client(rd)  # type: ignore[arg-type]
    d["estoque"] = ev_total
    d["abaixo_minimo"] = d["estoque_minimo"] > 0 and ev_total < d["estoque_minimo"]
    d["sem_estoque"] = ev_total <= 0
    return d


def _row_to_admin_product(row) -> Dict:
    d = _product_row_to_client(row)
    d.update(
        {
            "abaixo_minimo": d["estoque"] < d["estoque_minimo"],
            "sem_estoque": d["estoque"] <= 0,
        }
    )
    return d


def count_products_admin_filtered(
    q: Optional[str],
    categoria: str = "todos",
    status: str = "todos",
) -> int:
    """Conta produtos na biblioteca admin (filtros sobre saldo agregado nos eventos)."""
    where, params = _admin_products_library_filter_clause(q, categoria, status)
    sql = f"SELECT COUNT(*) AS c {_EVT_PRODUCTS_JOIN} WHERE {where}"
    with get_conn() as conn:
        row = conn.execute(sql, params).fetchone()
    return int(row["c"] if row else 0)


def list_products_admin_slice(
    q: Optional[str],
    categoria: str = "todos",
    status: str = "todos",
    *,
    limit: int,
    offset: int,
) -> List[Dict]:
    """Página da biblioteca de produtos com saldo total nos eventos."""
    where, params = _admin_products_library_filter_clause(q, categoria, status)
    order_sql, order_params = _product_search_order_clause(q, alias="p")
    sql = (
        f"SELECT p.*, COALESCE(ev_agg.ev_stock_total, 0) AS stock_events_total "
        f"{_EVT_PRODUCTS_JOIN} WHERE {where} "
        f"ORDER BY {order_sql} LIMIT ? OFFSET ?"
    )
    qparams = list(params) + list(order_params) + [int(limit), int(max(0, offset))]
    with get_conn() as conn:
        rows = conn.execute(sql, qparams).fetchall()
    return [_admin_products_library_row_to_admin_product(r) for r in rows]


def get_product(product_id: int) -> Optional[Dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM products WHERE id = ?", (int(product_id),)
        ).fetchone()
    if not row:
        return None
    d = _product_row_to_client(row)
    d.update(
        {
            "abaixo_minimo": d["estoque"] < d["estoque_minimo"],
            "sem_estoque": d["estoque"] <= 0,
        }
    )
    return d


def get_product_in_event(event_id: int, product_id: int) -> Optional[Dict]:
    """Catálogo + saldos do produto dentro do evento (formato compatível com ``get_product``)."""
    base = get_product(product_id)
    if base is None:
        return None
    with get_conn() as conn:
        ep = conn.execute(
            "SELECT stock, min_stock, backorder_limit, price FROM event_products "
            "WHERE event_id = ? AND product_id = ?",
            (int(event_id), int(product_id)),
        ).fetchone()
    if ep is None:
        return None
    est = int(ep["stock"] or 0)
    mn = int(ep["min_stock"] or 0)
    library_price = float(base.get("preco") or 0)
    event_price = ep["price"]
    out = dict(base)
    out["estoque"] = est
    out["estoque_minimo"] = mn
    out["backorder_limit"] = int(
        ep["backorder_limit"] if ep["backorder_limit"] is not None else -1
    )
    out["preco_biblioteca"] = library_price
    out["preco"] = float(event_price) if event_price is not None else library_price
    out["preco_evento_override"] = event_price is not None
    out["abaixo_minimo"] = mn > 0 and est < mn
    out["sem_estoque"] = est <= 0
    return out


def update_product_min_stock(product_id: int, min_stock: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE products SET min_stock = ?, updated_at = ? WHERE id = ?",
            (max(0, int(min_stock)), _now_iso(), int(product_id)),
        )
        return cur.rowcount > 0


def update_product_price(product_id: int, price: float) -> bool:
    p = round(float(price), 2)
    if p < 0:
        return False
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE products SET price = ?, updated_at = ? WHERE id = ?",
            (p, _now_iso(), int(product_id)),
        )
        return cur.rowcount > 0


def set_product_active(product_id: int, active: bool) -> bool:
    """Ativo/inativo pelo admin. A decisão dele vale sobre a ativação automática
    de produto que veio do ERP sem preço (``erp_awaiting_price``)."""
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE products SET active = ?, erp_awaiting_price = 0, updated_at = ? WHERE id = ?",
            (1 if active else 0, _now_iso(), int(product_id)),
        )
        return cur.rowcount > 0


def product_awaiting_erp_price(product_id: int) -> bool:
    """Produto que veio do ERP sem preço e está inativo esperando o preço."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM products WHERE id = ? AND active = 0 AND erp_awaiting_price = 1",
            (int(product_id),),
        ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# Sincronização com o ERP
# ---------------------------------------------------------------------------

#: Colunas de ``products`` que a sincronização do ERP pode alterar. Estoque,
#: mínimo, ativo, família e preço do evento são do Totem e nunca são tocados.
_ERP_SYNC_FIELDS = (
    "name", "category", "description", "price", "brand", "erp_codvol",
    "erp_group_code", "anvisa_code", "supplier_ref", "supplier_name",
)


#: Produtos gravados por transação na sincronização do catálogo.
_ERP_UPSERT_BATCH = 100
#: Pausa entre lotes (s). O SQLite não enfileira quem espera para gravar: uma
#: venda só entra se acordar no intervalo entre dois lotes, e o tempo de espera
#: dela chega a 100 ms entre tentativas. Sem pausa, ela pode ficar segundos parada.
_ERP_UPSERT_PAUSE = 0.15


def _erp_text(value) -> Optional[str]:
    """Texto do ERP sem espaços sobrando (``DESCRPROD`` vem com espaços à direita)."""
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _erp_int(value) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def find_local_product_for_codprod(
    conn: sqlite3.Connection, codprod: int
) -> Optional[sqlite3.Row]:
    """Produto local vinculado ao ``CODPROD``: vínculo gravado, SKU igual ou alias."""
    code = int(codprod)
    row = conn.execute(
        "SELECT * FROM products WHERE erp_codprod = ?", (code,)
    ).fetchone()
    if row:
        return row
    row = conn.execute(
        "SELECT * FROM products WHERE sku = ? AND erp_codprod IS NULL "
        "ORDER BY active DESC, id ASC LIMIT 1",
        (str(code),),
    ).fetchone()
    if row:
        return row
    return conn.execute(
        """
        SELECT p.* FROM product_sku_aliases a
          JOIN products p ON p.id = a.product_id
         WHERE a.sku = ? AND p.erp_codprod IS NULL
         LIMIT 1
        """,
        (str(code),),
    ).fetchone()


def upsert_products_from_erp(
    items: Iterable[Dict],
    *,
    image_writer=None,
) -> Dict:
    """Aplica o catálogo do ERP na biblioteca local, sem renumerar nada.

    Cada item: ``codprod`` (obrigatório), ``name``, ``category``, ``group_code``,
    ``brand``, ``codvol``, ``anvisa_code``, ``supplier_ref``, ``supplier_name``,
    ``description``, ``price`` (``None`` = sem preço no ERP) e ``image``
    (bytes ou ``None``).

    - Produto já vinculado (``erp_codprod``, SKU = ``CODPROD`` ou alias) é
      atualizado; o ``id`` local, estoque, eventos, vendas e promoções ficam.
    - Produto novo recebe ``id`` gerado pelo banco (as faixas de ``CODPROD`` e
      dos ids antigos se sobrepõem) e SKU = ``CODPROD``.
    - Produto sem preço mantém o último preço; sem imagem mantém a foto.
    - Produto **novo** sem preço entra inativo (``erp_awaiting_price``) e é
      ativado na sincronização que trouxer o preço, se o admin não tiver
      ativado/desativado antes.
    - Produto que não veio na lista **não** é desativado.
    - Nome da variante herdado da Wake (``variant_name``) é limpo quando o ERP
      manda o nome: a variante passa a exibir o nome do ERP.

    ``image_writer(product_id, data) -> (caminho | None, gravou)`` grava a
    imagem e devolve o caminho local (padrão: ``product_images.store_image_bytes``).
    Os arquivos são gravados depois que o lote é confirmado no banco, fora da
    transação. ``images`` conta só as fotos novas ou trocadas.

    Retorna contadores e até 50 avisos legíveis.
    """
    writer = image_writer or product_images.store_image_bytes
    stats: Dict = {
        "inserted": 0, "updated": 0, "unchanged": 0, "skipped": 0,
        "images": 0, "activated": 0, "warnings": 0, "messages": [], "inserted_ids": [],
    }

    def warn(msg: str) -> None:
        stats["warnings"] += 1
        if len(stats["messages"]) < 50:
            stats["messages"].append(msg)

    now = _now_iso()
    items = list(items)
    # Um lote por transação: o SQLite fica travado para escrita enquanto a
    # transação está aberta, e o checkout não pode esperar o catálogo inteiro.
    for start in range(0, len(items), _ERP_UPSERT_BATCH):
        if start:
            time.sleep(_ERP_UPSERT_PAUSE)
        batch_images: List[Tuple[int, bytes]] = []
        with get_conn() as conn:
            for item in items[start:start + _ERP_UPSERT_BATCH]:
                codprod = _erp_int(item.get("codprod"))
                if not codprod or codprod <= 0:
                    stats["skipped"] += 1
                    continue
                name = _erp_text(item.get("name"))
                price = item.get("price")
                incoming = {
                    "name": name,
                    "category": _erp_text(item.get("category")),
                    "description": _erp_text(item.get("description")),
                    "price": round(float(price), 2) if price is not None else None,
                    "brand": _erp_text(item.get("brand")),
                    "erp_codvol": _erp_text(item.get("codvol")),
                    "erp_group_code": _erp_int(item.get("group_code")),
                    "anvisa_code": _erp_text(item.get("anvisa_code")),
                    "supplier_ref": _erp_text(item.get("supplier_ref")),
                    "supplier_name": _erp_text(item.get("supplier_name")),
                }
                row = find_local_product_for_codprod(conn, codprod)

                if row is None:
                    if not name:
                        warn(f"CODPROD {codprod}: sem descrição, não cadastrado.")
                        stats["skipped"] += 1
                        continue
                    # Sem preço, ativo ele iria ao totem por R$ 0,00 ao entrar num evento.
                    awaiting_price = incoming["price"] is None
                    if awaiting_price:
                        warn(
                            f"CODPROD {codprod} ({name}): novo e sem preço; cadastrado inativo "
                            "até o ERP informar o preço."
                        )
                    sku = str(codprod)
                    if conn.execute("SELECT 1 FROM products WHERE sku = ?", (sku,)).fetchone():
                        sku = f"ERP-{codprod}"
                        warn(f"CODPROD {codprod}: SKU já usado por outro produto; cadastrado como {sku}.")
                    cur = conn.execute(
                        """
                        INSERT INTO products
                            (sku, name, category, description, price, image, stock,
                             min_stock, active, erp_awaiting_price, erp_codprod, erp_codvol,
                             brand, erp_group_code, anvisa_code, supplier_ref, supplier_name,
                             erp_synced_at, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, NULL, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            sku, name, incoming["category"] or "Geral",
                            incoming["description"], incoming["price"] or 0.0,
                            DEFAULT_MIN_STOCK, 0 if awaiting_price else 1,
                            1 if awaiting_price else 0, codprod, incoming["erp_codvol"],
                            incoming["brand"], incoming["erp_group_code"],
                            incoming["anvisa_code"], incoming["supplier_ref"],
                            incoming["supplier_name"], now, now, now,
                        ),
                    )
                    local_id = int(cur.lastrowid)
                    stats["inserted"] += 1
                    stats["inserted_ids"].append(local_id)
                else:
                    local_id = int(row["id"])
                    changes: Dict[str, object] = {}
                    for field in _ERP_SYNC_FIELDS:
                        new = incoming[field]
                        if new is None:
                            continue  # ERP sem o dado: preserva o valor local
                        old = row[field]
                        if field == "price":
                            if old is None or abs(float(old) - float(new)) > 0.004:
                                changes[field] = new
                        elif old != new:
                            changes[field] = new
                    if row["erp_codprod"] is None:
                        changes["erp_codprod"] = codprod
                    if name and (row["variant_name"] or "").strip():
                        # Nome da variante herdado da Wake: com o nome do ERP
                        # valendo, a etiqueta passa a sair dele (``_variant_suffix_from_name``).
                        changes["variant_name"] = None
                    awaiting = bool(row["erp_awaiting_price"])
                    if awaiting and incoming["price"] is not None:
                        # Cadastrado inativo por falta de preço e o preço chegou.
                        changes["price"] = incoming["price"]
                        changes["active"] = 1
                        changes["erp_awaiting_price"] = 0
                        stats["activated"] += 1
                    elif awaiting:
                        warn(f"CODPROD {codprod} ({row['name']}): ainda sem preço no ERP; segue inativo.")
                    # Sem preço em produto já cadastrado é o normal: o preço vem
                    # pela fila de preços (``apply_erp_prices``), não pela de cadastro.
                    if changes:
                        changes["erp_synced_at"] = now
                        changes["updated_at"] = now
                        assignments = ", ".join(f"{k} = ?" for k in changes)
                        conn.execute(
                            f"UPDATE products SET {assignments} WHERE id = ?",
                            (*changes.values(), local_id),
                        )
                        stats["updated"] += 1
                    else:
                        conn.execute(
                            "UPDATE products SET erp_synced_at = ? WHERE id = ?",
                            (now, local_id),
                        )
                        stats["unchanged"] += 1

                if item.get("image"):
                    batch_images.append((local_id, item["image"]))
        # Lote já confirmado: gravar os arquivos não segura o banco para as vendas.
        stats["images"] += _apply_erp_images(batch_images, writer)
    return stats


def _apply_erp_images(images: List[Tuple[int, bytes]], writer) -> int:
    """Grava as fotos de um lote e aponta ``products.image`` para elas.

    Disco primeiro, fora de transação; depois uma transação curta só com os
    caminhos. Devolve quantas fotos mudaram (arquivo novo/trocado ou produto
    que ainda não apontava para a cópia local).
    """
    saved: List[Tuple[int, str, bool]] = []
    for product_id, data in images:
        path, written = writer(product_id, data)
        if path:
            saved.append((product_id, path, written))
    if not saved:
        return 0
    changed = 0
    with get_conn() as conn:
        for product_id, path, written in saved:
            cur = conn.execute(
                "UPDATE products SET image = ? WHERE id = ? AND COALESCE(image, '') != ?",
                (path, product_id, path),
            )
            if written or cur.rowcount:
                changed += 1
    return changed


def get_erp_prices(codprods: Iterable[int]) -> Dict[int, float]:
    """Último preço recebido do ERP para cada ``CODPROD`` (os que não têm ficam de fora)."""
    codes = sorted({int(c) for c in codprods})
    out: Dict[int, float] = {}
    with get_conn() as conn:
        for start in range(0, len(codes), 500):
            chunk = codes[start:start + 500]
            marks = ",".join("?" * len(chunk))
            for r in conn.execute(
                f"SELECT codprod, price FROM erp_prices WHERE codprod IN ({marks})", chunk
            ):
                out[int(r["codprod"])] = float(r["price"])
    return out


def apply_erp_prices(prices: Dict[int, float]) -> Dict:
    """Um lote da fila de preços do ERP: guarda em ``erp_prices`` e aplica no catálogo.

    - Produto cadastrado: preço-base atualizado (o preço por evento não muda).
    - Produto que esperava o preço (``erp_awaiting_price``) é ativado.
    - CODPROD ainda sem produto aqui: só fica guardado, e o produto entra com
      esse preço quando chegar pela fila de cadastro.
    """
    stats = {"prices": 0, "price_changed": 0, "activated": 0, "price_orphans": 0}
    now = _now_iso()
    with get_conn() as conn:
        for codprod, raw in prices.items():
            price = round(float(raw), 2)
            conn.execute(
                "INSERT INTO erp_prices (codprod, price, received_at) VALUES (?, ?, ?) "
                "ON CONFLICT(codprod) DO UPDATE SET price = excluded.price, "
                "received_at = excluded.received_at",
                (int(codprod), price, now),
            )
            stats["prices"] += 1
            row = find_local_product_for_codprod(conn, int(codprod))
            if row is None:
                stats["price_orphans"] += 1
                continue
            changes: Dict[str, object] = {}
            if row["price"] is None or abs(float(row["price"]) - price) > 0.004:
                changes["price"] = price
                stats["price_changed"] += 1
            if row["erp_awaiting_price"] and not row["active"]:
                changes["active"] = 1
                changes["erp_awaiting_price"] = 0
                stats["activated"] += 1
            if row["erp_codprod"] is None:
                changes["erp_codprod"] = int(codprod)
            if changes:
                changes["erp_synced_at"] = now
                changes["updated_at"] = now
                assignments = ", ".join(f"{k} = ?" for k in changes)
                conn.execute(
                    f"UPDATE products SET {assignments} WHERE id = ?",
                    (*changes.values(), int(row["id"])),
                )
    return stats


def get_product_erp_link_summary(limit: int = 50) -> Dict:
    """Produtos ativos sem ``CODPROD`` (não podem ir num pedido ao ERP)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, sku, name FROM products "
            "WHERE erp_codprod IS NULL AND active = 1 ORDER BY id LIMIT ?",
            (int(limit),),
        ).fetchall()
        missing = conn.execute(
            "SELECT COUNT(*) FROM products WHERE erp_codprod IS NULL AND active = 1"
        ).fetchone()[0]
        linked = conn.execute(
            "SELECT COUNT(*) FROM products WHERE erp_codprod IS NOT NULL"
        ).fetchone()[0]
    return {
        "missing": int(missing),
        "linked": int(linked),
        "products": [dict(r) for r in rows],
    }


_CODVOL_RE = re.compile(r"[A-Z0-9]{1,6}")


def set_product_erp_codvol(product_id: int, codvol: str) -> str:
    """Unidade de venda do ERP (``CODVOL``) informada à mão no admin.

    Reserva para quando a fila de cadastro do ERP ainda não trouxe o produto:
    a sincronização que trouxer o cadastro grava o valor do ERP por cima.
    Devolve a unidade normalizada (maiúsculas).
    """
    unit = str(codvol or "").strip().upper()
    if not _CODVOL_RE.fullmatch(unit):
        raise ValueError("Unidade inválida: use a sigla do ERP (ex.: UN, CX, PC).")
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE products SET erp_codvol = ?, updated_at = ? WHERE id = ?",
            (unit, _now_iso(), int(product_id)),
        )
        if cur.rowcount == 0:
            raise ValueError("Produto não encontrado.")
    return unit


def set_product_erp_codprod(product_id: int, codprod: Optional[int]) -> None:
    """Vincula (ou desvincula, com ``None``) um produto local a um ``CODPROD``."""
    with get_conn() as conn:
        if codprod is not None:
            other = conn.execute(
                "SELECT id, name FROM products WHERE erp_codprod = ? AND id != ?",
                (int(codprod), int(product_id)),
            ).fetchone()
            if other:
                raise ValueError(
                    f"O CODPROD {int(codprod)} já está vinculado ao produto "
                    f"#{int(other['id'])} ({other['name']})."
                )
        cur = conn.execute(
            "UPDATE products SET erp_codprod = ?, updated_at = ? WHERE id = ?",
            (int(codprod) if codprod is not None else None, _now_iso(), int(product_id)),
        )
        if cur.rowcount == 0:
            raise ValueError("Produto não encontrado.")


def create_local_product(
    *,
    sku: str,
    name: str,
    category: str,
    price: float,
    image: str = "",
    min_stock: int = DEFAULT_MIN_STOCK,
    description: str = "",
    brand: str = "",
    variant_name: str = "",
    family_id: Optional[int] = None,
    erp_codprod: Optional[int] = None,
    erp_codvol: str = "",
) -> Dict:
    """Cadastra um produto local (demo: sem sincronizar com o ERP).

    Retorna o dict no formato do catálogo. ``brand``/``erp_codprod``/``erp_codvol``
    deixam o produto pronto para a tela de Integração ERP simulada.
    """
    sku_s = (sku or "").strip()
    name_s = (name or "").strip()
    cat_s = (category or "").strip() or "Geral"
    if not sku_s:
        raise ValueError("Informe o SKU do produto.")
    if not name_s:
        raise ValueError("Informe o nome do produto.")
    price_f = round(float(price), 2)
    if price_f < 0:
        raise ValueError("O preço não pode ser negativo.")
    now = _now_iso()
    desc = (description or "").strip() or f"{name_s} — {cat_s}"
    img = (image or "").strip()
    with get_conn() as conn:
        exists = conn.execute(
            "SELECT id FROM products WHERE LOWER(sku) = LOWER(?)",
            (sku_s,),
        ).fetchone()
        if exists:
            raise ValueError(f"Já existe um produto com o SKU {sku_s}.")
        if erp_codprod is not None:
            taken = conn.execute(
                "SELECT id FROM products WHERE erp_codprod = ?", (int(erp_codprod),)
            ).fetchone()
            if taken:
                raise ValueError(f"Já existe um produto com o código ERP {int(erp_codprod)}.")
        cur = conn.execute(
            """
            INSERT INTO products
                (sku, name, category, description, price, image,
                 stock, min_stock, active, created_at, updated_at,
                 brand, variant_name, family_id, erp_codprod, erp_codvol)
            VALUES (?, ?, ?, ?, ?, ?, 0, ?, 1, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sku_s, name_s, cat_s, desc, price_f, img,
                max(0, int(min_stock)), now, now,
                (brand or "").strip() or None,
                (variant_name or "").strip() or None,
                int(family_id) if family_id else None,
                int(erp_codprod) if erp_codprod is not None else None,
                (erp_codvol or "").strip().upper() or None,
            ),
        )
        row = conn.execute(
            "SELECT * FROM products WHERE id = ?",
            (int(cur.lastrowid),),
        ).fetchone()
    return _product_row_to_client(row)
