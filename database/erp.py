"""Integração com o ERP: configuração, fila de envio e montagem de pedidos.

Só persistência e regras sobre o banco. O transporte HTTP fica em
``sankhya_api.py`` e a orquestração (worker da fila, sincronização do
catálogo) em ``erp_sync.py``.

Fluxo de um pedido
------------------
1. A venda é confirmada com AUT → ``enqueue_order_in_conn`` grava uma linha
   ``pedido`` em ``erp_outbox`` e ``transactions.erp_status`` vira ``na_fila``.
2. O worker pega as linhas vencidas (``claim_due_jobs``), garante o
   ``CODPARC`` do cliente e envia o pedido.
3. Sucesso → ``enviado``. Falha de rede → nova tentativa mais tarde, sem
   limite (o estande pode ficar horas sem internet). Recusa do ERP → até
   ``MAX_BUSINESS_ATTEMPTS`` tentativas e depois ``erro``, à espera do admin.
   Login recusado ou endereço do gateway errado → a fila inteira pausa, sem
   gastar tentativas (``release_claimed_jobs``).
4. Estorno de venda já enviada gera uma linha ``cancelamento``; se o pedido
   ainda não tinha saído, ele só é retirado da fila.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import unicodedata
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .connection import _now_iso, get_conn
from .payment_methods import CHECKOUT_METHODS, MAX_CARD_INSTALLMENTS, PAYMENT_METHOD_LABELS

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

#: Chaves editáveis no admin. Credenciais NÃO ficam aqui (vão no ``.env``).
SETTINGS_DEFAULTS: Dict[str, str] = {
    "codemp": "1",
    "codvend": "",
    # Local de estoque do ERP de onde saem os itens vendidos (CODLOCALORIG).
    "codlocalorig": "",
    "orders_enabled": "0",
}

#: Formas com um único CODTIPVENDA; o crédito tem um código por parcela
#: (1x até ``MAX_CARD_INSTALLMENTS``, o mesmo limite do checkout).
SINGLE_CODE_METHODS = tuple(m for m in CHECKOUT_METHODS if m != "credito")

#: Situação do pedido no ERP (``transactions.erp_status``).
ERP_STATUS_LABELS = {
    "nao_enviado": "Não enviado",
    "na_fila": "Na fila",
    "enviado": "Enviado",
    "erro": "Erro no envio",
    "cancelamento_pendente": "Cancelamento na fila",
    "cancelamento_erro": "Erro no cancelamento",
    "cancelado": "Cancelado no ERP",
}

OUTBOX_STATUS_LABELS = {
    "pendente": "Aguardando envio",
    "processando": "Enviando",
    "enviado": "Enviado",
    "erro": "Erro",
    "descartado": "Descartado",
}

#: Recusas do ERP (dado inválido, cadastro incompleto) antes de parar e
#: esperar o admin. Falhas de rede não contam: repetem até a internet voltar.
MAX_BUSINESS_ATTEMPTS = 5
#: Espera entre tentativas (minutos), pela ordem; a última se repete.
RETRY_BACKOFF_MINUTES = (1, 5, 30, 120)
#: Linha presa em ``processando`` (processo caiu no meio do envio).
STALE_PROCESSING_MINUTES = 15


def get_erp_settings() -> Dict[str, str]:
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM erp_settings").fetchall()
    out = dict(SETTINGS_DEFAULTS)
    # Chaves aposentadas (ex.: order_ref_field, hoje IDPEDIDO fixo) ficam de fora.
    out.update({r["key"]: r["value"] for r in rows if r["key"] in SETTINGS_DEFAULTS})
    return out


def save_erp_settings(values: Dict[str, Any]) -> None:
    now = _now_iso()
    with get_conn() as conn:
        for key, value in values.items():
            if key not in SETTINGS_DEFAULTS:
                continue
            conn.execute(
                """
                INSERT INTO erp_settings (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                               updated_at = excluded.updated_at
                """,
                (key, str(value if value is not None else "").strip(), now),
            )


def orders_readiness(settings: Optional[Dict[str, str]] = None) -> List[str]:
    """O que falta configurar no banco para começar a enviar pedidos."""
    cfg = settings or get_erp_settings()
    missing: List[str] = []
    if not (cfg.get("codemp") or "").strip().isdigit():
        missing.append("Empresa (CODEMP)")
    if not (cfg.get("codlocalorig") or "").strip().isdigit():
        missing.append("Local de origem (CODLOCALORIG)")
    with get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM erp_payment_types").fetchone()[0]
    if not n:
        missing.append("Tipos de negociação (CODTIPVENDA)")
    if (cfg.get("orders_enabled") or "0") != "1":
        missing.append("Envio de pedidos ativado")
    return missing


# ---------------------------------------------------------------------------
# Formas de pagamento → tipo de negociação (CODTIPVENDA)
# ---------------------------------------------------------------------------

def list_payment_types() -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT payment_method, installments, codtipvenda, description "
            "FROM erp_payment_types"
        ).fetchall()
    return [dict(r) for r in rows]


def payment_type_grid() -> List[Dict]:
    """Uma linha por combinação que o checkout produz.

    PIX, Pix Inter, débito, dinheiro e faturado têm um código cada; o crédito
    tem um por número de parcelas (1x até ``MAX_CARD_INSTALLMENTS``).

    Com a lista do ERP (``erp_tipvenda``) já consultada, cada linha traz
    ``sankhya`` (o tipo liberado com aquele código, ou ``None`` se o código
    não está mais liberado) e ``suggested`` (código achado pelo nome).
    """
    saved = {
        (r["payment_method"], r["installments"]): r for r in list_payment_types()
    }
    catalog = {t["codtipvenda"]: t for t in list_sankhya_payment_types()}
    grid: List[Dict] = []
    for method in SINGLE_CODE_METHODS:
        row = saved.get((method, None)) or {}
        grid.append({
            "key": method,
            "payment_method": method,
            "installments": None,
            "label": PAYMENT_METHOD_LABELS[method],
            "codtipvenda": row.get("codtipvenda"),
            "description": row.get("description") or "",
        })
    for n in range(1, MAX_CARD_INSTALLMENTS + 1):
        row = saved.get(("credito", n)) or {}
        grid.append({
            "key": f"credito_{n}",
            "payment_method": "credito",
            "installments": n,
            "label": f"Cartão de crédito {n}x" + (" (à vista)" if n == 1 else ""),
            "codtipvenda": row.get("codtipvenda"),
            "description": row.get("description") or "",
        })
    for item in grid:
        code = item["codtipvenda"]
        item["sankhya"] = catalog.get(int(code)) if code is not None else None
        item["suggested"] = suggest_codtipvenda(
            item["payment_method"], item["installments"], catalog.values()
        )
    return grid


def save_payment_types(rows: Iterable[Dict]) -> int:
    """Substitui o mapeamento inteiro. Linha sem código é removida.

    Com a lista do ERP consultada, só aceita códigos liberados nela e
    grava a descrição do ERP (a digitada vale só sem a lista).
    """
    now = _now_iso()
    catalog = {t["codtipvenda"]: t for t in list_sankhya_payment_types()}
    clean: List[Tuple] = []
    for r in rows:
        method = (r.get("payment_method") or "").strip().lower()
        if method not in CHECKOUT_METHODS:
            continue
        inst = r.get("installments")
        inst = int(inst) if inst not in (None, "") else None
        if inst is not None and not 1 <= inst <= MAX_CARD_INSTALLMENTS:
            continue  # parcelamento fora do que o checkout permite
        code = str(r.get("codtipvenda") or "").strip()
        if not code:
            continue
        if not code.isdigit():
            raise ValueError(
                f"CODTIPVENDA inválido para {PAYMENT_METHOD_LABELS[method]}"
                f"{f' {inst}x' if inst else ''}: use só números."
            )
        label = f"{PAYMENT_METHOD_LABELS[method]}{f' {inst}x' if inst else ''}"
        if catalog and int(code) not in catalog:
            raise ValueError(
                f"CODTIPVENDA {code} ({label}) não está entre os tipos de negociação "
                "liberados no ERP. Atualize a lista ou escolha outro código."
            )
        desc = catalog[int(code)]["description"] if catalog else (r.get("description") or "").strip()
        clean.append((method, inst, int(code), desc or None, now))
    with get_conn() as conn:
        conn.execute("DELETE FROM erp_payment_types")
        conn.executemany(
            "INSERT INTO erp_payment_types "
            "(payment_method, installments, codtipvenda, description, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            clean,
        )
    return len(clean)


def list_sankhya_payment_types(include_inactive: bool = False) -> List[Dict]:
    """Tipos de negociação recebidos da fila ``orders/payment``, por código."""
    sql = "SELECT codtipvenda, description, dhalter, active, synced_at FROM erp_tipvenda"
    if not include_inactive:
        sql += " WHERE active = 1"
    with get_conn() as conn:
        rows = conn.execute(sql + " ORDER BY codtipvenda").fetchall()
    return [dict(r) for r in rows]


def sankhya_payment_types_synced_at() -> Optional[str]:
    with get_conn() as conn:
        row = conn.execute("SELECT MAX(synced_at) FROM erp_tipvenda").fetchone()
    return row[0] if row else None


def save_sankhya_payment_types(types: Iterable[Dict]) -> Dict[str, Any]:
    """Grava um lote da fila de tipos de negociação (``orders/payment``).

    A fila só traz o que mudou no ERP: código que não veio continua como
    está. Quem decide se o código vale é o ``STATUS`` de cada item (``active``).

    Devolve ``{received, new, activated, deactivated, in_use_inactive}``:
    ``in_use_inactive`` são códigos desativados neste lote que ainda estão
    escolhidos numa forma de pagamento (pedido com eles para na fila até
    alguém trocar).
    """
    now = _now_iso()
    incoming = {int(t["codtipvenda"]): t for t in types if t.get("codtipvenda") is not None}
    if not incoming:
        return {"received": 0, "new": [], "activated": [], "deactivated": [], "in_use_inactive": []}
    with get_conn() as conn:
        before = {
            int(r["codtipvenda"]): int(r["active"])
            for r in conn.execute("SELECT codtipvenda, active FROM erp_tipvenda").fetchall()
        }
        conn.executemany(
            """
            INSERT INTO erp_tipvenda (codtipvenda, description, dhalter, active, synced_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(codtipvenda) DO UPDATE SET description = excluded.description,
                                                   dhalter = excluded.dhalter,
                                                   active = excluded.active,
                                                   synced_at = excluded.synced_at
            """,
            [
                (code, t.get("description"), t.get("dhalter"), 1 if t.get("active", True) else 0, now)
                for code, t in incoming.items()
            ],
        )
        # Descrição dos códigos já escolhidos acompanha o nome no ERP.
        for code, t in incoming.items():
            conn.execute(
                "UPDATE erp_payment_types SET description = ? WHERE codtipvenda = ?",
                (t.get("description"), code),
            )
        in_use = {
            int(r["codtipvenda"])
            for r in conn.execute("SELECT DISTINCT codtipvenda FROM erp_payment_types").fetchall()
        }
    active_now = {code for code, t in incoming.items() if t.get("active", True)}
    deactivated = sorted(code for code in incoming if code not in active_now and before.get(code) == 1)
    return {
        "received": len(incoming),
        "new": sorted(code for code in active_now if code not in before),
        "activated": sorted(code for code in active_now if before.get(code) == 0),
        "deactivated": deactivated,
        "in_use_inactive": sorted(code for code in deactivated if code in in_use),
    }


def _fold_upper(text: str) -> str:
    norm = unicodedata.normalize("NFD", str(text or "").upper())
    return "".join(ch for ch in norm if unicodedata.category(ch) != "Mn")


def suggest_codtipvenda(
    payment_method: str, installments: Optional[int], catalog: Iterable[Dict]
) -> Optional[int]:
    """Código do ERP cujo nome corresponde à forma de pagamento.

    Pelos nomes vistos em 09/10/2026: "POS PIX CIELO", "PIX/TRANSF INTER",
    "POS CARTAO DEBITO", "A VISTA", "FATURADO", "POS CARTAO 3X". Só sugere
    quando exatamente um nome casa; senão, ``None``.
    """
    def matches(desc: str) -> bool:
        d = _fold_upper(desc)
        if payment_method == "pix":
            return "PIX" in d and "INTER" not in d
        if payment_method == "pix_inter":
            return "PIX" in d and "INTER" in d
        if payment_method == "debito":
            return "DEBITO" in d
        if payment_method == "dinheiro":
            return "DINHEIRO" in d or (re.search(r"\bA VISTA\b", d) is not None and "CARTAO" not in d)
        if payment_method == "faturado":
            return "FATURADO" in d
        if payment_method == "credito":
            m = re.search(r"(\d+)\s*X\b", d)
            return ("CARTAO" in d or "CREDITO" in d) and "DEBITO" not in d and m is not None \
                and int(m.group(1)) == int(installments or 1)
        return False

    found = [int(t["codtipvenda"]) for t in catalog if matches(t.get("description") or "")]
    return found[0] if len(found) == 1 else None


def unused_sankhya_payment_types() -> List[Dict]:
    """Tipos liberados no ERP que nenhuma forma de pagamento do Totem usa."""
    used = {int(r["codtipvenda"]) for r in list_payment_types()}
    return [t for t in list_sankhya_payment_types() if int(t["codtipvenda"]) not in used]


def resolve_codtipvenda(
    conn: sqlite3.Connection, payment_method: Optional[str], installments: Optional[int]
) -> Optional[int]:
    """Regra mais específica vence: crédito 3x antes de "crédito, qualquer parcela".

    Vendas antigas com ``cartao`` (antes da separação crédito/débito) usam os
    códigos do crédito.
    """
    method = (payment_method or "").strip().lower()
    if method == "cartao":
        method = "credito"
    if method != "credito":
        installments = None
    elif not installments:
        installments = 1
    row = None
    if installments is not None:
        row = conn.execute(
            "SELECT codtipvenda FROM erp_payment_types "
            "WHERE payment_method = ? AND installments = ?",
            (method, int(installments)),
        ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT codtipvenda FROM erp_payment_types "
            "WHERE payment_method = ? AND installments IS NULL",
            (method,),
        ).fetchone()
    return int(row["codtipvenda"]) if row else None


# ---------------------------------------------------------------------------
# Clientes (CPF → CODPARC)
# ---------------------------------------------------------------------------

def _digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def get_cached_codparc(cpf: Any) -> Optional[int]:
    digits = _digits(cpf)
    if not digits:
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT codparc FROM erp_customers WHERE cpf = ?", (digits,)
        ).fetchone()
    return int(row["codparc"]) if row else None


def save_customer_codparc(cpf: Any, codparc: int, name: Optional[str] = None) -> None:
    digits = _digits(cpf)
    if not digits:
        return
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO erp_customers (cpf, codparc, name, synced_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(cpf) DO UPDATE SET codparc = excluded.codparc,
                                           name = excluded.name,
                                           synced_at = excluded.synced_at
            """,
            (digits, int(codparc), name, _now_iso()),
        )


# ---------------------------------------------------------------------------
# Montagem do pedido
# ---------------------------------------------------------------------------

def _valid_cpf(digits: str) -> bool:
    if len(digits) != 11 or digits == digits[0] * 11:
        return False
    for size in (9, 10):
        total = sum(int(digits[i]) * (size + 1 - i) for i in range(size))
        check = (total * 10) % 11 % 10
        if check != int(digits[size]):
            return False
    return True


def _erp_city(value: Any) -> str:
    """Cidade no padrão do cadastro do ERP: maiúsculas, sem acento nem apóstrofo."""
    text = unicodedata.normalize("NFD", str(value or "").strip())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("'", "").replace("´", "").replace("`", "")
    return " ".join(text.upper().split())


def resolve_codvend(
    conn: sqlite3.Connection, tx: Dict, settings: Dict[str, str]
) -> Tuple[Optional[int], Optional[str]]:
    """``CODVEND`` do vendedor que fez a venda. Devolve ``(codvend, problema)``.

    Vendedor sem código: a venda **não** sai com o código de outra pessoa; fica
    na fila com erro até o admin cadastrar o CODVEND dele. O CODVEND padrão das
    configurações só vale para venda cujo vendedor foi excluído do Totem.
    """
    seller_id = tx.get("seller_id")
    if seller_id:
        row = conn.execute(
            "SELECT name, erp_codvend FROM sellers WHERE id = ?", (int(seller_id),)
        ).fetchone()
        if row is not None:
            if row["erp_codvend"] is not None:
                return int(row["erp_codvend"]), None
            return None, (
                f"Vendedor {row['name']} sem CODVEND do ERP "
                "(cadastre em Vendedores e reenvie)"
            )
    fallback = str(settings.get("codvend") or "").strip()
    if fallback.isdigit():
        return int(fallback), None
    who = tx.get("seller_name") or "excluído"
    return None, (
        f"Vendedor {who} não existe mais no Go Sell e não há CODVEND padrão configurado"
    )


def client_problems(tx: Dict) -> List[str]:
    """Dados do cliente que o ERP exige e que faltam (ou são inválidos) na venda.

    ``tx`` usa as chaves de ``transactions`` (``client_cpf``, ``client_zipcode``...).
    Vale na montagem do cadastro do cliente.

    Demo: o ERP simulado aceita cliente sem endereço; só CPF válido e nome são
    exigidos para cadastrar o parceiro. Venda sem CPF vai como consumidor final
    (``CONSUMIDOR_FINAL_CODPARC``, ver ``erp_sync._process_order``).
    """
    problems: List[str] = []
    if not _valid_cpf(_digits(tx.get("client_cpf"))):
        problems.append("CPF válido")
    if not str(tx.get("client_name") or "").strip():
        problems.append("nome")
    return problems


#: Parceiro genérico do ERP simulado para vendas sem CPF.
CONSUMIDOR_FINAL_CODPARC = 1


def has_valid_client_cpf(tx: Dict) -> bool:
    return _valid_cpf(_digits(tx.get("client_cpf")))


def require_client_for_erp(tx: Dict) -> None:
    """Demo: o checkout não bloqueia por dados do cliente (CPF e endereço são opcionais)."""
    return None


def build_customer_payload(
    tx: Dict, settings: Dict[str, str], codparc: Optional[int], codvend: Optional[int] = None
) -> Tuple[Dict, List[str]]:
    """Corpo de ``/clientes/create-update-geral`` a partir dos dados gravados na venda."""
    problems = client_problems(tx)
    cpf = _digits(tx.get("client_cpf"))
    name = " ".join(str(tx.get("client_name") or "").split())
    address = {
        "ENDERECO": " ".join(str(tx.get("client_address") or "").split()),
        "CEP": _digits(tx.get("client_zipcode")),
        "NUMEND": " ".join(str(tx.get("client_number") or "").split()) or "S/N",
        "COMPLENDERECO": " ".join(str(tx.get("client_complement") or "").split()),
        "BAIRRO": " ".join(str(tx.get("client_neighborhood") or "").split()),
        "CIDADE": _erp_city(tx.get("client_city")),
        "ESTADO": str(tx.get("client_state") or "").strip().upper(),
    }
    phone = _digits(tx.get("client_phone"))
    if phone.startswith("55") and len(phone) > 11:
        phone = phone[2:]
    payload: Dict[str, Any] = {
        "CODPARC": str(codparc) if codparc else "",
        "TIPPESSOA": "F",
        "NOMEPARC": name,
        "RAZAOSOCIAL": name,
        "CGC_CPF": cpf,
        "IDENTINSCESTAD": "ISENTO",
        "CODVEND": str(codvend) if codvend else "0",
        "TELEFONE": phone if len(phone) == 10 else "",
        "CELULAR": phone if len(phone) == 11 else "",
        "EMAIL": str(tx.get("client_email") or "").strip(),
        "TIPOENDERECO": "",
        **address,
    }
    if problems:
        problems = ["Cadastro do cliente incompleto: " + ", ".join(problems)]
    return payload, problems


#: CONTROLE do ERP para produto sem controle de lote.
NO_LOT_CONTROLE = " "


def allocate_lots(
    conn: sqlite3.Connection,
    codprod: int,
    codlocal: Optional[int],
    qty: int,
    label: str = "",
    exclude_tx: Optional[int] = None,
    taken: Optional[Dict[Tuple[int, int, str], float]] = None,
) -> Tuple[List[Tuple[str, int]], Optional[str]]:
    """Reparte ``qty`` entre os lotes (``CONTROLE``) do produto no local de origem.

    Como no ERP, cada unidade sai de um lote que tem saldo naquele local:
    - saldo de cada lote = ``DISPONIVEL`` recebido do ERP menos o que pedidos
      já enviados tiraram dele desde então (``erp_stock_allocations``; a venda
      ``exclude_tx`` não conta contra ela mesma) e menos ``taken`` (linhas
      anteriores deste mesmo pedido);
    - começa pelo lote de maior saldo, para dividir o item o mínimo possível;
    - produto sem controle de lote tem um "lote" só, o ``' '``.

    Devolve ``([(controle, quantidade), ...], problema)``. Com problema (sem
    estoque recebido, sem saldo no local, saldo menor que a venda) o pedido não
    sai: o ERP recusaria.
    """
    name = label or str(codprod)
    if codlocal is None:
        return [], None  # falta o local; o problema já foi apontado no cabeçalho
    rows = conn.execute(
        """
        SELECT s.controle, s.disponivel,
               COALESCE((SELECT SUM(a.qty) FROM erp_stock_allocations a
                          WHERE a.codprod = s.codprod AND a.codlocal = s.codlocal
                            AND a.controle = s.controle
                            AND a.created_at >= s.received_at
                            AND a.transaction_id != ?), 0) AS used
          FROM erp_stock s
         WHERE s.codprod = ? AND s.codlocal = ?
        """,
        (int(exclude_tx or 0), int(codprod), int(codlocal)),
    ).fetchall()
    if not rows:
        known = conn.execute(
            "SELECT 1 FROM erp_stock WHERE codprod = ? LIMIT 1", (int(codprod),)
        ).fetchone()
        if not known:
            return [], (
                f"Produto {name} sem estoque/lote recebido do ERP; "
                "sincronize o catálogo (fila de estoque)"
            )
        return [], f"Produto {name} sem estoque no local {codlocal}"
    taken = taken if taken is not None else {}
    lots = []
    for r in rows:
        controle = str(r["controle"])
        free = float(r["disponivel"] or 0) - float(r["used"] or 0)
        free -= taken.get((int(codprod), int(codlocal), controle), 0)
        if free >= 1:
            lots.append((controle, int(free)))
    lots.sort(key=lambda lot: (-lot[1], lot[0]))
    parts: List[Tuple[str, int]] = []
    remaining = int(qty)
    for controle, free in lots:
        if remaining <= 0:
            break
        use = min(free, remaining)
        parts.append((controle, use))
        remaining -= use
    if remaining > 0:
        available = int(qty) - remaining
        return [], (
            f"Produto {name}: saldo insuficiente no local {codlocal} "
            f"(disponível {available}, vendido {int(qty)}); sincronize o catálogo "
            "ou confira o estoque no ERP"
        )
    for controle, use in parts:
        key = (int(codprod), int(codlocal), controle)
        taken[key] = taken.get(key, 0) + use
    return parts, None


def record_allocations_in_conn(conn: sqlite3.Connection, tx_id: int, payload: Dict) -> None:
    """Grava o que o pedido enviado tirou de cada lote (substitui o registro anterior)."""
    conn.execute("DELETE FROM erp_stock_allocations WHERE transaction_id = ?", (int(tx_id),))
    now = _now_iso()
    for item in (payload or {}).get("itens") or []:
        try:
            codprod = int(item["CODPROD"])
            codlocal = int(item["CODLOCALORIG"])
            qty = float(item["QTDNEG"])
        except (KeyError, TypeError, ValueError):
            continue
        conn.execute(
            "INSERT INTO erp_stock_allocations "
            "(transaction_id, codprod, codlocal, controle, qty, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (int(tx_id), codprod, codlocal, str(item.get("CONTROLE") or NO_LOT_CONTROLE), qty, now),
        )


def record_allocations(tx_id: int, payload: Dict) -> None:
    with get_conn() as conn:
        record_allocations_in_conn(conn, tx_id, payload)


def release_allocations_in_conn(conn: sqlite3.Connection, tx_id: int) -> None:
    """Pedido cancelado ou que nunca chegou ao ERP: devolve o saldo dos lotes."""
    conn.execute("DELETE FROM erp_stock_allocations WHERE transaction_id = ?", (int(tx_id),))


def apply_erp_stock(rows: Iterable[Dict], replaced: set) -> int:
    """Um lote da fila de estoque (``/products/stock``) em ``erp_stock``.

    Cada produto que chega traz a situação atual dele: na primeira vez que
    aparece numa sincronização, as linhas antigas dele são apagadas (lote que
    acabou some). ``replaced`` guarda esses produtos entre os lotes da mesma
    sincronização, para um produto dividido em dois lotes não perder a 1ª parte.
    """
    now = _now_iso()
    n = 0
    with get_conn() as conn:
        for r in rows:
            try:
                codprod = int(r.get("CODPROD"))
                codlocal = int(r.get("CODLOCAL"))
            except (TypeError, ValueError):
                continue
            raw = r.get("CONTROLE")
            controle = str(raw) if raw is not None and str(raw).strip() else NO_LOT_CONTROLE
            if codprod not in replaced:
                conn.execute("DELETE FROM erp_stock WHERE codprod = ?", (codprod,))
                replaced.add(codprod)
            values = []
            for key in ("ESTOQUE", "RESERVADO", "DISPONIVEL"):
                try:
                    values.append(float(r.get(key) or 0))
                except (TypeError, ValueError):
                    values.append(0.0)
            conn.execute(
                "INSERT OR REPLACE INTO erp_stock "
                "(codprod, codlocal, controle, estoque, reservado, disponivel, received_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (codprod, codlocal, controle, *values, now),
            )
            n += 1
    return n


def _fmt_dtneg(created_at: Any) -> str:
    raw = str(created_at or "").strip()
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        dt = datetime.now()
    return dt.strftime("%d/%m/%Y")


def build_order_payload(
    conn: sqlite3.Connection,
    tx: Dict,
    settings: Dict[str, str],
    codparc: int,
    codvend: Optional[int] = None,
) -> Tuple[Dict, List[str]]:
    """Corpo de ``/orders/create-order``. Devolve ``(payload, problemas)``; com problema não envia.

    Formato do exemplo do integrador (08/10/2026): ``IDPEDIDO`` no cabeçalho é o
    sequencial inteiro da venda (``ensure_idpedido``; o campo no ERP é
    numérico, então o ``OM...`` não serve); cada item leva
    ``SEQITEMPED`` ("1", "2"... na ordem da venda), ``CODLOCALORIG`` (local de
    estoque configurado) e ``CONTROLE`` (lote, de ``allocate_lots``). ``NUMAUT``
    no cabeçalho é o AUT registrado na confirmação do pagamento. Venda com
    retirada pendente leva ``OBSINTERNA`` (``pending_pickup_note``) no cabeçalho. Item cuja
    quantidade não cabe num lote só é dividido em uma linha por lote, com o
    mesmo preço e o desconto repartido pela quantidade.

    - ``VLRUNIT`` é o preço de lista do item (antes da promoção).
    - ``VLRDESC`` é o desconto total do item: promoção + rateio do desconto
      manual do vendedor (``transactions.total`` abaixo da soma dos itens), de
      modo que o pedido no ERP feche no valor que o cliente pagou.
    """
    problems: List[str] = []
    if not tx.get("erp_idpedido"):
        problems.append("Venda sem IDPEDIDO")
    raw_local = str(settings.get("codlocalorig") or "").strip()
    codlocal = int(raw_local) if raw_local.isdigit() else None
    if codlocal is None:
        problems.append("Local de origem (CODLOCALORIG) não configurado")
    codtipvenda = resolve_codtipvenda(
        conn, tx.get("payment_method"), tx.get("card_installments")
    )
    if codtipvenda is None:
        pm = (tx.get("payment_method") or "?").strip().lower()
        if pm == "cartao":
            pm = "credito"
        label = PAYMENT_METHOD_LABELS.get(pm, pm)
        if pm == "credito":
            label += f" {int(tx.get('card_installments') or 1)}x"
        problems.append(f"Sem CODTIPVENDA cadastrado para {label}")
    else:
        tv = conn.execute(
            "SELECT active FROM erp_tipvenda WHERE codtipvenda = ?", (int(codtipvenda),)
        ).fetchone()
        has_catalog = conn.execute("SELECT 1 FROM erp_tipvenda LIMIT 1").fetchone() is not None
        if has_catalog and (tv is None or not int(tv["active"])):
            # Recusado pelo ERP de qualquer forma: para aqui, com o motivo claro.
            problems.append(
                f"CODTIPVENDA {codtipvenda} não está mais entre os tipos de negociação "
                "liberados no ERP; troque o código em Tipos de negociação"
            )
    # AUT gravado na confirmação do pagamento (maquininha, comprovante do Pix
    # Inter ou o interno DINHEIRO/FATURADO), enviado como texto.
    numaut = str(tx.get("aut") or "").strip() or None
    if numaut is None:
        problems.append("Venda sem AUT (NUMAUT)")

    items = conn.execute(
        """
        SELECT ti.id, ti.product_id, ti.product_sku, ti.product_name,
               ti.unit_price, ti.original_price, ti.quantity, ti.subtotal,
               ti.quantity_delivered, ti.gift_item_id,
               p.erp_codprod, p.erp_codvol
          FROM transaction_items ti
          LEFT JOIN products p ON p.id = CAST(ti.product_id AS INTEGER)
         WHERE ti.transaction_id = ?
         ORDER BY ti.id
        """,
        (int(tx["id"]),),
    ).fetchall()

    lines: List[Dict] = []
    taken: Dict[Tuple[int, int, str], float] = {}  # lotes já usados neste pedido
    pending: List[Tuple[str, str, int]] = []  # (CODPROD, nome, qtd) a retirar depois
    avulsos: List[Tuple[str, int]] = []  # brindes avulsos: só na observação interna
    for it in items:
        qty = int(it["quantity"] or 0)
        if qty <= 0:
            continue
        if it["gift_item_id"] is not None:
            # Brinde avulso não existe no ERP (sem CODPROD): não vira item.
            avulsos.append(((it["product_name"] or "Brinde").strip(), qty))
            continue
        codprod = it["erp_codprod"]
        codvol = it["erp_codvol"]
        if codprod is None and (it["product_sku"] or "").strip():
            alt = conn.execute(
                "SELECT erp_codprod, erp_codvol FROM products "
                "WHERE sku = ? AND erp_codprod IS NOT NULL",
                (it["product_sku"].strip(),),
            ).fetchone()
            if alt:
                codprod, codvol = alt["erp_codprod"], alt["erp_codvol"]
        label = (it["product_sku"] or it["product_name"] or "?").strip()
        if codprod is None:
            problems.append(f"Produto {label} sem CODPROD do ERP")
            continue
        if not (codvol or "").strip():
            problems.append(
                f"Produto {label} sem unidade (CODVOL); informe a unidade na Fila de envio "
                "ou sincronize o catálogo"
            )
        unit = float(it["unit_price"] or 0)
        original = it["original_price"]
        list_price = float(original) if original is not None and float(original) >= unit else unit
        net = float(it["subtotal"] if it["subtotal"] is not None else unit * qty)
        parts, problem = allocate_lots(
            conn, int(codprod), codlocal, qty, label, exclude_tx=int(tx["id"]), taken=taken
        )
        if problem:
            problems.append(problem)
        pending_qty = qty - max(0, int(it["quantity_delivered"] or 0))
        if pending_qty > 0:
            pending.append((str(codprod), (it["product_name"] or label).strip(), pending_qty))
        lines.append({
            "codprod": int(codprod),
            "lots": parts,
            "codvol": (codvol or "").strip(),
            "qty": qty,
            "list_price": round(list_price, 2),
            "gross": round(list_price * qty, 2),
            "net": round(net, 2),
        })
    if not lines and not problems:
        problems.append("Pedido sem itens")

    # Desconto manual do vendedor: rateado pelo valor líquido dos itens.
    net_sum = round(sum(line["net"] for line in lines), 2)
    total = round(float(tx.get("total") or 0), 2)
    extra = round(net_sum - total, 2) if net_sum - total > 0.004 else 0.0
    if extra and net_sum > 0:
        # Nenhum item recebe mais desconto do que vale; o resto dos centavos
        # vai para os itens que ainda têm saldo (o líquido nunca fica negativo).
        remaining = extra
        for line in lines:
            share = min(round(extra * line["net"] / net_sum, 2), line["net"], remaining)
            line["net"] = round(line["net"] - share, 2)
            remaining = round(remaining - share, 2)
        for line in lines:
            if remaining <= 0:
                break
            share = min(line["net"], remaining)
            line["net"] = round(line["net"] - share, 2)
            remaining = round(remaining - share, 2)

    itens = []
    for line in lines:
        desc = round(max(0.0, line["gross"] - line["net"]), 2)
        perc = round(desc / line["gross"] * 100, 4) if line["gross"] > 0 else 0
        # Uma linha por lote; o desconto acompanha a quantidade e a última
        # parte leva os centavos que sobram (a soma fecha no desconto do item).
        parts = line["lots"] or [(NO_LOT_CONTROLE, line["qty"])]
        desc_left = desc
        for i, (controle, part_qty) in enumerate(parts):
            if i == len(parts) - 1:
                part_desc = round(desc_left, 2)
            else:
                part_desc = round(desc * part_qty / line["qty"], 2)
                desc_left = round(desc_left - part_desc, 2)
            itens.append({
                "CODPROD": str(line["codprod"]),
                "CODLOCALORIG": str(codlocal or ""),
                "CONTROLE": controle,
                "QTDNEG": part_qty,
                "VLRUNIT": line["list_price"],
                "CODVOL": line["codvol"],
                "SEQITEMPED": str(len(itens) + 1),
                "PERCDESC": perc,
                "VLRDESC": part_desc,
            })

    payload: Dict[str, Any] = {
        "CODPARC": int(codparc) if codparc else None,
        "DTNEG": _fmt_dtneg(tx.get("created_at")),
        "CODTIPVENDA": codtipvenda,
        "CODVEND": int(codvend) if codvend else None,
        "CODEMP": int(settings["codemp"]) if str(settings.get("codemp") or "").isdigit() else None,
        "IDPEDIDO": str(tx["erp_idpedido"]) if tx.get("erp_idpedido") else None,
        "NUMAUT": numaut,
        "VLRFRETE": 0,
        "itens": itens,
    }
    obs = pending_pickup_note(tx, pending, avulsos)
    if obs:
        payload["OBSINTERNA"] = obs
    return payload, problems


#: Tamanho máximo do texto enviado em ``OBSINTERNA`` (observação interna do pedido).
OBSINTERNA_MAX = 4000


def pending_pickup_note(
    tx: Dict,
    pending: List[Tuple[str, str, int]],
    avulsos: Optional[List[Tuple[str, int]]] = None,
) -> Optional[str]:
    """Texto da observação interna: retirada pendente e brindes avulsos.

    Retirada pendente: uma linha por produto, ``CODPROD - nome - quantidade``,
    com a situação no momento em que o pedido vai ao ERP. Brindes avulsos
    (cadastrados só no Go Sell, sem CODPROD): ``nome - quantidade``. Sem nenhum
    dos dois, ``None`` (o campo nem vai no corpo).
    """
    if not pending and not avulsos:
        return None
    order = str(tx.get("order_number") or "").strip()
    suffix = f" (Go Sell, pedido {order}):" if order else ":"
    lines: List[str] = []
    if pending:
        totals: Dict[Tuple[str, str], int] = {}
        for code, name, qty in pending:  # mesmo produto em duas linhas (ex.: brinde) soma
            totals[(code, name)] = totals.get((code, name), 0) + int(qty)
        lines.append("Itens com retirada pendente" + suffix)
        lines += [f"{code} - {name} - {qty} un." for (code, name), qty in totals.items()]
    if avulsos:
        gift_totals: Dict[str, int] = {}
        for name, qty in avulsos:
            gift_totals[name] = gift_totals.get(name, 0) + int(qty)
        lines.append("Brindes avulsos entregues, fora do ERP" + suffix)
        lines += [f"{name} - {qty} un." for name, qty in gift_totals.items()]
    text = "\n".join(lines)
    if len(text) > OBSINTERNA_MAX:
        text = text[: OBSINTERNA_MAX - 1].rstrip() + "…"
    return text


# ---------------------------------------------------------------------------
# Fila de envio (erp_outbox)
# ---------------------------------------------------------------------------

def idpedido_base() -> int:
    """``TOTEM_IDPEDIDO_BASE``: somado ao sequencial. Banco de testes que envie ao
    mesmo ERP usa outra faixa (ex.: 900000000) para não repetir IDPEDIDO."""
    raw = (os.environ.get("TOTEM_IDPEDIDO_BASE") or "").strip()
    return int(raw) if raw.isdigit() else 0


def ensure_idpedido(conn: sqlite3.Connection, tx_id: int) -> int:
    """IDPEDIDO (inteiro) da venda; cria na primeira chamada. Nunca muda depois.

    O campo do ERP é numérico, então o número ``OM...`` não serve. O
    sequencial fica em ``erp_counters`` e só anda para a frente: venda apagada
    ou reset do totem não devolvem números já usados.
    """
    row = conn.execute(
        "SELECT erp_idpedido FROM transactions WHERE id = ?", (int(tx_id),)
    ).fetchone()
    if row is None:
        raise ValueError(f"Venda {tx_id} não existe.")
    if row["erp_idpedido"]:
        return int(row["erp_idpedido"])
    conn.execute("INSERT OR IGNORE INTO erp_counters (name, value) VALUES ('idpedido', 0)")
    base = idpedido_base()
    while True:
        conn.execute("UPDATE erp_counters SET value = value + 1 WHERE name = 'idpedido'")
        value = conn.execute(
            "SELECT value FROM erp_counters WHERE name = 'idpedido'"
        ).fetchone()["value"]
        candidate = base + int(value)
        taken = conn.execute(
            "SELECT 1 FROM transactions WHERE erp_idpedido = ?", (candidate,)
        ).fetchone()
        if not taken:
            break
    conn.execute(
        "UPDATE transactions SET erp_idpedido = ? WHERE id = ?", (candidate, int(tx_id))
    )
    return candidate


def enqueue_order_in_conn(conn: sqlite3.Connection, tx_id: int) -> bool:
    """Põe a venda confirmada na fila. Idempotente (``UNIQUE (kind, transaction_id)``)."""
    ensure_idpedido(conn, tx_id)
    now = _now_iso()
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO erp_outbox
            (kind, transaction_id, status, next_attempt_at, created_at, updated_at)
        VALUES ('pedido', ?, 'pendente', ?, ?, ?)
        """,
        (int(tx_id), now, now, now),
    )
    if cur.rowcount:
        conn.execute(
            "UPDATE transactions SET erp_status = 'na_fila' WHERE id = ?", (int(tx_id),)
        )
    return bool(cur.rowcount)


def handle_refund_in_conn(conn: sqlite3.Connection, tx_id: int) -> str:
    """Estorno: retira da fila o pedido não enviado ou enfileira o cancelamento.

    Retorna ``'fora_da_fila'``, ``'retirado'`` ou ``'cancelamento'``.
    """
    order = conn.execute(
        "SELECT id, status, last_error FROM erp_outbox "
        "WHERE kind = 'pedido' AND transaction_id = ?",
        (int(tx_id),),
    ).fetchone()
    if order is None:
        return "fora_da_fila"
    now = _now_iso()
    if order["status"] in ("pendente", "descartado") or (
        order["status"] == "erro" and not is_uncertain_send(order)
    ):
        conn.execute(
            "UPDATE erp_outbox SET status = 'descartado', last_error = ?, updated_at = ? "
            "WHERE id = ?",
            ("Venda estornada antes do envio ao ERP.", now, int(order["id"])),
        )
        conn.execute(
            "UPDATE transactions SET erp_status = 'nao_enviado' WHERE id = ?", (int(tx_id),)
        )
        return "retirado"
    conn.execute(
        """
        INSERT OR IGNORE INTO erp_outbox
            (kind, transaction_id, status, next_attempt_at, created_at, updated_at)
        VALUES ('cancelamento', ?, 'pendente', ?, ?, ?)
        """,
        (int(tx_id), now, now, now),
    )
    conn.execute(
        "UPDATE transactions SET erp_status = 'cancelamento_pendente' WHERE id = ?",
        (int(tx_id),),
    )
    return "cancelamento"


def note_items_changed_in_conn(conn: sqlite3.Connection, tx_id: int) -> Optional[str]:
    """Item trocado depois da confirmação.

    Pedido ainda na fila: nada a fazer, o corpo é montado na hora do envio.
    Pedido saindo neste instante ou já enviado: o ERP não é atualizado
    sozinho; devolve um aviso.
    """
    sending = conn.execute(
        "SELECT 1 FROM erp_outbox WHERE kind = 'pedido' AND transaction_id = ? "
        "AND status = 'processando'",
        (int(tx_id),),
    ).fetchone()
    if sending:
        return (
            "Este pedido estava sendo enviado ao ERP no momento da troca e pode "
            "ter saído com o item antigo. Confira o pedido no ERP."
        )
    row = conn.execute(
        "SELECT erp_status, erp_order_id FROM transactions WHERE id = ?", (int(tx_id),)
    ).fetchone()
    if row and row["erp_status"] == "enviado":
        ref = f" (pedido {row['erp_order_id']})" if row["erp_order_id"] else ""
        return (
            f"Este pedido já foi enviado ao ERP{ref}. "
            "Ajuste o item também no ERP."
        )
    return None


def enqueue_unsent_orders(event_id: Optional[int] = None) -> int:
    """Envio explícito do histórico: vendas confirmadas que nunca foram à fila."""
    sql = (
        "SELECT t.id FROM transactions t "
        "WHERE t.status = 'confirmado' AND COALESCE(t.erp_status, 'nao_enviado') = 'nao_enviado' "
        "AND NOT EXISTS (SELECT 1 FROM erp_outbox o "
        "                WHERE o.transaction_id = t.id AND o.kind = 'pedido' "
        "                  AND o.status != 'descartado')"
    )
    params: List[Any] = []
    if event_id is not None:
        sql += " AND t.event_id = ?"
        params.append(int(event_id))
    with get_conn() as conn:
        ids = [int(r[0]) for r in conn.execute(sql, params).fetchall()]
        now = _now_iso()
        for tx_id in ids:
            ensure_idpedido(conn, tx_id)
            conn.execute(
                """
                INSERT INTO erp_outbox
                    (kind, transaction_id, status, next_attempt_at, created_at, updated_at)
                VALUES ('pedido', ?, 'pendente', ?, ?, ?)
                ON CONFLICT(kind, transaction_id) DO UPDATE SET
                    status = 'pendente', attempts = 0, last_error = NULL,
                    next_attempt_at = excluded.next_attempt_at,
                    updated_at = excluded.updated_at
                """,
                (tx_id, now, now, now),
            )
            conn.execute(
                "UPDATE transactions SET erp_status = 'na_fila' WHERE id = ?", (tx_id,)
            )
    return len(ids)


def count_unsent_orders(event_id: Optional[int] = None) -> int:
    sql = (
        "SELECT COUNT(*) FROM transactions WHERE status = 'confirmado' "
        "AND COALESCE(erp_status, 'nao_enviado') = 'nao_enviado'"
    )
    params: List[Any] = []
    if event_id is not None:
        sql += " AND event_id = ?"
        params.append(int(event_id))
    with get_conn() as conn:
        return int(conn.execute(sql, params).fetchone()[0])


#: Erro gravado quando não dá para saber se o ERP recebeu o envio.
UNCERTAIN_SEND_ERROR = (
    "Sem confirmação do ERP: o envio pode ter entrado no ERP. "
    "Confira no ERP antes de reenviar (reenviar sem conferir pode duplicar)."
)


def is_uncertain_send(job: Any) -> bool:
    """Item em erro porque o envio pode ter entrado no ERP sem confirmação."""
    return str(job["last_error"] or "").startswith(UNCERTAIN_SEND_ERROR)


def reset_stale_processing() -> int:
    """Linhas presas em ``processando`` (o processo caiu no meio do envio).

    Não voltam sozinhas para a fila: o ERP pode ter gravado antes da queda.
    Vão para ``erro`` e esperam o admin conferir no ERP e reenviar.
    """
    cutoff = (datetime.now() - timedelta(minutes=STALE_PROCESSING_MINUTES)).isoformat(
        timespec="seconds"
    )
    now = _now_iso()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, kind, transaction_id FROM erp_outbox "
            "WHERE status = 'processando' AND updated_at < ?",
            (cutoff,),
        ).fetchall()
        for r in rows:
            conn.execute(
                "UPDATE erp_outbox SET status = 'erro', next_attempt_at = NULL, "
                "last_error = ?, updated_at = ? WHERE id = ? AND status = 'processando'",
                (UNCERTAIN_SEND_ERROR, now, int(r["id"])),
            )
            _set_failed_tx_status(conn, r["kind"], int(r["transaction_id"]))
        return len(rows)


def _set_failed_tx_status(conn: sqlite3.Connection, kind: str, tx_id: int) -> None:
    """Venda reflete o item da fila que parou em ``erro``."""
    if kind == "pedido":
        conn.execute(
            "UPDATE transactions SET erp_status = 'erro' WHERE id = ? "
            "AND erp_status IN ('na_fila', 'erro')",
            (tx_id,),
        )
    else:
        conn.execute(
            "UPDATE transactions SET erp_status = 'cancelamento_erro' WHERE id = ? "
            "AND erp_status IN ('cancelamento_pendente', 'cancelamento_erro')",
            (tx_id,),
        )


def claim_due_jobs(limit: int = 20) -> List[Dict]:
    """Marca como ``processando`` as linhas vencidas e as devolve (pedido antes de cancelamento)."""
    now = _now_iso()
    claimed: List[Dict] = []
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM erp_outbox
             WHERE status = 'pendente'
               AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
             ORDER BY CASE kind WHEN 'pedido' THEN 0 ELSE 1 END, id
             LIMIT ?
            """,
            (now, int(limit)),
        ).fetchall()
        for r in rows:
            cur = conn.execute(
                "UPDATE erp_outbox SET status = 'processando', updated_at = ? "
                "WHERE id = ? AND status = 'pendente'",
                (now, int(r["id"])),
            )
            if cur.rowcount:
                claimed.append(dict(r))
    return claimed


def load_transaction_for_erp(conn: sqlite3.Connection, tx_id: int) -> Optional[Dict]:
    row = conn.execute("SELECT * FROM transactions WHERE id = ?", (int(tx_id),)).fetchone()
    return dict(row) if row else None


def cancel_reason(conn: sqlite3.Connection, tx: Dict) -> str:
    """``motivo`` do cancelamento no ERP: o estorno no Go Sell e quem o fez.

    Quem estornou fica só no movimento de estoque do estorno (``created_by``).
    """
    order = (tx.get("order_number") or "").strip() or f"#{tx.get('id')}"
    row = conn.execute(
        "SELECT created_by FROM stock_movements "
        "WHERE transaction_id = ? AND reason LIKE 'Estorno%' AND delta > 0 "
        "ORDER BY id DESC LIMIT 1",
        (int(tx["id"]),),
    ).fetchone()
    who = " ".join(str(row["created_by"] or "").split()) if row else ""
    return f"Estorno no Go Sell - pedido {order}" + (f" por {who}" if who else "")


def get_order_job(conn: sqlite3.Connection, tx_id: int) -> Optional[Dict]:
    row = conn.execute(
        "SELECT * FROM erp_outbox WHERE kind = 'pedido' AND transaction_id = ?",
        (int(tx_id),),
    ).fetchone()
    return dict(row) if row else None


def mark_job_sent(
    job: Dict,
    *,
    payload: Optional[Dict],
    response: Any,
    erp_order_id: Optional[str] = None,
    codparc: Optional[int] = None,
) -> None:
    now = _now_iso()
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE erp_outbox
               SET status = 'enviado', attempts = attempts + 1, payload_json = ?,
                   response_json = ?, last_error = NULL, updated_at = ?
             WHERE id = ?
            """,
            (
                json.dumps(payload, ensure_ascii=False) if payload is not None else None,
                json.dumps(response, ensure_ascii=False, default=str),
                now,
                int(job["id"]),
            ),
        )
        tx_id = int(job["transaction_id"])
        if job["kind"] == "pedido":
            current = conn.execute(
                "SELECT erp_status FROM transactions WHERE id = ?", (tx_id,)
            ).fetchone()
            # Estorno durante o envio já pôs o cancelamento na fila: não sobrescreve.
            status = (
                "cancelamento_pendente"
                if current and current["erp_status"] == "cancelamento_pendente"
                else "enviado"
            )
            conn.execute(
                "UPDATE transactions SET erp_status = ?, erp_order_id = ?, "
                "erp_sent_at = ?, erp_codparc = COALESCE(?, erp_codparc) WHERE id = ?",
                (status, erp_order_id, now, codparc, tx_id),
            )
            if payload is not None:
                record_allocations_in_conn(conn, tx_id, payload)
        else:
            conn.execute(
                "UPDATE transactions SET erp_status = 'cancelado' WHERE id = ?", (tx_id,)
            )
            release_allocations_in_conn(conn, tx_id)


def mark_job_failed(
    job: Dict,
    error: str,
    *,
    transient: bool,
    payload: Optional[Dict] = None,
    response: Any = None,
    final: bool = False,
) -> str:
    """Registra a falha e agenda a próxima tentativa. Retorna o novo status da linha.

    ``transient`` (rede/gateway fora do ar): tenta de novo sem limite.
    ``final``: não adianta repetir sem ação do admin (ex.: cadastro incompleto).
    """
    attempts = int(job.get("attempts") or 0) + 1
    give_up = final or (not transient and attempts >= MAX_BUSINESS_ATTEMPTS)
    wait = RETRY_BACKOFF_MINUTES[min(attempts, len(RETRY_BACKOFF_MINUTES)) - 1]
    next_at = (datetime.now() + timedelta(minutes=wait)).isoformat(timespec="seconds")
    status = "erro" if give_up else "pendente"
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE erp_outbox
               SET status = ?, attempts = ?, next_attempt_at = ?, last_error = ?,
                   payload_json = COALESCE(?, payload_json),
                   response_json = COALESCE(?, response_json), updated_at = ?
             WHERE id = ?
            """,
            (
                status,
                attempts,
                None if give_up else next_at,
                (error or "")[:2000],
                json.dumps(payload, ensure_ascii=False) if payload is not None else None,
                json.dumps(response, ensure_ascii=False, default=str) if response is not None else None,
                _now_iso(),
                int(job["id"]),
            ),
        )
        if give_up:
            _set_failed_tx_status(conn, job["kind"], int(job["transaction_id"]))
    return status


def release_claimed_jobs(jobs: Iterable[Dict], reason: str, retry_at: datetime) -> int:
    """Devolve à fila itens que o worker pegou e não chegou a enviar.

    Para falhas que não são do pedido (login recusado, endereço do gateway
    errado): não conta tentativa nem põe a venda em erro, só guarda o motivo
    para o painel e agenda a volta.
    """
    now = _now_iso()
    next_at = retry_at.isoformat(timespec="seconds")
    released = 0
    with get_conn() as conn:
        for job in jobs:
            cur = conn.execute(
                "UPDATE erp_outbox SET status = 'pendente', next_attempt_at = ?, "
                "last_error = ?, updated_at = ? WHERE id = ? AND status = 'processando'",
                (next_at, (reason or "")[:2000], now, int(job["id"])),
            )
            released += cur.rowcount
    return released


def close_claimed_job(job: Dict, reason: str, tx_erp_status: str) -> None:
    """O worker encerra um item que ele mesmo pegou (``processando``) sem enviar.

    Diferente de ``mark_job_discarded`` (ação do admin), aceita ``processando`` e
    recebe a situação final da venda: um cancelamento cujo pedido nunca chegou
    ao ERP deixa a venda ``nao_enviado``, não ``cancelado``.
    """
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE erp_outbox SET status = 'descartado', last_error = ?, updated_at = ? "
            "WHERE id = ? AND status = 'processando'",
            (reason, _now_iso(), int(job["id"])),
        )
        if cur.rowcount:
            conn.execute(
                "UPDATE transactions SET erp_status = ? WHERE id = ?",
                (tx_erp_status, int(job["transaction_id"])),
            )


def mark_job_discarded(job_id: int, reason: str) -> None:
    """Admin tira o item da fila.

    Pedido: a venda volta a ``nao_enviado``. Cancelamento: o admin cancelou
    direto no ERP, e a venda passa a ``cancelado``.
    """
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM erp_outbox WHERE id = ?", (int(job_id),)).fetchone()
        if row is None:
            raise ValueError("Item da fila não encontrado.")
        if row["status"] in ("enviado", "processando"):
            raise ValueError("Este item já foi enviado ou está sendo enviado.")
        conn.execute(
            "UPDATE erp_outbox SET status = 'descartado', last_error = ?, updated_at = ? "
            "WHERE id = ?",
            (reason, _now_iso(), int(job_id)),
        )
        tx_status = "nao_enviado" if row["kind"] == "pedido" else "cancelado"
        conn.execute(
            "UPDATE transactions SET erp_status = ? WHERE id = ?",
            (tx_status, int(row["transaction_id"])),
        )
        # Pedido que não vai (ou cancelado à mão no ERP): o saldo dos lotes volta.
        release_allocations_in_conn(conn, int(row["transaction_id"]))


#: Unidades sugeridas no formulário, além das que já existem no catálogo.
COMMON_CODVOL = ("UN", "CX", "PC", "KT", "FR", "PT", "CJ", "RL", "TB", "GL")


def find_product_id_for_code(code: str) -> Optional[int]:
    """Produto da biblioteca pelo código digitado: CODPROD, SKU ou id local."""
    text = str(code or "").strip().lstrip("#")
    if not text:
        return None
    with get_conn() as conn:
        if text.isdigit():
            row = conn.execute(
                "SELECT id FROM products WHERE erp_codprod = ?", (int(text),)
            ).fetchone()
            if row:
                return int(row["id"])
        row = conn.execute(
            "SELECT id FROM products WHERE sku = ? ORDER BY active DESC, id LIMIT 1", (text,)
        ).fetchone()
        if row:
            return int(row["id"])
        if text.isdigit():
            row = conn.execute("SELECT id FROM products WHERE id = ?", (int(text),)).fetchone()
            if row:
                return int(row["id"])
    return None


def known_codvols() -> List[str]:
    """Siglas de unidade já vistas no catálogo + as mais comuns (para sugestão)."""
    with get_conn() as conn:
        seen = [r[0] for r in conn.execute(
            "SELECT erp_codvol FROM products WHERE COALESCE(erp_codvol, '') != '' "
            "GROUP BY erp_codvol ORDER BY COUNT(*) DESC"
        )]
    return list(dict.fromkeys([*seen, *COMMON_CODVOL]))


def products_missing_codvol_in_queue() -> List[Dict]:
    """Produtos sem unidade (CODVOL) em pedidos que esperam na fila (pendente/erro)."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT p.id, p.sku, p.name, p.erp_codprod,
                   GROUP_CONCAT(DISTINCT t.order_number) AS orders
              FROM erp_outbox o
              JOIN transactions t ON t.id = o.transaction_id
              JOIN transaction_items ti ON ti.transaction_id = t.id
              JOIN products p ON p.id = CAST(ti.product_id AS INTEGER)
             WHERE o.kind = 'pedido' AND o.status IN ('pendente', 'erro')
               AND COALESCE(p.erp_codvol, '') = ''
             GROUP BY p.id
             ORDER BY p.name
            """
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["orders"] = sorted((d.get("orders") or "").split(","))
        out.append(d)
    return out


def requeue_jobs_blocked_by_codvol(product_id: int) -> int:
    """Pedidos parados por falta de unidade deste produto voltam para a fila.

    Os que ainda tiverem outro problema voltam a ``erro`` na próxima passada,
    já com o motivo novo.
    """
    with get_conn() as conn:
        ids = [int(r[0]) for r in conn.execute(
            """
            SELECT DISTINCT o.id
              FROM erp_outbox o
              JOIN transaction_items ti ON ti.transaction_id = o.transaction_id
             WHERE o.kind = 'pedido' AND o.status = 'erro'
               AND o.last_error LIKE '%sem unidade (CODVOL)%'
               AND CAST(ti.product_id AS INTEGER) = ?
            """,
            (int(product_id),),
        )]
    for job_id in ids:
        retry_job(job_id)
    return len(ids)


def retry_job(job_id: int) -> None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM erp_outbox WHERE id = ?", (int(job_id),)).fetchone()
        if row is None:
            raise ValueError("Item da fila não encontrado.")
        if row["status"] not in ("erro", "pendente", "descartado"):
            raise ValueError("Só itens com erro, aguardando ou descartados podem ser reenviados.")
        if row["kind"] == "pedido":
            tx = conn.execute(
                "SELECT status FROM transactions WHERE id = ?", (int(row["transaction_id"]),)
            ).fetchone()
            if tx is None or tx["status"] != "confirmado":
                raise ValueError("A venda não está mais confirmada; não há o que enviar.")
        now = _now_iso()
        conn.execute(
            "UPDATE erp_outbox SET status = 'pendente', attempts = 0, next_attempt_at = ?, "
            "updated_at = ? WHERE id = ?",
            (now, now, int(job_id)),
        )
        conn.execute(
            "UPDATE transactions SET erp_status = ? WHERE id = ?",
            (
                "na_fila" if row["kind"] == "pedido" else "cancelamento_pendente",
                int(row["transaction_id"]),
            ),
        )


def outbox_counts() -> Dict[str, int]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM erp_outbox GROUP BY status"
        ).fetchall()
    counts = {k: 0 for k in OUTBOX_STATUS_LABELS}
    counts.update({r["status"]: int(r["n"]) for r in rows})
    return counts


def list_outbox(status: Optional[str] = None, limit: int = 100, offset: int = 0) -> List[Dict]:
    sql = (
        "SELECT o.*, t.order_number, t.erp_idpedido, t.client_name, t.total, t.event_id, t.status AS tx_status, "
        "t.erp_status, t.erp_order_id, t.erp_codparc, e.name AS event_name "
        "FROM erp_outbox o JOIN transactions t ON t.id = o.transaction_id "
        "LEFT JOIN events e ON e.id = t.event_id"
    )
    params: List[Any] = []
    if status and status in OUTBOX_STATUS_LABELS:
        sql += " WHERE o.status = ?"
        params.append(status)
    sql += " ORDER BY CASE o.status WHEN 'erro' THEN 0 WHEN 'pendente' THEN 1 " \
           "WHEN 'processando' THEN 2 ELSE 3 END, o.updated_at DESC LIMIT ? OFFSET ?"
    params += [int(limit), int(max(0, offset))]
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["status_label"] = OUTBOX_STATUS_LABELS.get(d["status"], d["status"])
        d["payload_pretty"] = _pretty_json(d.get("payload_json"))
        d["response_pretty"] = _pretty_json(d.get("response_json"))
        out.append(d)
    return out


def _pretty_json(text: Optional[str]) -> Optional[str]:
    """JSON gravado numa linha só, indentado para leitura no painel."""
    if not text:
        return None
    try:
        return json.dumps(json.loads(text), ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return text


# ---------------------------------------------------------------------------
# Histórico de sincronizações
# ---------------------------------------------------------------------------

def start_sync_run(kind: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO erp_sync_runs (kind, started_at, status) VALUES (?, ?, 'executando')",
            (kind, _now_iso()),
        )
        return int(cur.lastrowid)


def finish_sync_run(
    run_id: int,
    status: str,
    *,
    stats: Optional[Dict] = None,
    message: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    s = stats or {}
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE erp_sync_runs
               SET finished_at = ?, status = ?, inserted = ?, updated = ?, unchanged = ?,
                   warnings = ?, images = ?, message = ?, error = ?
             WHERE id = ?
            """,
            (
                _now_iso(), status, int(s.get("inserted") or 0), int(s.get("updated") or 0),
                int(s.get("unchanged") or 0), int(s.get("warnings") or 0),
                int(s.get("images") or 0), message, (error or None) and error[:2000],
                int(run_id),
            ),
        )


def update_sync_progress(run_id: int, stats: Dict, message: Optional[str]) -> None:
    """Contadores parciais de uma sincronização em andamento (painel ao vivo).

    ``message`` descreve a etapa atual; ``finish_sync_run`` troca pelo resumo.
    """
    s = stats or {}
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE erp_sync_runs
               SET inserted = ?, updated = ?, unchanged = ?, warnings = ?, images = ?,
                   message = ?
             WHERE id = ? AND status = 'executando'
            """,
            (
                int(s.get("inserted") or 0), int(s.get("updated") or 0),
                int(s.get("unchanged") or 0), int(s.get("warnings") or 0),
                int(s.get("images") or 0), message, int(run_id),
            ),
        )


#: Sincronização ``executando`` há mais tempo que isso é considerada morta.
SYNC_RUN_STALE_HOURS = 2


def other_sync_running(kind: str, exclude_run_id: Optional[int] = None) -> bool:
    """Outra sincronização do mesmo tipo em andamento, inclusive em outro processo WSGI."""
    cutoff = (datetime.now() - timedelta(hours=SYNC_RUN_STALE_HOURS)).isoformat(
        timespec="seconds"
    )
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM erp_sync_runs WHERE kind = ? AND status = 'executando' "
            "AND started_at >= ? AND id != ? LIMIT 1",
            (kind, cutoff, int(exclude_run_id or 0)),
        ).fetchone()
    return row is not None


def abandon_running_syncs() -> None:
    """Na subida do app: sincronização que estava rodando quando o processo caiu."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE erp_sync_runs SET status = 'erro', finished_at = ?, "
            "error = 'Interrompida (o sistema foi reiniciado durante a sincronização).' "
            "WHERE status = 'executando'",
            (_now_iso(),),
        )


def list_sync_runs(limit: int = 10) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM erp_sync_runs ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
    return [dict(r) for r in rows]
