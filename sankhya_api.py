"""ERP simulado da demonstração Go Sell.

Substitui o cliente HTTP do ERP real com **as mesmas funções e classes de
erro**, para ``erp_sync.py`` e ``database/erp.py`` rodarem sem alteração:
sincronização do catálogo (filas de preço, cadastro e estoque), tipos de
negociação, consulta de cliente por CPF, cadastro de cliente, envio e
cancelamento de pedidos. Nada sai da máquina; os dados vêm de
``data/erp_mock.py``.

Comportamento:

- As filas do catálogo começam "aguardando integração" e esvaziam ao serem
  confirmadas (``ack_*``), como no ERP real. ``rearm_queues()`` (chamado no
  reset da demo) volta tudo ao estado inicial.
- ``create_order`` responde com um ``NUNOTA`` sequencial após uma pequena
  espera (``DEMO_ERP_LATENCY``, em segundos; padrão 0,6).
- O cadastro de ``FLAKY_CUSTOMER_KEY`` é recusado uma vez logo após o seed,
  para a fila mostrar um erro e o botão "Reenviar" (o reenvio passa). ``DEMO_ERP_FAIL_RATE`` (0 a 1)
  faz pedidos falharem ao acaso, se quiser mostrar mais erros.
"""
from __future__ import annotations

import base64
import logging
import os
import random
import threading
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from data import erp_mock

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://erp.simulado.gosell.demo/api/v1"


class SankhyaError(Exception):
    """Falha na integração. ``transient`` = vale tentar de novo mais tarde."""

    transient = False

    def __init__(self, message: str, *, response: Any = None):
        super().__init__(redact(message))
        self.response = response


class SankhyaConfigError(SankhyaError):
    """Configuração do ERP ausente ou errada: a fila inteira para."""


class SankhyaAuthError(SankhyaConfigError):
    """O ERP recusou o login."""


class SankhyaNetworkError(SankhyaError):
    """Sem internet, ERP fora do ar ou lento demais."""

    transient = True


class SankhyaRejected(SankhyaError):
    """O ERP respondeu e recusou (dado inválido, regra do ERP)."""


class SankhyaUncertain(SankhyaError):
    """O pedido pode ter chegado ao ERP, mas a resposta se perdeu."""


# ---------------------------------------------------------------------------
# Configuração (sempre "configurado" na demo)
# ---------------------------------------------------------------------------

def base_url() -> str:
    return DEFAULT_BASE_URL


def missing_config() -> List[str]:
    return []


def is_configured() -> bool:
    return True


def url_problems() -> List[str]:
    return []


def product_filter_param() -> str:
    return "codprod"


def client_filter_param() -> str:
    return "cpf"


def redact(text: Any) -> str:
    return str(text or "")


def _latency() -> float:
    try:
        return max(0.0, float(os.environ.get("DEMO_ERP_LATENCY", "0.6")))
    except ValueError:
        return 0.6


def _fail_rate() -> float:
    try:
        return min(1.0, max(0.0, float(os.environ.get("DEMO_ERP_FAIL_RATE", "0"))))
    except ValueError:
        return 0.0


def _wait(scale: float = 1.0) -> None:
    delay = _latency() * scale
    if delay > 0:
        time.sleep(delay)


def check_connection() -> float:
    """Simula um login no ERP e devolve quanto levou (segundos)."""
    started = time.monotonic()
    _wait(0.6)
    return time.monotonic() - started


# ---------------------------------------------------------------------------
# Estado do ERP simulado (em memória, por processo)
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_pending: Dict[str, set] = {}
_flaky_armed = False
_price_overrides: Dict[int, float] = {}
_next_codparc = 60000
_next_nunota = 880000


def _db_max(sql: str) -> int:
    """Maior número já gravado no banco (os contadores não repetem após reiniciar)."""
    try:
        from database.connection import get_conn

        with get_conn() as conn:
            row = conn.execute(sql).fetchone()
        return int(row[0] or 0)
    except Exception:
        return 0


def _all_products() -> List[Dict]:
    return [*erp_mock.CATALOG, *erp_mock.ERP_ONLY]


def rearm_queues(*, arm_flaky: bool = False) -> None:
    """Volta as filas e contadores ao estado inicial (reset da demonstração).

    As novidades de ``FOLLOWUP_*`` ficam guardadas até ``stage_followup_updates``.
    ``arm_flaky``: a próxima tentativa de cadastrar o cliente "em análise" é
    recusada uma vez (o seed liga, para a fila nascer com um erro).
    """
    global _next_codparc, _next_nunota, _flaky_armed
    codes = {int(p["codprod"]) for p in _all_products()} - set(erp_mock.FOLLOWUP_NEW_PRODUCTS)
    with _lock:
        _pending["products"] = set(codes)
        _pending["prices"] = set(codes)
        _pending["stock"] = set(codes)
        _pending["payment"] = {int(t["CODTIPVENDA"]) for t in erp_mock.PAYMENT_TYPES}
        _flaky_armed = arm_flaky
        _price_overrides.clear()
        _next_codparc = 60000
        _next_nunota = 880000


def stage_followup_updates() -> None:
    """Põe nas filas um produto novo e um reajuste de preço, para a próxima
    sincronização do painel ter o que mostrar."""
    new_codes = set(erp_mock.FOLLOWUP_NEW_PRODUCTS)
    with _lock:
        _price_overrides.update(erp_mock.FOLLOWUP_PRICES)
        _pending["products"] |= new_codes
        _pending["stock"] |= new_codes
        _pending["prices"] |= new_codes | set(erp_mock.FOLLOWUP_PRICES)



rearm_queues()
stage_followup_updates()


def _take(queue: str, limit: int = 20) -> List[int]:
    with _lock:
        return sorted(_pending.get(queue, set()))[:limit]


def _ack_queue(queue: str, codes: Iterable[int]) -> None:
    with _lock:
        _pending.get(queue, set()).difference_update(int(c) for c in codes)


def _product_row(p: Dict) -> Dict:
    image = None
    if "sku" not in p:  # só o ERP tem: manda a foto, como o ERP real
        svg = erp_mock.product_svg(p["name"], f"ERP {p['codprod']}", p["bg"], p["accent"])
        image = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return {
        "CODPROD": p["codprod"],
        "DESCRPROD": p["name"],
        "DESCRGRUPOPROD": p["category"],
        "CODGRUPOPROD": erp_mock.GROUP_CODES.get(p["category"]),
        "MARCA": p["brand"],
        "CODVOL": p["codvol"],
        "CODANVISA": None,
        "REFFORN": f"REF-{p['codprod']}",
        "FORNECEDOR": f"{p['brand']} Distribuidora",
        "CARACTERISTICAS": f"{p['name']} — {p['brand']}",
        "IMAGEM": image,
    }


# ---------------------------------------------------------------------------
# Catálogo
# ---------------------------------------------------------------------------

def fetch_products(codprod: Optional[int] = None) -> List[Dict]:
    _wait(0.5)
    by_code = {int(p["codprod"]): p for p in _all_products()}
    if codprod is not None:
        p = by_code.get(int(codprod))
        return [_product_row(p)] if p else []
    return [_product_row(by_code[c]) for c in _take("products")]


def ack_products(codes: Iterable[int]) -> None:
    _ack_queue("products", codes)


def fetch_prices() -> List[Dict]:
    _wait(0.3)
    by_code = {int(p["codprod"]): p for p in _all_products()}
    return [
        {"CODPROD": c, "CODEMP": erp_mock.CODEMP,
         "PRECO": _price_overrides.get(c, by_code[c]["price"])}
        for c in _take("prices")
    ]


def ack_prices(codes: Iterable[int]) -> None:
    _ack_queue("prices", codes)


def fetch_stock() -> List[Dict]:
    """Dois lotes por produto no local de origem, com saldo folgado."""
    _wait(0.3)
    rows: List[Dict] = []
    for c in _take("stock"):
        for i, (lote, qty) in enumerate(((f"L{c}A", 400), (f"L{c}B", 250))):
            rows.append({
                "CODPROD": c, "CODLOCAL": erp_mock.CODLOCAL, "DESCRLOCAL": erp_mock.DESCRLOCAL,
                "CONTROLE": lote, "ESTOQUE": qty, "RESERVADO": 10 * i, "DISPONIVEL": qty - 10 * i,
            })
    return rows


def ack_stock(codes: Iterable[int]) -> None:
    _ack_queue("stock", codes)


def fetch_payment_types() -> List[Dict]:
    _wait(0.2)
    pending = set(_take("payment", limit=50))
    stamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    return [
        {
            "codtipvenda": int(t["CODTIPVENDA"]),
            "description": t["DESCRTIPVENDA"],
            "dhalter": stamp,
            "active": t["STATUS"] != "I",
        }
        for t in erp_mock.PAYMENT_TYPES
        if int(t["CODTIPVENDA"]) in pending
    ]


def ack_payment_types(codes: Iterable[int]) -> None:
    _ack_queue("payment", codes)


# ---------------------------------------------------------------------------
# Clientes
# ---------------------------------------------------------------------------

def _doc_digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def fetch_client_by_cpf(cpf: Any, timeout: Optional[float] = None) -> Optional[Dict]:
    """Parceiro do ERP simulado com este CPF, no formato de ``GET clients``."""
    digits = _doc_digits(cpf)
    if len(digits) != 11:
        return None
    _wait(0.5)
    for c in erp_mock.all_erp_customers():
        if c["cpf"] == digits:
            return {
                "CODPARC": c["codparc"],
                "CNPJ_CPF": c["cpf"],
                "NOME_FANTASIA": c["name"],
                "RAZAO_SOCIAL": c["name"],
                "EMAIL": c["email"],
                "CELULAR": c["phone"],
                "TELEFONE": "",
                "CEP": c["zipcode"],
                "ENDERECO": c["address"],
                "NUMERO": c["number"],
                "COMPLEMENTO": c["complement"],
                "BAIRRO": c["neighborhood"],
                "CIDADE": c["city"],
                "ESTADO": c["state"],
                "ATIVO": "S",
                "DATA_ALTERACAO": "01102026 10:00:00",
            }
    return None


def create_update_client(payload: Dict) -> Dict:
    """Cria (ou acha pelo CPF) o parceiro. Devolve ``{codparc, raw}``."""
    global _next_codparc, _flaky_armed
    _wait(0.4)
    cpf = _doc_digits(payload.get("CGC_CPF"))
    flaky = erp_mock.customer_by_key(erp_mock.FLAKY_CUSTOMER_KEY)
    if cpf == flaky["cpf"]:
        with _lock:
            reject = _flaky_armed
            _flaky_armed = False
        if reject:
            raise SankhyaRejected(
                "Parceiro em análise de crédito no ERP (simulação). Reenvie para liberar.",
                response={"success": False, "message": "Parceiro em análise de crédito"},
            )
    for c in erp_mock.all_erp_customers():
        if c["cpf"] == cpf:
            codparc = int(c["codparc"])
            break
    else:
        floor = _db_max("SELECT MAX(codparc) FROM erp_customers WHERE codparc >= 60000")
        with _lock:
            _next_codparc = max(_next_codparc, floor) + 1
            codparc = _next_codparc
    raw = {"success": True, "data": [{"CODPARC": str(codparc), "NOMEPARC": payload.get("NOMEPARC")}]}
    return {"codparc": codparc, "raw": raw}


# ---------------------------------------------------------------------------
# Pedidos
# ---------------------------------------------------------------------------

def create_order(payload: Dict) -> Dict:
    """Registra o pedido no ERP simulado. Devolve ``{order_id, raw}``."""
    global _next_nunota
    _wait(1.0)
    if _fail_rate() and random.random() < _fail_rate():
        raise SankhyaRejected(
            "ERP simulado recusou o pedido (DEMO_ERP_FAIL_RATE).",
            response={"success": False},
        )
    floor = _db_max(
        "SELECT MAX(CAST(erp_order_id AS INTEGER)) FROM transactions WHERE erp_order_id GLOB '[0-9]*'"
    )
    with _lock:
        _next_nunota = max(_next_nunota, floor) + 1
        nunota = _next_nunota
    raw = {
        "success": True,
        "message": f"Orçamento {nunota} incluído com sucesso (ERP simulado).",
        "data": [{"NUNOTA": nunota, "IDPEDIDO": payload.get("IDPEDIDO")}],
    }
    return {"order_id": str(nunota), "raw": raw}


DEFAULT_CANCEL_REASON = "Cancelado pelo usuário"


def cancel_order(idpedido: Any, motivo: Optional[str] = None) -> Dict:
    text = str(idpedido or "").strip()
    if not text.isdigit() or int(text) <= 0:
        raise SankhyaRejected(
            f"Venda sem IDPEDIDO válido ({idpedido!r}): não há como identificar o pedido no ERP."
        )
    _wait(0.5)
    reason = " ".join(str(motivo or "").split())[:200] or DEFAULT_CANCEL_REASON
    return {
        "idpedido": int(text),
        "motivo": reason,
        "message": {"success": True, "message": f"Pedido/Orçamento com ID {text} cancelado (ERP simulado)."},
    }
