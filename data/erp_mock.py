"""Dados fictícios do ERP simulado da demonstração (ver ``sankhya_api.py``).

Tudo aqui é inventado: produtos, marcas, clientes, CPFs (com dígitos
verificadores válidos, mas gerados) e tipos de negociação. O seed
(``seed/demo.py``) usa as mesmas listas, para o catálogo local, a carteira de
clientes e o ERP simulado contarem a mesma história.
"""
from __future__ import annotations

from typing import Dict, List

#: Empresa e local de estoque do ERP simulado.
CODEMP = 1
CODLOCAL = 101
DESCRLOCAL = "CD Go Sell — São Paulo"


def cpf_with_check_digits(base9: str) -> str:
    """Completa 9 dígitos com os 2 verificadores (CPF válido, mas fictício)."""
    digits = [int(c) for c in base9]
    for _ in range(2):
        weight = len(digits) + 1
        total = sum(d * (weight - i) for i, d in enumerate(digits))
        rest = (total * 10) % 11
        digits.append(0 if rest == 10 else rest)
    return "".join(map(str, digits))


def format_cpf(digits: str) -> str:
    return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"


# ---------------------------------------------------------------------------
# Catálogo
# ---------------------------------------------------------------------------

#: Produtos do catálogo da demo, já vinculados ao ERP (``codprod``).
#: ``stock``/``min_stock`` são o estoque inicial no evento principal.
CATALOG: List[Dict] = [
    {"codprod": 10101, "sku": "GS-EQ-001", "name": "Scanner intraoral 3D", "category": "Equipamentos",
     "brand": "Dentalis", "codvol": "UN", "price": 18900.00, "min_stock": 3, "stock": 9,
     "accent": "#0891b2", "bg": "#ecfeff"},
    {"codprod": 10102, "sku": "GS-EQ-002", "name": "Autoclave compacta 12L", "category": "Equipamentos",
     "brand": "Medvia", "codvol": "UN", "price": 4200.00, "min_stock": 2, "stock": 6,
     "accent": "#0e7490", "bg": "#ecfeff"},
    {"codprod": 10103, "sku": "GS-EQ-003", "name": "Fotopolimerizador LED", "category": "Equipamentos",
     "brand": "Dentalis", "codvol": "UN", "price": 890.00, "min_stock": 4, "stock": 13,
     "accent": "#155e75", "bg": "#ecfeff"},
    {"codprod": 10201, "sku": "GS-CS-001", "name": "Luva nitrilo (caixa 100)", "category": "Consumíveis",
     "brand": "Orisa", "codvol": "CX", "price": 42.00, "min_stock": 20, "stock": 82,
     "accent": "#d97706", "bg": "#fffbeb"},
    {"codprod": 10202, "sku": "GS-CS-002", "name": "Resina composta A2 (4g)", "category": "Consumíveis",
     "brand": "Medvia", "codvol": "UN", "price": 68.00, "min_stock": 10, "stock": 3,
     "accent": "#b45309", "bg": "#fffbeb"},
    {"codprod": 10203, "sku": "GS-CS-003", "name": "Sugador descartável (pct 40)", "category": "Consumíveis",
     "brand": "Orisa", "codvol": "PT", "price": 18.00, "min_stock": 15, "stock": 0,
     "accent": "#92400e", "bg": "#fffbeb"},
    {"codprod": 10204, "sku": "GS-CS-004", "name": "Fio dental profissional", "category": "Consumíveis",
     "brand": "Orisa", "codvol": "UN", "price": 12.00, "min_stock": 10, "stock": 43,
     "accent": "#ca8a04", "bg": "#fffbeb"},
    {"codprod": 10301, "sku": "GS-AC-001", "name": "Óculos de proteção", "category": "Acessórios",
     "brand": "Medvia", "codvol": "UN", "price": 35.00, "min_stock": 8, "stock": 29,
     "accent": "#7c3aed", "bg": "#f5f3ff"},
    {"codprod": 10302, "sku": "GS-AC-002", "name": "Avental descartável (pct 10)", "category": "Acessórios",
     "brand": "Orisa", "codvol": "PT", "price": 28.00, "min_stock": 5, "stock": 17,
     "accent": "#6d28d9", "bg": "#f5f3ff"},
    {"codprod": 10303, "sku": "GS-AC-003", "name": "Espelho clínico nº 5", "category": "Acessórios",
     "brand": "Dentalis", "codvol": "UN", "price": 22.00, "min_stock": 10, "stock": 30,
     "accent": "#5b21b6", "bg": "#f5f3ff"},
]

#: Produtos que existem só no ERP simulado: aparecem na biblioteca na primeira
#: sincronização (botão "Sincronizar com ERP"), com foto gerada.
ERP_ONLY: List[Dict] = [
    {"codprod": 10401, "name": "Kit acabamento e polimento", "category": "Consumíveis",
     "brand": "Dentalis", "codvol": "KT", "price": 156.00, "accent": "#0f766e", "bg": "#f0fdfa"},
    {"codprod": 10402, "name": "Broca diamantada 1012 (cx 10)", "category": "Consumíveis",
     "brand": "Medvia", "codvol": "CX", "price": 74.50, "accent": "#be123c", "bg": "#fff1f2"},
    {"codprod": 10403, "name": "Cadeira clínica compacta", "category": "Equipamentos",
     "brand": "Dentalis", "codvol": "UN", "price": 23500.00, "accent": "#1d4ed8", "bg": "#eff6ff"},
    {"codprod": 10404, "name": "Máscara tripla (cx 50)", "category": "Acessórios",
     "brand": "Orisa", "codvol": "CX", "price": 19.90, "accent": "#4d7c0f", "bg": "#f7fee7"},
]

#: Novidades que o ERP simulado guarda para a primeira sincronização feita na
#: demonstração (o seed já sincroniza o resto): um produto novo e um reajuste.
FOLLOWUP_NEW_PRODUCTS = {10404}
FOLLOWUP_PRICES = {10303: 24.00}

GROUP_CODES = {"Equipamentos": 1100, "Consumíveis": 1200, "Acessórios": 1300}


def product_svg(name: str, code: str, bg: str, accent: str) -> str:
    """Ilustração simples do produto (SVG), usada no seed e no ERP simulado."""
    safe_name = name.replace("&", "&amp;").replace("<", "&lt;")
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 400" role="img" aria-label="{safe_name}">
  <rect width="400" height="400" rx="28" fill="{bg}"/>
  <circle cx="200" cy="148" r="54" fill="{accent}" opacity="0.18"/>
  <rect x="150" y="118" width="100" height="72" rx="14" fill="{accent}"/>
  <rect x="168" y="132" width="64" height="44" rx="8" fill="#ffffff" opacity="0.85"/>
  <text x="200" y="248" text-anchor="middle" font-family="Inter, Arial, sans-serif" font-size="20" font-weight="700" fill="#134e4a">{safe_name}</text>
  <text x="200" y="278" text-anchor="middle" font-family="Inter, Arial, sans-serif" font-size="14" fill="#64748b">{code}</text>
</svg>
"""


# ---------------------------------------------------------------------------
# Tipos de negociação (CODTIPVENDA)
# ---------------------------------------------------------------------------

PAYMENT_TYPES: List[Dict] = [
    {"CODTIPVENDA": 110, "DESCRTIPVENDA": "POS PIX GOSELL", "STATUS": "A"},
    {"CODTIPVENDA": 120, "DESCRTIPVENDA": "POS CARTAO DEBITO", "STATUS": "A"},
    {"CODTIPVENDA": 130, "DESCRTIPVENDA": "DINHEIRO", "STATUS": "A"},
    {"CODTIPVENDA": 140, "DESCRTIPVENDA": "FATURADO 30 DIAS", "STATUS": "A"},
    *[
        {"CODTIPVENDA": 200 + n, "DESCRTIPVENDA": f"POS CARTAO {n}X", "STATUS": "A"}
        for n in range(1, 13)
    ],
    {"CODTIPVENDA": 190, "DESCRTIPVENDA": "BOLETO BANCARIO", "STATUS": "A"},
]


# ---------------------------------------------------------------------------
# Clientes
# ---------------------------------------------------------------------------

#: Carteira fictícia. ``codparc`` = parceiro já cadastrado no ERP simulado
#: (``None`` = cliente novo: o ERP cria o cadastro no primeiro pedido).
CUSTOMERS: List[Dict] = [
    {"key": "carla", "name": "Carla Mendes", "cpf": cpf_with_check_digits("318452907"),
     "email": "carla.mendes@exemplo.com", "phone": "11988881001", "zipcode": "04538132",
     "address": "Avenida Brigadeiro Faria Lima", "number": "3477", "complement": "Sala 42",
     "neighborhood": "Itaim Bibi", "city": "São Paulo", "state": "SP", "codparc": 50101},
    {"key": "rafael", "name": "Rafael Souza", "cpf": cpf_with_check_digits("209371654"),
     "email": "rafael.souza@exemplo.com", "phone": "21977772002", "zipcode": "22071000",
     "address": "Avenida Atlântica", "number": "1702", "complement": "",
     "neighborhood": "Copacabana", "city": "Rio de Janeiro", "state": "RJ", "codparc": 50102},
    {"key": "helena", "name": "Helena Alves", "cpf": cpf_with_check_digits("547120983"),
     "email": "helena.alves@exemplo.com", "phone": "31966663003", "zipcode": "30140071",
     "address": "Rua da Bahia", "number": "1148", "complement": "Conj. 801",
     "neighborhood": "Centro", "city": "Belo Horizonte", "state": "MG", "codparc": 50103},
    {"key": "paulo", "name": "Paulo Henrique Dias", "cpf": cpf_with_check_digits("663019248"),
     "email": "paulo.dias@exemplo.com", "phone": "41955554004", "zipcode": "80420090",
     "address": "Rua Comendador Araújo", "number": "143", "complement": "",
     "neighborhood": "Batel", "city": "Curitiba", "state": "PR", "codparc": 50104},
    {"key": "juliana", "name": "Juliana Prado", "cpf": cpf_with_check_digits("781264530"),
     "email": "juliana.prado@exemplo.com", "phone": "51944445005", "zipcode": "90570020",
     "address": "Rua Padre Chagas", "number": "79", "complement": "Loja 2",
     "neighborhood": "Moinhos de Vento", "city": "Porto Alegre", "state": "RS", "codparc": 50105},
    {"key": "thiago", "name": "Thiago Nogueira", "cpf": cpf_with_check_digits("125896347"),
     "email": "thiago.nogueira@exemplo.com", "phone": "71933336006", "zipcode": "41820020",
     "address": "Avenida Tancredo Neves", "number": "1632", "complement": "Torre Sul",
     "neighborhood": "Caminho das Árvores", "city": "Salvador", "state": "BA", "codparc": 50106},
    {"key": "beatriz", "name": "Beatriz Lacerda", "cpf": cpf_with_check_digits("934507126"),
     "email": "beatriz.lacerda@exemplo.com", "phone": "81922227007", "zipcode": "51020010",
     "address": "Avenida Boa Viagem", "number": "3200", "complement": "Apto 1201",
     "neighborhood": "Boa Viagem", "city": "Recife", "state": "PE", "codparc": None},
    {"key": "eduardo", "name": "Eduardo Matsuda", "cpf": cpf_with_check_digits("456781239"),
     "email": "eduardo.matsuda@exemplo.com", "phone": "11911118008", "zipcode": "01310200",
     "address": "Avenida Paulista", "number": "2064", "complement": "",
     "neighborhood": "Bela Vista", "city": "São Paulo", "state": "SP", "codparc": None},
    {"key": "fernanda", "name": "Fernanda Ribeiro", "cpf": cpf_with_check_digits("872036415"),
     "email": "fernanda.ribeiro@exemplo.com", "phone": "62900009009", "zipcode": "74150040",
     "address": "Avenida T-4", "number": "619", "complement": "",
     "neighborhood": "Setor Bueno", "city": "Goiânia", "state": "GO", "codparc": 50109},
]

#: Cliente cujo cadastro o ERP simulado recusa na primeira tentativa (mostra a
#: fila com erro e o botão "Reenviar"; a segunda tentativa passa).
FLAKY_CUSTOMER_KEY = "beatriz"

#: Cliente que existe só no ERP (nunca comprou no Go Sell): digitar o CPF dele
#: no checkout mostra o preenchimento vindo do ERP.
ERP_ONLY_CUSTOMER: Dict = {
    "name": "Gustavo Ferraz", "cpf": cpf_with_check_digits("390817254"),
    "email": "gustavo.ferraz@exemplo.com", "phone": "85988880010", "zipcode": "60160230",
    "address": "Avenida Dom Luís", "number": "500", "complement": "Sala 1110",
    "neighborhood": "Meireles", "city": "Fortaleza", "state": "CE", "codparc": 50110,
}


def customer_by_key(key: str) -> Dict:
    for c in CUSTOMERS:
        if c["key"] == key:
            return c
    raise KeyError(key)


def all_erp_customers() -> List[Dict]:
    """Parceiros que o ERP simulado já conhece (com ``codparc``)."""
    return [c for c in [*CUSTOMERS, ERP_ONLY_CUSTOMER] if c.get("codparc")]
