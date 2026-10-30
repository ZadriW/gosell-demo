"""Formas de pagamento do checkout: valores gravados, rótulos e regras.

``transactions.payment_method`` guarda o valor da chave. ``cartao`` é legado:
vendas antigas, de antes da separação entre crédito e débito, quando o
checkout tinha um único "Cartão" (1x podia ser qualquer um dos dois). Ele
continua sendo exibido como "Cartão", e no ERP usa os códigos do crédito.
"""
from __future__ import annotations

from typing import Optional

#: Ordem de exibição no checkout e no mapeamento de tipos de negociação.
CHECKOUT_METHODS = ("pix", "credito", "debito", "dinheiro", "faturado")

PAYMENT_METHOD_LABELS = {
    "pix": "PIX",
    "pix_inter": "Pix Inter",
    "credito": "Cartão de crédito",
    "debito": "Cartão de débito",
    "dinheiro": "Dinheiro",
    "faturado": "Faturado",
    "cartao": "Cartão",
}

#: Métodos que aceitam parcelamento (1x a ``MAX_CARD_INSTALLMENTS``).
INSTALLMENT_METHODS = frozenset({"credito", "cartao"})

#: Teto de parcelas do checkout. O mapeamento de CODTIPVENDA do ERP tem uma
#: linha por parcela até este mesmo número (``MAX_PARCELAS_UI`` em
#: ``static/js/payment-form.js`` precisa acompanhar).
MAX_CARD_INSTALLMENTS = 12

#: Métodos confirmados sem maquininha: o AUT é gravado com este código interno.
INTERNAL_AUT = {"dinheiro": "DINHEIRO", "faturado": "FATURADO"}

DEFAULT_METHOD = "credito"


def normalize_payment_method(value: Optional[str]) -> str:
    """Valor aceito do front. ``cartao`` (sessão antiga do checkout) vira crédito."""
    v = (value or "").strip().lower()
    if v in CHECKOUT_METHODS:
        return v
    return DEFAULT_METHOD


def accepts_installments(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in INSTALLMENT_METHODS


def payment_method_label(value: Optional[str], installments=None) -> str:
    v = (value or "").strip().lower()
    label = PAYMENT_METHOD_LABELS.get(v)
    if label is None:
        return "—"
    if v in INSTALLMENT_METHODS:
        try:
            n = int(installments)
        except (TypeError, ValueError):
            n = None
        if n is not None and n > 1:
            return f"{label} em {n}x"
    return label
