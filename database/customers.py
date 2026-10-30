"""Perfil analítico de clientes derivado das vendas.

Não existe cadastro de clientes no totem: o cliente é informado no checkout de
cada venda. Este módulo reconstrói a carteira agrupando ``transactions`` por
**identidade** — CPF quando disponível (é o campo mais confiável: preenchido em
100% das vendas confirmadas), com fallback em e-mail, telefone e, por último,
nome normalizado.

O CPF é comparado apenas pelos dígitos (``082.628.475-20`` e ``08262847520``
são o mesmo cliente) e CPFs-sentinela de dígito repetido (``000.000.000-00``)
são ignorados como identidade — senão todas as vendas com CPF de teste virariam
um único "cliente" fantasma.

Só vendas pagas (``status = 'confirmado'``) definem clientes: pendentes,
canceladas e estornadas ficam fora da carteira, dos perfis e do autopreenchimento
do checkout. Uma venda cancelada por CPF digitado errado não pode associar esse
CPF ao nome de outra pessoa.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .connection import fold_search_text, get_conn

# Ordem de precedência da identidade: do campo mais confiável para o menos.
IDENTITY_KINDS = ("cpf", "email", "phone", "name")

# CPFs de dígito repetido são placeholder de teste, não identidade real.
_PLACEHOLDER_CPFS = tuple(str(d) * 11 for d in range(10))
# Literais gerados em código (nunca entrada do usuário) — seguro interpolar.
_CPF_PLACEHOLDER_SQL = ", ".join(f"'{cpf}'" for cpf in _PLACEHOLDER_CPFS)

# Normaliza os campos de cliente e deriva (ident_kind, ident_value) por venda.
_IDENT_CTE = f"""
WITH norm AS (
    SELECT
        t.id, t.order_number, t.created_at, t.total, t.items_count,
        t.event_id, t.seller_id, t.seller_name, t.payment_method,
        t.card_installments, t.delivery_status, t.handover_status,
        t.client_name, t.client_cpf, t.client_email, t.client_phone,
        t.client_cro_uf, t.client_cro_numero, t.client_city, t.client_state,
        LOWER(TRIM(COALESCE(t.status, ''))) AS status_norm,
        REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(t.client_cpf, ''),
            '.', ''), '-', ''), '/', ''), ' ', '') AS cpf_digits,
        LOWER(TRIM(COALESCE(t.client_email, ''))) AS email_norm,
        REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(t.client_phone, ''),
            '(', ''), ')', ''), '-', ''), ' ', ''), '+', ''), '.', '') AS phone_digits,
        LOWER(TRIM(COALESCE(t.client_name, ''))) AS name_norm
      FROM transactions t
     WHERE LOWER(TRIM(COALESCE(t.status, ''))) = 'confirmado'
),
ident AS (
    SELECT *,
        CASE
            WHEN LENGTH(cpf_digits) >= 11
             AND cpf_digits NOT IN ({_CPF_PLACEHOLDER_SQL}) THEN 'cpf'
            WHEN email_norm <> ''                           THEN 'email'
            WHEN LENGTH(phone_digits) >= 10                 THEN 'phone'
            WHEN name_norm <> ''                            THEN 'name'
            ELSE ''
        END AS ident_kind,
        CASE
            WHEN LENGTH(cpf_digits) >= 11
             AND cpf_digits NOT IN ({_CPF_PLACEHOLDER_SQL}) THEN cpf_digits
            WHEN email_norm <> ''                           THEN email_norm
            WHEN LENGTH(phone_digits) >= 10                 THEN phone_digits
            WHEN name_norm <> ''                            THEN name_norm
            ELSE ''
        END AS ident_value
      FROM norm
)
"""

# Agregado por cliente sobre vendas confirmadas + contadores dos outros status.
# ``avg_interval_days`` = média de dias entre compras consecutivas; fica NULL
# para quem comprou uma única vez (não existe intervalo a medir).
_AGG_SQL = """
agg AS (
    SELECT
        ident_kind,
        ident_value,
        COUNT(*)                            AS orders_count,
        COALESCE(SUM(total), 0)             AS revenue,
        COALESCE(SUM(items_count), 0)       AS units,
        MIN(created_at)                     AS first_purchase_at,
        MAX(created_at)                     AS last_purchase_at,
        MAX(total)                          AS max_order_total,
        COUNT(DISTINCT event_id)            AS events_count,
        CASE WHEN COUNT(*) > 1
             THEN (julianday(MAX(created_at)) - julianday(MIN(created_at)))
                  / (COUNT(*) - 1)
        END                                 AS avg_interval_days,
        MAX(NULLIF(TRIM(COALESCE(client_email, '')), '')) AS any_email,
        MAX(NULLIF(TRIM(COALESCE(client_phone, '')), '')) AS any_phone
      FROM ident
     WHERE ident_kind <> '' AND status_norm = 'confirmado'{event_clause}
     GROUP BY ident_kind, ident_value
),
latest AS (
    SELECT ident_kind, ident_value, client_name, client_cpf,
           client_email, client_phone, client_cro_uf, client_cro_numero,
           client_city, client_state,
           ROW_NUMBER() OVER (
               PARTITION BY ident_kind, ident_value
               ORDER BY created_at DESC, id DESC
           ) AS rn
      FROM ident
     WHERE ident_kind <> '' AND status_norm = 'confirmado'{event_clause}
)
"""

# Allowlist de ordenação: a chave vem da querystring, nunca o SQL.
_SORTS = {
    "revenue": "a.revenue DESC, a.orders_count DESC",
    "orders": "a.orders_count DESC, a.revenue DESC",
    "recent": "a.last_purchase_at DESC",
    "oldest": "a.first_purchase_at ASC",
    "ticket": "(a.revenue / a.orders_count) DESC",
    "frequency": "a.avg_interval_days IS NULL, a.avg_interval_days ASC",
    "name": "LOWER(TRIM(COALESCE(l.client_name, ''))) ASC",
}
DEFAULT_CUSTOMER_SORT = "revenue"
CUSTOMER_SORTS = tuple(_SORTS)


def _only_digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def normalize_customer_ident(kind: str, ident: Any) -> Tuple[str, str]:
    """Normaliza ``(kind, ident)`` da URL para a forma usada no agrupamento.

    Permite que ``/admin/clientes/cpf/082.628.475-20`` e
    ``/admin/clientes/cpf/08262847520`` resolvam o mesmo perfil.
    """
    k = (kind or "").strip().lower()
    if k not in IDENTITY_KINDS:
        return "", ""
    raw = str(ident or "").strip()
    if k == "cpf":
        return k, _only_digits(raw)
    if k == "phone":
        return k, _only_digits(raw)
    if k == "email":
        return k, raw.lower()
    return k, " ".join(raw.lower().split())


def get_customer_display_name(kind: str, ident: Any) -> str:
    """Nome da venda mais recente do cliente (``""`` se não achar).

    Consulta enxuta para a trilha de navegação — evita montar o perfil inteiro
    só para rotular o breadcrumb.
    """
    norm_kind, norm_ident = normalize_customer_ident(kind, ident)
    if not norm_kind or not norm_ident:
        return ""
    with get_conn() as conn:
        row = conn.execute(
            f"""
            {_IDENT_CTE}
            SELECT client_name
              FROM ident
             WHERE ident_kind = ? AND ident_value = ?
               AND status_norm = 'confirmado'
             ORDER BY created_at DESC, id DESC
             LIMIT 1
            """,
            (norm_kind, norm_ident),
        ).fetchone()
    return (row["client_name"] or "").strip() if row else ""


# Campos copiados para o formulário de checkout quando o CPF é reconhecido.
# A chave é o nome do campo no formulário; o valor, a coluna em ``transactions``.
_AUTOFILL_FIELDS = {
    "name": "client_name",
    "email": "client_email",
    "phone": "client_phone",
    "cro_uf": "client_cro_uf",
    "cro_numero": "client_cro_numero",
    "zipcode": "client_zipcode",
    "address": "client_address",
    "number": "client_number",
    "complement": "client_complement",
    "neighborhood": "client_neighborhood",
    "city": "client_city",
    "state": "client_state",
}


def find_customer_by_cpf(cpf: Any) -> Optional[Dict[str, Any]]:
    """Dados já conhecidos do cliente com este CPF, ou ``None`` se for inédito.

    Cada campo recebe o valor **mais recente não vazio** do histórico, em vez de
    espelhar só a última venda: campos opcionais (e-mail, CEP) costumam ficar em
    branco em parte das compras, e copiar a última apagaria o que o cliente já
    tinha informado antes.

    Só vendas pagas contam: venda pendente ou cancelada costuma ser justamente a
    que teve o CPF digitado errado, e usá-la preencheria o nome de outra pessoa.
    """
    digits = _only_digits(cpf)
    if len(digits) != 11 or digits in _PLACEHOLDER_CPFS:
        return None

    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT {", ".join(_AUTOFILL_FIELDS.values())},
                   created_at, event_id
              FROM transactions
             WHERE REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(client_cpf, ''),
                   '.', ''), '-', ''), '/', ''), ' ', '') = ?
               AND LOWER(TRIM(COALESCE(status, ''))) = 'confirmado'
             ORDER BY created_at DESC, id DESC
            """,
            (digits,),
        ).fetchall()

    known: Dict[str, Any] = {}
    for row in rows:
        for field, column in _AUTOFILL_FIELDS.items():
            if field not in known:
                value = (row[column] or "").strip()
                if value:
                    known[field] = value
    if not known:
        return None

    known.update(
        {
            "cpf": digits,
            "orders_count": len(rows),
            "events_count": len(
                {r["event_id"] for r in rows if r["event_id"] is not None}
            ),
            "last_purchase_at": rows[0]["created_at"],
        }
    )
    return known


def _sankhya_text(value: Any) -> str:
    """Texto do gateway sem espaços de preenchimento; ``None``/``null`` viram vazio."""
    s = " ".join(str(value or "").split())
    return "" if s.lower() in ("none", "null") else s


def _sankhya_phone(value: Any) -> str:
    """Telefone com DDD (10 ou 11 dígitos). O ERP grava ``(099) ...`` e ``+55``."""
    digits = _only_digits(_sankhya_text(value))
    if len(digits) in (12, 13) and digits.startswith("55"):
        digits = digits[2:]
    digits = digits.lstrip("0")
    return digits if len(digits) in (10, 11) else ""


def customer_from_sankhya(row: Dict[str, Any]) -> Dict[str, Any]:
    """Linha de ``GET clients`` no formato do formulário do checkout.

    Só traz os campos com valor. CRO não vem do ERP (o gateway grava em
    ``AD_CRO``, mas ``/clients`` não devolve).
    """
    state = _sankhya_text(row.get("ESTADO")).upper()
    zipcode = _only_digits(row.get("CEP"))
    fields = {
        "name": _sankhya_text(row.get("NOME_FANTASIA")) or _sankhya_text(row.get("RAZAO_SOCIAL")),
        "email": _sankhya_text(row.get("EMAIL")).lower(),
        "phone": _sankhya_phone(row.get("CELULAR")) or _sankhya_phone(row.get("TELEFONE")),
        "zipcode": zipcode if len(zipcode) == 8 else "",
        "address": _sankhya_text(row.get("ENDERECO")),
        "number": _sankhya_text(row.get("NUMERO")),
        "complement": _sankhya_text(row.get("COMPLEMENTO")),
        "neighborhood": _sankhya_text(row.get("BAIRRO")),
        "city": _sankhya_text(row.get("CIDADE")),
        "state": state if len(state) == 2 and state.isalpha() else "",
    }
    return {k: v for k, v in fields.items() if v}


def merge_customer_sources(
    sankhya_row: Optional[Dict[str, Any]], local: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Dados do checkout: o cadastro do ERP primeiro, o Go Sell completa.

    Campo a campo — o que o ERP não tem (CRO, e-mail em branco) vem do
    histórico do Go Sell. ``source`` diz de onde veio o cliente; ``from_gosell``
    lista os campos completados pelo Go Sell quando a origem é o ERP.
    """
    if not sankhya_row and not local:
        return None
    local = dict(local or {})
    if not sankhya_row:
        local["source"] = "gosell"
        return local
    merged = dict(local)
    from_sankhya = customer_from_sankhya(sankhya_row)
    merged.update(from_sankhya)
    try:
        codparc: Optional[int] = int(sankhya_row.get("CODPARC"))
    except (TypeError, ValueError):
        codparc = None
    merged.update({
        "source": "sankhya",
        "codparc": codparc,
        "sankhya_active": _sankhya_text(sankhya_row.get("ATIVO")).upper() != "N",
        "from_gosell": sorted(
            f for f in _AUTOFILL_FIELDS if f in local and f not in from_sankhya
        ),
        "orders_count": int(local.get("orders_count") or 0),
        "events_count": int(local.get("events_count") or 0),
    })
    return merged


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _days_since(value: Any) -> Optional[float]:
    dt = _parse_dt(value)
    if dt is None:
        return None
    return max(0.0, (datetime.now() - dt).total_seconds() / 86400.0)


def _share(part: Any, whole: Any) -> float:
    try:
        total = float(whole or 0)
        if total <= 0:
            return 0.0
        return round(float(part or 0) / total * 100.0, 2)
    except (TypeError, ValueError):
        return 0.0


def _event_clause(event_id: Optional[int]) -> str:
    return " AND event_id = ?" if event_id else ""


def _event_params(event_id: Optional[int], times: int) -> List[Any]:
    return [int(event_id)] * times if event_id else []


def _scoped_revenue(conn, event_id: Optional[int]) -> Dict[str, Any]:
    """Faturamento confirmado do escopo (evento ou sistema inteiro).

    É o denominador do "percentual do faturamento" de cada cliente.
    """
    clause = " AND event_id = ?" if event_id else ""
    row = conn.execute(
        f"""
        SELECT COALESCE(SUM(total), 0)       AS revenue,
               COUNT(*)                      AS orders,
               COALESCE(SUM(items_count), 0) AS units
          FROM transactions
         WHERE LOWER(TRIM(COALESCE(status, ''))) = 'confirmado'{clause}
        """,
        _event_params(event_id, 1),
    ).fetchone()
    return {
        "revenue": float(row["revenue"] or 0),
        "orders": int(row["orders"] or 0),
        "units": int(row["units"] or 0),
    }


def _customer_row(row, total_revenue: float) -> Dict[str, Any]:
    orders = int(row["orders_count"] or 0)
    revenue = float(row["revenue"] or 0)
    name = (row["client_name"] or "").strip()
    interval = row["avg_interval_days"]
    return {
        "kind": row["ident_kind"],
        "ident": row["ident_value"],
        "name": name or "(sem nome)",
        "cpf": (row["client_cpf"] or "").strip(),
        "email": (row["client_email"] or row["any_email"] or "").strip(),
        "phone": (row["client_phone"] or row["any_phone"] or "").strip(),
        "cro_uf": (row["client_cro_uf"] or "").strip(),
        "cro_numero": (row["client_cro_numero"] or "").strip(),
        # Código de parceiro (CODPARC) no ERP, gravado no 1º envio de pedido.
        "codparc": row["erp_codparc"] if "erp_codparc" in row.keys() else None,
        "city": (row["client_city"] or "").strip(),
        "state": (row["client_state"] or "").strip(),
        "orders_count": orders,
        "units": int(row["units"] or 0),
        "revenue": revenue,
        "avg_ticket": round(revenue / orders, 2) if orders else 0.0,
        "max_order_total": float(row["max_order_total"] or 0),
        "events_count": int(row["events_count"] or 0),
        "first_purchase_at": row["first_purchase_at"],
        "last_purchase_at": row["last_purchase_at"],
        "days_since_last": _days_since(row["last_purchase_at"]),
        "avg_interval_days": float(interval) if interval is not None else None,
        "revenue_share": _share(revenue, total_revenue),
        "is_recurring": orders > 1,
    }


def list_customers(
    *,
    query: Optional[str] = None,
    event_id: Optional[int] = None,
    sort: str = DEFAULT_CUSTOMER_SORT,
    limit: Optional[int] = None,
    offset: int = 0,
) -> Dict[str, Any]:
    """Carteira de clientes agregada, com paginação, busca e ordenação.

    ``event_id`` restringe **e escopa** as métricas àquele evento (inclusive o
    denominador do percentual de faturamento), o que faz o percentual somar 100%
    dentro da página filtrada.

    Retorna ``{"rows": [...], "total": N, "summary": {...}}``.
    """
    sort_key = sort if sort in _SORTS else DEFAULT_CUSTOMER_SORT
    order_by = _SORTS[sort_key]
    ev_clause = _event_clause(event_id)
    agg_sql = _AGG_SQL.format(event_clause=ev_clause)

    term = fold_search_text(" ".join((query or "").split()))
    search_clause = ""
    search_params: List[Any] = []
    if term:
        like = f"%{term}%"
        # search_fold() ignora caixa e acento dos dois lados — sem ele, "galvão"
        # (digitado em minúsculo) não bate com "GALVÃO" salvo na venda, porque o
        # LOWER() nativo do SQLite não normaliza o "Ã" para "ã".
        conditions = [
            "search_fold(l.client_name) LIKE ?",
            "search_fold(l.client_email) LIKE ?",
            "search_fold(l.client_cro_numero) LIKE ?",
        ]
        search_params = [like, like, like]
        # CPF/telefone só entram na busca quando o termo tem dígitos — senão
        # um "%%" em cima do campo casaria com todo mundo.
        digits = _only_digits(term)
        if digits:
            digit_like = f"%{digits}%"
            conditions.append(
                "REPLACE(REPLACE(REPLACE(COALESCE(l.client_cpf, ''), '.', ''), '-', ''), ' ', '') LIKE ?"
            )
            conditions.append(
                "REPLACE(REPLACE(REPLACE(COALESCE(l.client_phone, ''), '(', ''), ')', ''), '-', '') LIKE ?"
            )
            search_params += [digit_like, digit_like]
            # CODPARC só por igualdade: um "76" casaria com metade da carteira.
            if len(digits) <= 9:
                conditions.append("ec.codparc = ?")
                search_params.append(int(digits))
        search_clause = " AND (" + " OR ".join(conditions) + ")"

    # ``event_clause`` aparece 2x no bloco de CTEs (agg, latest).
    cte_params = _event_params(event_id, 2)
    base = f"""
        {_IDENT_CTE},
        {agg_sql}
        SELECT a.*, l.client_name, l.client_cpf, l.client_email, l.client_phone,
               l.client_cro_uf, l.client_cro_numero, l.client_city, l.client_state,
               ec.codparc AS erp_codparc
          FROM agg a
          JOIN latest l
            ON l.ident_kind = a.ident_kind
           AND l.ident_value = a.ident_value
           AND l.rn = 1
          LEFT JOIN erp_customers ec
            ON a.ident_kind = 'cpf' AND ec.cpf = a.ident_value
         WHERE 1 = 1{search_clause}
    """

    with get_conn() as conn:
        scope = _scoped_revenue(conn, event_id)
        total_revenue = scope["revenue"]

        counted = conn.execute(
            f"SELECT COUNT(*) AS n FROM ({base})",
            cte_params + search_params,
        ).fetchone()
        total = int(counted["n"] or 0)

        sql = f"{base} ORDER BY {order_by}"
        params = cte_params + search_params
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params = params + [int(limit), max(0, int(offset))]
        rows = [
            _customer_row(r, total_revenue)
            for r in conn.execute(sql, params).fetchall()
        ]

        # Resumo da carteira inteira (ignora busca/paginação, respeita o evento).
        overview = conn.execute(
            f"""
            {_IDENT_CTE},
            {agg_sql}
            SELECT COUNT(*)                                   AS customers,
                   COALESCE(SUM(a.orders_count), 0)           AS orders,
                   COALESCE(SUM(a.revenue), 0)                AS revenue,
                   COALESCE(SUM(a.units), 0)                  AS units,
                   SUM(CASE WHEN a.orders_count > 1 THEN 1 ELSE 0 END) AS recurring,
                   MAX(a.revenue)                             AS top_revenue,
                   AVG(a.avg_interval_days)                   AS avg_interval_days
              FROM agg a
            """,
            cte_params,
        ).fetchone()

    customers = int(overview["customers"] or 0)
    orders = int(overview["orders"] or 0)
    revenue = float(overview["revenue"] or 0)
    recurring = int(overview["recurring"] or 0)
    avg_interval = overview["avg_interval_days"]
    summary = {
        "customers": customers,
        "orders": orders,
        "revenue": revenue,
        "units": int(overview["units"] or 0),
        "avg_ticket": round(revenue / orders, 2) if orders else 0.0,
        "avg_revenue_per_customer": round(revenue / customers, 2) if customers else 0.0,
        "avg_orders_per_customer": round(orders / customers, 2) if customers else 0.0,
        "recurring": recurring,
        "recurring_share": _share(recurring, customers),
        "one_time": max(0, customers - recurring),
        "top_share": _share(overview["top_revenue"], revenue),
        "avg_interval_days": float(avg_interval) if avg_interval is not None else None,
        "scope_revenue": total_revenue,
        "scope_orders": scope["orders"],
        # Vendas sem identidade aproveitável não entram na carteira.
        "unidentified_orders": max(0, scope["orders"] - orders),
    }
    return {"rows": rows, "total": total, "summary": summary, "sort": sort_key}


# Faixas de faturamento por cliente: (limite superior exclusivo em R$,
# rótulo completo, rótulo curto para o eixo do gráfico).
_REVENUE_BANDS = (
    (500, "Até R$ 500", "até 500"),
    (1000, "R$ 500–1 mil", "500–1 mil"),
    (2000, "R$ 1–2 mil", "1–2 mil"),
    (5000, "R$ 2–5 mil", "2–5 mil"),
    (10000, "R$ 5–10 mil", "5–10 mil"),
    (None, "Acima de R$ 10 mil", "10 mil+"),
)
PORTFOLIO_MAX_DAYS = 180
PORTFOLIO_TOP = 10


def get_portfolio_analytics(event_id: Optional[int] = None) -> Dict[str, Any]:
    """Séries da carteira inteira para os gráficos d3 da tela de Clientes.

    Mesmo recorte de :func:`list_customers` — vendas confirmadas com identidade,
    no escopo do evento — mas sem busca nem paginação: o gráfico descreve a
    carteira, não a página da tabela.

    - ``acquisition`` : série diária contínua {day, label, new, returning,
                        orders, revenue, cumulative_customers}
    - ``frequency``   : clientes por número de pedidos (1, 2, 3, 4, 5+)
    - ``bands``       : clientes por faixa de faturamento
    - ``top``         : maiores clientes {kind, ident, name, revenue, orders}
    - ``totals``      : {customers, revenue, orders, recurring, median_revenue}
    """
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            {_IDENT_CTE}
            SELECT ident_kind, ident_value, created_at, total, client_name
              FROM ident
             WHERE ident_kind <> '' AND status_norm = 'confirmado'{_event_clause(event_id)}
             ORDER BY created_at ASC, id ASC
            """,
            _event_params(event_id, 1),
        ).fetchall()

    people: Dict[Tuple[str, str], Dict[str, Any]] = {}
    days: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        key = (r["ident_kind"], r["ident_value"])
        total = float(r["total"] or 0)
        person = people.get(key)
        first_time = person is None
        if first_time:
            person = people[key] = {
                "kind": key[0], "ident": key[1], "name": "", "revenue": 0.0, "orders": 0,
            }
        person["revenue"] += total
        person["orders"] += 1
        name = (r["client_name"] or "").strip()
        if name:
            person["name"] = name  # linhas em ordem crescente: fica o nome mais recente

        day = str(r["created_at"] or "")[:10]
        slot = days.setdefault(day, {"new": set(), "returning": set(), "orders": 0, "revenue": 0.0})
        slot["orders"] += 1
        slot["revenue"] += total
        # Recorrente no dia = já tinha comprado em um dia anterior do escopo.
        if first_time:
            slot["new"].add(key)
        elif key not in slot["new"]:
            slot["returning"].add(key)

    # ---------- aquisição: série diária contínua (lacunas viram zero) ----------
    acquisition: List[Dict[str, Any]] = []
    parsed = {}
    for day, slot in days.items():
        try:
            parsed[datetime.fromisoformat(day).date()] = slot
        except ValueError:
            continue
    if parsed:
        cursor, last = min(parsed), max(parsed)
        running = 0
        while cursor <= last and len(acquisition) < PORTFOLIO_MAX_DAYS:
            slot = parsed.get(cursor)
            new = len(slot["new"]) if slot else 0
            running += new
            acquisition.append({
                "day": cursor.isoformat(),
                "label": cursor.strftime("%d/%m"),
                "new": new,
                "returning": len(slot["returning"]) if slot else 0,
                "orders": slot["orders"] if slot else 0,
                "revenue": round(slot["revenue"], 2) if slot else 0.0,
                "cumulative_customers": running,
            })
            cursor += timedelta(days=1)

    ranked = sorted(people.values(), key=lambda p: (-p["revenue"], -p["orders"]))
    n = len(ranked)
    revenue_total = sum(p["revenue"] for p in ranked)

    # ---------- frequência de compra ----------
    freq_labels = ("1 pedido", "2 pedidos", "3 pedidos", "4 pedidos", "5+ pedidos")
    frequency = [
        {"label": label, "orders_min": i + 1, "customers": 0, "revenue": 0.0}
        for i, label in enumerate(freq_labels)
    ]
    for p in ranked:
        bucket = frequency[min(p["orders"], 5) - 1]
        bucket["customers"] += 1
        bucket["revenue"] += p["revenue"]
    while len(frequency) > 2 and frequency[-1]["customers"] == 0:
        frequency.pop()

    # ---------- faixas de faturamento ----------
    bands = [
        {"label": label, "short": short, "customers": 0, "revenue": 0.0}
        for _, label, short in _REVENUE_BANDS
    ]
    for p in ranked:
        for i, (upper, _, _) in enumerate(_REVENUE_BANDS):
            if upper is None or p["revenue"] < upper:
                bands[i]["customers"] += 1
                bands[i]["revenue"] += p["revenue"]
                break
    while len(bands) > 2 and bands[-1]["customers"] == 0:
        bands.pop()

    revenues = sorted(p["revenue"] for p in ranked)
    median = 0.0
    if revenues:
        mid = n // 2
        median = revenues[mid] if n % 2 else (revenues[mid - 1] + revenues[mid]) / 2

    return {
        "acquisition": acquisition,
        "frequency": [
            {**b, "revenue": round(b["revenue"], 2)} for b in frequency
        ],
        "bands": [{**b, "revenue": round(b["revenue"], 2)} for b in bands],
        "top": [
            {
                "kind": p["kind"],
                "ident": p["ident"],
                "name": p["name"] or "(sem nome)",
                "revenue": round(p["revenue"], 2),
                "orders": p["orders"],
            }
            for p in ranked[:PORTFOLIO_TOP]
        ],
        "totals": {
            "customers": n,
            "revenue": round(revenue_total, 2),
            "orders": len(rows),
            "recurring": sum(1 for p in ranked if p["orders"] > 1),
            "median_revenue": round(median, 2),
        },
    }


def _scope_clause(event_id: Optional[int], alias: str = "") -> str:
    return f" AND {alias}event_id = ?" if event_id else ""


def _profile_orders(
    conn, kind: str, ident: str, event_id: Optional[int] = None
) -> List[Dict[str, Any]]:
    """Compras pagas do cliente, da mais recente à mais antiga."""
    rows = conn.execute(
        f"""
        {_IDENT_CTE}
        SELECT i.id, i.order_number, i.created_at, i.total, i.items_count,
               i.status_norm AS status, i.payment_method, i.card_installments,
               i.delivery_status, i.handover_status, i.event_id, i.seller_id,
               i.seller_name, e.name AS event_name, e.badge_color AS event_badge_color
          FROM ident i
          LEFT JOIN events e ON e.id = i.event_id
         WHERE i.ident_kind = ? AND i.ident_value = ?{_scope_clause(event_id, "i.")}
         ORDER BY i.created_at DESC, i.id DESC
        """,
        (kind, ident, *_event_params(event_id, 1)),
    ).fetchall()

    orders = [dict(r) for r in rows]
    # Dias desde a compra confirmada anterior (a lista está em ordem decrescente).
    confirmed = [o for o in orders if o["status"] == "confirmado"]
    for newer, older in zip(confirmed, confirmed[1:]):
        a, b = _parse_dt(newer["created_at"]), _parse_dt(older["created_at"])
        newer["days_since_previous"] = (
            round((a - b).total_seconds() / 86400.0, 2) if a and b else None
        )
    return orders


def _profile_breakdowns(
    conn, kind: str, ident: str, event_id: Optional[int] = None
) -> Dict[str, List[Dict[str, Any]]]:
    """Top produtos, categorias, formas de pagamento, eventos e vendedores.

    ``event_id`` restringe tudo a um evento, menos ``events`` — a lista de
    eventos do cliente é o que alimenta o próprio filtro.
    """
    ident_args = (kind, ident)
    scoped_args = (kind, ident, *_event_params(event_id, 1))
    scope_i = _scope_clause(event_id, "i.")
    scope = _scope_clause(event_id)

    products = conn.execute(
        f"""
        {_IDENT_CTE}
        SELECT ti.product_name,
               MAX(COALESCE(ti.product_sku, ''))  AS product_sku,
               MAX(COALESCE(ti.product_id, ''))   AS product_id,
               MAX(COALESCE(ti.category, ''))     AS category,
               SUM(ti.quantity)                   AS units,
               SUM(ti.subtotal)                   AS revenue,
               COUNT(DISTINCT ti.transaction_id)  AS orders
          FROM ident i
          JOIN transaction_items ti ON ti.transaction_id = i.id
         WHERE i.ident_kind = ? AND i.ident_value = ?
           AND i.status_norm = 'confirmado'{scope_i}
         GROUP BY LOWER(TRIM(ti.product_name))
         ORDER BY units DESC, revenue DESC
        """,
        scoped_args,
    ).fetchall()

    categories = conn.execute(
        f"""
        {_IDENT_CTE}
        SELECT COALESCE(NULLIF(TRIM(ti.category), ''), 'Sem categoria') AS category,
               SUM(ti.quantity) AS units,
               SUM(ti.subtotal) AS revenue
          FROM ident i
          JOIN transaction_items ti ON ti.transaction_id = i.id
         WHERE i.ident_kind = ? AND i.ident_value = ?
           AND i.status_norm = 'confirmado'{scope_i}
         GROUP BY LOWER(TRIM(COALESCE(ti.category, '')))
         ORDER BY revenue DESC
        """,
        scoped_args,
    ).fetchall()

    payments = conn.execute(
        f"""
        {_IDENT_CTE}
        SELECT COALESCE(NULLIF(TRIM(payment_method), ''), 'cartao') AS method,
               COUNT(*)              AS orders,
               COALESCE(SUM(total), 0) AS revenue
          FROM ident
         WHERE ident_kind = ? AND ident_value = ? AND status_norm = 'confirmado'{scope}
         GROUP BY LOWER(TRIM(COALESCE(payment_method, '')))
         ORDER BY revenue DESC
        """,
        scoped_args,
    ).fetchall()

    events = conn.execute(
        f"""
        {_IDENT_CTE}
        SELECT i.event_id, e.name AS event_name, e.badge_color,
               COUNT(*)                AS orders,
               COALESCE(SUM(i.total), 0) AS revenue,
               MAX(i.created_at)       AS last_purchase_at
          FROM ident i
          LEFT JOIN events e ON e.id = i.event_id
         WHERE i.ident_kind = ? AND i.ident_value = ? AND i.status_norm = 'confirmado'
         GROUP BY i.event_id
         ORDER BY revenue DESC
        """,
        ident_args,
    ).fetchall()

    sellers = conn.execute(
        f"""
        {_IDENT_CTE}
        SELECT seller_id,
               COALESCE(NULLIF(TRIM(seller_name), ''), 'Sem vendedor') AS seller_name,
               COUNT(*)                AS orders,
               COALESCE(SUM(total), 0) AS revenue
          FROM ident
         WHERE ident_kind = ? AND ident_value = ? AND status_norm = 'confirmado'{scope}
         GROUP BY seller_id
         ORDER BY revenue DESC
        """,
        scoped_args,
    ).fetchall()

    monthly = conn.execute(
        f"""
        {_IDENT_CTE}
        SELECT strftime('%Y-%m', created_at) AS month,
               COUNT(*)                      AS orders,
               COALESCE(SUM(total), 0)       AS revenue,
               COALESCE(SUM(items_count), 0) AS units
          FROM ident
         WHERE ident_kind = ? AND ident_value = ? AND status_norm = 'confirmado'{scope}
         GROUP BY month
         ORDER BY month ASC
        """,
        scoped_args,
    ).fetchall()

    return {
        "products": [dict(r) for r in products],
        "categories": [dict(r) for r in categories],
        "payments": [dict(r) for r in payments],
        "events": [dict(r) for r in events],
        "sellers": [dict(r) for r in sellers],
        "monthly": [dict(r) for r in monthly],
    }


def _interval_stats(confirmed_dates: List[datetime]) -> Dict[str, Any]:
    """Estatísticas de periodicidade a partir das datas de compra confirmadas.

    ``series`` traz cada intervalo na ordem em que aconteceu — é o que o
    gráfico de ritmo de compra desenha.
    """
    ordered = sorted(d for d in confirmed_dates if d is not None)
    gaps = [
        (b - a).total_seconds() / 86400.0
        for a, b in zip(ordered, ordered[1:])
    ]
    if not gaps:
        return {
            "count": 0,
            "avg_days": None,
            "min_days": None,
            "max_days": None,
            "median_days": None,
            "series": [],
        }
    ranked = sorted(gaps)
    mid = len(ranked) // 2
    median = ranked[mid] if len(ranked) % 2 else (ranked[mid - 1] + ranked[mid]) / 2
    return {
        "count": len(gaps),
        "avg_days": round(sum(gaps) / len(gaps), 2),
        "min_days": round(min(gaps), 2),
        "max_days": round(max(gaps), 2),
        "median_days": round(median, 2),
        "series": [
            {
                "index": i + 1,
                "from": a.isoformat(timespec="seconds"),
                "to": b.isoformat(timespec="seconds"),
                "days": round(gap, 4),
            }
            for i, (a, b, gap) in enumerate(zip(ordered, ordered[1:], gaps))
        ],
    }


def get_customer_profile(
    kind: str, ident: Any, event_id: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    """Perfil analítico completo de um cliente, ou ``None`` se não existir.

    Inclui KPIs (receita, ticket médio, participação no faturamento total),
    periodicidade de compra, top produtos/categorias, formas de pagamento,
    eventos, vendedores que o atenderam e o histórico de pedidos.

    ``event_id`` recorta histórico e análise (produtos, categorias, pagamentos,
    vendedores, ritmo de compra) a um dos eventos em que o cliente comprou;
    os KPIs do cabeçalho e a lista de eventos continuam cobrindo tudo. Um
    evento em que o cliente não comprou é ignorado (``event_filter`` = None).
    """
    norm_kind, norm_ident = normalize_customer_ident(kind, ident)
    if not norm_kind or not norm_ident:
        return None

    with get_conn() as conn:
        scope = _scoped_revenue(conn, None)
        total_revenue = scope["revenue"]

        head = conn.execute(
            f"""
            {_IDENT_CTE},
            {_AGG_SQL.format(event_clause="")}
            SELECT a.*, l.client_name, l.client_cpf, l.client_email, l.client_phone,
                   l.client_cro_uf, l.client_cro_numero, l.client_city, l.client_state,
                   ec.codparc AS erp_codparc
              FROM agg a
              JOIN latest l
                ON l.ident_kind = a.ident_kind
               AND l.ident_value = a.ident_value
               AND l.rn = 1
              LEFT JOIN erp_customers ec
                ON a.ident_kind = 'cpf' AND ec.cpf = a.ident_value
             WHERE a.ident_kind = ? AND a.ident_value = ?
            """,
            (norm_kind, norm_ident),
        ).fetchone()
        if head is None:
            return None

        customer = _customer_row(head, total_revenue)
        all_parts = _profile_breakdowns(conn, norm_kind, norm_ident)
        bought_in = {e["event_id"] for e in all_parts["events"] if e["event_id"]}
        if event_id not in bought_in:
            event_id = None
        parts = (
            _profile_breakdowns(conn, norm_kind, norm_ident, event_id)
            if event_id
            else all_parts
        )
        orders = _profile_orders(conn, norm_kind, norm_ident, event_id)

        # Posição no ranking de faturamento da carteira inteira (mesmo escopo
        # global do percentual de participação).
        standing = conn.execute(
            f"""
            {_IDENT_CTE},
            {_AGG_SQL.format(event_clause="")}
            SELECT COUNT(*) AS customers,
                   COALESCE(SUM(CASE WHEN revenue > ? THEN 1 ELSE 0 END), 0) AS above
              FROM agg
            """,
            (customer["revenue"],),
        ).fetchone()

    revenue = customer["revenue"]
    # Participações relativas ao recorte exibido (evento filtrado ou tudo).
    scope_revenue = sum(float(o["total"] or 0) for o in orders)
    for item in parts["products"]:
        item["share"] = _share(item["revenue"], scope_revenue)
    for item in parts["categories"]:
        item["share"] = _share(item["revenue"], scope_revenue)
    for item in parts["payments"]:
        item["share"] = _share(item["revenue"], scope_revenue)
    for item in all_parts["events"]:
        item["share"] = _share(item["revenue"], revenue)
    scope_event = next(
        (e for e in all_parts["events"] if event_id and e["event_id"] == event_id), None
    )

    confirmed_dates = [
        _parse_dt(o["created_at"]) for o in orders if o["status"] == "confirmado"
    ]
    intervals = _interval_stats([d for d in confirmed_dates if d])

    # Projeção simples da próxima compra: última data + periodicidade média.
    next_expected = None
    if intervals["avg_days"] and confirmed_dates:
        last = max(d for d in confirmed_dates if d)
        next_expected = (last + timedelta(days=intervals["avg_days"])).isoformat(
            timespec="seconds"
        )

    customers_total = int(standing["customers"] or 0)
    rank = int(standing["above"] or 0) + 1
    customer.update(
        {
            "identity": {"kind": norm_kind, "ident": norm_ident},
            "rank": rank,
            "customers_total": customers_total,
            "rank_top_pct": round(rank / customers_total * 100.0, 1) if customers_total else None,
            "intervals": intervals,
            "next_purchase_expected_at": next_expected,
            "distinct_products": len(all_parts["products"]),
            "distinct_categories": len(all_parts["categories"]),
            "event_filter": event_id,
            "scope": {
                "event_id": event_id,
                "event_name": (scope_event or {}).get("event_name") or "",
                "orders_count": len(orders),
                "revenue": round(scope_revenue, 2),
                "units": sum(int(o["items_count"] or 0) for o in orders),
                "distinct_products": len(parts["products"]),
                "distinct_categories": len(parts["categories"]),
            },
            "top_products": parts["products"][:10],
            "all_products": parts["products"],
            "categories": parts["categories"],
            "payments": parts["payments"],
            "events": all_parts["events"],
            "sellers": parts["sellers"],
            "monthly": parts["monthly"],
            "orders": orders,
            "total_revenue_all": total_revenue,
        }
    )
    return customer
