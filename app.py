"""Go Sell Demo — aplicação Flask (boas-vindas + painéis admin/vendedor).

Demonstração comercial com dados fictícios: o ERP é simulado (``sankhya_api.py``),
as imagens são locais e o banco é semeado por ``seed/demo.py``.

- ``/``                Tela de boas-vindas; o catálogo público não é autoatendimento.
- ``/catalogo`` etc. Rotas antigas redirecionam para ``/`` (venda só com vendedor logado).
- ``/api/...``         Endpoints JSON (vendas exigem sessão do vendedor).
- ``/admin/...``       Painel administrativo.
- ``/vendedor/...``    Painel do vendedor (catálogo/venda, minhas vendas, estoque, movimentações, transações).
"""

from __future__ import annotations

import csv
import hmac
import io
import logging
import math
import os
import re
import secrets
import unicodedata
import subprocess
import threading
import sys

import totem_env  # noqa: F401 — carrega .env / totem.env antes da integração ERP
from receipt_tokens import sign_receipt_token, verify_receipt_token
from functools import wraps
from urllib.parse import unquote, urlparse
from datetime import date, datetime

try:
    import xlrd as _xlrd  # .xls legacy (BIFF)
    _XLRD_AVAILABLE = True
except ImportError:
    _xlrd = None
    _XLRD_AVAILABLE = False


def _pip_install(package: str) -> None:
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", package],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _load_xlsx_workbook(file_bytes: bytes):
    """Abre um .xlsx com openpyxl, instalando a lib neste Python se ainda faltar."""
    try:
        from openpyxl import load_workbook
    except ImportError:
        try:
            _pip_install("openpyxl>=3.1.0,<4")
            from openpyxl import load_workbook
        except Exception as exc:
            raise RuntimeError(
                "Não foi possível usar o openpyxl neste Python. "
                f"Execute: {sys.executable} -m pip install openpyxl"
            ) from exc
    return load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)

from flask import (
    Flask,
    Response,
    current_app,
    flash,
    g,
    get_flashed_messages,
    has_request_context,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_wtf.csrf import CSRFError, CSRFProtect
from itsdangerous import BadSignature, URLSafeSerializer
from werkzeug.routing.exceptions import BuildError
from werkzeug.security import check_password_hash, generate_password_hash

from data.products import CATEGORIES
from database import (
    GIFT_RULE_LABELS,
    RULE_TYPE_LABELS,
    active_gift_rules_for_cart,
    create_gift_item,
    create_gift_rule,
    delete_gift_item,
    delete_gift_rule,
    get_gift_item,
    get_gift_rule,
    list_gift_items_for_event,
    list_gift_rules_for_event,
    toggle_gift_rule_active,
    update_gift_item,
    update_gift_rule,
    active_promotion_names_by_product_id,
    active_promotion_tooltip_by_product_id,
    build_promo_display_map,
    create_promotion,
    delete_promotion,
    enrich_product_with_promo,
    get_active_promotions_for_event,
    get_promotion,
    list_promotions_for_event,
    list_promotions_for_event_export,
    product_ids_with_active_promotions_for_event,
    quote_cart_items_for_event,
    toggle_promotion_active,
    update_promotion,
    add_product_to_event,
    add_seller_to_event,
    archive_event,
    count_products_admin_filtered,
    create_event,
    create_seller_account,
    confirm_item_delivery,
    confirm_items_delivery,
    confirm_transaction_handover,
    confirm_transaction_with_aut,
    count_event_product_ledger,
    count_pending_delivery_transactions,
    EXPORT_STOCK_CSV_CAP,
    create_transaction,
    update_pending_transaction,
    delete_seller,
    ensure_seller_account,
    event_badge_style_pairs,
    find_product_by_sku_or_id,
    get_active_event_for_seller,
    get_event,
    get_event_financial_report,
    get_event_sales_analytics,
    get_event_sales_dashboard,
    get_event_stats,
    get_event_stock_stats,
    get_product,
    get_product_events_stock_total,
    get_product_in_event,
    get_products_library_stats,
    cancel_pending_transaction_for_seller,
    get_pending_transaction_if_owned,
    get_pending_transaction_restore_payload,
    get_seller,
    get_seller_admin_event_selection_id,
    get_seller_by_email,
    get_seller_by_username,
    get_stats,
    find_customer_by_cpf,
    merge_customer_sources,
    get_customer_display_name,
    get_customer_profile,
    get_seller_analytics,
    list_seller_events,
    get_portfolio_analytics,
    list_customers,
    CUSTOMER_SORTS,
    DEFAULT_CUSTOMER_SORT,
    get_transaction_by_order_number,
    init_db,
    DEFAULT_MIN_STOCK,
    list_active_event_product_stocks,
    list_active_product_stocks,
    list_distinct_product_categories,
    count_event_products_filtered,
    count_stock_movements,
    count_transactions_for_event,
    count_transactions_for_seller,
    list_event_product_ledger,
    list_event_products,
    list_event_products_filtered_for_client,
    list_event_products_for_client,
    list_event_products_slice,
    list_event_sellers,
    list_event_stock_movements,
    list_events,
    list_products_admin,
    list_products_admin_slice,
    list_products_for_client,
    summarize_catalog_option_groups,
    list_sellers,
    PANEL_PERMISSIONS,
    PERMISSIONS,
    PERMISSION_KEYS,
    PRESETS,
    clean_permissions,
    describe_user,
    list_panel_events_for_user,
    normalize_role,
    set_user_access,
    user_can_access_event,
    list_stock_movements,
    list_transaction_items_for_event_period,
    list_transactions,
    list_transactions_for_event,
    list_transactions_for_seller,
    list_transactions_summary_for_event_period,
    normalize_event_badge_color,
    pending_delivery_units_by_product_for_event,
    units_sold_by_product_for_event,
    units_sold_by_product_for_seller,
    units_sold_by_product_per_event,
    refund_transaction,
    replace_transaction_item_product,
    register_event_stock_adjustment,
    register_event_stock_entry,
    register_event_stock_exit,
    remove_product_from_event,
    replace_seller_event_assignment,
    reset_totem_to_default_state,
    restore_event,
    set_product_active,
    create_local_product,
    get_product_erp_link_summary,
    product_awaiting_erp_price,
    list_sellers_without_codvend,
    normalize_seller_codvend,
    set_product_erp_codprod,
    set_product_erp_codvol,
    update_event,
    update_event_product_backorder_limit,
    update_event_product_price,
    update_event_product_stock,
    update_seller_account,
    update_seller_last_login,
    update_transaction_receipt_note,
    validate_seller_username,
    update_event_goals,
    EVENT_OPERATION_TYPES,
    cancel_pending_transaction,
    event_operation_noun,
    event_operation_type_label,
    event_ops_open,
    get_transaction,
    normalize_event_operation_type,
    set_event_operations_closed,
    list_checkout_stock_conflicts,
    release_seller_checkout_holds,
    sync_seller_checkout_holds,
)
import breadcrumbs
import erp_sync
import product_images
import sankhya_api
from database import erp as erp_db
from database.payment_methods import (
    accepts_installments,
    normalize_payment_method,
    payment_method_label,
)


# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

app = Flask(__name__)

# SECRET_KEY persistida em totem.env (totem_env.ensure_persistent_secret_key).
app.secret_key = os.environ.get("TOTEM_SECRET_KEY") or secrets.token_hex(32)

# CSRF (Flask-WTF): mesma chave da sessão; sem limite de tempo para o token na sessão atual.
app.config.setdefault("WTF_CSRF_TIME_LIMIT", None)
csrf = CSRFProtect(app)


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _safe_internal_url(
    candidate: str | None,
    fallback: str,
    *,
    path_prefix: str | None = None,
) -> str:
    """Bloqueia open-redirect: só path relativo no mesmo host (ou URL absoluta do próprio host)."""
    if not candidate:
        return fallback
    raw = str(candidate).strip()
    if not raw or "\\" in raw or raw.startswith("\\"):
        return fallback
    if raw.startswith("//"):
        return fallback

    parsed = urlparse(raw)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme not in ("http", "https"):
            return fallback
        try:
            here = urlparse(request.host_url)
        except Exception:
            return fallback
        if parsed.netloc.lower() != (here.netloc or "").lower():
            return fallback
        path = parsed.path or "/"
    else:
        nested = urlparse(raw)
        if nested.scheme or nested.netloc:
            return fallback
        path = nested.path or ""
        parsed = nested
        if not path.startswith("/"):
            return fallback

    path = unquote(path)
    if not path.startswith("/") or path.startswith("//"):
        return fallback
    if ".." in path.split("/"):
        return fallback
    if path_prefix and not (path == path_prefix or path.startswith(path_prefix + "/")):
        return fallback

    out = path
    if parsed.query:
        out += "?" + parsed.query
    if parsed.fragment:
        out += "#" + parsed.fragment
    return out


def _with_url_fragment(url: str, fragment: str | None) -> str:
    """Substitui o fragmento da URL (o Referer normalmente não inclui ``#…``)."""
    base = (url or "").split("#", 1)[0]
    frag = (fragment or "").lstrip("#")
    return f"{base}#{frag}" if frag else base


def _redirect_back(fallback: str, *, fragment: str | None = None):
    """Redirect para o Referer só se for do mesmo host; senão ``fallback``."""
    target = _safe_internal_url(request.referrer, fallback)
    if fragment:
        target = _with_url_fragment(target, fragment)
    return redirect(target)


def _forwarded_query_args(*route_params: str) -> dict:
    """Query string atual para repassar a ``url_for`` num redirect de compatibilidade.

    Descarta as chaves reservadas do ``url_for`` (``_external``, ``_scheme``,
    ``_anchor``...) e as que já são parâmetros da rota: ``?event_id=9`` ou
    ``?_scheme=x`` derrubariam a rota com erro 500 em vez de serem ignorados.
    """
    blocked = set(route_params)
    return {
        key: value
        for key, value in request.args.to_dict(flat=True).items()
        if key and not key.startswith("_") and key not in blocked
    }


@app.after_request
def _security_headers(response):
    """Cabeçalhos defensivos (CVE-2026-27205: páginas autenticadas não devem ir para cache compartilhado)."""
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if not request.path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", "private, no-store")
    return response


@app.errorhandler(CSRFError)
def handle_csrf_error(_e):
    accept = (request.headers.get("Accept") or "").lower()
    wants_json = (
        request.path.startswith("/api/")
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in accept
    )
    if wants_json:
        return jsonify(
            error=(
                "Sessão expirada ou token de segurança inválido. "
                "Recarregue a página e tente novamente."
            ),
        ), 400
    flash(
        "Sessão expirada ou token de segurança inválido. Recarregue a página e tente novamente.",
        "error",
    )
    return _redirect_back(url_for("welcome"))

# Credenciais do admin da demo (visíveis na tela inicial).
ADMIN_USERNAME = os.environ.get("TOTEM_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("TOTEM_ADMIN_PASS", "demo")
# As demais contas (ana, bruno, marina, carlos) vêm do seed (seed/demo.py).
ADMIN_AUTH_COOKIE = "totem_admin_auth"
SELLER_AUTH_COOKIE = "totem_seller_auth"
USER_AUTH_COOKIE = "totem_user_auth"
ADMIN_AUTH_SALT = "totem-admin-auth"
SELLER_AUTH_SALT = "totem-seller-auth"
USER_AUTH_SALT = "totem-user-auth"
AUTH_COOKIE_OPTIONS = {
    "httponly": True,
    "samesite": "Lax",
}


def _url_if_registered(endpoint: str, *, fallback: str) -> str:
    """Evita BuildError se o endpoint ainda não existir no mapa de rotas."""
    try:
        views = current_app.view_functions
    except RuntimeError:
        return fallback
    if endpoint in views:
        return url_for(endpoint)
    return fallback


def _seller_pending_cancel_url(tx_id: int) -> str:
    """POST para descartar pedido pendente; fallback seguro se ``url_for`` falhar."""
    eid = int(tx_id)

    def fallback_path() -> str:
        prefix = (
            (request.script_root or "").rstrip("/") if has_request_context() else ""
        )
        return f"{prefix}/vendedor/pedido/{eid}/cancelar-pendente"

    try:
        views = current_app.view_functions
    except RuntimeError:
        return fallback_path()
    if "seller_cancel_pending_transaction" not in views:
        return fallback_path()
    try:
        return url_for("seller_cancel_pending_transaction", tx_id=eid)
    except BuildError:
        return fallback_path()


def _normalize_payment_method_for_db(value) -> str:
    return normalize_payment_method(value)


def _payment_method_label(value, card_installments=None) -> str:
    return payment_method_label(value, card_installments)


def _format_brl(value) -> str:
    try:
        n = float(value or 0)
    except (TypeError, ValueError):
        n = 0.0
    formatted = f"{n:,.2f}"
    formatted = formatted.replace(",", "§").replace(".", ",").replace("§", ".")
    return f"R$ {formatted}"


def _card_installment_plan_text(total, payment_method, card_installments):
    """Texto do parcelamento no cartão (ex.: ``2x de R$187,25``)."""
    if not accepts_installments(payment_method):
        return None
    try:
        n = int(card_installments)
    except (TypeError, ValueError):
        return None
    if n < 2:
        return None
    try:
        t = float(total or 0)
    except (TypeError, ValueError):
        return None
    if t <= 0:
        return None
    per = round(t / n, 2)
    return f"{n}x de {_format_brl(per)}"


def _display_created_by(value) -> str:
    """Texto exibível do campo created_by (remove prefixo interno ``vendedor:``)."""
    if value is None:
        return ""
    s = str(value).strip()
    if s.lower().startswith("vendedor:"):
        return s[9:].strip()
    if s.lower().startswith("seller:"):
        tail = s[7:].strip()
        if tail.isdigit():
            row = get_seller(int(tail))
            if row and row.get("name"):
                return str(row["name"]).strip()
        return tail or s
    return s


def _event_badge_style_filter(raw) -> str:
    """CSS inline para badge de evento (fundo + texto legível)."""
    bg, fg = event_badge_style_pairs(raw)
    if not bg or not fg:
        return ""
    return f"background-color:{bg};color:{fg};"


def _parcelas_cartao_filter(total, payment_method, installments):
    """Filtro Jinja: ``{{ total | parcelas_cartao(pm, inst) }}``."""
    return _card_installment_plan_text(total, payment_method, installments) or ""


# Registro imediato: garante o filtro Jinja mesmo com importações parciais / reload.
def _op_noun_filter(source, plural=False, lower=False):
    """Filtro Jinja: ``{{ event | op_noun }}`` / ``op_noun(lower=True)`` / ``op_noun(plural=True)``."""
    text = event_operation_noun(source, plural=bool(plural))
    return text.lower() if lower else text


app.add_template_filter(_display_created_by, "display_created_by")
app.add_template_filter(_event_badge_style_filter, "event_badge_style")
app.add_template_filter(_parcelas_cartao_filter, "parcelas_cartao")
app.add_template_filter(event_operation_type_label, "event_operation_type_label")
app.add_template_filter(_op_noun_filter, "op_noun")
app.add_template_global(event_ops_open, "event_ops_open")
app.add_template_global(EVENT_OPERATION_TYPES, "event_operation_types")

# Inicializa o schema e o seed da demonstração (se o banco estiver vazio).
init_db()
try:
    from seed.demo import ensure_demo_data

    ensure_demo_data()
except Exception as exc:
    app.logger.warning("Seed da demonstração indisponível: %s", exc)


def _auth_serializer(salt: str) -> URLSafeSerializer:
    return URLSafeSerializer(app.secret_key, salt=salt)


def _load_auth_cookie(cookie_name: str, salt: str) -> dict | None:
    token = request.cookies.get(cookie_name)
    if not token:
        return None
    try:
        data = _auth_serializer(salt).loads(token)
    except BadSignature:
        return None
    return data if isinstance(data, dict) else None


def _auth_cookie_kwargs() -> dict:
    opts = dict(AUTH_COOKIE_OPTIONS)
    if _env_flag("TOTEM_COOKIE_SECURE") or request.is_secure:
        opts["secure"] = True
    return opts


def _set_auth_cookie(response, cookie_name: str, salt: str, payload: dict):
    response.set_cookie(
        cookie_name,
        _auth_serializer(salt).dumps(payload),
        **AUTH_COOKIE_OPTIONS,
    )
    return response


def _delete_auth_cookie(response, cookie_name: str):
    response.delete_cookie(cookie_name, samesite=AUTH_COOKIE_OPTIONS["samesite"])
    return response


def _admin_auth() -> dict | None:
    data = _load_auth_cookie(ADMIN_AUTH_COOKIE, ADMIN_AUTH_SALT)
    if data and data.get("is_admin"):
        return data
    if session.get("is_admin"):
        return {
            "is_admin": True,
            "admin_user": session.get("admin_user", "admin"),
        }
    return None


def _user_auth() -> dict | None:
    """Login único dos usuários cadastrados (cookie ``totem_user_auth``)."""
    data = _load_auth_cookie(USER_AUTH_COOKIE, USER_AUTH_SALT)
    if not data:
        return None
    try:
        uid = int(data.get("user_id"))
    except (TypeError, ValueError):
        return None
    return {"user_id": uid} if uid > 0 else None


def _current_user() -> dict | None:
    """Usuário cadastrado logado e ativo, com tipo e permissões (cache por requisição).

    Vale o login único e, para sessões abertas antes dele, o cookie antigo do
    vendedor.
    """
    if hasattr(g, "_current_user"):
        return g._current_user
    uid = 0
    auth = _user_auth()
    if auth:
        uid = auth["user_id"]
    else:
        legacy = _load_auth_cookie(SELLER_AUTH_COOKIE, SELLER_AUTH_SALT)
        if legacy and legacy.get("is_seller"):
            try:
                uid = int(legacy.get("seller_id") or 0)
            except (TypeError, ValueError):
                uid = 0
        elif session.get("is_seller"):
            try:
                uid = int(session.get("seller_id") or 0)
            except (TypeError, ValueError):
                uid = 0
    user = None
    if uid > 0:
        row = get_seller(uid)
        if row is not None and row.get("active"):
            user = describe_user(row)
    g._current_user = user
    return user


#: Conta mestre do ``.env``: todas as permissões do painel, em todos os eventos.
def _master_account() -> dict:
    return {
        "id": None,
        "master": True,
        "name": ADMIN_USERNAME,
        "username": ADMIN_USERNAME,
        "role": "administrador",
        "role_label": "Administrador",
        "permission_list": list(PANEL_PERMISSIONS),
        "can_sell": False,
        "has_panel": True,
        "all_events": True,
        "panel_event_ids": [],
    }


def _panel_account() -> dict | None:
    """Quem está no painel administrativo: a conta mestre ou um usuário com
    alguma permissão do painel."""
    if hasattr(g, "_panel_account"):
        return g._panel_account
    account = None
    if _admin_auth():
        account = _master_account()
    else:
        user = _current_user()
        if user is not None and user.get("has_panel"):
            account = user
    g._panel_account = account
    return account


#: "Gerir transações" inclui ver a lista.
_PERMISSION_IMPLIES = {"transacoes_ver": ("transacoes_gerir",)}


def _can(permission: str | None, event_id: int | None = None) -> bool:
    """A conta do painel tem a permissão (e acesso ao evento, quando houver).

    ``permission`` = ``None``: área sem permissão própria — só quem tem todas as
    permissões do painel (Administrador).
    """
    account = _panel_account()
    if account is None:
        return False
    if account.get("master"):
        return True
    perms = set(account.get("permission_list") or [])
    if permission is None:
        ok = set(PANEL_PERMISSIONS) <= perms
    else:
        ok = permission in perms or any(p in perms for p in _PERMISSION_IMPLIES.get(permission, ()))
    if ok and event_id is not None:
        ok = user_can_access_event(account, int(event_id))
    return ok


def _seller_auth() -> dict | None:
    """Usuário logado que pode vender (painel do vendedor)."""
    user = _current_user()
    if user is None or not user.get("can_sell"):
        return None
    login = user.get("username") or user.get("email") or ""
    return {
        "is_seller": True,
        "seller_id": int(user["id"]),
        "seller_name": user.get("name") or "Vendedor",
        "seller_username": login,
        "seller_email": login,
    }


def _is_admin_logged_in() -> bool:
    return _panel_account() is not None


def _is_seller_logged_in() -> bool:
    return bool(_seller_auth())


def _totem_theme_scope() -> str | None:
    """Escopo de preferência visual: admin ou vendedor, por login."""
    path = (request.path or "").lower()
    if path.startswith("/admin"):
        if _is_admin_logged_in():
            return f"admin:{_current_admin_user()}"
        return "admin:_guest"
    if path.startswith("/vendedor"):
        auth = _seller_auth()
        if auth and auth.get("seller_id"):
            return f"seller:{int(auth['seller_id'])}"
        return "seller:_guest"
    return None


@app.context_processor
def _inject_panel_permissions():
    """``can(permissão, event_id)`` e ``panel_account`` para as telas (inclui os parciais da API)."""
    return {
        "can": _can,
        "event_home_url": _event_home_url,
        "panel_account": _panel_account(),
        "current_user": _current_user(),
    }


@app.context_processor
def _inject_totem_theme_scope():
    return {"totem_theme_scope": _totem_theme_scope()}


def _cart_scope() -> str:
    """Dono do carrinho no navegador (vendedor + evento ativo); ``""`` fora do painel.

    O cart.js descarta o carrinho salvo quando este valor muda, para que uma
    troca de vendedor ou de evento não herde os itens montados no anterior.
    """
    seller_id = (_seller_auth() or {}).get("seller_id")
    if not seller_id:
        return ""
    ev = get_active_event_for_seller(int(seller_id))
    return f"s{int(seller_id)}:e{ev['id'] if ev else 0}"


@app.context_processor
def _inject_cart_scope():
    return {"totem_cart_scope": _cart_scope}


def _cart_gift_rules() -> list:
    """Brindes ativos do evento do vendedor, para o carrinho mostrar a linha grátis.

    A venda recalcula o brinde no servidor; isto só antecipa o que o pedido ganha.
    """
    seller_id = (_seller_auth() or {}).get("seller_id")
    if not seller_id:
        return []
    ev = get_active_event_for_seller(int(seller_id))
    return active_gift_rules_for_cart(int(ev["id"])) if ev else []


@app.context_processor
def _inject_cart_gift_rules():
    return {"cart_gift_rules": _cart_gift_rules}


# ---------------------------------------------------------------------------
# Trilha de navegação (breadcrumb) — admin e vendedor
# ---------------------------------------------------------------------------

# Allowlist: só as páginas abaixo entram na trilha. O que não está aqui
# (APIs, downloads, login/logout, nota pública, redirecionamentos) é ignorado.
_TRAIL_LABELS = {
    "admin_events": "Eventos",
    "admin_products": "Produtos",
    "admin_sellers": "Usuários",
    "admin_financeiro": "Financeiro",
    "admin_event_stock": "Estoque",
    "admin_event_transactions": "Transações",
    "admin_event_movements": "Movimentações",
    "admin_event_promotions": "Promoções",
    "admin_event_customers": "Clientes",
    "seller_sale": "Catálogo",
    "seller_dashboard": "Minhas vendas",
    "seller_stock": "Estoque",
    "seller_restore_pending_checkout": "Retomar pedido",
}


def _trail_url(endpoint: str, **kwargs) -> str:
    """``url_for`` tolerante: ``""`` quando o endpoint não existe no mapa de rotas."""
    try:
        return url_for(endpoint, **kwargs)
    except BuildError:
        return ""


def _trail_entity_name(kind: str, entity_id) -> str:
    """Nome legível de evento/produto/vendedor/promoção (``""`` se não achar).

    Produtos usam a chave ``nome`` (formato cliente do catálogo); eventos,
    vendedores e promoções usam ``name`` — por isso a leitura tenta as duas.
    """
    try:
        row_id = int(entity_id)
    except (TypeError, ValueError):
        return ""
    try:
        if kind == "product":
            row = get_product(row_id)
        elif kind == "seller":
            row = get_seller(row_id)
        elif kind == "promotion":
            row = get_promotion(row_id)
        else:
            row = get_event(row_id)
    except Exception:
        return ""
    if not row:
        return ""
    return str(row.get("name") or row.get("nome") or row.get("titulo") or "").strip()


def _trail_customer_name(kind, ident) -> str:
    """Nome do cliente para a trilha (``""`` quando a identidade não resolve)."""
    try:
        return get_customer_display_name(str(kind or ""), ident)
    except Exception:
        return ""


def _trail_event_ancestors(event_id: int) -> list:
    """Ancestrais das páginas de evento: lista de eventos + dashboard do evento."""
    if not event_id:
        return []
    name = _trail_entity_name("event", event_id)
    if not name:
        return []
    # Só o que a conta pode abrir: sem "Eventos" para quem não gere eventos e,
    # sem o dashboard, o evento leva à primeira área liberada dele.
    out = []
    if _can("eventos"):
        out.append({"u": _trail_url("admin_events"), "l": "Eventos"})
    out.append({"u": _event_home_url(int(event_id)) or _trail_url("admin_event_detail", event_id=int(event_id)), "l": name})
    return out


def _trail_entry_for(endpoint: str, view_args: dict | None) -> dict | None:
    """Rótulo + ancestrais da página atual. ``None`` = página fora da trilha."""
    args = view_args or {}
    label = _TRAIL_LABELS.get(endpoint)
    ancestors: list = []
    event_id = _parse_int(args.get("event_id"), 0)

    if endpoint == "admin_event_detail":
        ancestors = _trail_event_ancestors(event_id)
        label = ancestors[-1]["l"] if ancestors else (label or "Evento")
        ancestors = ancestors[:-1]
    elif endpoint in (
        "admin_event_stock",
        "admin_event_movements",
        "admin_event_promotions",
        "admin_event_customers",
    ):
        ancestors = _trail_event_ancestors(event_id)
    elif endpoint == "admin_event_transactions":
        ancestors = _trail_event_ancestors(event_id)
        # Chegando pelo pedido na página do Cliente (``admin-customer__sku``):
        # a trilha mostra o Cliente de origem em vez do contexto genérico do evento.
        cli_kind = (request.args.get("cliente_kind") or "").strip()
        cli_ident = (request.args.get("cliente_ident") or "").strip()
        if cli_kind and cli_ident:
            customers_url = (
                _trail_url("admin_event_customers", event_id=event_id) if event_id else ""
            )
            customer_name = _trail_customer_name(cli_kind, cli_ident)
            customer_url = (
                _trail_url(
                    "admin_event_customer_detail",
                    event_id=event_id,
                    kind=cli_kind,
                    ident=cli_ident,
                )
                if event_id
                else ""
            )
            if customers_url and customer_name and customer_url:
                ancestors = ancestors + [
                    {"u": customers_url, "l": "Clientes"},
                    {"u": customer_url, "l": customer_name},
                ]
    elif endpoint == "admin_event_stock_product":
        ancestors = _trail_event_ancestors(event_id)
        stock_url = _trail_url("admin_event_stock", event_id=event_id) if event_id else ""
        if stock_url:
            ancestors.append({"u": stock_url, "l": "Estoque"})
        label = _trail_entity_name("product", args.get("product_id")) or "Produto"
    elif endpoint in ("admin_event_promotion_detail", "admin_event_promotion_new"):
        ancestors = _trail_event_ancestors(event_id)
        promos_url = _trail_url("admin_event_promotions", event_id=event_id) if event_id else ""
        if promos_url:
            ancestors.append({"u": promos_url, "l": "Promoções"})
        if endpoint == "admin_event_promotion_new":
            label = "Nova promoção"
        else:
            label = _trail_entity_name("promotion", args.get("promo_id")) or "Promoção"
    elif endpoint == "admin_product_detail":
        ancestors = [{"u": _trail_url("admin_products"), "l": "Produtos"}]
        label = _trail_entity_name("product", args.get("product_id")) or "Produto"
    elif endpoint == "admin_seller_detail":
        ancestors = [{"u": _trail_url("admin_sellers"), "l": "Usuários"}]
        label = _trail_entity_name("seller", args.get("seller_id")) or "Usuário"
    elif endpoint == "admin_event_customer_detail":
        ancestors = _trail_event_ancestors(event_id)
        customers_url = (
            _trail_url("admin_event_customers", event_id=event_id) if event_id else ""
        )
        if customers_url:
            ancestors.append({"u": customers_url, "l": "Clientes"})
        label = _trail_customer_name(args.get("kind"), args.get("ident")) or "Cliente"
    elif endpoint == "seller_stock_product":
        ancestors = [{"u": _trail_url("seller_stock"), "l": "Estoque"}]
        label = _trail_entity_name("product", args.get("product_id")) or "Produto"

    if not label:
        return None
    return {"label": label, "ancestors": ancestors}


def _trail_scope() -> str:
    """Escopo da trilha conforme a área autenticada da requisição (``""`` = fora)."""
    path = (request.path or "").lower()
    try:
        if path.startswith("/admin") and _is_admin_logged_in():
            return "admin"
        if path.startswith("/vendedor") and _is_seller_logged_in():
            return "seller"
    except Exception:
        return ""
    return ""


def _trail_current_url() -> str:
    """URL interna da página atual (preserva filtros/paginação para o voltar)."""
    query = request.query_string.decode("utf-8", "replace") if request.query_string else ""
    return f"{request.path}?{query}" if query else request.path


def _trail_load() -> list:
    """Trilha persistida no cookie dedicado (lista vazia quando ilegível)."""
    try:
        return breadcrumbs.normalize_payload(
            _load_auth_cookie(breadcrumbs.COOKIE_NAME, breadcrumbs.COOKIE_SALT)
        )
    except Exception:
        return []


@app.before_request
def _totem_trail_record():
    """Registra a página atual na trilha antes do template renderizar.

    Só páginas da allowlist entram (``_TRAIL_LABELS``), apenas em GET autenticado
    e nunca em respostas JSON/API — assim a trilha não polui com polling,
    downloads e afins.
    """
    g.totem_trail = _trail_load()
    g.totem_trail_dirty = False
    try:
        scope = _trail_scope()
        if not scope or request.method != "GET" or not request.endpoint:
            return
        if request.path.startswith("/api/"):
            return
        if _wants_json_response():
            return
        entry = _trail_entry_for(request.endpoint, request.view_args)
        if entry is None:
            return
        g.totem_trail = breadcrumbs.push(
            g.totem_trail,
            scope=scope,
            url=_trail_current_url(),
            label=entry["label"],
            ancestors=entry["ancestors"],
        )
        g.totem_trail_dirty = True
    except Exception:
        app.logger.warning("Falha ao registrar a trilha de navegação", exc_info=True)


def _trail_cookie_token(items) -> str:
    """Serializa a trilha no mesmo formato assinado usado pelos cookies de login."""
    return _auth_serializer(breadcrumbs.COOKIE_SALT).dumps(breadcrumbs.payload(items))


@app.after_request
def _totem_trail_persist(response):
    """Grava a trilha no cookie dedicado (ou remove o cookie quando ela fica vazia).

    Se o cookie não couber no orçamento de bytes, a trilha é reduzida em etapas
    e, em último caso, descartada — nunca quebra a resposta.
    """
    if not getattr(g, "totem_trail_dirty", False):
        return response
    try:
        items = breadcrumbs.normalize_items(getattr(g, "totem_trail", None))
        for candidate in (items, items[-4:], items[-2:]):
            if not candidate:
                break
            token = _trail_cookie_token(candidate)
            if len(token) <= breadcrumbs.MAX_COOKIE_CHARS:
                response.set_cookie(
                    breadcrumbs.COOKIE_NAME,
                    token,
                    max_age=breadcrumbs.COOKIE_MAX_AGE,
                    path="/",
                    **_auth_cookie_kwargs(),
                )
                return response
        response.delete_cookie(
            breadcrumbs.COOKIE_NAME,
            samesite=AUTH_COOKIE_OPTIONS["samesite"],
        )
    except Exception:
        app.logger.warning("Falha ao gravar a trilha de navegação", exc_info=True)
    return response


def _trail_view() -> dict:
    """Trilha pronta para o template: itens do escopo atual + URL de voltar."""
    scope = _trail_scope()
    if not scope:
        return {"totem_trail": [], "totem_trail_back": ""}
    stored = getattr(g, "totem_trail", None)
    current_path = request.path
    view = [
        {"url": item["u"], "label": item["l"], "is_current": item["p"] == current_path}
        for item in breadcrumbs.for_scope(stored, scope)
    ]
    if view and not any(item["is_current"] for item in view):
        view.append(
            {
                "url": current_path,
                "label": _TRAIL_LABELS.get(request.endpoint or "") or "Página atual",
                "is_current": True,
            }
        )
    return {
        "totem_trail": view,
        "totem_trail_back": breadcrumbs.previous_url(stored, scope),
    }


@app.context_processor
def _inject_totem_trail():
    """Expõe a trilha de navegação (breadcrumb) para todas as páginas dos painéis."""
    try:
        return _trail_view()
    except Exception:
        return {"totem_trail": [], "totem_trail_back": ""}


def _trail_reset_scope(scope: str) -> None:
    """Esvazia a trilha de um escopo (login/logout) sem afetar o outro painel."""
    try:
        g.totem_trail = breadcrumbs.drop_scope(getattr(g, "totem_trail", None), scope)
        g.totem_trail_dirty = True
    except Exception:
        app.logger.warning("Falha ao limpar a trilha de navegação", exc_info=True)




def _clear_admin_session() -> None:
    """Remove apenas credenciais do painel admin (preserva vendedor na mesma sessão)."""
    for key in ("is_admin", "admin_user"):
        session.pop(key, None)


def _clear_all_auth(response):
    """Sai de tudo: conta mestre, usuário e o cookie antigo do vendedor."""
    _clear_admin_session()
    _clear_seller_session()
    for name in (ADMIN_AUTH_COOKIE, SELLER_AUTH_COOKIE, USER_AUTH_COOKIE):
        _delete_auth_cookie(response, name)
    return response


def _clear_seller_session() -> None:
    """Remove apenas credenciais do painel do vendedor (preserva admin na mesma sessão)."""
    for key in ("is_seller", "seller_id", "seller_name", "seller_email", "seller_username"):
        session.pop(key, None)


def _current_admin_user() -> str:
    """Login de quem está no painel (vai no ``created_by`` das movimentações)."""
    account = _panel_account() or {}
    return account.get("username") or "admin"


def _current_seller_id() -> int:
    auth = _seller_auth() or {}
    return int(auth["seller_id"])


def _current_seller_display_name() -> str:
    auth = _seller_auth() or {}
    name = str(auth.get("seller_name") or "").strip()
    if name:
        return name
    try:
        seller = get_seller(_current_seller_id())
    except (KeyError, TypeError, ValueError):
        seller = None
    if seller:
        return str(seller.get("name") or "Vendedor").strip() or "Vendedor"
    return "Vendedor"


EVENT_OPS_CLOSED_MSG = (
    "As operações deste evento foram encerradas. "
    "Estoque, vendas e transações ficam disponíveis apenas para consulta."
)


def _response_event_ops_closed(event_id: int | None = None, *, seller: bool = False):
    """403 JSON ou flash+redirect quando o evento está só para consulta."""
    wants_json = False
    if has_request_context():
        wants_json = (
            _wants_json_response()
            or request.path.startswith("/api/")
            or request.path.startswith("/vendedor/api/")
        )
        seller = seller or request.path.startswith("/vendedor/")
    if wants_json:
        return jsonify({"error": EVENT_OPS_CLOSED_MSG}), 403
    flash(EVENT_OPS_CLOSED_MSG, "error")
    if seller:
        return redirect(url_for("seller_sale"))
    if event_id:
        return _redirect_back(url_for("admin_event_detail", event_id=event_id))
    return _redirect_back( url_for("admin_events"))


def _reject_if_event_ops_closed(event):
    """Retorna resposta 403/redirect se o evento não estiver com operações abertas."""
    if event is not None and not event_ops_open(event):
        try:
            eid = int(event["id"])
        except (KeyError, TypeError, ValueError):
            eid = None
        return _response_event_ops_closed(eid)
    return None


def require_event_ops_open(view):
    """Bloqueia POST de evento com operações encerradas (consulta apenas)."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        event_id = kwargs.get("event_id")
        if event_id is not None:
            blocked = _reject_if_event_ops_closed(get_event(int(event_id)))
            if blocked is not None:
                return blocked
        return view(*args, **kwargs)

    return wrapped


def _reject_if_tx_event_ops_closed(tx_id: int):
    """Bloqueia mutação de transação cujo evento está encerrado."""
    tx = get_transaction(int(tx_id))
    if not tx:
        return None
    eid = tx.get("event_id")
    if not eid:
        return None
    return _reject_if_event_ops_closed(get_event(int(eid)))


#: Permissão de cada área do painel (``_endpoint_permission``). O que não está
#: aqui nem nos prefixos fica só para Administrador (todas as permissões).
_PERMISSION_BY_ENDPOINT = {
    "admin_event_transactions": "transacoes_ver",
    "admin_event_transaction_pdf": "transacoes_ver",
    "admin_api_event_transactions": "transacoes_ver",
    "admin_event_sales_export_xlsx": "transacoes_ver",
    "admin_event_sales_export_csv": "transacoes_ver",
    "admin_event_transaction_refund": "transacoes_gerir",
    "admin_event_transaction_note": "transacoes_gerir",
    "admin_event_cancel_pending_transaction": "transacoes_gerir",
    "admin_event_confirm_item_delivery": "transacoes_gerir",
    "admin_event_confirm_items_delivery": "transacoes_gerir",
    "admin_event_confirm_transaction_handover": "transacoes_gerir",
    "admin_transaction_replace_item": "transacoes_gerir",
    "admin_event_add_product": "estoque",
    "admin_event_remove_product": "estoque",
    "admin_event_import_xls": "estoque",
    "admin_event_cache_product_images": "estoque",
    "admin_event_detail": "dashboard",
    "admin_event_goals": "dashboard",
    "admin_events": "eventos",
    "admin_event_create": "eventos",
    "admin_event_edit": "eventos",
    "admin_event_archive": "eventos",
    "admin_event_restore": "eventos",
    "admin_event_delete": "eventos",
    "admin_event_close_operations": "eventos",
    "admin_event_reopen_operations": "eventos",
    "admin_sellers": "usuarios",
    "admin_products_cache_images": "produtos",
    "admin_products_bulk_add_to_event": "produtos",
    "admin_sync_catalog_erp": "erp",
    "admin_reset_system": "reiniciar",
    "admin_product_create": "produtos",
}
_PERMISSION_BY_PREFIX = (
    ("admin_event_promotion", "promocoes"),
    ("admin_event_gift", "promocoes"),
    ("admin_event_stock", "estoque"),
    ("admin_api_event_stock", "estoque"),
    ("admin_event_movements", "estoque"),
    ("admin_api_event_movements", "estoque"),
    ("admin_event_customer", "clientes"),
    ("admin_erp", "erp"),
    ("admin_financeiro", "financeiro"),
    ("admin_seller", "usuarios"),
    ("admin_product", "produtos"),
    ("admin_api_product", "produtos"),
    ("admin_api_stock", "produtos"),
    ("admin_stock", "produtos"),
)


def _endpoint_permission(endpoint: str | None) -> str | None:
    e = endpoint or ""
    if e in _PERMISSION_BY_ENDPOINT:
        return _PERMISSION_BY_ENDPOINT[e]
    for prefix, perm in _PERMISSION_BY_PREFIX:
        if e.startswith(prefix):
            return perm
    return None


def _route_event_id(kwargs: dict) -> int | None:
    """Evento da rota: ``event_id`` na URL ou o da transação (troca de item)."""
    if kwargs.get("event_id") is not None:
        return int(kwargs["event_id"])
    if request.endpoint == "admin_transaction_replace_item" and kwargs.get("tx_id"):
        tx = get_transaction(int(kwargs["tx_id"]))
        if tx and tx.get("event_id"):
            return int(tx["event_id"])
    return None


def admin_required(view):
    """Painel administrativo: conta com a permissão da área (e acesso ao evento)."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        wants_json = request.path.startswith("/admin/api/") or _wants_json_response()
        account = _panel_account()
        if account is None:
            if wants_json:
                return jsonify({"error": "Sessão expirada. Entre novamente."}), 401
            if _seller_auth():  # vendedor sem permissões do painel
                flash("Seu usuário não tem acesso ao painel administrativo.", "error")
                return redirect(_seller_home_url())
            return redirect(url_for("login", next=request.path))
        permission = _endpoint_permission(request.endpoint)
        if not _can(permission, _route_event_id(kwargs)):
            if wants_json:
                return jsonify({"error": "Seu usuário não tem permissão para esta área."}), 403
            flash("Seu usuário não tem permissão para esta área.", "error")
            home = _admin_home_url()
            return redirect(home if home != request.path else url_for("panel_no_access"))
        return view(*args, **kwargs)

    return wrapped


def seller_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        auth = _seller_auth()
        if not auth:
            if _panel_account() is not None and not request.path.startswith("/vendedor/api/"):
                flash("Seu usuário não tem a permissão de vender.", "error")
                return redirect(_admin_home_url())
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


# ---------------------------------------------------------------------------
# Rotas do cliente
# ---------------------------------------------------------------------------

@app.route("/")
def welcome():
    return render_template("welcome.html")


@app.route("/catalogo")
def catalog():
    return redirect(url_for("welcome"))


@app.route("/pagamento")
def payment():
    return redirect(url_for("welcome"))


@app.route("/pagamento/aguardando")
def payment_waiting():
    return redirect(url_for("welcome"))


# ---------------------------------------------------------------------------
# API pública (consumida pelo front do totem)
# ---------------------------------------------------------------------------

def _parse_seller_total(payload: dict, client: dict):
    """Total negociado pelo vendedor (desconto manual). None = usar total calculado."""
    raw = payload.get("seller_total")
    if raw is None:
        raw = client.get("seller_total")
    if raw is None or raw == "":
        return None
    try:
        return round(float(raw), 2)
    except (TypeError, ValueError):
        raise ValueError("Valor total informado é inválido.") from None


@app.route("/api/produtos")
def api_products():
    category = request.args.get("categoria", "").strip()
    query = request.args.get("q", "").strip().lower()
    products = list_products_for_client(category=category or None, query=query or None)
    return jsonify(products)


@app.route("/api/transacoes", methods=["POST"])
def api_create_transaction():
    """Cria uma transação pendente (estoque ainda não baixado) e retorna o ``id``.

    O estoque só é decrescido após o vendedor digitar e confirmar o AUT via
    ``PATCH /api/transacoes/<id>/aut``.
    """
    payload = request.get_json(silent=True) or {}
    items = payload.get("items") or payload.get("itens") or []
    client = payload.get("client") or {}
    payment_method = _normalize_payment_method_for_db(
        payload.get("payment_method") or client.get("payment_method"),
    )

    # CRO: UF e número informados no checkout (sem API externa)
    cro_uf = (client.get("cro_uf") or "").strip().upper()
    cro_numero = (client.get("cro_numero") or "").strip()

    try:
        inst_raw = payload.get("installments")
        if inst_raw is None:
            inst_raw = client.get("installments")
        card_installments = None
        if inst_raw not in (None, ""):
            try:
                card_installments = int(inst_raw)
            except (TypeError, ValueError):
                raise ValueError("Número de parcelas inválido.") from None

        auth = _seller_auth()
        if not auth:
            raise ValueError(
                "É necessário estar logado como vendedor para registrar a venda."
            )
        row = get_seller(int(auth["seller_id"]))
        if row is None or not row.get("active"):
            raise ValueError("Sessão de vendedor inválida ou inativa.")
        seller = {"id": int(row["id"]), "name": row["name"]}

        # Busca o evento ativo do vendedor (se houver)
        active_event = get_active_event_for_seller(int(seller["id"]))
        event_id = int(active_event["id"]) if active_event else None
        if active_event is not None and not event_ops_open(active_event):
            return jsonify({"error": EVENT_OPS_CLOSED_MSG}), 403

        result = create_transaction(
            items,
            created_by=f"vendedor:{seller['name']}",
            seller_id=int(seller["id"]),
            seller_name=seller["name"],
            event_id=event_id,
            client_name=client.get("name"),
            client_cpf=client.get("cpf"),
            client_email=client.get("email"),
            client_phone=client.get("phone"),
            client_zipcode=client.get("zipcode"),
            client_address=client.get("address"),
            client_number=client.get("number"),
            client_complement=client.get("complement"),
            client_city=client.get("city"),
            client_state=client.get("state"),
            client_neighborhood=client.get("neighborhood"),
            payment_method=payment_method,
            card_installments=card_installments,
            client_cro_uf=cro_uf or None,
            client_cro_numero=cro_numero or None,
            seller_total=_parse_seller_total(payload, client),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        app.logger.exception("Falha ao criar transação pendente")
        return jsonify({"error": "Não foi possível registrar a transação."}), 500

    # Remove chaves internas (_normalized, _demand, _event_id, _created_by) da resposta.
    public = {k: v for k, v in result.items() if not k.startswith("_")}
    return jsonify(public), 201


@app.route("/api/transacoes/<int:tx_id>", methods=["PATCH"])
def api_update_pending_transaction(tx_id: int):
    """Atualiza pedido pendente (ex.: retomada após alterar carrinho ou pagamento)."""
    payload = request.get_json(silent=True) or {}
    items = payload.get("items") or payload.get("itens") or []
    client = payload.get("client") or {}
    payment_method = _normalize_payment_method_for_db(
        payload.get("payment_method") or client.get("payment_method"),
    )

    cro_uf = (client.get("cro_uf") or "").strip().upper()
    cro_numero = (client.get("cro_numero") or "").strip()

    try:
        inst_raw = payload.get("installments")
        if inst_raw is None:
            inst_raw = client.get("installments")
        card_installments = None
        if inst_raw not in (None, ""):
            try:
                card_installments = int(inst_raw)
            except (TypeError, ValueError):
                raise ValueError("Número de parcelas inválido.") from None

        auth = _seller_auth()
        if not auth:
            raise ValueError(
                "É necessário estar logado como vendedor para atualizar o pedido."
            )
        row = get_seller(int(auth["seller_id"]))
        if row is None or not row.get("active"):
            raise ValueError("Sessão de vendedor inválida ou inativa.")

        seller_ev = get_active_event_for_seller(int(row["id"]))
        if seller_ev is not None and not event_ops_open(seller_ev):
            return jsonify({"error": EVENT_OPS_CLOSED_MSG}), 403

        result = update_pending_transaction(
            int(tx_id),
            seller_id=int(row["id"]),
            items=items,
            client_name=client.get("name"),
            client_cpf=client.get("cpf"),
            client_email=client.get("email"),
            client_phone=client.get("phone"),
            client_zipcode=client.get("zipcode"),
            client_address=client.get("address"),
            client_number=client.get("number"),
            client_complement=client.get("complement"),
            client_city=client.get("city"),
            client_state=client.get("state"),
            client_neighborhood=client.get("neighborhood"),
            payment_method=payment_method,
            card_installments=card_installments,
            client_cro_uf=cro_uf or None,
            client_cro_numero=cro_numero or None,
            seller_total=_parse_seller_total(payload, client),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        app.logger.exception("Falha ao atualizar transação pendente")
        return jsonify({"error": "Não foi possível atualizar o pedido."}), 500

    return jsonify(result), 200


@app.route("/api/transacoes/<int:tx_id>/aut", methods=["PATCH"])
def api_confirm_transaction_aut(tx_id: int):
    """Salva o AUT, confirma a transação e baixa o estoque.

    Body JSON: ``{"aut": "123456"}``
    """
    payload = request.get_json(silent=True) or {}
    aut = (payload.get("aut") or "").strip()

    try:
        auth = _seller_auth()
        if not auth:
            raise ValueError(
                "É necessário estar logado como vendedor para confirmar a venda."
            )
        row = get_seller(int(auth["seller_id"]))
        if row is None or not row.get("active"):
            raise ValueError("Sessão de vendedor inválida ou inativa.")
        seller_name = row["name"]

        result = confirm_transaction_with_aut(
            tx_id, aut, created_by=f"vendedor:{seller_name}"
        )
        erp_sync.notify_worker()
        order_number = (result.get("order_number") or "").strip()
        if order_number:
            result["receipt_token"] = sign_receipt_token(
                order_number, app.secret_key
            )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        app.logger.exception("Falha ao confirmar transação com AUT")
        return jsonify({"error": "Não foi possível confirmar a transação."}), 500

    return jsonify(result), 200


# ---------------------------------------------------------------------------
# Painel administrativo — autenticação
# ---------------------------------------------------------------------------

#: Áreas de um evento, na ordem em que a conta é levada para elas.
_EVENT_AREAS = (
    ("dashboard", "admin_event_detail"),
    ("transacoes_ver", "admin_event_transactions"),
    ("promocoes", "admin_event_promotions"),
    ("estoque", "admin_event_stock"),
    ("clientes", "admin_event_customers"),
)
_GLOBAL_AREAS = (
    ("eventos", "admin_events"),
    ("produtos", "admin_products"),
    ("usuarios", "admin_sellers"),
    ("financeiro", "admin_financeiro"),
    ("erp", "admin_erp"),
)


def _event_home_url(event_id: int) -> str | None:
    """Primeira área do evento que a conta pode abrir (``None`` = nenhuma)."""
    for perm, endpoint in _EVENT_AREAS:
        if _can(perm, event_id):
            return url_for(endpoint, event_id=int(event_id))
    return None


def _panel_events() -> list:
    """Eventos do seletor do painel para a conta logada."""
    account = _panel_account()
    if account is None:
        return []
    if account.get("all_events"):
        return list_events(include_archived=True)
    return list_panel_events_for_user(account)


def _admin_home_url() -> str:
    """Primeira página que a conta do painel pode abrir."""
    if _panel_account() is None:
        return url_for("login")
    if _can("eventos"):
        return url_for("admin_events")
    for ev in sorted(_panel_events(), key=lambda e: (not e.get("active"), -int(e["id"]))):
        url = _event_home_url(int(ev["id"]))
        if url:
            return url
    for perm, endpoint in _GLOBAL_AREAS:
        if _can(perm):
            return url_for(endpoint)
    return url_for("panel_no_access")


def _user_home_url(user: dict | None) -> str:
    """Para onde o usuário vai depois de entrar."""
    if user is None:
        return url_for("login")
    if user.get("can_sell") and user.get("role") != "administrador":
        return _seller_home_url()
    if user.get("has_panel"):
        return _admin_home_url()
    if user.get("can_sell"):
        return _seller_home_url()
    return url_for("panel_no_access")


@app.route("/admin")
def admin_index():
    if not _is_admin_logged_in():
        return redirect(url_for("login"))
    return redirect(_admin_home_url())


@app.route("/entrar", methods=["GET", "POST"])
def login():
    """Login único: conta mestre do ``.env`` ou usuário cadastrado em Usuários."""
    if request.method == "GET":
        if request.args.get("trocar"):
            # "Fazer login com usuário diferente" (tela inicial): encerra a
            # sessão atual e mostra o formulário em vez de levar ao painel.
            account = _panel_account() or _current_user() or {}
            _trail_reset_scope("admin")
            _trail_reset_scope("seller")
            response = Response(render_template(
                "login.html", error=None, username="",
                switched_from=account.get("name") or "",
            ))
            return _clear_all_auth(response)
        if _admin_auth():
            return redirect(_admin_home_url())
        user = _current_user()
        if user is not None and (user.get("has_panel") or user.get("can_sell")):
            return redirect(_user_home_url(user))

    error = None
    username = ""
    if request.method == "POST":
        username = (request.form.get("username") or request.form.get("email") or "").strip()
        password = request.form.get("password") or ""
        user_ok = hmac.compare_digest(username.encode(), ADMIN_USERNAME.encode())
        pass_ok = hmac.compare_digest(password.encode(), ADMIN_PASSWORD.encode())
        if user_ok and pass_ok:
            response = redirect(_safe_internal_url(request.args.get("next"), url_for("admin_index")))
            _clear_all_auth(response)
            _trail_reset_scope("admin")  # cada login começa com a trilha limpa
            return _set_auth_cookie(
                response, ADMIN_AUTH_COOKIE, ADMIN_AUTH_SALT,
                {"is_admin": True, "admin_user": username},
            )
        row = get_seller_by_username(username)
        if row and row.get("active") and check_password_hash(row["password_hash"], password):
            user = describe_user(row)
            if not (user.get("has_panel") or user.get("can_sell")):
                error = "Seu usuário ainda não tem nenhuma permissão. Fale com o administrador."
            else:
                update_seller_last_login(int(row["id"]))
                g._current_user = user  # para a página inicial já valer este login
                g.pop("_panel_account", None)
                home = _user_home_url(user)
                response = redirect(_safe_internal_url(request.args.get("next"), home))
                _clear_all_auth(response)
                _trail_reset_scope("admin")
                _trail_reset_scope("seller")
                return _set_auth_cookie(
                    response, USER_AUTH_COOKIE, USER_AUTH_SALT, {"user_id": int(row["id"])},
                )
        else:
            error = "Usuário ou senha inválidos."
    return render_template("login.html", error=error, username=username)


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    """Endereço antigo do login do admin: vale o login único."""
    return redirect(url_for("login", next=request.args.get("next") or None), code=307 if request.method == "POST" else 302)


@app.route("/sair", methods=["POST", "GET"])
def logout():
    _trail_reset_scope("admin")
    _trail_reset_scope("seller")
    return _clear_all_auth(redirect(url_for("login")))


@app.route("/admin/logout", methods=["POST", "GET"])
def admin_logout():
    return logout()


@app.route("/painel/sem-acesso")
def panel_no_access():
    """Usuário logado sem nenhuma área liberada (ou sem eventos)."""
    user = _current_user()
    if user is None and not _admin_auth():
        return redirect(url_for("login"))
    return render_template("panel_no_access.html", user=user)


@app.route("/marketing")
@app.route("/marketing/login")
def marketing_legacy_login():
    """Endereços do painel de Marketing antigo: hoje o login é único."""
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Painel do vendedor — autenticação e leitura
# ---------------------------------------------------------------------------

@app.route("/vendedor")
def seller_index():
    if not _is_seller_logged_in():
        return redirect(url_for("login"))
    return redirect(_seller_home_url())


@app.route("/vendedor/login", methods=["GET", "POST"])
def seller_login():
    """Endereço antigo do login do vendedor: vale o login único."""
    return redirect(url_for("login", next=request.args.get("next") or None), code=307 if request.method == "POST" else 302)


@app.route("/vendedor/logout", methods=["POST", "GET"])
def seller_logout():
    return logout()


def _seller_home_url() -> str:
    """Página inicial do painel do vendedor (catálogo / venda)."""
    url = _url_if_registered("seller_sale", fallback="/vendedor/venda")
    return (url or "").strip() or "/vendedor/venda"


def _seller_shell_context(**extra):
    auth = _seller_auth() or {}
    seller_id_raw = auth.get("seller_id")
    seller_event = (
        get_active_event_for_seller(int(seller_id_raw))
        if seller_id_raw
        else None
    )
    ctx = {
        "seller_name": auth.get("seller_name", "Vendedor"),
        "seller_username": auth.get("seller_username") or auth.get("seller_email", ""),
        "now": datetime.now(),
        "seller_event": seller_event,
        "seller_pending_cancel_url": _seller_pending_cancel_url,
        "seller_home_url": _seller_home_url(),
    }
    ctx.update(extra)
    venda_url = _seller_home_url()
    ctx["seller_venda_url"] = venda_url
    return ctx


def _get_seller_event():
    """Retorna o evento ativo do vendedor logado, ou None."""
    try:
        seller_id = _current_seller_id()
    except (KeyError, TypeError):
        return None
    return get_active_event_for_seller(seller_id)


def _reject_if_seller_event_ops_closed():
    """Bloqueia ações de venda se o evento do vendedor estiver encerrado."""
    ev = _get_seller_event()
    if ev is None or event_ops_open(ev):
        return None
    return _response_event_ops_closed(int(ev["id"]), seller=True)


def _seller_pending_sales_count(seller_id: int, seller_ev) -> int:
    """Conta vendas pendentes do vendedor (escopo do evento quando houver)."""
    if seller_ev:
        return count_transactions_for_event(
            int(seller_ev["id"]),
            seller_id=seller_id,
            status="pendente",
        )
    return count_transactions_for_seller(seller_id, status="pendente")


def _seller_totem_flow() -> dict:
    """URLs do fluxo de venda no painel (consumido pelo JS)."""
    return {
        "payment": _url_if_registered(
            "seller_payment", fallback="/vendedor/pagamento"
        ),
        "paymentWaiting": _url_if_registered(
            "seller_payment_waiting", fallback="/vendedor/pagamento/aguardando"
        ),
        "catalog": _seller_home_url(),
        "home": _seller_home_url(),
        "checkoutUnlock": _url_if_registered(
            "seller_checkout_unlock",
            fallback="/vendedor/api/checkout/desbloquear",
        ),
        "checkoutHold": _url_if_registered(
            "seller_api_checkout_hold",
            fallback="/vendedor/api/checkout/reserva",
        ),
        "checkoutHoldRelease": _url_if_registered(
            "seller_api_checkout_hold_release",
            fallback="/vendedor/api/checkout/liberar",
        ),
        "checkoutHoldConflicts": _url_if_registered(
            "seller_api_checkout_hold_conflicts",
            fallback="/vendedor/api/checkout/conflitos",
        ),
    }


#: Endereço da loja (fictício), preenchido pelo botão "Loja" do checkout. Os
#: dados ficam aqui (e não numa consulta de CEP) para o botão funcionar sem
#: internet no estande.
STORE_ADDRESS = {
    "zipcode": "01310-100",
    "address": "Avenida Paulista",
    "number": "1000",
    "complement": "Estande Go Sell",
    "neighborhood": "Bela Vista",
    "city": "São Paulo",
    "state": "SP",
}


def _seller_payment_page_context() -> dict:
    flow = _seller_totem_flow()
    return {
        "store_address": STORE_ADDRESS,
        "totem_flow": flow,
        "payment_catalog_url": _url_if_registered(
            "seller_sale", fallback="/vendedor/venda"),
        "payment_home_url": _seller_home_url(),
        "client_lookup_url": _url_if_registered(
            "seller_api_customer_lookup", fallback="/vendedor/api/clientes/consulta"),
        "resume_pending_aut": None,
        "seller_backorder": True,
    }


def _attach_pending_delivery_units(
    products: list,
    event_id: int,
    *,
    seller_id: int | None = None,
) -> None:
    """Anexa ``pending_delivery_units`` (retirada pendente) a cada produto do evento."""
    if not products:
        return
    pending_map = pending_delivery_units_by_product_for_event(
        event_id,
        product_ids=[int(p["id"]) for p in products],
        seller_id=seller_id,
    )
    for p in products:
        p["pending_delivery_units"] = int(pending_map.get(int(p["id"]), 0))


@app.route("/vendedor/venda", endpoint="seller_sale")
@seller_required
def seller_sale():
    seller_ev = _get_seller_event()
    if seller_ev:
        products = list_event_products_for_client(seller_ev["id"])
        # Enriquece cada produto com dados de promoção ativa do evento.
        promos = get_active_promotions_for_event(seller_ev["id"])
        promo_map = build_promo_display_map(promos)
        products = [enrich_product_with_promo(p, promo_map) for p in products]
        _attach_pending_delivery_units(
            products, seller_ev["id"], seller_id=_current_seller_id(),
        )
        summarize_catalog_option_groups(products)
        # No modo evento, estoque + preços + promoções vêm do mesmo polling (15s).
        catalog_stock_api_url = ""
        catalog_promo_refresh_api_url = url_for(
            "seller_api_event_catalog_promos_refresh",
        )
    else:
        products = list_products_for_client()
        catalog_stock_api_url = _url_if_registered(
            "seller_api_catalog_stock",
            fallback="/vendedor/api/catalogo/estoque",
        )
        catalog_promo_refresh_api_url = ""
    return render_template(
        "seller/catalog.html",
        products=products,
        totem_flow=_seller_totem_flow(),
        catalog_stock_api_url=catalog_stock_api_url,
        catalog_promo_refresh_api_url=catalog_promo_refresh_api_url,
        catalog_readonly=bool(seller_ev) and not event_ops_open(seller_ev),
        # Catálogo é tela cheia (sem coluna de conteúdo): a trilha fica oculta,
        # mas a página continua sendo registrada para servir de «voltar».
        totem_trail_enabled=False,
        **_seller_shell_context(active_section="venda"),
    )


@app.route(
    "/vendedor/api/checkout/desbloquear",
    methods=["POST"],
    endpoint="seller_checkout_unlock",
)
def seller_checkout_unlock():
    """Valida a senha do vendedor logado para desbloquear o checkout inativo."""
    auth = _seller_auth()
    if not auth:
        return jsonify({"error": "Sessão expirada. Faça login novamente."}), 401
    seller = get_seller(int(auth["seller_id"]))
    if seller is None or not seller.get("active"):
        return jsonify({"error": "Sessão expirada. Faça login novamente."}), 401
    payload = request.get_json(silent=True) or {}
    password = payload.get("password")
    if password is None:
        password = request.form.get("password") or ""
    if not isinstance(password, str) or not password:
        return jsonify({"error": "Informe a senha de login."}), 400
    if not check_password_hash(seller["password_hash"], password):
        return jsonify({"error": "Senha inválida."}), 403
    return jsonify({"ok": True})


def _seller_checkout_hold_items():
    payload = request.get_json(silent=True) or {}
    items = payload.get("items")
    if items is None:
        items = payload.get("itens")
    return items if isinstance(items, list) else []


def _seller_checkout_hold_seq() -> int:
    payload = request.get_json(silent=True) or {}
    raw = payload.get("seq")
    try:
        seq = int(raw)
    except (TypeError, ValueError):
        seq = 0
    return max(0, seq)


@app.route(
    "/vendedor/api/checkout/reserva",
    methods=["POST"],
    endpoint="seller_api_checkout_hold",
)
@seller_required
def seller_api_checkout_hold():
    """Publica o carrinho da tela de pagamento e devolve conflitos visíveis."""
    blocked = _reject_if_seller_event_ops_closed()
    if blocked is not None:
        return blocked
    seller_ev = _get_seller_event()
    if seller_ev is None:
        return jsonify({"ok": True, "conflicts": []})
    conflicts = sync_seller_checkout_holds(
        int(seller_ev["id"]),
        _current_seller_id(),
        _current_seller_display_name(),
        _seller_checkout_hold_items(),
        seq=_seller_checkout_hold_seq(),
    )
    return jsonify({"ok": True, "conflicts": conflicts})


@app.route(
    "/vendedor/api/checkout/liberar",
    methods=["POST"],
    endpoint="seller_api_checkout_hold_release",
)
@seller_required
def seller_api_checkout_hold_release():
    """Remove as reservas do vendedor (volta ao catálogo ou carrinho vazio)."""
    seller_ev = _get_seller_event()
    if seller_ev is None:
        return jsonify({"ok": True, "conflicts": []})
    release_seller_checkout_holds(
        int(seller_ev["id"]),
        _current_seller_id(),
        seq=_seller_checkout_hold_seq(),
    )
    return jsonify({"ok": True, "conflicts": []})


@app.route(
    "/vendedor/api/checkout/conflitos",
    methods=["POST"],
    endpoint="seller_api_checkout_hold_conflicts",
)
@seller_required
def seller_api_checkout_hold_conflicts():
    """Consulta conflitos no catálogo sem publicar reserva deste caixa."""
    seller_ev = _get_seller_event()
    if seller_ev is None:
        return jsonify({"ok": True, "conflicts": []})
    conflicts = list_checkout_stock_conflicts(
        int(seller_ev["id"]),
        _current_seller_id(),
        _seller_checkout_hold_items(),
    )
    return jsonify({"ok": True, "conflicts": conflicts})


@app.route("/vendedor/pagamento", endpoint="seller_payment")
@seller_required
def seller_payment():
    blocked = _reject_if_seller_event_ops_closed()
    if blocked is not None:
        return blocked
    return render_template("payment.html", **_seller_payment_page_context())


@app.route("/vendedor/pagamento/aguardando", endpoint="seller_payment_waiting")
@seller_required
def seller_payment_waiting():
    ctx = dict(_seller_payment_page_context())
    resume = None
    raw = (request.args.get("pendente") or "").strip()
    if raw.isdigit():
        sid = _current_seller_id()
        row = get_pending_transaction_if_owned(int(raw), sid)
        if row:
            resume = {
                "transaction_id": row["id"],
                "order_number": row["order_number"],
            }
    ctx["resume_pending_aut"] = resume
    return render_template("payment_waiting.html", **ctx)


@app.route("/vendedor/pedido/<int:tx_id>/refazer-checkout", endpoint="seller_restore_pending_checkout")
@seller_required
def seller_restore_pending_checkout(tx_id: int):
    """Restaura carrinho + formulário do cliente e envia à tela de pagamento (pedido pendente de AUT)."""
    blocked = _reject_if_seller_event_ops_closed()
    if blocked is not None:
        return blocked
    sid = _current_seller_id()
    payload = get_pending_transaction_restore_payload(tx_id, sid)
    if not payload or not payload.get("cart_items"):
        flash(
            "Não foi possível restaurar este pedido. Verifique se está pendente de AUT e se os produtos ainda existem.",
            "error",
        )
        return redirect(url_for("seller_dashboard"))
    return render_template(
        "seller/restore_checkout.html",
        restore_payload=payload,
        payment_url=url_for("seller_payment"),
    )


@app.route(
    "/vendedor/pedido/<int:tx_id>/cancelar-pendente",
    methods=["POST"],
    endpoint="seller_cancel_pending_transaction",
)
@seller_required
def seller_cancel_pending_transaction(tx_id: int):
    """Descarta pedido pendente (marca como cancelado); não altera estoque nem faturamento."""
    blocked = _reject_if_tx_event_ops_closed(tx_id)
    if blocked is not None:
        return blocked
    try:
        cancel_pending_transaction_for_seller(tx_id, _current_seller_id())
        flash("Pedido pendente descartado.", "error")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("seller_dashboard"))


@app.route(
    "/vendedor/pedido/<int:tx_id>/observacao-nota",
    methods=["POST"],
)
@seller_required
def seller_transaction_note(tx_id: int):
    """Vendedor grava observação impressa na nota do próprio pedido."""
    blocked = _reject_if_tx_event_ops_closed(tx_id)
    if blocked is not None:
        return blocked
    note = request.form.get("receipt_note") or ""
    try:
        result = update_transaction_receipt_note(
            tx_id,
            note,
            expected_seller_id=_current_seller_id(),
        )
        order_label = result.get("order_number") or f"#{tx_id}"
        if result.get("receipt_note"):
            flash(f"Observação da nota do pedido {order_label} salva.", "success")
        else:
            flash(f"Observação da nota do pedido {order_label} removida.", "warning")
    except ValueError as exc:
        flash(str(exc), "error")
    return _redirect_back(
        url_for("seller_dashboard"),
        fragment=f"tx-{tx_id}",
    )


def _seller_dashboard_transactions_view() -> tuple:
    """Lista paginada de transações do vendedor logado (escopo do evento quando houver)."""
    seller_id = _current_seller_id()
    seller_ev = _get_seller_event()

    pedido = (request.args.get("pedido") or "").strip()
    status_raw = (request.args.get("status") or "").strip().lower()
    valid_status = frozenset({"todos", "confirmado", "pendente", "cancelado", "estornado", "entregue"})
    status_norm = status_raw if status_raw in valid_status else "todos"

    entrega_raw = (request.args.get("entrega") or "").strip().lower()
    entrega_norm = entrega_raw if entrega_raw == "parcial" else "todos"
    delivery_api = None if entrega_norm == "todos" else entrega_norm

    date_arg_raw = (request.args.get("data") or "").strip()
    on_date = _parse_tx_filter_date_arg(date_arg_raw)
    filter_date_display = on_date or ""

    per_page = SELLER_DASHBOARD_TX_PER_PAGE
    page = max(1, _parse_int(request.args.get("page"), 1))
    status_api = None if status_norm == "todos" else status_norm

    if seller_ev:
        ev_id = int(seller_ev["id"])
        total = count_transactions_for_event(
            ev_id,
            seller_id=seller_id,
            order_search=pedido or None,
            status=status_api,
            on_date=on_date,
            delivery=delivery_api,
        )
    else:
        total = count_transactions_for_seller(
            seller_id,
            order_search=pedido or None,
            status=status_api,
            on_date=on_date,
            delivery=delivery_api,
        )

    total_pages = max(1, (total + per_page - 1) // per_page) if total > 0 else 1
    page = min(page, total_pages)
    offset = (page - 1) * per_page

    if seller_ev:
        transactions = list_transactions_for_event(
            int(seller_ev["id"]),
            seller_id=seller_id,
            order_search=pedido or None,
            status=status_api,
            on_date=on_date,
            delivery=delivery_api,
            limit=per_page,
            offset=offset,
        )
    else:
        transactions = list_transactions_for_seller(
            seller_id,
            order_search=pedido or None,
            status=status_api,
            on_date=on_date,
            delivery=delivery_api,
            limit=per_page,
            offset=offset,
        )

    showing_from = offset + 1 if total > 0 else 0
    showing_to = min(offset + len(transactions), total) if total > 0 else 0

    filters = {
        "pedido": pedido,
        "status": status_norm,
        "entrega": entrega_norm,
        "data": filter_date_display,
    }
    pagination = {
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "showing_from": showing_from,
        "showing_to": showing_to,
    }
    return transactions, filters, pagination


@app.route("/vendedor/dashboard")
@seller_required
def seller_dashboard():
    seller_id = _current_seller_id()
    seller_ev = _get_seller_event()
    # KPIs no mesmo recorte da lista: só o evento em que o vendedor está operando.
    stats = get_stats(
        seller_id=seller_id, event_id=int(seller_ev["id"]) if seller_ev else None,
    )
    transactions, filters, pagination = _seller_dashboard_transactions_view()
    pending_sales_count = _seller_pending_sales_count(seller_id, seller_ev)
    pending_delivery_count = count_pending_delivery_transactions(
        seller_id=seller_id,
        event_id=int(seller_ev["id"]) if seller_ev else None,
    )
    return render_template(
        "seller/dashboard.html",
        stats=stats,
        pending_sales_count=pending_sales_count,
        pending_delivery_count=pending_delivery_count,
        transactions=transactions,
        filters=filters,
        pagination=pagination,
        seller_id=seller_id,
        **_seller_shell_context(active_section="dashboard"),
    )


@app.route("/vendedor/estoque")
@seller_required
def seller_stock():
    seller_ev = _get_seller_event()
    if seller_ev:
        ev_id = seller_ev["id"]
        seller_id = _current_seller_id()
        products, filters, pagination = _seller_event_stock_page_view(ev_id, seller_id)
        ev_stats = get_event_stock_stats(ev_id)
        stock = {
            "products_count": ev_stats["products_count"],
            "products_active": ev_stats["products_count"],
            "units_in_stock": ev_stats["units_in_stock"],
            "stock_value": ev_stats["stock_value"],
            "below_min": ev_stats["below_min"],
            "out_of_stock": ev_stats["sem_estoque"],
        }
        stock_api_url = url_for(
            "seller_api_event_stock",
            q=filters["q"],
            status=filters["status"],
            per_page=filters["per_page"],
            page=pagination["page"],
            sort=filters["sort"],
            dir=filters["dir"],
        )
        promo_product_ids = product_ids_with_active_promotions_for_event(ev_id)
        promo_tooltips = active_promotion_tooltip_by_product_id(ev_id)
        promo_names = active_promotion_names_by_product_id(ev_id)
        return render_template(
            "seller/stock.html",
            products=products,
            stock=stock,
            categories=[],
            filters=filters,
            pagination=pagination,
            allowed_per_page=ALLOWED_ADMIN_STOCK_PER_PAGE,
            stock_api_url=stock_api_url,
            promo_product_ids=promo_product_ids,
            promo_tooltips=promo_tooltips,
            promo_names=promo_names,
            **_seller_shell_context(active_section="estoque"),
        )
    products, filters, pagination = _admin_stock_page_view()
    seller_id = _current_seller_id()
    product_ids = [int(p["id"]) for p in products]
    sold_map = units_sold_by_product_for_seller(seller_id, product_ids=product_ids)
    for p in products:
        p["units_sold"] = int(sold_map.get(int(p["id"]), 0))
    return render_template(
        "seller/stock.html",
        products=products,
        stock=get_products_library_stats(),
        categories=_admin_stock_library_category_options(),
        filters=filters,
        pagination=pagination,
        allowed_per_page=ALLOWED_ADMIN_STOCK_PER_PAGE,
        stock_api_url=url_for("seller_api_stock",
                              q=filters["q"], categoria=filters["categoria"],
                              status=filters["status"],
                              per_page=filters["per_page"], page=pagination["page"]),
        promo_product_ids=frozenset(),
        promo_tooltips={},
        promo_names={},
        **_seller_shell_context(active_section="estoque"),
    )


@app.route("/vendedor/estoque/<int:product_id>")
@seller_required
def seller_stock_product(product_id: int):
    """Página de produto individual para vendedor: vendas e retiradas pendentes dele."""
    seller_id = _current_seller_id()
    seller_ev = _get_seller_event()

    if seller_ev:
        product = get_product_in_event(int(seller_ev["id"]), product_id)
        if product is None:
            flash("Produto não encontrado neste evento.", "error")
            return redirect(url_for("seller_stock"))
        product = _enrich_product_promo_for_event(product, int(seller_ev["id"]))
    else:
        product = get_product(product_id)
        if product is None:
            flash("Produto não encontrado.", "error")
            return redirect(url_for("seller_stock"))

    pedido = (request.args.get("pedido") or "").strip()
    per_page = EVENT_PRODUCT_MOVEMENTS_PER_PAGE
    page = max(1, _parse_int(request.args.get("page"), 1))

    if seller_ev:
        # Mescla vendas confirmadas + linhas sintéticas de retirada pendente (como no admin).
        ev_id = int(seller_ev["id"])
        total = count_event_product_ledger(
            ev_id,
            product_id,
            reference=pedido or None,
            seller_id=seller_id,
        )
        total_pages = max(1, (total + per_page - 1) // per_page) if total > 0 else 1
        page = min(page, total_pages)
        offset = (page - 1) * per_page
        movements = list_event_product_ledger(
            ev_id,
            product_id,
            reference=pedido or None,
            seller_id=seller_id,
            limit=per_page,
            offset=offset,
        )
    else:
        total = count_stock_movements(
            product_id=product_id,
            movement_type="venda",
            reference=pedido or None,
            seller_id=seller_id,
        )
        total_pages = max(1, (total + per_page - 1) // per_page) if total > 0 else 1
        page = min(page, total_pages)
        offset = (page - 1) * per_page
        movements = list_stock_movements(
            product_id=product_id,
            movement_type="venda",
            reference=pedido or None,
            seller_id=seller_id,
            limit=per_page,
            offset=offset,
        )

    showing_from = offset + 1 if total > 0 else 0
    showing_to = min(offset + len(movements), total) if total > 0 else 0

    filters = {
        "pedido": pedido,
    }
    pagination = {
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "showing_from": showing_from,
        "showing_to": showing_to,
    }

    return render_template(
        "seller/stock_product.html",
        product=product,
        movements=movements,
        filters=filters,
        pagination=pagination,
        **_seller_shell_context(active_section="estoque"),
    )


@app.route("/vendedor/transacoes")
@seller_required
def seller_transactions():
    """Redireciona para o dashboard (histórico de transações unificado)."""
    return redirect(url_for("seller_dashboard", **_forwarded_query_args()))


def _seller_transaction_api(tx: dict) -> dict:
    """Serializa transação para o endpoint JSON do vendedor (somente leitura)."""
    items = tx.get("items") or []
    return {
        "id": int(tx["id"]),
        "order_number": tx["order_number"],
        "created_at_display": datahora_filter(tx["created_at"]),
        "items_count": int(tx["items_count"] or 0),
        "total_display": brl_filter(tx["total"]),
        "status": tx["status"],
        "delivery_status": tx.get("delivery_status") or "completa",
        "payment_method": tx.get("payment_method"),
        "payment_method_label": _payment_method_label(
            tx.get("payment_method"),
            tx.get("card_installments"),
        ),
        "parcelas_cartao": _card_installment_plan_text(
            tx.get("total"),
            tx.get("payment_method"),
            tx.get("card_installments"),
        ),
        "aut": tx.get("aut") or None,
        "items": [
            {
                "product_name": it.get("product_name") or "",
                "product_sku": it.get("product_sku"),
                "category": it.get("category") or "-",
                "quantity": int(it.get("quantity") or 0),
                "unit_price_display": brl_filter(it.get("unit_price")),
                "subtotal_display": brl_filter(it.get("subtotal")),
            }
            for it in items
        ],
    }


@app.route("/vendedor/api/estoque")
@seller_required
def seller_api_stock():
    seller_ev = _get_seller_event()
    if seller_ev:
        ev_id = seller_ev["id"]
        seller_id = _current_seller_id()
        products = list_event_products_for_client(ev_id)
        sold_map = units_sold_by_product_for_event(
            ev_id, product_ids=[int(p["id"]) for p in products], seller_id=seller_id,
        )
        for p in products:
            p["units_sold"] = int(sold_map.get(int(p["id"]), 0))
        ev_stats = get_event_stock_stats(ev_id)
        stock = {
            "products_count": ev_stats["products_count"],
            "products_active": ev_stats["products_count"],
            "units_in_stock": ev_stats["units_in_stock"],
            "stock_value": ev_stats["stock_value"],
            "below_min": ev_stats["below_min"],
            "out_of_stock": ev_stats["sem_estoque"],
        }
        return jsonify({
            "stock": stock,
            "pagination": {"page": 1, "per_page": len(products), "total": len(products), "total_pages": 1},
            "products": [{**p, "status": _event_product_status({
                "stock": p["estoque"], "min_stock": p["estoque_minimo"], "product_active": p["ativo"],
            })} for p in products],
        })
    products, _filters, pagination = _admin_stock_page_view()
    seller_id = _current_seller_id()
    product_ids = [int(p["id"]) for p in products]
    sold_map = units_sold_by_product_for_seller(seller_id, product_ids=product_ids)
    for p in products:
        p["units_sold"] = int(sold_map.get(int(p["id"]), 0))
    return jsonify({
        "stock": get_products_library_stats(),
        "pagination": {
            "page": pagination["page"],
            "per_page": pagination["per_page"],
            "total": pagination["total"],
            "total_pages": pagination["total_pages"],
        },
        "products": [
            {**p, "status": _product_status(p)}
            for p in products
        ],
    })


@app.route("/vendedor/api/evento/estoque")
@seller_required
def seller_api_event_stock():
    """API de polling para a listagem de estoque do evento no painel do vendedor."""
    seller_ev = _get_seller_event()
    if seller_ev is None:
        return jsonify({"error": "Não associado a nenhum evento ativo."}), 404
    ev_id = seller_ev["id"]
    seller_id = _current_seller_id()
    products, _filters, pagination = _seller_event_stock_page_view(ev_id, seller_id)
    ev_stats = get_event_stock_stats(ev_id)
    promo_ids = product_ids_with_active_promotions_for_event(ev_id)
    promo_tooltips = active_promotion_tooltip_by_product_id(ev_id)
    promo_names = active_promotion_names_by_product_id(ev_id)
    stock = {
        "products_count": ev_stats["products_count"],
        "products_active": ev_stats["products_count"],
        "units_in_stock": ev_stats["units_in_stock"],
        "stock_value": ev_stats["stock_value"],
        "below_min": ev_stats["below_min"],
        "out_of_stock": ev_stats["sem_estoque"],
    }
    return jsonify({
        "stock": stock,
        "pagination": {
            "page": pagination["page"],
            "per_page": pagination["per_page"],
            "total": pagination["total"],
            "total_pages": pagination["total_pages"],
        },
        "products": [
            {
                **p,
                "status": _event_product_status({
                    "stock": p["estoque"],
                    "min_stock": p["estoque_minimo"],
                    "product_active": p["ativo"],
                }),
                "active_promo": int(p["id"]) in promo_ids,
                "promo_tooltip": promo_tooltips.get(int(p["id"]), ""),
                "promo_name": promo_names.get(int(p["id"]), ""),
            }
            for p in products
        ],
    })


@app.route(
    "/vendedor/api/catalogo/estoque",
    endpoint="seller_api_catalog_stock",
)
@seller_required
def seller_api_catalog_stock():
    """Payload mínimo para atualizar estoque nos cards do catálogo (polling)."""
    return jsonify({"products": list_active_product_stocks()})


@app.route("/vendedor/api/evento/catalogo/estoque", endpoint="seller_api_event_catalog_stock")
@seller_required
def seller_api_event_catalog_stock():
    """Polling de estoque do catálogo em modo evento."""
    seller_ev = _get_seller_event()
    if seller_ev is None:
        return jsonify({"products": list_active_product_stocks()})
    products = list_active_event_product_stocks(seller_ev["id"])
    _attach_pending_delivery_units(
        products, seller_ev["id"], seller_id=_current_seller_id(),
    )
    return jsonify({"products": products})


@app.route(
    "/vendedor/api/evento/catalogo/precos-promocoes",
    endpoint="seller_api_event_catalog_promos_refresh",
)
@seller_required
def seller_api_event_catalog_promos_refresh():
    """Catálogo do evento com preços e promoções recalculados (polling ~15s no front).

    Retorna o mesmo formato que ``list_event_products_for_client``, já enriquecido
    com ``em_promocao``, ``preco_original``, ``promo_badge``, etc.

    ``catalog_ids`` traz, em ordem, os cards que o catálogo deve exibir — o front
    compara com os da tela para notar produtos adicionados ou removidos pelo
    administrador. Com ``?cards=1,2`` a resposta inclui também o HTML desses
    cards (``cards``), renderizado com o mesmo template da página.
    """
    seller_ev = _get_seller_event()
    if seller_ev is None:
        return jsonify(
            {"error": "Este endpoint só se aplica a vendedores associados a um evento."}
        ), 404
    products = list_event_products_for_client(seller_ev["id"])
    promos = get_active_promotions_for_event(seller_ev["id"])
    promo_map = build_promo_display_map(promos)
    products = [enrich_product_with_promo(p, promo_map) for p in products]
    _attach_pending_delivery_units(
        products, seller_ev["id"], seller_id=_current_seller_id(),
    )
    summarize_catalog_option_groups(products)
    visible = [p for p in products if not p.get("catalog_oculto")]
    payload = {
        "products": products,
        "catalog_ids": [str(p["id"]) for p in visible],
    }
    wanted = {
        s.strip() for s in request.args.get("cards", "").split(",") if s.strip()
    }
    if wanted:
        payload["cards"] = {
            str(p["id"]): render_template("partials/catalog_card.html", product=p)
            for p in visible
            if str(p["id"]) in wanted
        }
    return jsonify(payload)


@app.route("/vendedor/api/clientes/consulta", endpoint="seller_api_customer_lookup")
@seller_required
def seller_api_customer_lookup():
    """Dados já conhecidos de um CPF, para o checkout preencher o resto do formulário.

    Primeiro o cadastro do parceiro no ERP (quando a consulta está
    configurada); o histórico do Go Sell completa os campos que faltarem e é a
    fonte única quando o CPF não está no ERP, a consulta está desligada ou
    o ERP não respondeu a tempo. O histórico cobre todos os eventos — o
    mesmo critério de identidade da carteira de clientes.
    """
    cpf = re.sub(r"\D", "", request.args.get("cpf", ""))
    local = find_customer_by_cpf(cpf)
    sankhya_row, sankhya_status = (
        erp_sync.lookup_client(cpf) if len(cpf) == 11 else (None, "off")
    )
    customer = merge_customer_sources(sankhya_row, local)
    if customer is None:
        return jsonify({"found": False, "sankhya": sankhya_status})
    return jsonify({"found": True, "customer": customer, "sankhya": sankhya_status})


@app.route("/api/carrinho/cotacao", methods=["POST"])
@seller_required
def api_cart_promo_quote():
    """Cotação promocional do carrinho (mesma lógica da criação de transação)."""
    seller_ev = _get_seller_event()
    if seller_ev is None:
        return jsonify({"error": "Cotação disponível apenas para vendas em evento."}), 404
    if not event_ops_open(seller_ev):
        return jsonify({"error": EVENT_OPS_CLOSED_MSG}), 403
    payload = request.get_json(silent=True) or {}
    items = payload.get("items") or payload.get("itens") or []
    try:
        quote = quote_cart_items_for_event(int(seller_ev["id"]), items)
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(quote)


@app.route("/vendedor/api/dashboard")
@seller_required
def seller_api_dashboard():
    """JSON com os mesmos dados do dashboard."""
    seller_id = _current_seller_id()
    seller_ev = _get_seller_event()
    event_id = int(seller_ev["id"]) if seller_ev else None
    if event_id:
        transactions = list_transactions_for_event(event_id, seller_id=seller_id, limit=100)
    else:
        transactions = list_transactions(limit=100, seller_id=seller_id)
    latest_tx_id = max((int(t["id"]) for t in transactions), default=0)
    pending_sales_count = _seller_pending_sales_count(seller_id, seller_ev)
    pending_delivery_count = count_pending_delivery_transactions(
        seller_id=seller_id,
        event_id=int(seller_ev["id"]) if seller_ev else None,
    )
    return jsonify({
        "stats": get_stats(seller_id=seller_id, event_id=event_id),
        "pending_sales_count": pending_sales_count,
        "pending_delivery_count": pending_delivery_count,
        "latest_tx_id": latest_tx_id,
        "transactions": [_seller_transaction_api(t) for t in transactions],
    })


# ---------------------------------------------------------------------------
# Painel administrativo — vendedores
# ---------------------------------------------------------------------------

_SELLER_FORM_FIELD_ORDER = ("name", "username", "event_id", "password")


#: Permissões que valem dentro de um evento: exigem escolher os eventos do painel.
_EVENT_SCOPED_PERMISSIONS = frozenset({
    "transacoes_ver", "transacoes_gerir", "promocoes", "estoque", "dashboard", "clientes",
})


def _parse_user_access_form(form) -> tuple[dict, str | None]:
    """Tipo, permissões e eventos do painel do formulário de usuário.

    Nos tipos pré-definidos valem as permissões da pré-definição; no
    personalizado, as caixas marcadas (``permissions``).
    """
    role = normalize_role(form.get("role"))
    if role == "personalizado":
        perms = clean_permissions(form.getlist("permissions"))
    else:
        perms = list(PRESETS[role]["permissions"])
    all_events = role == "administrador" or form.get("all_events") == "1"
    event_ids = list(dict.fromkeys(int(v) for v in form.getlist("panel_event_id") if str(v).isdigit()))
    access = {
        "role": role,
        "permissions": perms,
        "all_events": all_events,
        "event_ids": event_ids,
        "can_sell": "vender" in perms,
    }
    error = None
    if not perms:
        error = "Marque ao menos uma permissão (ou escolha um tipo pré-definido)."
    elif set(perms) & _EVENT_SCOPED_PERMISSIONS and not all_events and not event_ids:
        error = "Escolha os eventos que este usuário acessa no painel (ou marque «Todos os eventos»)."
    return access, error


def _save_user_access(user_id: int, access: dict) -> None:
    set_user_access(
        user_id,
        role=access["role"],
        permissions=access["permissions"],
        all_events=access["all_events"],
        event_ids=access["event_ids"],
    )


def _user_form_context(seller: dict | None = None, access: dict | None = None) -> dict:
    """Catálogo de permissões, pré-definições e eventos para o formulário de usuário."""
    user = describe_user(seller) if seller else None
    if access is None and user is not None:
        access = {
            "role": user["role"],
            "permissions": user["permission_list"],
            "all_events": user["all_events"],
            "event_ids": user["panel_event_ids"],
            "can_sell": user["can_sell"],
        }
    return {
        "user": user,
        "user_access": access or {
            "role": "vendedor", "permissions": list(PRESETS["vendedor"]["permissions"]),
            "all_events": False, "event_ids": [], "can_sell": True,
        },
        "permission_catalog": PERMISSIONS,
        "presets": PRESETS,
        "event_scoped_permissions": sorted(_EVENT_SCOPED_PERMISSIONS),
        "events_for_panel": list_events(include_archived=False),
    }


def _first_seller_form_error_message(errors: dict[str, str]) -> str:
    for key in _SELLER_FORM_FIELD_ORDER:
        if key in errors:
            return errors[key]
    return next(iter(errors.values()), "Verifique os campos destacados.")


def _parse_new_seller_post(form, can_sell: bool = True) -> tuple[dict[str, str], dict[str, str]]:
    """Validação do cadastro de usuário. Retorna (erros_por_campo, valores_para_reexibir).

    O evento de venda só é exigido de quem tem a permissão de vender.
    """
    name = (form.get("name") or "").strip()
    username = (form.get("username") or form.get("email") or "").strip().lower()
    password = form.get("password") or ""
    event_id_raw = (form.get("event_id") or "").strip()
    event_id = _parse_int(event_id_raw, 0)

    errors: dict[str, str] = {}
    if not name:
        errors["name"] = "Informe o nome do usuário."
    try:
        username = validate_seller_username(username)
    except ValueError as exc:
        errors["username"] = str(exc)

    if len(password) < 6:
        errors["password"] = "A senha deve ter pelo menos 6 caracteres."

    if not can_sell:
        pass  # sem permissão de vender, não há evento de venda
    elif event_id <= 0:
        errors["event_id"] = "Selecione o evento em que este usuário vai vender."
    else:
        ev = get_event(event_id)
        if ev is None:
            errors["event_id"] = "Evento não encontrado."
        elif not event_ops_open(ev):
            errors["event_id"] = (
                "Não é possível associar o vendedor a um evento com operações encerradas."
            )

    if "username" not in errors and username and get_seller_by_username(username):
        errors["username"] = "Já existe um usuário com este login."

    codvend_raw = (form.get("erp_codvend") or "").strip()
    try:
        normalize_seller_codvend(codvend_raw)
    except ValueError as exc:
        errors["erp_codvend"] = str(exc)

    def keep(field: str, value: str) -> str:
        return "" if field in errors else value

    repop = {
        "name": keep("name", name),
        "username": keep("username", username),
        "password": keep("password", password),
        "event_id": "" if "event_id" in errors else event_id_raw,
        "erp_codvend": keep("erp_codvend", codvend_raw),
    }
    return errors, repop


def _parse_edit_seller_post(form, _seller_id: int) -> tuple[dict[str, str], dict]:
    """Validação da edição de vendedor. Retorna (erros_por_campo, valores_para_reexibir)."""
    name = (form.get("name") or "").strip()
    username = (form.get("username") or form.get("email") or "").strip().lower()
    active = form.get("active") == "1"
    password = form.get("password") or ""
    event_id_raw = (form.get("event_id") or "").strip()

    errors: dict[str, str] = {}
    if not name:
        errors["name"] = "Nome do vendedor é obrigatório."
    try:
        username = validate_seller_username(username)
    except ValueError as exc:
        errors["username"] = str(exc)

    if password and len(password) < 6:
        errors["password"] = "A nova senha deve ter pelo menos 6 caracteres."

    if not event_id_raw:
        pass  # sem evento permitido
    else:
        eid = _parse_int(event_id_raw, 0)
        if eid <= 0:
            errors["event_id"] = "Selecione um evento válido ou «Sem evento»."
        else:
            ev = get_event(eid)
            if ev is None:
                errors["event_id"] = "Evento não encontrado."
            else:
                current = get_seller_admin_event_selection_id(_seller_id)
                if int(current or 0) != eid and not event_ops_open(ev):
                    errors["event_id"] = (
                        "Não é possível associar o vendedor a um evento com operações encerradas."
                    )

    codvend_raw = (form.get("erp_codvend") or "").strip()
    try:
        normalize_seller_codvend(codvend_raw)
    except ValueError as exc:
        errors["erp_codvend"] = str(exc)

    repop = {
        "name": "" if "name" in errors else name,
        "username": "" if "username" in errors else username,
        "erp_codvend": "" if "erp_codvend" in errors else codvend_raw,
        "active": active,
        "password": "" if "password" in errors else password,
        "event_id": "" if "event_id" in errors else event_id_raw,
    }
    return errors, repop


@app.route("/admin/vendedores")
@app.route("/admin/vendedores/<path:rest>")
def admin_sellers_legacy_redirect(rest: str = ""):
    """Endereços antigos da seção Vendedores (hoje Usuários)."""
    return redirect("/admin/usuarios" + (f"/{rest}" if rest else ""))


@app.route("/admin/usuarios", methods=["GET", "POST"])
@admin_required
def admin_sellers():
    seller_form = None
    seller_form_errors: dict[str, str] = {}
    access = None
    if request.method == "POST":
        access, access_error = _parse_user_access_form(request.form)
        seller_form_errors, seller_form = _parse_new_seller_post(request.form, can_sell=access["can_sell"])
        if access_error:
            seller_form_errors["access"] = access_error
        if not seller_form_errors:
            try:
                event_id = _parse_int(request.form.get("event_id") or "", 0)
                seller = create_seller_account(
                    (request.form.get("name") or "").strip(),
                    (request.form.get("username") or request.form.get("email") or "").strip().lower(),
                    generate_password_hash(request.form.get("password") or ""),
                    pin_hash=None,
                    erp_codvend=(
                        normalize_seller_codvend(request.form.get("erp_codvend"))
                        if access["can_sell"] else None
                    ),
                )
                _save_user_access(int(seller["id"]), access)
                msg = f"Usuário {seller['name']} ({PRESETS[access['role']]['label']}) criado"
                if access["can_sell"]:
                    add_seller_to_event(event_id, int(seller["id"]))
                    ev = get_event(event_id)
                    msg += f", vendendo no evento «{(ev or {}).get('name') or f'#{event_id}'}»"
                flash(msg + ".", "success")
                return redirect(url_for("admin_seller_detail", seller_id=seller["id"]))
            except ValueError as exc:
                msg = str(exc)
                repop = _parse_new_seller_post(request.form, can_sell=access["can_sell"])[1]
                if "codvend" in msg.lower():
                    seller_form_errors = {"erp_codvend": msg}
                    seller_form = repop
                    seller_form["erp_codvend"] = ""
                elif "usuário" in msg.lower() and ("já" in msg.lower() or "existente" in msg.lower()):
                    seller_form_errors = {"username": msg}
                    seller_form = repop
                    seller_form["username"] = ""
                else:
                    flash(msg, "error")
                    seller_form = repop
                    seller_form_errors = {}
        if seller_form_errors:
            flash(_first_seller_form_error_message(seller_form_errors), "error")

    users = [describe_user(s) for s in list_sellers()]
    events_for_seller_form = list_events(include_archived=True)
    return render_template(
        "admin/sellers.html",
        sellers=users,
        seller_form=seller_form,
        seller_form_errors=seller_form_errors,
        events_for_seller_form=events_for_seller_form,
        **_user_form_context(None, access),
        **_admin_shell_context(active_section="usuarios"),
    )


def _admin_seller_transactions_view(seller_id: int, event_id: int | None = None):
    """Lista paginada de vendas do vendedor (admin) com filtros de status/entrega."""
    pedido = (request.args.get("pedido") or "").strip()
    status_raw = (request.args.get("status") or "").strip().lower()
    valid_status = frozenset({"todos", "confirmado", "pendente", "cancelado", "estornado", "entregue"})
    status_norm = status_raw if status_raw in valid_status else "todos"
    status_api = None if status_norm == "todos" else status_norm

    entrega_raw = (request.args.get("entrega") or "").strip().lower()
    entrega_norm = entrega_raw if entrega_raw == "parcial" else "todos"
    delivery_api = None if entrega_norm == "todos" else entrega_norm

    date_arg_raw = (request.args.get("data") or "").strip()
    on_date = _parse_tx_filter_date_arg(date_arg_raw)
    filter_date_display = on_date or ""

    per_page = _parse_int(request.args.get("per_page"), DEFAULT_ADMIN_MOVEMENTS_PER_PAGE)
    if per_page not in ALLOWED_ADMIN_STOCK_PER_PAGE:
        per_page = DEFAULT_ADMIN_MOVEMENTS_PER_PAGE
    page = max(1, _parse_int(request.args.get("page"), 1))

    total = count_transactions_for_seller(
        seller_id,
        order_search=pedido or None,
        status=status_api,
        on_date=on_date,
        delivery=delivery_api,
        event_id=event_id,
    )
    total_pages = max(1, (total + per_page - 1) // per_page) if total > 0 else 1
    page = min(page, total_pages)
    offset = (page - 1) * per_page

    transactions = list_transactions_for_seller(
        seller_id,
        order_search=pedido or None,
        status=status_api,
        on_date=on_date,
        delivery=delivery_api,
        event_id=event_id,
        limit=per_page,
        offset=offset,
    )

    showing_from = offset + 1 if total > 0 else 0
    showing_to = min(offset + len(transactions), total) if total > 0 else 0

    filters = {
        "pedido": pedido,
        "status": status_norm,
        "entrega": entrega_norm,
        "data": filter_date_display,
        "per_page": per_page,
        "evento": event_id,
    }
    pagination = {
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "showing_from": showing_from,
        "showing_to": showing_to,
    }
    return transactions, filters, pagination


def _seller_scoped_context(seller: dict) -> dict:
    """Dados da página do vendedor que respeitam o filtro de evento (``?evento=``).

    KPIs, análise e lista de vendas seguem o mesmo recorte. O filtro só vale
    para eventos em que o vendedor tem vendas; qualquer outro valor = todos.

    ``show_sales``: só para quem vende ou já vendeu (ex.: vendedor que virou
    Marketing). Administrador, Marketing e personalizado sem "vender", sem
    nenhuma venda, não têm os blocos de vendas na página.
    """
    seller_id = int(seller["id"])
    if not describe_user(seller)["can_sell"] and count_transactions_for_seller(seller_id) == 0:
        return {"show_sales": False, "seller_events": [], "seller_event_filter": None}
    seller_events = list_seller_events(seller_id)
    raw = (request.args.get("evento") or "").strip()
    event_id = int(raw) if raw.isdigit() else None
    if event_id not in {e["id"] for e in seller_events}:
        event_id = None

    analytics = get_seller_analytics(seller_id, event_id)
    # Rótulos de pagamento vivem na apresentação, como no perfil do cliente.
    charts = {
        "seller_by_day": analytics["by_day"],
        "intervals": analytics["intervals"],
        "payment_methods": [
            {**p, "label": _payment_method_label(p["method"])}
            for p in analytics["payment_methods"]
        ],
    }
    transactions, filters, pagination = _admin_seller_transactions_view(seller_id, event_id)
    return {
        "show_sales": True,
        "stats": get_stats(seller_id=seller_id, event_id=event_id),
        "analytics": analytics,
        "charts": charts,
        "transactions": transactions,
        "filters": filters,
        "pagination": pagination,
        "seller_events": seller_events,
        "seller_event_filter": event_id,
    }


@app.route("/admin/usuarios/<int:seller_id>")
@admin_required
def admin_seller_detail(seller_id: int):
    seller = get_seller(seller_id)
    if seller is None:
        flash("Vendedor não encontrado.", "error")
        return redirect(url_for("admin_sellers"))
    events_for_seller_form = list_events(include_archived=True)
    seller_primary_event_id = get_seller_admin_event_selection_id(seller_id)
    return render_template(
        "admin/seller_detail.html",
        seller=seller,
        **_seller_scoped_context(seller),
        allowed_per_page=ALLOWED_ADMIN_STOCK_PER_PAGE,
        seller_form=None,
        seller_form_errors={},
        events_for_seller_form=events_for_seller_form,
        seller_primary_event_id=seller_primary_event_id,
        **_user_form_context(seller),
        **_admin_shell_context(active_section="usuarios"),
    )


@app.route("/admin/usuarios/<int:seller_id>/editar", methods=["POST"])
@admin_required
def admin_seller_update(seller_id: int):
    seller = get_seller(seller_id)
    if seller is None:
        flash("Vendedor não encontrado.", "error")
        return redirect(url_for("admin_sellers"))

    seller_form_errors, seller_form = _parse_edit_seller_post(request.form, seller_id)
    access, access_error = _parse_user_access_form(request.form)
    if access_error:
        seller_form_errors["access"] = access_error
    account = _panel_account() or {}
    if not account.get("master") and account.get("id") == seller_id:
        # Ninguém tira de si mesmo o acesso a esta tela (ficaria sem como desfazer).
        if "usuarios" not in access["permissions"] or request.form.get("active") != "1":
            seller_form_errors["access"] = (
                "Você não pode desativar a sua própria conta nem tirar dela a permissão de Usuários."
            )
    if seller_form_errors:
        flash(_first_seller_form_error_message(seller_form_errors), "error")
        events_for_seller_form = list_events(include_archived=True)
        seller_primary_event_id = get_seller_admin_event_selection_id(seller_id)
        return render_template(
            "admin/seller_detail.html",
            seller=seller,
            **_seller_scoped_context(seller),
            allowed_per_page=ALLOWED_ADMIN_STOCK_PER_PAGE,
            seller_form=seller_form,
            seller_form_errors=seller_form_errors,
            events_for_seller_form=events_for_seller_form,
            seller_primary_event_id=seller_primary_event_id,
            **_user_form_context(seller, access),
            **_admin_shell_context(active_section="usuarios"),
        )

    name = (request.form.get("name") or "").strip()
    username = (request.form.get("username") or request.form.get("email") or "").strip().lower()
    active = request.form.get("active") == "1"
    password = request.form.get("password") or ""
    event_raw = (request.form.get("event_id") or "").strip()
    assigned_event_id = None if not event_raw else _parse_int(event_raw, 0)
    password_hash = None
    if password:
        password_hash = generate_password_hash(password)
    try:
        seller = update_seller_account(
            seller_id,
            name=name,
            username=username,
            active=active,
            password_hash=password_hash,
            clear_pin_hash=True,
            erp_codvend=(
                normalize_seller_codvend(request.form.get("erp_codvend"))
                if access["can_sell"] else seller.get("erp_codvend")
            ),
        )
        _save_user_access(seller_id, access)
    except ValueError as exc:
        msg = str(exc)
        seller_form_errors: dict[str, str] = {}
        bad_field = "username"
        if "codvend" in msg.lower():
            bad_field = "erp_codvend"
            seller_form_errors["erp_codvend"] = msg
        elif "usuário" in msg.lower() or "usuario" in msg.lower():
            seller_form_errors["username"] = msg
        else:
            flash(msg, "error")
            return redirect(url_for("admin_seller_detail", seller_id=seller_id))
        seller_form = _parse_edit_seller_post(request.form, seller_id)[1]
        seller_form[bad_field] = ""
        flash(_first_seller_form_error_message(seller_form_errors), "error")
        events_for_seller_form = list_events(include_archived=True)
        seller_primary_event_id = get_seller_admin_event_selection_id(seller_id)
        return render_template(
            "admin/seller_detail.html",
            seller=seller,
            **_seller_scoped_context(seller),
            allowed_per_page=ALLOWED_ADMIN_STOCK_PER_PAGE,
            seller_form=seller_form,
            seller_form_errors=seller_form_errors,
            events_for_seller_form=events_for_seller_form,
            seller_primary_event_id=seller_primary_event_id,
            **_user_form_context(seller, access),
            **_admin_shell_context(active_section="usuarios"),
        )

    try:
        # Evento de venda só para quem vende; sem a permissão, o vínculo sai.
        replace_seller_event_assignment(seller_id, assigned_event_id if access["can_sell"] else None)
    except ValueError as exc:
        flash(
            f"Dados de {seller['name']} foram atualizados, mas o vínculo com o evento não foi alterado: {exc}",
            "error",
        )
    else:
        flash(f"Dados de {seller['name']} atualizados.", "success")
    return redirect(url_for("admin_seller_detail", seller_id=seller_id))


@app.route(
    "/admin/usuarios/<int:seller_id>/transacoes/<int:tx_id>/itens/<int:item_id>/entregar",
    methods=["POST"],
)
@admin_required
def admin_seller_confirm_item_delivery(seller_id: int, tx_id: int, item_id: int):
    """Confirma a retirada de um item pendente (escopo do vendedor no admin)."""
    seller = get_seller(seller_id)
    if seller is None:
        flash("Vendedor não encontrado.", "error")
        return redirect(url_for("admin_sellers"))
    blocked = _reject_if_tx_event_ops_closed(tx_id)
    if blocked is not None:
        return blocked
    try:
        result = confirm_item_delivery(
            tx_id,
            item_id,
            seller_id=seller_id,
            created_by=_current_admin_user(),
        )
        msg = (
            f"Entrega confirmada: {result['delivered_now']} un. de "
            f"'{result['product_name']}'."
        )
        if result["still_pending"] > 0:
            msg += f" Ainda pendente: {result['still_pending']} un."
        flash(msg, "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return _redirect_back(url_for("admin_seller_detail", seller_id=seller_id))


@app.route(
    "/admin/usuarios/<int:seller_id>/transacoes/<int:tx_id>/entregar",
    methods=["POST"],
)
@admin_required
def admin_seller_confirm_items_delivery(seller_id: int, tx_id: int):
    """Confirma a retirada de vários itens pendentes (escopo do vendedor no admin)."""
    seller = get_seller(seller_id)
    if seller is None:
        flash("Vendedor não encontrado.", "error")
        return redirect(url_for("admin_sellers"))
    blocked = _reject_if_tx_event_ops_closed(tx_id)
    if blocked is not None:
        return blocked
    item_ids = request.form.getlist("item_ids")
    try:
        result = confirm_items_delivery(
            tx_id,
            item_ids,
            seller_id=seller_id,
            created_by=_current_admin_user(),
        )
        msg = (
            f"Entrega confirmada: {result['items_count']} produto(s), "
            f"{result['units_delivered']} un."
        )
        if result.get("errors"):
            msg += " Alguns itens não puderam ser entregues (verifique o estoque)."
            flash(msg, "warning")
            for err in result["errors"][:5]:
                flash(err, "error")
        else:
            flash(msg, "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return _redirect_back(url_for("admin_seller_detail", seller_id=seller_id))


@app.route(
    "/admin/usuarios/<int:seller_id>/transacoes/<int:tx_id>/entregue",
    methods=["POST"],
)
@admin_required
def admin_seller_confirm_transaction_handover(seller_id: int, tx_id: int):
    """Marca o pedido inteiro como entregue/retirado (escopo do vendedor no admin)."""
    seller = get_seller(seller_id)
    if seller is None:
        flash("Vendedor não encontrado.", "error")
        return redirect(url_for("admin_sellers"))
    blocked = _reject_if_tx_event_ops_closed(tx_id)
    if blocked is not None:
        return blocked
    try:
        confirm_transaction_handover(tx_id, seller_id=seller_id)
        flash("Pedido marcado como entregue.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return _redirect_back(url_for("admin_seller_detail", seller_id=seller_id))


@app.route(
    "/admin/usuarios/<int:seller_id>/transacoes/<int:tx_id>/observacao-nota",
    methods=["POST"],
)
@admin_required
def admin_seller_transaction_note(seller_id: int, tx_id: int):
    seller = get_seller(seller_id)
    if seller is None:
        flash("Vendedor não encontrado.", "error")
        return redirect(url_for("admin_sellers"))
    blocked = _reject_if_tx_event_ops_closed(tx_id)
    if blocked is not None:
        return blocked
    note = request.form.get("receipt_note") or ""
    try:
        result = update_transaction_receipt_note(
            tx_id,
            note,
            expected_seller_id=seller_id,
        )
        order_label = result.get("order_number") or f"#{tx_id}"
        if result.get("receipt_note"):
            flash(f"Observação da nota do pedido {order_label} salva.", "success")
        else:
            flash(f"Observação da nota do pedido {order_label} removida.", "warning")
    except ValueError as exc:
        flash(str(exc), "error")
    return _redirect_back(
        url_for("admin_seller_detail", seller_id=seller_id),
        fragment=f"tx-{tx_id}",
    )


@app.route(
    "/admin/usuarios/<int:seller_id>/excluir",
    methods=["POST"],
    endpoint="admin_seller_delete",
)
@admin_required
def admin_seller_delete(seller_id: int):
    account = _panel_account() or {}
    if not account.get("master") and account.get("id") == seller_id:
        flash("Você não pode excluir a sua própria conta.", "error")
        return redirect(url_for("admin_seller_detail", seller_id=seller_id))
    try:
        deleted = delete_seller(seller_id)
        flash(
            f"Cadastro de {deleted['name']} excluído. "
            "Vendas antigas permanecem no histórico, sem vínculo a esta conta.",
            "success",
        )
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin_sellers"))


# ---------------------------------------------------------------------------
# Financeiro
# ---------------------------------------------------------------------------

def _parse_fin_filters():
    """Lê parâmetros de filtro da rota Financeiro."""
    event_id_raw = request.args.get("evento") or ""
    date_from = (request.args.get("de") or "").strip()
    date_to = (request.args.get("ate") or "").strip()
    # validação mínima de formato
    import re
    _date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    if date_from and not _date_re.match(date_from):
        date_from = ""
    if date_to and not _date_re.match(date_to):
        date_to = ""
    try:
        ev_id = int(event_id_raw) if event_id_raw else None
    except ValueError:
        ev_id = None
    return ev_id, date_from or None, date_to or None


@app.route("/admin/financeiro")
@admin_required
def admin_financeiro():
    all_events = list_events(include_archived=True)
    ev_id, date_from, date_to = _parse_fin_filters()
    if ev_id is None and all_events:
        ev_id = int(all_events[0]["id"])
    report = None
    selected_event = None
    if ev_id is not None:
        selected_event = get_event(ev_id)
        if selected_event:
            report = get_event_financial_report(
                ev_id, date_from=date_from, date_to=date_to
            )
            # Rótulo de pagamento vive na camada de apresentação, não na consulta.
            for row in report["payment_methods"]:
                row["label"] = _payment_method_label(row["method"])
    return render_template(
        "admin/financeiro.html",
        selected_event_id=ev_id,
        date_from=date_from or "",
        date_to=date_to or "",
        report=report,
        **_admin_shell_context(active_section="financeiro"),
    )


@app.route("/admin/financeiro/pdf")
@admin_required
def admin_financeiro_pdf():
    """Renderiza a versão para impressão/PDF do relatório financeiro."""
    ev_id, date_from, date_to = _parse_fin_filters()
    if ev_id is None:
        return redirect(url_for("admin_financeiro"))
    event = get_event(ev_id)
    if event is None:
        flash("Evento não encontrado.", "error")
        return redirect(url_for("admin_financeiro"))
    report = get_event_financial_report(ev_id, date_from=date_from, date_to=date_to)
    return render_template(
        "admin/financeiro_pdf.html",
        report=report,
        all_events=list_events(include_archived=True),
        selected_event_id=ev_id,
        date_from=date_from or "",
        date_to=date_to or "",
        now=datetime.now(),
    )


@app.route("/admin/reiniciar-sistema", methods=["POST"])
@admin_required
def admin_reset_system():
    """Restaura a demonstração ao estado inicial (``seed.demo.reset_demo_data``)."""
    if request.form.get("confirm_reset") != "1":
        flash(
            "Para reiniciar o sistema, marque a caixa de confirmação e tente novamente.",
            "error",
        )
        return redirect(_admin_home_url())
    try:
        from seed.demo import reset_demo_data

        result = reset_demo_data()
        flash(
            "Demonstração restaurada ao estado inicial. "
            f"{result['products']} produtos, {result['users']} usuários, "
            f"{result['confirmed_sales']} vendas de exemplo e "
            f"{result['pending_sales']} pedido pendente.",
            "success",
        )
    except Exception:
        app.logger.exception("Falha ao reiniciar o sistema")
        flash(
            "Não foi possível reiniciar o sistema. Verifique os logs ou tente novamente.",
            "error",
        )
    return redirect(_admin_home_url())


# ---------------------------------------------------------------------------
# Painel administrativo — estoque
# ---------------------------------------------------------------------------

def _admin_shell_context(**extra):
    """Contexto comum para todas as telas do painel (topbar/nav).

    O seletor de evento só mostra os eventos que a conta pode abrir.
    """
    account = _panel_account() or {}
    return {
        "admin_user": account.get("name") or _current_admin_user(),
        "now": datetime.now(),
        "all_events": _panel_events(),
        "admin_home_url": _admin_home_url(),
        **extra,
    }


ALLOWED_ADMIN_STOCK_PER_PAGE = (10, 25, 50, 100)
DEFAULT_ADMIN_STOCK_PER_PAGE = 25
DEFAULT_ADMIN_MOVEMENTS_PER_PAGE = 25
EVENT_PRODUCT_MOVEMENTS_PER_PAGE = 20
SELLER_DASHBOARD_TX_PER_PAGE = 20
EVENT_IMPORT_XLS_MOTIVO_REF = "Importação por Planilha"


def _admin_stock_list_query_params():
    """Parâmetros GET compartilhados entre biblioteca admin/vendedor e suas APIs JSON."""
    q = (request.args.get("q") or "").strip()
    category = (request.args.get("categoria") or "").strip()
    status = (request.args.get("status") or "").strip()
    per_page = _parse_int(request.args.get("per_page"), DEFAULT_ADMIN_STOCK_PER_PAGE)
    if per_page not in ALLOWED_ADMIN_STOCK_PER_PAGE:
        per_page = DEFAULT_ADMIN_STOCK_PER_PAGE
    page = max(1, _parse_int(request.args.get("page"), 1))
    return q, category, status, per_page, page


#: Colunas ordenáveis da grade de estoque do evento — espelha ``_EVENT_STOCK_SORT_COLUMNS``.
EVENT_STOCK_SORT_FIELDS = {"preco", "vendas", "estoque", "minimo"}

#: Colunas ordenáveis da tabela de transações do evento — espelha ``_EVENT_TX_SORT_COLUMNS``.
EVENT_TX_SORT_FIELDS = {"data", "itens", "total"}


def _sortable_table_params(allowed_fields):
    """Lê ``sort``/``dir`` da querystring para a seta ▲/▼ alternadora do cabeçalho de uma tabela."""
    sort = (request.args.get("sort") or "").strip().lower()
    if sort not in allowed_fields:
        sort = ""
    direction = (request.args.get("dir") or "desc").strip().lower()
    if direction not in {"asc", "desc"}:
        direction = "desc"
    return sort, direction


def _admin_stock_page_view(*, ignore_status_filter: bool = False):
    q_display, category, status, per_page, page = _admin_stock_list_query_params()
    q_lower = q_display.lower() if q_display else ""
    q_filter = q_lower or None
    cat_norm = category or "todos"
    stat_norm = "todos" if ignore_status_filter else (status or "todos")

    total = count_products_admin_filtered(q_filter, cat_norm, stat_norm)
    total_pages = max(1, (total + per_page - 1) // per_page) if total > 0 else 1
    page = min(page, total_pages)
    offset = (page - 1) * per_page
    products = list_products_admin_slice(
        q_filter,
        cat_norm,
        stat_norm,
        limit=per_page,
        offset=offset,
    )

    showing_from = offset + 1 if total > 0 else 0
    showing_to = min(offset + len(products), total) if total > 0 else 0

    filters = {
        "q": q_display,
        "categoria": cat_norm,
        "status": stat_norm,
        "per_page": per_page,
    }
    pagination = {
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "showing_from": showing_from,
        "showing_to": showing_to,
    }
    return products, filters, pagination


#: Vendedor não vê preço nesta grade — só pode ordenar pelas colunas que enxerga.
SELLER_STOCK_SORT_FIELDS = {"vendas", "estoque", "minimo"}


def _seller_event_stock_page_view(event_id: int, seller_id: int):
    """Lista paginada do estoque do evento no painel do vendedor (busca + situação, sem categoria)."""
    q_display, _category, status, per_page, page = _admin_stock_list_query_params()
    q_lower = q_display.lower() if q_display else ""
    q_filter = q_lower or None
    stat_norm = (status or "todos").strip().lower()
    if stat_norm not in {"todos", "ok", "baixo", "sem_estoque", "inativo"}:
        stat_norm = "todos"
    sort, sort_dir = _sortable_table_params(SELLER_STOCK_SORT_FIELDS)

    total = count_event_products_filtered(event_id, q_filter, "todos", stat_norm)
    total_pages = max(1, (total + per_page - 1) // per_page) if total > 0 else 1
    page = min(page, total_pages)
    offset = (page - 1) * per_page
    products = list_event_products_filtered_for_client(
        event_id,
        q_filter,
        stat_norm,
        limit=per_page,
        offset=offset,
        sort=sort,
        direction=sort_dir,
        sort_seller_id=seller_id,
    )
    product_ids = [int(p["id"]) for p in products]
    sold_map = units_sold_by_product_for_event(
        event_id,
        product_ids=product_ids,
        seller_id=seller_id,
    )
    for p in products:
        p["units_sold"] = int(sold_map.get(int(p["id"]), 0))

    showing_from = offset + 1 if total > 0 else 0
    showing_to = min(offset + len(products), total) if total > 0 else 0

    filters = {
        "q": q_display,
        "status": stat_norm,
        "per_page": per_page,
        "sort": sort,
        "dir": sort_dir,
    }
    pagination = {
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "showing_from": showing_from,
        "showing_to": showing_to,
    }
    return products, filters, pagination


def _admin_event_stock_page_view(event_id: int):
    """Lista paginada de produtos no evento com os mesmos filtros GET da biblioteca geral."""
    q_display, category, status, per_page, page = _admin_stock_list_query_params()
    entrega_raw = (request.args.get("entrega") or "todos").strip().lower()
    entrega_norm = entrega_raw if entrega_raw in {"todos", "pendente"} else "todos"
    q_lower = q_display.lower() if q_display else ""
    q_filter = q_lower or None
    cat_norm = category or "todos"
    stat_norm = status or "todos"
    sort, sort_dir = _sortable_table_params(EVENT_STOCK_SORT_FIELDS)

    total = count_event_products_filtered(
        event_id, q_filter, cat_norm, stat_norm, entrega=entrega_norm,
    )
    total_pages = max(1, (total + per_page - 1) // per_page) if total > 0 else 1
    page = min(page, total_pages)
    offset = (page - 1) * per_page
    products = list_event_products_slice(
        event_id,
        q_filter,
        cat_norm,
        stat_norm,
        limit=per_page,
        offset=offset,
        entrega=entrega_norm,
        sort=sort,
        direction=sort_dir,
    )
    product_ids = [int(p["product_id"]) for p in products]
    pending_map = pending_delivery_units_by_product_for_event(
        event_id,
        product_ids=product_ids,
    )
    sold_map = units_sold_by_product_for_event(
        event_id,
        product_ids=product_ids,
    )
    for p in products:
        pid = int(p["product_id"])
        p["pending_delivery_units"] = int(pending_map.get(pid, 0))
        p["units_sold"] = int(sold_map.get(pid, 0))

    showing_from = offset + 1 if total > 0 else 0
    showing_to = min(offset + len(products), total) if total > 0 else 0

    filters = {
        "q": q_display,
        "categoria": cat_norm,
        "status": stat_norm,
        "entrega": entrega_norm,
        "per_page": per_page,
        "sort": sort,
        "dir": sort_dir,
    }
    pagination = {
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "showing_from": showing_from,
        "showing_to": showing_to,
    }
    return products, filters, pagination


def _event_stock_return_filters_from_form() -> dict:
    """Lê ret_* dos POSTs para preservar filtros ao redirecionar."""
    per_page = _parse_int(request.form.get("ret_per_page"), DEFAULT_ADMIN_STOCK_PER_PAGE)
    if per_page not in ALLOWED_ADMIN_STOCK_PER_PAGE:
        per_page = DEFAULT_ADMIN_STOCK_PER_PAGE
    entrega = (request.form.get("ret_entrega") or "todos").strip().lower()
    if entrega not in {"todos", "pendente"}:
        entrega = "todos"
    return {
        "q": (request.form.get("ret_q") or "").strip(),
        "categoria": (request.form.get("ret_categoria") or "todos").strip(),
        "status": (request.form.get("ret_status") or "todos").strip(),
        "entrega": entrega,
        "per_page": per_page,
        "page": max(1, _parse_int(request.form.get("ret_page"), 1)),
    }


def _url_for_admin_event_stock_list(event_id: int, filters: dict, *, page_override: int | None = None) -> str:
    per_page = _parse_int(filters.get("per_page"), DEFAULT_ADMIN_STOCK_PER_PAGE)
    if per_page not in ALLOWED_ADMIN_STOCK_PER_PAGE:
        per_page = DEFAULT_ADMIN_STOCK_PER_PAGE
    if page_override is not None:
        pg = max(1, int(page_override))
    else:
        pg = max(1, _parse_int(filters.get("page"), 1))
    kw = {
        "event_id": event_id,
        "categoria": (filters.get("categoria") or "todos").strip(),
        "status": (filters.get("status") or "todos").strip(),
        "entrega": (filters.get("entrega") or "todos").strip(),
        "per_page": per_page,
        "page": pg,
    }
    q = (filters.get("q") or "").strip()
    if q:
        kw["q"] = q
    return url_for("admin_event_stock", **kw)


@app.route("/admin/estoque")
@admin_required
def admin_stock_legacy_redirect():
    qs = request.query_string.decode()
    target = url_for("admin_products")
    return redirect(f"{target}?{qs}" if qs else target, code=302)


def _admin_stock_library_category_options() -> list[str]:
    """Opções do filtro Categoria: banco (fonte de verdade) + categorias legadas em memória."""
    db_cats = list_distinct_product_categories()
    seen = {c.casefold() for c in db_cats}
    merged = list(db_cats)
    for c in CATEGORIES:
        label = str(c).strip() if c is not None else ""
        if label and label.casefold() not in seen:
            seen.add(label.casefold())
            merged.append(label)
    return sorted(merged, key=lambda s: s.casefold())


@app.route("/admin/produtos")
@admin_required
def admin_products():
    products, filters, pagination = _admin_stock_page_view(ignore_status_filter=True)
    _with_library_sales(products)
    return render_template(
        "admin/products.html",
        products=products,
        stock=get_products_library_stats(),
        categories=_admin_stock_library_category_options(),
        filters=filters,
        pagination=pagination,
        allowed_per_page=ALLOWED_ADMIN_STOCK_PER_PAGE,
        events_for_modal=[e for e in list_events(include_archived=False) if event_ops_open(e)],
        **_admin_shell_context(active_section="produtos"),
    )


@app.route("/admin/produtos/novo", methods=["POST"])
@admin_required
def admin_product_create():
    """Cadastro manual de produto (demo: fora da sincronização do ERP)."""
    name = (request.form.get("name") or "").strip()
    sku = (request.form.get("sku") or "").strip()
    category = (request.form.get("category") or "").strip() or "Geral"
    brand = (request.form.get("brand") or "").strip()
    price_raw = (request.form.get("price") or "").strip()
    if "," in price_raw:
        price_raw = price_raw.replace(".", "").replace(",", ".")
    try:
        price = float(price_raw)
    except (TypeError, ValueError):
        price = -1
    if not name or not sku or price < 0:
        flash("Informe nome, SKU e um preço válido para cadastrar o produto.", "error")
        return redirect(url_for("admin_products"))
    try:
        created = create_local_product(
            sku=sku,
            name=name,
            category=category,
            price=price,
            brand=brand,
            image="/static/images/products/placeholder.svg",
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin_products"))
    flash(f"Produto {created.get('nome') or name} cadastrado (SKU {sku}).", "success")
    return redirect(url_for("admin_products"))


@app.route("/admin/produtos/<int:product_id>/adicionar-ao-evento", methods=["POST"])
@admin_required
def admin_product_add_to_event(product_id: int):
    product = get_product(product_id)
    if product is None:
        return jsonify({"error": "Produto não encontrado."}), 404
    event_id = _parse_int(request.form.get("event_id") or "", 0)
    if event_id <= 0:
        return jsonify({"error": "Selecione um evento."}), 400
    event = get_event(event_id)
    if event is None:
        return jsonify({"error": "Evento não encontrado."}), 400
    blocked = _reject_if_event_ops_closed(event)
    if blocked is not None:
        return blocked
    initial_stock = max(0, _parse_int(request.form.get("initial_stock") or "", 0))
    min_stock = max(0, _parse_int(request.form.get("min_stock") or "", DEFAULT_MIN_STOCK))
    link_note = (request.form.get("link_note") or "").strip()
    if not link_note:
        return jsonify({"error": "Informe Motivo / Ref."}), 400
    try:
        add_product_to_event(
            event_id,
            product_id,
            initial_stock,
            min_stock,
            link_audit_reason=link_note,
            link_audit_reference=None,
            created_by=_current_admin_user(),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify({
        "ok": True,
        "message": f"\u00ab{product['name']}\u00bb adicionado ao evento \u00ab{event['name']}\u00bb.",
    })


@app.route("/admin/estoque/<int:product_id>")
@admin_required
def admin_stock_product_legacy_redirect(product_id: int):
    return redirect(url_for("admin_product_detail", product_id=product_id), code=302)


@app.route("/admin/produtos/<int:product_id>")
@admin_required
def admin_product_detail(product_id: int):
    product_base = get_product(product_id)
    if product_base is None:
        flash("Produto não encontrado.", "error")
        return redirect(url_for("admin_products"))
    ev_total = get_product_events_stock_total(product_id)
    product = dict(product_base)
    product["estoque"] = ev_total
    product["abaixo_minimo"] = product["estoque_minimo"] > 0 and ev_total < product["estoque_minimo"]
    product["sem_estoque"] = ev_total <= 0
    movements = list_stock_movements(product_id=product_id, limit=100)
    return render_template(
        "admin/product_detail.html",
        product=product,
        movements=movements,
        **_admin_shell_context(active_section="produtos"),
    )


def _parse_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_float(value):
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _parse_tx_filter_date_arg(value) -> str | None:
    """``YYYY-MM-DD`` para filtro por dia em ``transactions.created_at``; inválido/absento → ``None``."""
    s = (value or "").strip()
    if len(s) != 10:
        return None
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return None
    return s


def _wants_json_response() -> bool:
    """Detecta chamadas AJAX/API sem mudar o fluxo HTML existente."""
    return (
        request.accept_mimetypes.best == "application/json"
        or request.headers.get("X-Requested-With") == "fetch"
        or request.args.get("format") == "json"
    )


def _product_status(product: dict) -> dict:
    if not product["ativo"]:
        return {"label": "Inativo", "kind": "neutral"}
    if product["sem_estoque"]:
        return {"label": "Sem estoque", "kind": "danger"}
    if product["abaixo_minimo"]:
        return {"label": "Abaixo do mínimo", "kind": "warn"}
    return {"label": "OK", "kind": "success"}


def _cro_pedido_fields(row: dict | None) -> dict[str, str] | None:
    """UF e número do registro CRO salvos na transação (formulário de pagamento)."""
    if not row:
        return None

    def _strip_val(v):
        if v is None:
            return ""
        return str(v).strip()

    uf_s = _strip_val(row.get("client_cro_uf"))
    num_s = _strip_val(row.get("client_cro_numero"))
    if not uf_s and not num_s:
        return None
    out: dict[str, str] = {}
    if uf_s:
        out["uf"] = uf_s
    if num_s:
        out["numero_registro"] = num_s
    return out if out else None


def _movement_payload(movement: dict) -> dict:
    movement_type = movement.get("movement_type")
    ev_bg, ev_fg = event_badge_style_pairs(movement.get("event_badge_color"))
    try:
        d_raw = int(movement.get("delta") or 0)
    except (TypeError, ValueError):
        d_raw = 0
    if d_raw > 0:
        delta_kind = "positive"
    elif d_raw < 0:
        delta_kind = "negative"
    else:
        delta_kind = "neutral"
    created_by_display = _display_created_by(movement.get("created_by"))
    if not created_by_display:
        created_by_display = str(movement.get("seller_name") or "").strip()
    try:
        pid_int = int(movement.get("product_id") or 0)
    except (TypeError, ValueError):
        pid_int = 0
    product_url = url_for("admin_product_detail", product_id=pid_int) if pid_int > 0 else ""
    tx_url = ""
    ref = str(movement.get("reference") or "").strip()
    try:
        eid_int = int(movement.get("event_id") or 0)
    except (TypeError, ValueError):
        eid_int = 0
    try:
        tid_int = int(movement.get("transaction_id") or 0)
    except (TypeError, ValueError):
        tid_int = 0
    if eid_int > 0 and ref and tid_int > 0:
        tx_url = (
            url_for("admin_event_transactions", event_id=eid_int, pedido=ref)
            + f"#tx-{tid_int}"
        )
    return {
        **movement,
        "event_badge_bg": ev_bg,
        "event_badge_fg": ev_fg,
        "created_by_display": created_by_display,
        "created_at_display": datahora_filter(movement.get("created_at")),
        "movement_label": mov_label_filter(movement_type),
        "delta_display": signed_filter(movement.get("delta")),
        "delta_kind": delta_kind,
        "product_url": product_url,
        "tx_url": tx_url,
    }


def _products_library_detail_payload(product_id: int, *, limit: int = 100) -> dict:
    product_base = get_product(product_id)
    if product_base is None:
        raise ValueError("Produto não encontrado.")
    ev_total = get_product_events_stock_total(product_id)
    product = dict(product_base)
    product["estoque"] = ev_total
    product["abaixo_minimo"] = product["estoque_minimo"] > 0 and ev_total < product["estoque_minimo"]
    product["sem_estoque"] = ev_total <= 0
    movements = list_stock_movements(product_id=product_id, limit=limit)
    return {
        "product": {
            **product,
            "status": _product_status(product),
            "stock_value_display": brl_filter(ev_total * product["preco"]),
        },
        "movements": [_movement_payload(m) for m in movements],
    }


def _json_products_library_success(message: str, product_id: int, status_code: int = 200):
    payload = _products_library_detail_payload(product_id)
    payload["message"] = message
    return jsonify(payload), status_code


def _enrich_product_promo_for_event(product: dict, event_id: int) -> dict:
    """Acrescenta ``em_promocao`` / ``promo_nome`` ao produto no contexto do evento."""
    promos = get_active_promotions_for_event(event_id)
    promo_map = build_promo_display_map(promos)
    return enrich_product_with_promo(dict(product), promo_map)


def _attach_promo_name_to_product(product: dict, event_id: int) -> dict:
    """Anexa campos de promoção ao produto sem alterar o preço de lista exibido."""
    enriched = _enrich_product_promo_for_event(product, event_id)
    out = dict(product)
    out["em_promocao"] = bool(enriched.get("em_promocao"))
    out["promo_nome"] = enriched.get("promo_nome") or ""
    out["promo_badge"] = enriched.get("promo_badge") or ""
    return out


def _event_stock_product_payload(event_id: int, product_id: int, *, limit: int = 100) -> dict:
    product = get_product_in_event(event_id, product_id)
    if product is None:
        raise ValueError("Produto não encontrado neste evento.")
    pending_units = pending_delivery_units_by_product_for_event(
        event_id, product_ids=[product_id],
    ).get(int(product_id), 0)
    product = dict(product)
    product["pending_delivery_units"] = int(pending_units)
    product = _attach_promo_name_to_product(product, event_id)
    movements = list_event_product_ledger(
        event_id, product_id, limit=limit, offset=0,
    )
    return {
        "product": {
            **product,
            "status": _event_product_status({
                "stock": product["estoque"],
                "min_stock": product["estoque_minimo"],
                "product_active": product["ativo"],
            }),
            "stock_value_display": brl_filter(product["estoque"] * product["preco"]),
        },
        "movements": [_event_movement_payload(m, event_id=event_id) for m in movements],
    }


def _json_event_stock_success(message: str, event_id: int, product_id: int, status_code: int = 200):
    payload = _event_stock_product_payload(event_id, product_id)
    payload["message"] = message
    return jsonify(payload), status_code


@app.route("/admin/produtos/<int:product_id>/ativar", methods=["POST"])
@app.route("/admin/estoque/<int:product_id>/ativar", methods=["POST"])
@admin_required
def admin_product_toggle_active(product_id: int):
    active = request.form.get("active") == "1"
    if set_product_active(product_id, active):
        message = (
            "Produto ativado e disponível no totem." if active
            else "Produto desativado — não aparecerá no totem."
        )
        if _wants_json_response():
            return _json_products_library_success(message, product_id)
        flash(message, "success")
    else:
        if _wants_json_response():
            return jsonify({"error": "Não foi possível atualizar o produto."}), 400
        flash("Não foi possível atualizar o produto.", "error")
    return _redirect_back(url_for("admin_product_detail", product_id=product_id))


def _with_library_sales(products: list[dict]) -> list[dict]:
    """Biblioteca: vendas somadas de todos os eventos (``units_sold``) e o
    detalhamento por evento para o title da coluna (``sales_breakdown``)."""
    by_product = units_sold_by_product_per_event(int(p["id"]) for p in products)
    for p in products:
        rows = by_product.get(int(p["id"]), [])
        p["units_sold"] = sum(r["units_sold"] for r in rows)
        p["sales_breakdown"] = " · ".join(
            f"{r['event_name'] or 'Sem evento'}: {r['units_sold']}" for r in rows
        )
    return products


def _admin_api_products_list_payload():
    products, _filters, pagination = _admin_stock_page_view(ignore_status_filter=True)
    _with_library_sales(products)
    return jsonify({
        "stock": get_products_library_stats(),
        "pagination": {
            "page": pagination["page"],
            "per_page": pagination["per_page"],
            "total": pagination["total"],
            "total_pages": pagination["total_pages"],
        },
        "products": [
            {**p, "status": _product_status(p)}
            for p in products
        ],
    })


@app.route("/admin/api/produtos")
@admin_required
def admin_api_products():
    return _admin_api_products_list_payload()


@app.route("/admin/api/estoque")
@admin_required
def admin_api_stock_legacy():
    return _admin_api_products_list_payload()


@app.route("/admin/api/produtos/<int:product_id>")
@admin_required
def admin_api_products_detail(product_id: int):
    try:
        return jsonify(_products_library_detail_payload(product_id))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404


@app.route("/admin/api/estoque/<int:product_id>")
@admin_required
def admin_api_stock_product_legacy(product_id: int):
    try:
        return jsonify(_products_library_detail_payload(product_id))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404


_EXPORT_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _csv_cell(value):
    if value is None:
        return ""
    return value


def _csv_fmt_date(value) -> str:
    """Converte ISO datetime (AAAA-MM-DD...) para DD/MM/AAAA; retorna vazio se inválido."""
    s = str(value or "").strip().replace("T", " ")
    date_part = s[:10]  # AAAA-MM-DD
    try:
        return datetime.strptime(date_part, "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return date_part


def _csv_fmt_time(value) -> str:
    """Extrai HH:MM de ISO datetime; retorna vazio se inválido."""
    s = str(value or "").strip().replace("T", " ")
    time_part = s[11:16] if len(s) >= 16 else ""  # HH:MM
    if len(time_part) == 5 and time_part[2] == ":":
        return time_part
    return ""


def _csv_fmt_brl(value) -> str:
    """Formata valor numérico como decimal com 2 casas (ponto como separador decimal)."""
    if value is None or value == "":
        return ""
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def _csv_fmt_delta(value) -> str:
    """Formata variação de estoque com sinal explícito (+5 / -3)."""
    if value is None or value == "":
        return ""
    try:
        n = int(value)
        return f"+{n}" if n >= 0 else str(n)
    except (TypeError, ValueError):
        return str(value)


def _csv_fmt_status(value) -> str:
    """Capitaliza status da transação."""
    s = str(value or "").strip()
    return s.capitalize() if s else ""


def _xlsx_import_openpyxl():
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:
        _pip_install("openpyxl>=3.1.0,<4")
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    return Workbook, Alignment, Font, PatternFill, get_column_letter


def _xlsx_write_sheet(ws, header: list[str], rows: list[list]) -> None:
    """Escreve cabeçalho + linhas numa aba já criada, com filtro automático e valores numéricos nas colunas de R$/qtd."""
    _, Alignment, Font, PatternFill, get_column_letter = _xlsx_import_openpyxl()

    font_header = Font(name="Calibri", bold=True, size=11, color="FFFFFF")
    fill_header = PatternFill(start_color="1565C0", end_color="1565C0", fill_type="solid")
    font_data = Font(name="Calibri", size=11)
    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")

    money_cols = {i for i, h in enumerate(header, start=1) if "(R$)" in str(h)}
    qty_names = {"Qtd.", "Qtd. de Itens", "ID Interno", "ID Pedido", "ID Item"}
    qty_cols = {i for i, h in enumerate(header, start=1) if str(h) in qty_names}

    def coerce(col: int, value):
        if value is None or value == "":
            return None
        if col in money_cols:
            try:
                return float(value)
            except (TypeError, ValueError):
                return value
        if col in qty_cols:
            try:
                return int(value)
            except (TypeError, ValueError):
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return value
        return value

    for ci, hdr in enumerate(header, start=1):
        cell = ws.cell(row=1, column=ci, value=hdr)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = align_center

    for ri, row in enumerate(rows, start=2):
        for ci, raw in enumerate(row, start=1):
            val = coerce(ci, raw)
            cell = ws.cell(row=ri, column=ci, value=val)
            cell.font = font_data
            if ci in money_cols:
                cell.alignment = align_right
                if isinstance(val, (int, float)):
                    cell.number_format = "#,##0.00"
            elif ci in qty_cols:
                cell.alignment = align_center
            else:
                cell.alignment = align_left

    for ci, hdr in enumerate(header, start=1):
        ws.column_dimensions[get_column_letter(ci)].width = min(42, max(12, len(str(hdr)) + 3))

    last_col = get_column_letter(max(1, len(header)))
    last_row = max(1, len(rows) + 1)
    ws.auto_filter.ref = f"A1:{last_col}{last_row}"
    ws.freeze_panes = "A2"


def _xlsx_attachment_response(
    filename: str,
    header: list[str],
    rows: list[list],
    *,
    sheet_title: str = "Vendas",
) -> Response:
    """Planilha .xlsx com cabeçalho, filtro automático e valores numéricos nas colunas de R$/qtd."""
    return _xlsx_attachment_response_multi(filename, [(sheet_title or "Vendas", header, rows)])


def _xlsx_attachment_response_multi(
    filename: str,
    sheets: list[tuple[str, list[str], list[list]]],
) -> Response:
    """Planilha .xlsx com uma ou mais abas, cada uma com cabeçalho e filtro automático próprios."""
    Workbook, *_ = _xlsx_import_openpyxl()

    wb = Workbook()
    for idx, (title, header, rows) in enumerate(sheets):
        ws = wb.active if idx == 0 else wb.create_sheet()
        ws.title = (title or f"Página{idx + 1}")[:31]
        _xlsx_write_sheet(ws, header, rows)

    buf = io.BytesIO()
    wb.save(buf)
    return Response(
        buf.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@app.route("/admin/eventos/<int:event_id>/vendas/export.xlsx")
@admin_required
def admin_event_sales_export_xlsx(event_id: int):
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))

    raw_from = (request.args.get("data_inicio") or "").strip()
    raw_to = (request.args.get("data_fim") or "").strip()
    for label, raw in (("Data inicial", raw_from), ("Data final", raw_to)):
        if raw and not _EXPORT_DATE_RE.fullmatch(raw):
            flash(f"{label}: use o formato AAAA-MM-DD.", "error")
            return redirect(url_for("admin_event_detail", event_id=event_id))

    nivel = (request.args.get("nivel") or "pedidos").strip().lower()
    if nivel not in ("pedidos", "itens", "completo"):
        nivel = "pedidos"

    event_sellers_rows = list_event_sellers(event_id)
    event_seller_ids = {int(s["id"]) for s in event_sellers_rows}
    seller_raw = _parse_int(request.args.get("vendedor"), 0)
    seller_filter = seller_raw if seller_raw > 0 and seller_raw in event_seller_ids else None
    if seller_raw > 0 and seller_filter is None:
        flash("Vendedor inválido para este evento.", "error")
        return redirect(url_for("admin_event_transactions", event_id=event_id))
    seller_name = ""
    if seller_filter is not None:
        for s in event_sellers_rows:
            if int(s["id"]) == seller_filter:
                seller_name = str(s.get("name") or "").strip()
                break

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_ev = re.sub(r"[^a-zA-Z0-9_-]+", "_", (event.get("name") or str(event_id)))[:40].strip("_") or str(event_id)
    seller_suffix = ""
    if seller_filter is not None:
        safe_seller = re.sub(r"[^a-zA-Z0-9_-]+", "_", seller_name or str(seller_filter))[:40].strip("_") or str(seller_filter)
        seller_suffix = f"_vendedor_{safe_seller}"

    # Ordem lógica (aba Pedidos): identificação → data/hora → situação → vendedor →
    #                              financeiro → pagamento → cliente → ID interno
    orders_header = [
        "Código do Pedido",
        "Data",
        "Hora",
        "Status",
        "Vendedor",
        "Qtd. de Itens",
        "Valor Total (R$)",
        "Forma de Pagamento",
        "AUT",
        "Nome do Cliente",
        "CPF",
        "E-mail",
        "Telefone",
        "CEP",
        "Endereço",
        "Número",
        "Complemento",
        "Bairro",
        "Cidade",
        "UF",
        "ID Interno",
    ]

    def _build_order_rows(rows_data) -> list[list]:
        rows = []
        for t in rows_data:
            created = t.get("created_at")
            rows.append(
                [
                    _csv_cell(t.get("order_number")),
                    _csv_fmt_date(created),
                    _csv_fmt_time(created),
                    _csv_fmt_status(t.get("status")),
                    _csv_cell(t.get("seller_name")),
                    _csv_cell(t.get("items_count")),
                    _csv_fmt_brl(t.get("total")),
                    _csv_cell(
                        _payment_method_label(
                            t.get("payment_method"),
                            t.get("card_installments"),
                        )
                    ),
                    _csv_cell(t.get("aut")),
                    _csv_cell(t.get("client_name")),
                    _csv_cell(t.get("client_cpf")),
                    _csv_cell(t.get("client_email")),
                    _csv_cell(t.get("client_phone")),
                    _csv_cell(t.get("client_zipcode")),
                    _csv_cell(t.get("client_address")),
                    _csv_cell(t.get("client_number")),
                    _csv_cell(t.get("client_complement")),
                    _csv_cell(t.get("client_neighborhood")),
                    _csv_cell(t.get("client_city")),
                    _csv_cell(t.get("client_state")),
                    _csv_cell(t.get("id")),
                ]
            )
        return rows

    if nivel == "pedidos":
        rows_data = list_transactions_summary_for_event_period(
            event_id,
            date_from=raw_from or None,
            date_to=raw_to or None,
            seller_id=seller_filter,
        )
        fname = f"vendas_evento_{event_id}_{safe_ev}{seller_suffix}_pedidos_{ts}.xlsx"
        return _xlsx_attachment_response(fname, orders_header, _build_order_rows(rows_data))

    if nivel == "completo":
        # Duas abas relacionadas pelo "Código do Pedido"/"ID Pedido": Pedidos (1 linha por
        # transação, com vendedor e cliente) e Itens (1 linha por produto, sem repetir os
        # dados do cliente em cada linha — isso é o que tornava a planilha única confusa).
        orders_rows_data = list_transactions_summary_for_event_period(
            event_id,
            date_from=raw_from or None,
            date_to=raw_to or None,
            seller_id=seller_filter,
        )
        items_rows_data = list_transaction_items_for_event_period(
            event_id,
            date_from=raw_from or None,
            date_to=raw_to or None,
            seller_id=seller_filter,
        )
        items_header = [
            "Código do Pedido",
            "Produto",
            "SKU",
            "Categoria",
            "Qtd.",
            "Preço Unitário (R$)",
            "Subtotal (R$)",
            "ID Pedido",
            "ID Item",
        ]
        items_rows = []
        for ti in items_rows_data:
            items_rows.append(
                [
                    _csv_cell(ti.get("order_number")),
                    _csv_cell(ti.get("product_name")),
                    _csv_cell(ti.get("product_sku")),
                    _csv_cell(ti.get("category")),
                    _csv_cell(ti.get("quantity")),
                    _csv_fmt_brl(ti.get("unit_price")),
                    _csv_fmt_brl(ti.get("subtotal")),
                    _csv_cell(ti.get("transaction_id")),
                    _csv_cell(ti.get("item_id")),
                ]
            )
        fname = f"vendas_evento_{event_id}_{safe_ev}{seller_suffix}_completo_{ts}.xlsx"
        return _xlsx_attachment_response_multi(
            fname,
            [
                ("Pedidos", orders_header, _build_order_rows(orders_rows_data)),
                ("Itens", items_header, items_rows),
            ],
        )

    # nivel == "itens" — Ordem lógica: pedido → data/hora → vendedor → pagamento →
    #                     produto → quantidades → valores → IDs
    rows_data = list_transaction_items_for_event_period(
        event_id,
        date_from=raw_from or None,
        date_to=raw_to or None,
        seller_id=seller_filter,
    )
    header = [
        "Código do Pedido",
        "Data",
        "Hora",
        "Vendedor",
        "Forma de Pagamento",
        "AUT",
        "Produto",
        "SKU",
        "Categoria",
        "Qtd.",
        "Preço Unitário (R$)",
        "Subtotal (R$)",
        "ID Pedido",
        "ID Item",
    ]
    rows = []
    for ti in rows_data:
        created = ti.get("created_at")
        rows.append(
            [
                _csv_cell(ti.get("order_number")),
                _csv_fmt_date(created),
                _csv_fmt_time(created),
                _csv_cell(ti.get("seller_name")),
                _csv_cell(
                    _payment_method_label(
                        ti.get("payment_method"),
                        ti.get("card_installments"),
                    )
                ),
                _csv_cell(ti.get("aut")),
                _csv_cell(ti.get("product_name")),
                _csv_cell(ti.get("product_sku")),
                _csv_cell(ti.get("category")),
                _csv_cell(ti.get("quantity")),
                _csv_fmt_brl(ti.get("unit_price")),
                _csv_fmt_brl(ti.get("subtotal")),
                _csv_cell(ti.get("transaction_id")),
                _csv_cell(ti.get("item_id")),
            ]
        )
    fname = f"vendas_evento_{event_id}_{safe_ev}{seller_suffix}_itens_{ts}.xlsx"
    return _xlsx_attachment_response(fname, header, rows)


@app.route("/admin/eventos/<int:event_id>/vendas/export.csv")
@admin_required
def admin_event_sales_export_csv(event_id: int):
    """Compatibilidade: o download de vendas passou a ser .xlsx."""
    return redirect(
        url_for(
            "admin_event_sales_export_xlsx",
            event_id=event_id,
            **_forwarded_query_args("event_id"),
        )
    )


# ---------------------------------------------------------------------------
# Eventos de estoque — helpers
# ---------------------------------------------------------------------------

def _event_or_404(event_id: int):
    """Retorna o evento ou faz flash+redirect para a lista."""
    ev = get_event(event_id)
    if ev is None:
        flash("Evento não encontrado.", "error")
    return ev


def _event_badge_color_from_form(form) -> str | None:
    """Interpreta checkbox \"cor padrão\" + color picker do formulário de evento."""
    use_default = (form.get("badge_use_default") or "").strip().lower() in ("1", "on", "true", "yes")
    if use_default:
        return None
    return normalize_event_badge_color((form.get("badge_color") or "").strip())


def _event_operation_type_from_form(form) -> str | None:
    """Slug do tipo de operação (evento|congresso|stand) ou None se inválido."""
    return normalize_event_operation_type(form.get("operation_type"))


def _op_noun(event, *, plural: bool = False, lower: bool = False) -> str:
    """Substantivo da operação para flashes e textos no servidor."""
    text = event_operation_noun(event, plural=plural)
    return text.lower() if lower else text


def _event_subnav_context(event_id: int, active_tab: str) -> dict:
    """Contexto compartilhado para todas as sub-páginas do evento."""
    ev = get_event(event_id)
    stats = get_event_stock_stats(event_id) if ev else {}
    return {
        "event": ev,
        "stats": stats,
        "event_id": event_id,
        "active_event_tab": active_tab,
    }


def _event_movement_payload(movement: dict, *, event_id: int) -> dict:
    """Serializa uma movimentação de evento para JSON (polling / live forms)."""
    movement_type = movement.get("movement_type")
    ev_bg, ev_fg = event_badge_style_pairs(movement.get("event_badge_color"))
    try:
        d_raw = int(movement.get("delta") or 0)
    except (TypeError, ValueError):
        d_raw = 0
    if d_raw > 0:
        delta_kind = "positive"
    elif d_raw < 0:
        delta_kind = "negative"
    else:
        delta_kind = "neutral"
    try:
        product_id = int(movement["product_id"])
    except (TypeError, ValueError, KeyError):
        product_id = 0
    reference = (movement.get("reference") or "").strip()
    try:
        tx_id = int(movement["transaction_id"]) if movement.get("transaction_id") else 0
    except (TypeError, ValueError):
        tx_id = 0
    tx_url = ""
    if reference and tx_id > 0 and event_id:
        tx_url = (
            url_for("admin_event_transactions", event_id=int(event_id), pedido=reference)
            + f"#tx-{tx_id}"
        )
    return {
        **movement,
        "event_badge_bg": ev_bg,
        "event_badge_fg": ev_fg,
        "created_by_display": _display_created_by(movement.get("created_by")),
        "created_at_display": datahora_filter(movement.get("created_at")),
        "movement_label": mov_label_filter(movement_type),
        "delta_display": signed_filter(movement.get("delta")),
        "delta_kind": delta_kind,
        "product_url": url_for("admin_product_detail", product_id=product_id) if product_id else "",
        "is_pending_delivery": bool(movement.get("is_pending_delivery")),
        "tx_url": tx_url,
    }


def _event_product_status(ep: dict) -> dict:
    """Calcula situação de um produto dentro do evento."""
    if not ep.get("product_active", True):
        return {"label": "Inativo no catálogo", "kind": "neutral"}
    if int(ep.get("stock") or 0) == 0:
        return {"label": "Sem estoque", "kind": "danger"}
    if int(ep.get("min_stock") or 0) > 0 and int(ep["stock"]) < int(ep["min_stock"]):
        return {"label": "Abaixo do mínimo", "kind": "warn"}
    return {"label": "OK", "kind": "success"}


# ---------------------------------------------------------------------------
# Eventos de estoque — lista e CRUD principal
# ---------------------------------------------------------------------------

@app.route("/admin/eventos")
@admin_required
def admin_events():
    active = list_events(include_archived=False)
    archived = list_events(include_archived=True)
    archived = [e for e in archived if not e["active"]]
    return render_template(
        "admin/events.html",
        events=active,
        events_archived=archived,
        **_admin_shell_context(active_section="eventos"),
    )


@app.route("/admin/eventos/novo", methods=["POST"])
@admin_required
def admin_event_create():
    name = (request.form.get("name") or "").strip()
    description = (request.form.get("description") or "").strip()
    if not name:
        flash("O nome do evento é obrigatório.", "error")
        return redirect(url_for("admin_events"))
    operation_type = _event_operation_type_from_form(request.form)
    if not operation_type:
        flash("Escolha o tipo de operação: Evento, Congresso ou Stand.", "error")
        return redirect(url_for("admin_events"))
    badge_color = _event_badge_color_from_form(request.form)
    event_id = create_event(
        name, description, badge_color=badge_color, operation_type=operation_type
    )
    flash(f"{_op_noun({'operation_type': operation_type})} \"{name}\" criado com sucesso.", "success")
    return redirect(url_for("admin_event_detail", event_id=event_id))


@app.route("/admin/eventos/<int:event_id>")
@admin_required
def admin_event_detail(event_id: int):
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))
    stats = get_event_stock_stats(event_id)
    sales_day_page = max(1, _parse_int(request.args.get("sales_day_page"), 1))
    sales_dashboard = get_event_sales_dashboard(event_id, sales_days_page=sales_day_page)
    sales_analytics = get_event_sales_analytics(event_id)
    # Rótulo de pagamento vive na camada de apresentação, não na consulta.
    for row in sales_analytics["payment_methods"]:
        row["label"] = _payment_method_label(row["method"])
    sales_analytics["stock_health"] = {
        "ok": max(
            0,
            int(stats.get("products_count") or 0)
            - int(stats.get("below_min") or 0)
            - int(stats.get("sem_estoque") or 0),
        ),
        "below_min": int(stats.get("below_min") or 0),
        "out_of_stock": int(stats.get("sem_estoque") or 0),
    }
    sellers = list_event_sellers(event_id)
    return render_template(
        "admin/event_detail.html",
        event=event,
        stats=stats,
        sales_dashboard=sales_dashboard,
        sales_analytics=sales_analytics,
        sellers=sellers,
        active_event_tab="dashboard",
        **_admin_shell_context(active_section="eventos"),
    )


@app.route("/admin/eventos/<int:event_id>/editar", methods=["POST"])
@admin_required
@require_event_ops_open
def admin_event_edit(event_id: int):
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))
    name = (request.form.get("name") or "").strip()
    description = (request.form.get("description") or "").strip()
    if not name:
        flash(f"O nome do {_op_noun(event, lower=True)} é obrigatório.", "error")
        return redirect(url_for("admin_event_detail", event_id=event_id))
    operation_type = _event_operation_type_from_form(request.form)
    if not operation_type:
        flash("Escolha o tipo de operação: Evento, Congresso ou Stand.", "error")
        return redirect(url_for("admin_event_detail", event_id=event_id))
    badge_color = _event_badge_color_from_form(request.form)
    update_event(
        event_id, name, description, badge_color=badge_color, operation_type=operation_type
    )
    flash(f"{_op_noun({'operation_type': operation_type})} atualizado.", "success")
    return redirect(url_for("admin_event_detail", event_id=event_id))


@app.route("/admin/eventos/<int:event_id>/arquivar", methods=["POST"])
@admin_required
def admin_event_archive(event_id: int):
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))
    archive_event(event_id)
    flash(f"Evento \"{event['name']}\" arquivado.", "warning")
    return redirect(url_for("admin_events"))


@app.route("/admin/eventos/<int:event_id>/restaurar", methods=["POST"])
@admin_required
def admin_event_restore(event_id: int):
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))
    restore_event(event_id)
    flash(f"Evento \"{event['name']}\" reativado.", "success")
    return redirect(url_for("admin_events"))


@app.route("/admin/eventos/<int:event_id>/encerrar-operacoes", methods=["POST"])
@admin_required
def admin_event_close_operations(event_id: int):
    """Encerra vendas e edições; o evento permanece ativo só para consulta."""
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))
    if not int(event.get("active") or 0):
        flash(f"Reative o {_op_noun(event, lower=True)} arquivado antes de gerenciar as operações.", "error")
        return redirect(url_for("admin_event_detail", event_id=event_id))
    if not event_ops_open(event):
        flash(f"As operações deste {_op_noun(event, lower=True)} já estão encerradas.", "error")
        return redirect(url_for("admin_event_detail", event_id=event_id))
    set_event_operations_closed(event_id, True)
    flash(
        f"Operações do {_op_noun(event, lower=True)} \"{event['name']}\" encerradas. "
        "Admin e vendedores passam a consultar estoque, vendas e transações apenas em modo leitura.",
        "warning",
    )
    return redirect(url_for("admin_event_detail", event_id=event_id))


@app.route("/admin/eventos/<int:event_id>/reabrir-operacoes", methods=["POST"])
@admin_required
def admin_event_reopen_operations(event_id: int):
    """Reabre vendas e edições de um evento ativo com operações encerradas."""
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))
    if not int(event.get("active") or 0):
        flash(f"Reative o {_op_noun(event, lower=True)} arquivado antes de reabrir as operações.", "error")
        return redirect(url_for("admin_event_detail", event_id=event_id))
    if event_ops_open(event):
        flash(f"As operações deste {_op_noun(event, lower=True)} já estão abertas.", "error")
        return redirect(url_for("admin_event_detail", event_id=event_id))
    set_event_operations_closed(event_id, False)
    flash(f"Operações do {_op_noun(event, lower=True)} \"{event['name']}\" reabertas.", "success")
    return redirect(url_for("admin_event_detail", event_id=event_id))


@app.route(
    "/admin/eventos/<int:event_id>/excluir",
    methods=["POST"],
    endpoint="admin_event_delete",
)
@admin_required
def admin_event_delete(event_id: int):
    from database import delete_event

    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))
    try:
        summary = delete_event(event_id)
        flash(
            f"Evento \"{summary['name']}\" excluído permanentemente "
            f"({summary['transactions']} transação(ões), "
            f"{summary['stock_movements']} movimentação(ões) de estoque, "
            f"{summary['products']} produto(s) no evento, "
            f"{summary['promotions']} promoção(ões), "
            f"{summary['sellers']} vínculo(s) com vendedores).",
            "success",
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin_event_detail", event_id=event_id))
    return redirect(url_for("admin_events"))


# ---------------------------------------------------------------------------
# Eventos — sub-página Estoque
# ---------------------------------------------------------------------------

@app.route("/admin/eventos/<int:event_id>/estoque")
@admin_required
def admin_event_stock(event_id: int):
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))
    products, filters, pagination = _admin_event_stock_page_view(event_id)
    stats = get_event_stock_stats(event_id)
    promo_product_ids = product_ids_with_active_promotions_for_event(event_id)
    promo_tooltips = active_promotion_tooltip_by_product_id(event_id)
    promo_names = active_promotion_names_by_product_id(event_id)
    return render_template(
        "admin/event_stock.html",
        event=event,
        products=products,
        stats=stats,
        filters=filters,
        pagination=pagination,
        promo_product_ids=promo_product_ids,
        promo_tooltips=promo_tooltips,
        promo_names=promo_names,
        categories=_admin_stock_library_category_options(),
        allowed_per_page=ALLOWED_ADMIN_STOCK_PER_PAGE,
        active_event_tab="estoque",
        **_admin_shell_context(active_section="eventos"),
    )


@app.route("/admin/eventos/<int:event_id>/estoque/export.xlsx")
@admin_required
def admin_event_stock_export_xlsx(event_id: int):
    """Exporta a grade de estoque do evento em planilha .xlsx, respeitando os filtros ativos."""
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))

    q_display, category, status, _per_page, _page = _admin_stock_list_query_params()
    entrega_raw = (request.args.get("entrega") or "todos").strip().lower()
    entrega_norm = entrega_raw if entrega_raw in {"todos", "pendente"} else "todos"
    q_filter = q_display.lower() if q_display else None
    cat_norm = category or "todos"
    stat_norm = status or "todos"
    sort, sort_dir = _sortable_table_params(EVENT_STOCK_SORT_FIELDS)

    products = list_event_products_slice(
        event_id,
        q_filter,
        cat_norm,
        stat_norm,
        limit=EXPORT_STOCK_CSV_CAP,
        offset=0,
        entrega=entrega_norm,
        sort=sort,
        direction=sort_dir,
    )
    product_ids = [int(p["product_id"]) for p in products]
    sold_map = units_sold_by_product_for_event(event_id, product_ids=product_ids)
    pending_map = pending_delivery_units_by_product_for_event(event_id, product_ids=product_ids)

    header = [
        "Produto",
        "Vendas",
        "Estoque",
        "Mínimo",
        "Situação",
        "SKU",
        "Categoria",
        "Preço (R$)",
        "Valor em Estoque (R$)",
        "Retirada Pendente (un.)",
        "ID Interno",
    ]
    rows = []
    for p in products:
        pid = int(p["product_id"])
        stock = int(p.get("stock") or 0)
        min_stock = int(p.get("min_stock") or 0)
        price = float(p.get("price") or 0)
        status_info = _event_product_status({
            "stock": stock,
            "min_stock": min_stock,
            "product_active": p.get("product_active", True),
        })
        rows.append([
            _csv_cell(p.get("name")),
            int(sold_map.get(pid, 0)),
            stock,
            min_stock,
            _csv_cell(status_info["label"]),
            _csv_cell(p.get("sku")),
            _csv_cell(p.get("category")),
            _csv_fmt_brl(price),
            _csv_fmt_brl(stock * price),
            int(pending_map.get(pid, 0)),
            pid,
        ])

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_ev = re.sub(r"[^a-zA-Z0-9_-]+", "_", (event.get("name") or str(event_id)))[:40].strip("_") or str(event_id)
    fname = f"estoque_evento_{event_id}_{safe_ev}_{ts}.xlsx"
    return _xlsx_attachment_response(fname, header, rows, sheet_title="Estoque")


@app.route("/admin/eventos/<int:event_id>/estoque/export.csv")
@admin_required
def admin_event_stock_export_csv(event_id: int):
    """Compatibilidade: o download de estoque passou a ser .xlsx."""
    return redirect(
        url_for(
            "admin_event_stock_export_xlsx",
            event_id=event_id,
            **_forwarded_query_args("event_id"),
        )
    )


@app.route("/admin/eventos/<int:event_id>/estoque/<int:product_id>")
@admin_required
def admin_event_stock_product(event_id: int, product_id: int):
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))
    product = get_product_in_event(event_id, product_id)
    if product is None:
        flash(f"Produto não encontrado neste {_op_noun(event, lower=True)}.", "error")
        return redirect(url_for("admin_event_stock", event_id=event_id))

    pending_units = pending_delivery_units_by_product_for_event(
        event_id, product_ids=[product_id],
    ).get(int(product_id), 0)
    product = dict(product)
    product["pending_delivery_units"] = int(pending_units)
    product = _attach_promo_name_to_product(product, event_id)

    pedido = (request.args.get("pedido") or "").strip()
    tipo_raw = (request.args.get("tipo") or "").strip().lower()
    valid_tipo = frozenset({"todos", "entrada", "venda", "saida", "pendente"})
    tipo_norm = tipo_raw if tipo_raw in valid_tipo else "todos"
    movement_type_api = None if tipo_norm == "todos" else tipo_norm

    event_sellers_rows = list_event_sellers(event_id)
    event_seller_ids = {int(s["id"]) for s in event_sellers_rows}
    seller_raw = _parse_int(request.args.get("vendedor"), 0)
    seller_filter = seller_raw if seller_raw > 0 and seller_raw in event_seller_ids else None
    if seller_raw > 0 and seller_filter is None:
        seller_raw = 0

    per_page = EVENT_PRODUCT_MOVEMENTS_PER_PAGE
    page = max(1, _parse_int(request.args.get("page"), 1))

    total = count_event_product_ledger(
        event_id,
        product_id,
        movement_type=movement_type_api,
        reference=pedido or None,
        seller_id=seller_filter,
    )
    total_pages = max(1, (total + per_page - 1) // per_page) if total > 0 else 1
    page = min(page, total_pages)
    offset = (page - 1) * per_page

    movements = list_event_product_ledger(
        event_id,
        product_id,
        movement_type=movement_type_api,
        reference=pedido or None,
        seller_id=seller_filter,
        limit=per_page,
        offset=offset,
    )

    showing_from = offset + 1 if total > 0 else 0
    showing_to = min(offset + len(movements), total) if total > 0 else 0

    filters = {
        "pedido": pedido,
        "tipo": tipo_norm,
        "vendedor": seller_raw,
    }
    pagination = {
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "showing_from": showing_from,
        "showing_to": showing_to,
    }

    return render_template(
        "admin/event_stock_product.html",
        event=event,
        product=product,
        movements=movements,
        movement_filter_sellers=event_sellers_rows,
        filters=filters,
        pagination=pagination,
        active_event_tab="estoque",
        **_admin_shell_context(active_section="eventos"),
    )


def _erp_lookup_hint(err: str | None) -> str:
    """Complemento da mensagem de "produto não encontrado" conforme a falha no ERP."""
    if err == "erp_config":
        return (
            " A busca no ERP está indisponível: confira SANKHYA_* no .env "
            "(endereço, login e SANKHYA_PRODUCT_FILTER_PARAM; detalhes no log do sistema) "
            "ou rode a sincronização do catálogo."
        )
    if err == "erp_offline":
        return " O ERP não respondeu (sem internet?)."
    if err == "erp_rejected":
        return " O ERP recusou a consulta; veja o log do sistema."
    return ""


def _product_lookup_failed_message(q: str, err: str | None) -> str:
    """Mensagem de "não achei" para um código digitado no admin."""
    if err == "erp_no_price":
        return (
            f"Produto \"{q}\" existe no ERP, mas sem preço na empresa configurada (CODEMP). "
            "Ele foi cadastrado inativo e é ativado sozinho quando a sincronização do catálogo "
            "trouxer o preço (ou defina o preço e ative-o na biblioteca)."
        )
    return f"Produto \"{q}\" não encontrado no catálogo nem no ERP." + _erp_lookup_hint(err)


def _find_or_fetch_product(sku_or_id: str) -> tuple[dict | None, bool, str | None]:
    """Localiza um produto por SKU/CODPROD/ID, buscando no ERP quando ausente aqui.

    Retorna ``(produto_dict | None, veio_do_erp, erro)``. ``erro``:
    ``"erp_config"`` (busca avulsa não configurada), ``"erp_offline"``,
    ``"erp_rejected"`` ou ``"erp_no_price"`` (veio sem preço e ficou inativo);
    ``None`` quando a consulta rodou (achando ou não).
    """
    q = (sku_or_id or "").strip()
    if not q:
        return None, False, None

    local = find_product_by_sku_or_id(q)
    if local is not None:
        return local, False, None

    if not q.lstrip("#").strip().isdigit():
        return None, False, None
    if not sankhya_api.is_configured() or not sankhya_api.product_filter_param():
        return None, False, "erp_config"
    try:
        local_id = erp_sync.import_single_product(q)
    except sankhya_api.SankhyaConfigError as exc:
        app.logger.warning("Busca do produto %s no ERP: %s", q, exc)
        return None, False, "erp_config"
    except sankhya_api.SankhyaNetworkError:
        return None, False, "erp_offline"
    except sankhya_api.SankhyaError as exc:
        app.logger.warning("Busca do produto %s no ERP falhou: %s", q, exc)
        return None, False, "erp_rejected"
    if local_id is None:
        return None, False, None
    if product_awaiting_erp_price(local_id):
        return None, False, "erp_no_price"
    saved = get_product(local_id)
    if saved is None:
        return None, False, None
    saved = dict(saved, name=saved.get("nome"))
    app.logger.info("Produto importado do ERP: CODPROD=%s id=%s", q, local_id)
    return saved, True, None


@app.route("/admin/eventos/<int:event_id>/produtos/adicionar", methods=["POST"])
@admin_required
@require_event_ops_open
def admin_event_add_product(event_id: int):
    event = _event_or_404(event_id)
    preserved = _event_stock_return_filters_from_form()
    if event is None:
        return redirect(url_for("admin_events"))
    q = (request.form.get("sku_or_id") or "").strip()
    if not q:
        flash("Informe o SKU ou ID do produto.", "error")
        return redirect(_url_for_admin_event_stock_list(event_id, preserved))
    product, from_erp, lookup_err = _find_or_fetch_product(q)
    if product is None:
        flash(_product_lookup_failed_message(q, lookup_err), "error")
        return redirect(_url_for_admin_event_stock_list(event_id, preserved))
    try:
        add_product_to_event(event_id, int(product["id"]), 0)
        suffix = " (importado do ERP)" if from_erp else ""
        flash(f"Produto \"{product['name']}\" adicionado ao {_op_noun(event, lower=True)}{suffix}.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(_url_for_admin_event_stock_list(event_id, preserved, page_override=1))


def _flash_image_cache_stats(stats: dict) -> None:
    ok = int(stats.get("ok") or 0)
    skip = int(stats.get("skip") or 0)
    fail = int(stats.get("fail") or 0)
    if ok:
        flash(
            f"{ok} imagem(ns) salva(s) neste computador. "
            f"{skip} já estavam locais. {fail} falha(s).",
            "success" if fail == 0 else "error",
        )
    elif fail:
        flash(
            f"Nenhuma imagem nova foi salva ({fail} falha(s)). "
            "Verifique a internet e os endereços das imagens.",
            "error",
        )
    else:
        flash(
            f"Todas as imagens já estavam salvas neste computador ({skip} produto(s)).",
            "success",
        )


@app.route("/admin/eventos/<int:event_id>/produtos/baixar-imagens", methods=["POST"])
@admin_required
def admin_event_cache_product_images(event_id: int):
    """Copia as fotos remotas para disco, para o catálogo funcionar sem internet."""
    event = _event_or_404(event_id)
    preserved = _event_stock_return_filters_from_form()
    if event is None:
        return redirect(url_for("admin_events"))
    try:
        stats = product_images.cache_images_for_event(event_id)
    except Exception:
        app.logger.exception("Falha ao baixar imagens do evento %s", event_id)
        flash("Não foi possível baixar as imagens. Tente novamente com internet.", "error")
        return redirect(_url_for_admin_event_stock_list(event_id, preserved))
    _flash_image_cache_stats(stats)
    return redirect(_url_for_admin_event_stock_list(event_id, preserved))


@app.route("/admin/produtos/baixar-imagens", methods=["POST"])
@admin_required
def admin_products_cache_images():
    """Copia as fotos de toda a biblioteca para disco (preparação do evento)."""
    try:
        stats = product_images.cache_images_for_catalog()
    except Exception:
        app.logger.exception("Falha ao baixar imagens da biblioteca")
        flash("Não foi possível baixar as imagens. Tente novamente com internet.", "error")
        return redirect(url_for("admin_products"))
    _flash_image_cache_stats(stats)
    return redirect(url_for("admin_products"))


@app.route("/admin/catalogo/sincronizar-erp", methods=["POST"])
@admin_required
def admin_sync_catalog_erp():
    """Sincroniza nomes/categoria/marca/preço/imagem com o ERP, em segundo plano.

    NÃO altera estoque, vendas, promoções nem vínculos com eventos. Produto que
    sumiu da lista do ERP continua ativo aqui.
    """
    missing = sankhya_api.missing_config()
    if missing:
        flash(
            "Integração ERP não configurada. Preencha no .env: " + ", ".join(missing),
            "error",
        )
        return _redirect_back_admin()
    run_id = erp_sync.start_catalog_sync_async()
    if run_id is None:
        flash("Já existe uma sincronização do catálogo em andamento.", "info")
    else:
        flash(
            "Sincronização do catálogo com o ERP iniciada. Acompanhe o resultado em "
            "Integração ERP. Estoque e eventos não são alterados.",
            "success",
        )
    return _redirect_back_admin()


# ---------------------------------------------------------------------------
# Painel administrativo — integração ERP
# ---------------------------------------------------------------------------

_erp_worker_boot_lock = threading.Lock()
_erp_worker_booted = False


@app.before_request
def _boot_erp_worker():
    """Sobe o worker da fila e as miniaturas no primeiro request (o pai do
    reloader nunca atende)."""
    global _erp_worker_booted
    if _erp_worker_booted:
        return None
    with _erp_worker_boot_lock:
        if not _erp_worker_booted:
            _erp_worker_booted = True
            try:
                for problem in sankhya_api.url_problems():
                    app.logger.warning("Integração ERP: %s", problem)
                erp_db.abandon_running_syncs()
                erp_sync.start_worker()
            except Exception:
                app.logger.exception("Não foi possível iniciar o worker da fila ERP")
            try:
                # Miniaturas WebP dos cards para as fotos gravadas antes delas.
                product_images.start_thumbnail_backfill()
            except Exception:
                app.logger.exception("Não foi possível iniciar a geração das miniaturas")
    return None


app.add_template_global(erp_db.ERP_STATUS_LABELS, "erp_status_labels")


@app.route("/admin/integracao-erp")
@admin_required
def admin_erp():
    settings = erp_db.get_erp_settings()
    return render_template(
        "admin/erp.html",
        settings=settings,
        payment_grid=erp_db.payment_type_grid(),
        sankhya_payment_types=erp_db.list_sankhya_payment_types(),
        sankhya_payment_types_synced_at=erp_db.sankhya_payment_types_synced_at(),
        unused_payment_types=erp_db.unused_sankhya_payment_types(),
        sellers_without_codvend=list_sellers_without_codvend(),
        **_admin_erp_live_context(settings),
        **_admin_shell_context(active_section="erp"),
    )


#: Blocos da página Integração ERP que se atualizam sozinhos (partial de cada um).
ERP_LIVE_BLOCKS = {
    "status": "admin/partials/erp_status.html",
    "fila": "admin/partials/erp_queue.html",
    "historico": "admin/partials/erp_history.html",
    "catalogo": "admin/partials/erp_catalog.html",
}


def _admin_erp_live_context(settings: dict | None = None) -> dict:
    """Dados dos blocos ao vivo: situação, fila de envio, histórico e catálogo."""
    settings = settings or erp_db.get_erp_settings()
    status_filter = (request.args.get("status") or "").strip().lower()
    if status_filter not in erp_db.OUTBOX_STATUS_LABELS:
        status_filter = ""
    events_unsent = []
    for ev in list_events(include_archived=True):
        n = erp_db.count_unsent_orders(int(ev["id"]))
        if n:
            events_unsent.append({"id": int(ev["id"]), "name": ev["name"], "count": n})
    counts = erp_db.outbox_counts()
    sync_running = erp_sync.catalog_sync_running()
    return {
        "api_missing": sankhya_api.missing_config(),
        "api_url_problems": sankhya_api.url_problems(),
        "api_base_url": sankhya_api.redact(sankhya_api.base_url()),
        "product_filter_param": sankhya_api.product_filter_param(),
        "client_filter_param": sankhya_api.client_filter_param(),
        "readiness": erp_db.orders_readiness(settings),
        "queue_pause": erp_sync.queue_pause(),
        "counts": counts,
        "jobs": erp_db.list_outbox(status_filter or None, limit=200),
        "status_filter": status_filter,
        "outbox_status_labels": erp_db.OUTBOX_STATUS_LABELS,
        "sync_runs": erp_db.list_sync_runs(10),
        "sync_running": sync_running,
        "link_summary": get_product_erp_link_summary(50),
        "unsent_total": erp_db.count_unsent_orders(),
        "codvol_missing": erp_db.products_missing_codvol_in_queue(),
        "known_codvols": erp_db.known_codvols(),
        "events_unsent": events_unsent,
        # Algo andando: o painel consulta com mais frequência.
        "live_active": bool(sync_running or counts.get("processando") or counts.get("pendente")),
    }


@app.route("/admin/integracao-erp/ao-vivo")
@admin_required
def admin_erp_live():
    """Blocos da página Integração ERP já renderizados, para o painel ao vivo.

    ``?avisos=1`` (logo depois de uma ação do painel) devolve também as
    mensagens que a ação deixou, que de outro modo só apareceriam ao recarregar.
    """
    ctx = _admin_erp_live_context()
    payload = {
        "blocks": {
            name: render_template(template, **ctx) for name, template in ERP_LIVE_BLOCKS.items()
        },
        "active": ctx["live_active"],
    }
    if request.args.get("avisos") == "1":
        payload["flashes"] = [
            {"category": cat, "message": msg}
            for cat, msg in get_flashed_messages(with_categories=True)
        ]
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/admin/integracao-erp/configuracao", methods=["POST"])
@admin_required
def admin_erp_settings():
    form = request.form
    values = {
        "codemp": (form.get("codemp") or "").strip(),
        "codvend": (form.get("codvend") or "").strip(),
        "codlocalorig": (form.get("codlocalorig") or "").strip(),
        "orders_enabled": "1" if form.get("orders_enabled") else "0",
    }
    for key, label in (("codemp", "CODEMP"), ("codvend", "CODVEND"), ("codlocalorig", "CODLOCALORIG")):
        if values[key] and not values[key].isdigit():
            flash(f"{label} deve ter só números.", "error")
            return redirect(url_for("admin_erp"))
    erp_db.save_erp_settings(values)
    missing = erp_db.orders_readiness()
    if values["orders_enabled"] == "1" and missing:
        flash(
            "Configuração salva, mas o envio de pedidos só começa quando estiver completo: "
            + ", ".join(m for m in missing if m != "Envio de pedidos ativado") + ".",
            "warning",
        )
    else:
        flash("Configuração da integração salva.", "success")
    erp_sync.notify_worker()
    return redirect(url_for("admin_erp"))


@app.route("/admin/integracao-erp/tipos-negociacao", methods=["POST"])
@admin_required
def admin_erp_payment_types():
    rows = []
    for item in erp_db.payment_type_grid():
        rows.append({
            "payment_method": item["payment_method"],
            "installments": item["installments"],
            "codtipvenda": request.form.get(f"code_{item['key']}") or "",
            "description": request.form.get(f"desc_{item['key']}") or "",
        })
    try:
        saved = erp_db.save_payment_types(rows)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin_erp") + "#tipos-negociacao")
    flash(f"{saved} tipo(s) de negociação salvo(s).", "success")
    erp_sync.notify_worker()
    return redirect(url_for("admin_erp") + "#tipos-negociacao")


@app.route("/admin/integracao-erp/tipos-negociacao/atualizar", methods=["POST"])
@admin_required
def admin_erp_payment_types_refresh():
    """Esvazia a fila de tipos de negociação do ERP e confirma o que gravou."""
    try:
        result = erp_sync.sync_payment_types()
    except (sankhya_api.SankhyaError, ValueError) as exc:
        flash(f"Não foi possível consultar os tipos de negociação no ERP: {exc}", "error")
        return redirect(url_for("admin_erp") + "#tipos-negociacao")
    flash(result["message"], "warning" if result["in_use_inactive"] else "success")
    erp_sync.notify_worker()
    return redirect(url_for("admin_erp") + "#tipos-negociacao")


@app.route("/admin/integracao-erp/fila/<int:job_id>/reenviar", methods=["POST"])
@admin_required
def admin_erp_job_retry(job_id: int):
    try:
        erp_db.retry_job(job_id)
        flash("Item recolocado na fila; o envio acontece em instantes.", "success")
        erp_sync.notify_worker()
    except ValueError as exc:
        flash(str(exc), "error")
    return _redirect_back(url_for("admin_erp") + "#fila")


@app.route("/admin/integracao-erp/fila/<int:job_id>/descartar", methods=["POST"])
@admin_required
def admin_erp_job_discard(job_id: int):
    try:
        erp_db.mark_job_discarded(
            job_id, f"Retirado da fila por {_current_admin_user()}."
        )
        flash("Item retirado da fila.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return _redirect_back(url_for("admin_erp") + "#fila")


@app.route("/admin/integracao-erp/enviar-agora", methods=["POST"])
@admin_required
def admin_erp_process_now():
    erp_sync.resume_queue()  # admin corrigiu o login/endereço: não espera a pausa
    erp_sync.notify_worker()
    flash("Envio da fila solicitado. Atualize a página em alguns segundos.", "success")
    return redirect(url_for("admin_erp") + "#fila")


@app.route("/admin/integracao-erp/testar-conexao", methods=["POST"])
@admin_required
def admin_erp_test_connection():
    """Login novo no gateway, com o motivo exato quando falha."""
    target = url_for("admin_erp")
    missing = sankhya_api.missing_config()
    if missing:
        flash("Integração ERP não configurada. Preencha no .env: " + ", ".join(missing), "error")
        return redirect(target)
    try:
        elapsed = sankhya_api.check_connection()
    except sankhya_api.SankhyaAuthError as exc:
        flash(f"O gateway respondeu, mas recusou o login. {exc}", "error")
    except sankhya_api.SankhyaConfigError as exc:
        flash(f"Endereço do gateway errado. {exc}", "error")
    except sankhya_api.SankhyaNetworkError as exc:
        flash(f"Sem acesso ao gateway (fora do ar ou sem internet). {exc}", "error")
    except sankhya_api.SankhyaError as exc:
        flash(f"Falha ao testar a conexão. {exc}", "error")
    else:
        flash(f"Conexão OK: o ERP aceitou o login ({elapsed:.1f} s).", "success")
        # Acesso confirmado: a fila não precisa esperar uma pausa de login.
        erp_sync.resume_queue()
        erp_sync.notify_worker()
    return redirect(target)


@app.route("/admin/integracao-erp/enviar-historico", methods=["POST"])
@admin_required
def admin_erp_enqueue_history():
    raw_event = (request.form.get("event_id") or "").strip()
    event_id = int(raw_event) if raw_event.isdigit() else None
    n = erp_db.enqueue_unsent_orders(event_id)
    if n:
        flash(
            f"{n} venda(s) anterior(es) à integração colocada(s) na fila do ERP.",
            "success",
        )
        erp_sync.notify_worker()
    else:
        flash("Nenhuma venda confirmada pendente de envio.", "info")
    return redirect(url_for("admin_erp") + "#historico")


@app.route("/admin/integracao-erp/produtos/<int:product_id>/codprod", methods=["POST"])
@admin_required
def admin_erp_link_product(product_id: int):
    raw = (request.form.get("codprod") or "").strip()
    if raw and not raw.isdigit():
        flash("O CODPROD deve ter só números.", "error")
        return _redirect_back(url_for("admin_erp") + "#vinculos")
    try:
        set_product_erp_codprod(product_id, int(raw) if raw else None)
    except ValueError as exc:
        flash(str(exc), "error")
        return _redirect_back(url_for("admin_erp") + "#vinculos")
    flash(
        f"Produto #{product_id} vinculado ao CODPROD {raw}." if raw
        else f"Vínculo ERP do produto #{product_id} removido.",
        "success",
    )
    return _redirect_back(url_for("admin_erp") + "#vinculos")


@app.route("/admin/integracao-erp/produtos/unidade", methods=["POST"])
@admin_required
def admin_erp_set_codvol():
    """Unidade (CODVOL) informada à mão, enquanto o cadastro não vem do ERP.

    Aceita ``product_id`` (lista de pendências) ou ``produto`` (CODPROD/SKU
    digitado). Pedidos parados só por isso voltam para a fila.
    """
    target = url_for("admin_erp") + "#fila"
    pid = _parse_int(request.form.get("product_id") or "", 0)
    if pid <= 0:
        code = (request.form.get("produto") or "").strip()
        pid = erp_db.find_product_id_for_code(code) or 0
        if not pid:
            flash(f'Produto "{code}" não encontrado na biblioteca.', "error")
            return _redirect_back(target)
    product = get_product(pid)
    if product is None:
        flash("Produto não encontrado.", "error")
        return _redirect_back(target)
    try:
        unit = set_product_erp_codvol(int(product["id"]), request.form.get("codvol") or "")
    except ValueError as exc:
        flash(str(exc), "error")
        return _redirect_back(target)
    requeued = erp_db.requeue_jobs_blocked_by_codvol(int(product["id"]))
    label = product.get("erp_codprod") or product.get("sku") or product["id"]
    msg = f"Unidade {unit} gravada em {label} ({product.get('nome') or 'produto'})."
    if requeued:
        msg += f" {requeued} pedido(s) voltaram para a fila."
        erp_sync.notify_worker()
    flash(msg, "success")
    return _redirect_back(target)


def _redirect_back_admin():
    """Redireciona para a página de origem (mesmo host) ou biblioteca de produtos."""
    return _redirect_back(url_for("admin_products"))


# Cabeçalhos comparados já normalizados por ``_norm_header`` (minúsculas, sem
# acento, pontuação vira espaço): "Cód. Produto" -> "cod produto".
_XLS_SKU_COL_NAMES = frozenset({
    "produto", "cod produto", "codigo", "codigo produto", "codigo do produto",
    "cod do produto", "sku", "cod prod", "codprod", "item", "ref", "referencia", "cod",
})

# "produto" / "item" batem no nome comercial; o código costuma estar em outra coluna.
_XLS_SKU_COL_NAMES_WEAK = frozenset({"produto", "item", "ref", "referencia"})

#: Palavras que fazem do cabeçalho uma coluna de quantidade ("Qtd. Disponível",
#: "Quantidade", "Saldo em estoque"...). "Un."/"Unidade" ficam de fora: costumam
#: ser a unidade de medida (UN, CX), não a quantidade.
_XLS_STOCK_WORDS = frozenset({
    "quantidade", "quantidades", "qtd", "qtde", "qtdade", "qtds", "quant", "qt",
    "estoque", "saldo", "disponivel", "disponiveis", "qty", "quantity", "stock",
})

#: Mesmo com palavra de quantidade, o cabeçalho é de outra coisa
#: ("Estoque mínimo", "Qtd. reservada", "Valor em estoque").
_XLS_STOCK_EXCLUDE_WORDS = frozenset({
    "minimo", "minima", "min", "maximo", "maxima", "max", "reservado", "reservada",
    "reserva", "pedido", "pedida", "vendido", "vendida", "vendidos", "vendidas",
    "valor", "vlr", "preco", "custo",
})

#: Colunas de preço, da mais para a menos específica do preço de venda:
#: 1) "Preço", "Preço unitário", "Preço de venda";
#: 2) "Valor unitário", "Vlr. unitário", "Valor unidade";
#: 3) só "Valor"/"Vlr.".
#: Na mesma prioridade vale a coluna mais à esquerda.
_XLS_PRICE_WORDS = frozenset({"preco", "precos", "price"})
_XLS_VALUE_WORDS = frozenset({"valor", "vlr", "vl", "value"})
_XLS_UNIT_WORDS = frozenset({"unitario", "unit", "unidade", "un", "unid"})

#: Nunca é o preço de venda unitário: total da linha, custo, impostos, descontos.
_XLS_PRICE_EXCLUDE_WORDS = frozenset({
    "total", "subtotal", "custo", "compra", "desconto", "desc", "ipi", "icms",
    "st", "frete", "margem", "markup", "comissao", "%",
})


def _norm_header(value) -> str:
    """Cabeçalho comparável: minúsculas, sem acento, pontuação vira espaço."""
    text = _header_text(value)
    if not text:
        return ""
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9%]+", " ", text).split())


def _spreadsheet_kind(file_bytes: bytes, filename: str = "") -> str:
    name = (filename or "").lower()
    if file_bytes.startswith(b"PK"):
        return "xlsx"
    if file_bytes.startswith(b"\xd0\xcf\x11\xe0"):
        return "xls"
    if name.endswith(".xlsx"):
        return "xlsx"
    return "xls"


def _grid_cell(rows: list[list], r: int, c: int):
    if r < 0 or r >= len(rows) or c < 0:
        return None
    row = rows[r]
    if c >= len(row):
        return None
    return row[c]


def _is_numeric_value(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _header_text(value) -> str:
    if value is None or isinstance(value, (datetime, date)):
        return ""
    return str(value).strip().lower()


def _is_stock_header(h: str) -> bool:
    """``h`` normalizado (``_norm_header``) é de coluna de quantidade em estoque."""
    words = set(h.split())
    return bool(words & _XLS_STOCK_WORDS) and not (words & _XLS_STOCK_EXCLUDE_WORDS)


def _price_header_rank(h: str) -> int | None:
    """Prioridade da coluna de preço (1 = melhor) ou ``None`` se não for preço."""
    words = h.split()
    if not words or set(words) & _XLS_PRICE_EXCLUDE_WORDS:
        return None
    if set(words) & _XLS_PRICE_WORDS:
        return 1
    if set(words) & _XLS_VALUE_WORDS:
        if set(words) & _XLS_UNIT_WORDS:
            return 2
        if len(words) == 1:
            return 3
    return None


def _is_sku_header(h: str, *, allow_weak: bool) -> bool:
    if not h or h not in _XLS_SKU_COL_NAMES:
        return False
    if not allow_weak and h in _XLS_SKU_COL_NAMES_WEAK:
        return False
    return True


def _value_to_str(value) -> str:
    """Converte célula para SKU/texto limpo, sem '.0' em inteiros."""
    if value is None or isinstance(value, (datetime, date, bool)):
        return ""
    if _is_numeric_value(value):
        return str(int(value)) if float(value) == int(value) else str(value)
    return str(value).strip()


def _value_to_int(value) -> int:
    """Converte célula de quantidade para inteiro (>=0). Retorna 0 se vazia/inválida."""
    if _is_numeric_value(value):
        return max(0, int(value))
    if isinstance(value, str):
        try:
            return max(0, int(float(value.strip().replace(",", "."))))
        except (ValueError, TypeError):
            return 0
    return 0


_BR_THOUSANDS_RE = re.compile(r"^\d{1,3}(\.\d{3})+$")


def _parse_br_number(text: str) -> float | None:
    """Número escrito como texto, no formato brasileiro ou com ponto decimal.

    Aceita "R$ 1.234,56", "R$105", "45,00", "1.234", "105.5", com espaços
    comuns ou não separáveis (planilhas exportadas costumam trazer ``\xa0``).
    Com ponto e vírgula, o último é o decimal; só vírgula = decimal; só ponto
    em grupos de 3 ("12.500") = milhar. ``None`` se não for um número.
    """
    raw = (text or "").replace("\xa0", " ").strip()
    raw = re.sub(r"(?i)^r\$", "", raw).strip()
    raw = raw.replace(" ", "")
    if not raw or not re.fullmatch(r"[\d.,]+", raw) or not re.search(r"\d", raw):
        return None
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        if raw.count(",") > 1:
            return None
        raw = raw.replace(",", ".")
    elif raw.count(".") > 1 or _BR_THOUSANDS_RE.fullmatch(raw):
        if not _BR_THOUSANDS_RE.fullmatch(raw):
            return None
        raw = raw.replace(".", "")
    try:
        return float(raw)
    except ValueError:
        return None


def _value_to_float_or_none(value) -> float | None:
    """Converte célula de preço (número ou texto como "R$ 1.234,56").

    Retorna None se vazia, zero ou inválida.
    """
    if _is_numeric_value(value):
        v = round(float(value), 2)
        return v if v > 0 else None
    if isinstance(value, str):
        parsed = _parse_br_number(value)
        if parsed is None:
            return None
        v = round(parsed, 2)
        return v if v > 0 else None
    return None


def _load_spreadsheet_grids(file_bytes: bytes, filename: str = "") -> list[list[list]]:
    """Carrega cada aba como grade de valores Python (linhas × colunas)."""
    kind = _spreadsheet_kind(file_bytes, filename)
    grids: list[list[list]] = []
    if kind == "xlsx":
        wb = _load_xlsx_workbook(file_bytes)
        try:
            for ws in wb.worksheets:
                rows = [list(row) for row in ws.iter_rows(values_only=True)]
                if rows:
                    grids.append(rows)
        finally:
            wb.close()
        return grids
    if not _XLRD_AVAILABLE:
        try:
            _pip_install("xlrd>=2.0.1,<3")
            import xlrd as _xlrd_mod
            globals()["_xlrd"] = _xlrd_mod
            globals()["_XLRD_AVAILABLE"] = True
        except Exception as exc:
            raise RuntimeError(
                "Biblioteca xlrd não instalada. "
                f"Execute: {sys.executable} -m pip install xlrd"
            ) from exc
    wb = _xlrd.open_workbook(file_contents=file_bytes)
    for sheet_idx in range(wb.nsheets):
        sh = wb.sheet_by_index(sheet_idx)
        rows = []
        for r in range(sh.nrows):
            rows.append([sh.cell_value(r, c) if sh.cell_type(r, c) not in (0, 6) else None
                         for c in range(sh.ncols)])
        if rows:
            grids.append(rows)
    return grids


#: Layout legado, só para planilha SEM linha de cabeçalho: código na coluna A e
#: quantidade na coluna E (preço não é lido).
_XLS_LEGACY_STOCK_COL = 4


def _detect_spreadsheet_header(rows: list[list]) -> tuple[int | None, int, int | None, int | None]:
    """Retorna (header_row, sku_col, price_col_or_none, stock_col_or_none).

    Procura nas primeiras 15 linhas o cabeçalho com a coluna do código/SKU e,
    na mesma linha, as colunas de preço e de quantidade **pelo nome**:

    - quantidade: "Quantidade", "Qtd.", "Qtde", "Estoque", "Saldo",
      "Qtd. Disponível"... (``_is_stock_header``); "Estoque mínimo" e afins não
      contam. Sem esse cabeçalho, não há quantidade: o produto entra com 0;
    - preço: "Preço" > "Valor unitário"/"Vlr. unitário" > "Valor"
      (``_price_header_rank``); totais, custo e impostos nunca são preço.

    Uma coluna serve a um papel só. Sem nenhuma linha de cabeçalho, vale o
    layout legado (código na A, quantidade na E, sem preço).
    """
    nrows = len(rows)
    ncols = max((len(row) for row in rows), default=0)
    for r in range(min(15, nrows)):
        heads = [_norm_header(_grid_cell(rows, r, c)) for c in range(ncols)]
        sku_col = next((c for c, h in enumerate(heads) if _is_sku_header(h, allow_weak=False)), None)
        if sku_col is None:
            sku_col = next((c for c, h in enumerate(heads) if _is_sku_header(h, allow_weak=True)), None)
        if sku_col is None:
            continue
        stock_col = next(
            (c for c, h in enumerate(heads) if c != sku_col and _is_stock_header(h)), None,
        )
        ranked = [
            (rank, c) for c, h in enumerate(heads)
            if c not in (sku_col, stock_col) and (rank := _price_header_rank(h)) is not None
        ]
        price_col = min(ranked)[1] if ranked else None
        return r, sku_col, price_col, stock_col
    return None, 0, None, _XLS_LEGACY_STOCK_COL


def _column_label(rows: list[list], header_row: int | None, col: int | None) -> str:
    """Ex.: «E (Preço)»; para o relatório da importação."""
    if col is None:
        return ""
    letter = ""
    n = col + 1
    while n:
        n, rem = divmod(n - 1, 26)
        letter = chr(65 + rem) + letter
    title = str(_grid_cell(rows, header_row, col) or "").strip() if header_row is not None else ""
    return f"{letter} ({title})" if title else letter


def _parse_xls_sku_stock(
    file_bytes: bytes,
    filename: str = "",
    columns: list[dict] | None = None,
) -> list[tuple[str, float | None, int]]:
    """Lê planilha .xls/.xlsx e retorna lista de (sku, unit_price_or_none, stock_qty).

    As colunas vêm dos cabeçalhos (``_detect_spreadsheet_header``): sem coluna
    de quantidade reconhecida, a quantidade é 0; sem coluna de preço, o preço é
    ``None``. Planilha sem cabeçalho usa o layout legado (código A, quantidade E).

    ``columns``: recebe, por aba lida, as colunas usadas (``sku``, ``price``,
    ``stock``, ``header``) para a mensagem de resultado.

    SKUs duplicados somam estoque; o preço é o da primeira aparição.
    """
    grids = _load_spreadsheet_grids(file_bytes, filename)
    result_qty: dict[str, int] = {}
    result_price: dict[str, float | None] = {}

    for rows in grids:
        if len(rows) < 2:
            continue
        ncols = max((len(row) for row in rows), default=0)
        header_row, sku_col, price_col, stock_col = _detect_spreadsheet_header(rows)

        if header_row is not None:
            data_start = header_row + 1
        else:
            data_start = None
            for r in range(len(rows)):
                if _is_numeric_value(_grid_cell(rows, r, 0)):
                    data_start = r
                    break
            if data_start is None:
                continue

        found = 0
        unreadable: list[str] = []  # célula de preço preenchida que não virou número
        for r in range(data_start, len(rows)):
            sku = _value_to_str(_grid_cell(rows, r, sku_col))
            if not sku or " " in sku or not re.search(r"\d", sku):
                continue
            found += 1
            qty = 0
            if stock_col is not None and stock_col < ncols:
                qty = _value_to_int(_grid_cell(rows, r, stock_col))
            price = None
            if price_col is not None and price_col < ncols:
                cell = _grid_cell(rows, r, price_col)
                price = _value_to_float_or_none(cell)
                if price is None and str(cell if cell is not None else "").strip() and sku not in unreadable:
                    unreadable.append(sku)
            if sku in result_qty:
                result_qty[sku] += qty
                if result_price.get(sku) is None:
                    result_price[sku] = price  # 1ª linha sem preço legível: vale a seguinte
            else:
                result_qty[sku] = qty
                result_price[sku] = price
        if columns is not None and found:
            columns.append({
                "header": header_row is not None,
                "sku": _column_label(rows, header_row, sku_col),
                "price": _column_label(rows, header_row, price_col),
                "stock": _column_label(rows, header_row, stock_col if stock_col is not None and stock_col < ncols else None),
                "price_unreadable": [k for k in unreadable if result_price.get(k) is None],
            })

    return [(sku, result_price.get(sku), qty) for sku, qty in result_qty.items()]


def _import_columns_message(columns: list[dict]) -> str:
    """"Colunas lidas: código A (Produto) · preço E (Preço) · quantidade: ..." da 1ª aba."""
    if not columns:
        return ""
    col = columns[0]
    qty = (
        f"quantidade {col['stock']}" if col["stock"]
        else "quantidade: nenhuma coluna de quantidade (os produtos novos entram com estoque 0)"
    )
    price = f"preço {col['price']}" if col["price"] else "preço: nenhuma coluna de preço (mantido)"
    origin = "" if col["header"] else " (planilha sem cabeçalho: layout padrão)"
    msg = f"Colunas lidas{origin}: código {col['sku']} · {price} · {qty}"
    bad = [k for c in columns for k in c.get("price_unreadable") or []]
    if bad:
        shown = ", ".join(bad[:10]) + ("…" if len(bad) > 10 else "")
        msg += (
            f". Atenção: {len(bad)} preço(s) não puderam ser lidos e ficaram como estavam "
            f"(códigos {shown})"
        )
    return msg


def _apply_imported_event_price(
    event_id: int,
    product_id: int,
    unit_price: float | None,
) -> bool:
    """Grava o preço da planilha no evento. Retorna True se o valor de venda mudou.

    Célula vazia/zero não altera nada. Se o evento já tem o mesmo preço como
    override, a importação é idempotente (não regrava).
    """
    if unit_price is None:
        return False
    new_price = round(float(unit_price), 2)
    if new_price <= 0:
        return False
    existing = get_product_in_event(event_id, product_id)
    if existing is None:
        return False
    current = round(float(existing.get("preco") or 0), 2)
    already_override = bool(existing.get("preco_evento_override"))
    if already_override and current == new_price:
        return False
    if not update_event_product_price(event_id, product_id, new_price):
        return False
    return (not already_override) or current != new_price


@app.route("/admin/eventos/<int:event_id>/produtos/importar-xls", methods=["POST"])
@admin_required
@require_event_ops_open
def admin_event_import_xls(event_id: int):
    """Importa produtos em lote para o evento a partir de planilha .xls ou .xlsx.

    Lê o código/SKU, o preço unitário e a quantidade pelos cabeçalhos das
    colunas (``_detect_spreadsheet_header``): Preço / Valor unitário,
    Quantidade / Qtd. / Estoque / Saldo... A mensagem de resultado diz quais
    colunas foram usadas.
    Preço reconhecido atualiza o preço de venda no evento, inclusive em produtos
    que já estavam no catálogo do evento. Sem cabeçalho de preço, produtos novos
    herdam o preço-base da biblioteca e os já existentes mantêm o preço atual.
    Sem quantidade reconhecida, adiciona o produto com estoque 0.
    SKUs duplicados na planilha têm seus estoques somados antes da importação.
    Produto já no evento: a quantidade da planilha entra como entrada (soma ao estoque atual).
    """
    event = _event_or_404(event_id)
    preserved = _event_stock_return_filters_from_form()
    if event is None:
        return redirect(url_for("admin_events"))

    uploaded = request.files.get("xls_file")
    if not uploaded or not uploaded.filename:
        flash("Selecione uma planilha (.xls ou .xlsx) para importar.", "error")
        return redirect(_url_for_admin_event_stock_list(event_id, preserved))

    fname = (uploaded.filename or "").lower()
    if not (fname.endswith(".xls") or fname.endswith(".xlsx")):
        flash("Formato inválido. Envie um arquivo .xls ou .xlsx.", "error")
        return redirect(_url_for_admin_event_stock_list(event_id, preserved))

    try:
        file_bytes = uploaded.read()
        if len(file_bytes) > 5 * 1024 * 1024:
            flash("Arquivo muito grande (máx. 5 MB).", "error")
            return redirect(_url_for_admin_event_stock_list(event_id, preserved))
        sheet_columns: list[dict] = []
        sku_stock_pairs = _parse_xls_sku_stock(file_bytes, uploaded.filename or "", sheet_columns)
    except RuntimeError as exc:
        flash(str(exc), "error")
        return redirect(_url_for_admin_event_stock_list(event_id, preserved))
    except Exception as exc:
        flash(f"Não foi possível ler a planilha: {exc}", "error")
        return redirect(_url_for_admin_event_stock_list(event_id, preserved))

    if not sku_stock_pairs:
        flash(
            "Nenhum código de produto encontrado. A planilha precisa de uma coluna "
            "com cabeçalho de código (Produto, Código, SKU...) ou dos códigos na coluna A.",
            "error",
        )
        return redirect(_url_for_admin_event_stock_list(event_id, preserved))

    added: list[str] = []
    already: list[str] = []
    not_found: list[str] = []
    no_price: list[str] = []      # existem no ERP, mas sem preço (ficaram inativos)
    erp_fetched: list[str] = []   # SKUs trazidos do ERP na hora
    erp_lookup_err: str | None = None
    import_actor = _current_admin_user()
    total_units_imported = 0

    price_set_count = 0

    for sku, unit_price, qty in sku_stock_pairs:
        product, from_erp, lookup_err = _find_or_fetch_product(sku)
        if product is None and lookup_err == "erp_no_price":
            no_price.append(sku)
            continue
        if product is None:
            not_found.append(sku)
            erp_lookup_err = erp_lookup_err or lookup_err
            continue
        if from_erp:
            erp_fetched.append(sku)
        product_id = int(product["id"])
        try:
            add_product_to_event(
                event_id,
                product_id,
                qty,
                link_audit_reason=EVENT_IMPORT_XLS_MOTIVO_REF,
                link_audit_reference=None,
                created_by=import_actor,
            )
            if _apply_imported_event_price(event_id, product_id, unit_price):
                price_set_count += 1
            qty_label = f" ({qty} un.)" if qty > 0 else ""
            price_label = f" · R$ {unit_price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if unit_price is not None else ""
            added.append(f"{product['name']}{qty_label}{price_label}")
            total_units_imported += qty
        except ValueError:
            # Já no evento: soma a quantidade da planilha ao estoque atual e
            # atualiza o preço quando a planilha traz cabeçalho de preço.
            stock_applied = False
            if qty > 0:
                existing_ep = get_product_in_event(event_id, product_id)
                if existing_ep is not None:
                    try:
                        register_event_stock_entry(
                            event_id,
                            product_id,
                            qty,
                            reason=EVENT_IMPORT_XLS_MOTIVO_REF,
                            created_by=import_actor,
                        )
                        added.append(f"{product['name']} (+{qty} un.)")
                        total_units_imported += qty
                        stock_applied = True
                    except ValueError:
                        pass
            price_changed = _apply_imported_event_price(event_id, product_id, unit_price)
            if price_changed:
                price_set_count += 1
            if not stock_applied and not price_changed:
                already.append(product["name"])

    # Monta mensagem de resultado
    parts: list[str] = []
    if added:
        units_txt = f" · {total_units_imported} unidade(s) em estoque" if total_units_imported > 0 else ""
        parts.append(f"{len(added)} produto(s) adicionado(s) com sucesso{units_txt}")
    if price_set_count:
        parts.append(f"{price_set_count} preço(s) atualizado(s) no evento")
    if erp_fetched:
        parts.append(
            f"{len(erp_fetched)} produto(s) importado(s) do ERP e adicionado(s) ao catálogo: "
            f"{', '.join(erp_fetched[:5])}{'…' if len(erp_fetched) > 5 else ''}"
        )
    if already:
        parts.append(f"{len(already)} já estavam no evento")
    if not_found:
        short = not_found[:10]
        tail = "…" if len(not_found) > 10 else ""
        parts.append(
            f"{len(not_found)} código(s) não encontrado(s) no catálogo nem no ERP: "
            f"{', '.join(short)}{tail}.{_erp_lookup_hint(erp_lookup_err)}"
        )
    if no_price:
        parts.append(
            f"{len(no_price)} código(s) sem preço no ERP (cadastrado(s) inativo(s), "
            "ativados quando a sincronização trouxer o preço): "
            f"{', '.join(no_price[:10])}{'…' if len(no_price) > 10 else ''}"
        )

    summary = " · ".join(parts) if parts else "Nenhuma alteração realizada."
    columns_msg = _import_columns_message(sheet_columns)
    if columns_msg:
        summary = f"{summary}. {columns_msg}."
    category = (
        "success" if added or price_set_count
        else ("error" if (not_found or no_price) and not already else "info")
    )
    flash(summary, category)

    return redirect(_url_for_admin_event_stock_list(event_id, preserved, page_override=1))


@app.route("/admin/eventos/<int:event_id>/produtos/<int:product_id>/remover", methods=["POST"])
@admin_required
@require_event_ops_open
def admin_event_remove_product(event_id: int, product_id: int):
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))
    preserved = _event_stock_return_filters_from_form()
    remove_product_from_event(event_id, product_id)
    flash(f"Produto removido do {_op_noun(event, lower=True)}.", "warning")
    return redirect(_url_for_admin_event_stock_list(event_id, preserved))


@app.route("/admin/eventos/<int:event_id>/produtos/<int:product_id>/entrada", methods=["POST"])
@admin_required
@require_event_ops_open
def admin_event_stock_entry(event_id: int, product_id: int):
    if _event_or_404(event_id) is None:
        return redirect(url_for("admin_events"))
    qty = _parse_int(request.form.get("quantity"), 0)
    unit_cost = _parse_float(request.form.get("unit_cost"))
    reason = (request.form.get("reason") or "").strip() or None
    fallback = url_for("admin_event_stock_product", event_id=event_id, product_id=product_id)
    try:
        register_event_stock_entry(
            event_id,
            product_id,
            qty,
            unit_cost=unit_cost,
            reason=reason,
            created_by=_current_admin_user(),
        )
        message = f"Entrada de {qty} un. registrada."
        if _wants_json_response():
            return _json_event_stock_success(message, event_id, product_id)
        flash(message, "success")
    except ValueError as exc:
        if _wants_json_response():
            return jsonify({"error": str(exc)}), 400
        flash(str(exc), "error")
    return _redirect_back( fallback)


_BULK_STOCK_KINDS = {
    "entrada": {
        "register": "entry",
        "noun": "entrada",
        "empty": "Selecione ao menos um produto para adicionar estoque.",
        "done": "Entrada registrada em {count} produto(s), {units} unidade(s) no total.",
        "none": "Nenhuma entrada registrada.",
    },
    "saida": {
        "register": "exit",
        "noun": "saída",
        "empty": "Selecione ao menos um produto para registrar a saída.",
        "done": "Saída registrada em {count} produto(s), {units} unidade(s) no total.",
        "none": "Nenhuma saída registrada.",
    },
}


def _admin_event_stock_bulk_movement(event_id: int, kind: str):
    """Entrada ou saída de estoque para vários produtos do evento de uma só vez.

    Espera ``product_ids`` (lista de IDs marcados) + um campo ``qty_<id>`` por
    produto + um único ``reason`` (Motivo / Ref) aplicado a todos. Cada produto é
    movimentado de forma independente: os que falham (ex.: saída maior que o
    estoque) são listados no flash de erro sem desfazer os demais.
    """
    if _event_or_404(event_id) is None:
        return redirect(url_for("admin_events"))
    cfg = _BULK_STOCK_KINDS[kind]
    register = register_event_stock_entry if cfg["register"] == "entry" else register_event_stock_exit
    preserved = _event_stock_return_filters_from_form()
    fallback = _url_for_admin_event_stock_list(event_id, preserved)

    reason = (request.form.get("reason") or "").strip()
    if not reason:
        flash(f"Informe o Motivo / Ref. da {cfg['noun']} em lote.", "error")
        return redirect(fallback)

    product_ids = request.form.getlist("product_ids")
    if not product_ids:
        flash(cfg["empty"], "error")
        return redirect(fallback)

    admin_user = _current_admin_user()
    ok_count = 0
    ok_units = 0
    errors: list[str] = []
    for raw_id in product_ids:
        product_id = _parse_int(raw_id, 0)
        if product_id <= 0:
            continue
        qty = _parse_int(request.form.get(f"qty_{product_id}"), 0)
        if qty <= 0:
            errors.append(f"#{product_id}: quantidade inválida.")
            continue
        try:
            register(
                event_id,
                product_id,
                qty,
                reason=reason,
                created_by=admin_user,
            )
            ok_count += 1
            ok_units += qty
        except ValueError as exc:
            errors.append(f"#{product_id}: {exc}")

    if ok_count:
        flash(cfg["done"].format(count=ok_count, units=ok_units), "success")
    if errors:
        short = errors[:5]
        tail = f" (+{len(errors) - 5})" if len(errors) > 5 else ""
        flash("Falha em: " + "; ".join(short) + tail, "error")
    elif not ok_count:
        flash(cfg["none"], "error")

    return redirect(fallback)


@app.route("/admin/eventos/<int:event_id>/produtos/entrada-em-lote", methods=["POST"])
@admin_required
@require_event_ops_open
def admin_event_stock_bulk_entry(event_id: int):
    """Registra entrada de estoque para vários produtos do evento de uma só vez."""
    return _admin_event_stock_bulk_movement(event_id, "entrada")


@app.route("/admin/eventos/<int:event_id>/produtos/saida-em-lote", methods=["POST"])
@admin_required
@require_event_ops_open
def admin_event_stock_bulk_exit(event_id: int):
    """Registra saída de estoque para vários produtos do evento de uma só vez."""
    return _admin_event_stock_bulk_movement(event_id, "saida")


@app.route("/admin/eventos/<int:event_id>/produtos/<int:product_id>/saida", methods=["POST"])
@admin_required
@require_event_ops_open
def admin_event_stock_exit(event_id: int, product_id: int):
    if _event_or_404(event_id) is None:
        return redirect(url_for("admin_events"))
    qty = _parse_int(request.form.get("quantity"), 0)
    reason = (request.form.get("reason") or "").strip()
    fallback = url_for("admin_event_stock_product", event_id=event_id, product_id=product_id)
    try:
        register_event_stock_exit(
            event_id,
            product_id,
            qty,
            reason=reason or "Saída manual",
            created_by=_current_admin_user(),
        )
        message = f"Saída de {qty} un. registrada."
        if _wants_json_response():
            return _json_event_stock_success(message, event_id, product_id)
        flash(message, "success")
    except ValueError as exc:
        if _wants_json_response():
            return jsonify({"error": str(exc)}), 400
        flash(str(exc), "error")
    return _redirect_back( fallback)


@app.route("/admin/eventos/<int:event_id>/produtos/<int:product_id>/ajuste", methods=["POST"])
@admin_required
@require_event_ops_open
def admin_event_stock_adjust(event_id: int, product_id: int):
    if _event_or_404(event_id) is None:
        return redirect(url_for("admin_events"))
    new_qty = _parse_int(request.form.get("new_stock"), 0)
    reason = (request.form.get("reason") or "").strip()
    fallback = url_for("admin_event_stock_product", event_id=event_id, product_id=product_id)
    try:
        register_event_stock_adjustment(
            event_id,
            product_id,
            new_qty,
            reason=reason or "Ajuste manual",
            created_by=_current_admin_user(),
        )
        message = f"Estoque ajustado para {new_qty} un."
        if _wants_json_response():
            return _json_event_stock_success(message, event_id, product_id)
        flash(message, "success")
    except ValueError as exc:
        if _wants_json_response():
            return jsonify({"error": str(exc)}), 400
        flash(str(exc), "error")
    return _redirect_back( fallback)


@app.route("/admin/eventos/<int:event_id>/produtos/<int:product_id>/preco", methods=["POST"])
@admin_required
@require_event_ops_open
def admin_event_stock_price(event_id: int, product_id: int):
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))
    fallback = url_for("admin_event_stock_product", event_id=event_id, product_id=product_id)
    restore = (request.form.get("restore_base") or "").strip() in {"1", "true", "on"}
    noun_l = _op_noun(event, lower=True)
    if restore:
        stored = None
        message = f"Preço do {noun_l} restaurado para o valor da biblioteca."
    else:
        raw = (request.form.get("price") or "").strip().replace(",", ".")
        try:
            stored = round(float(raw), 2)
            if stored < 0:
                raise ValueError("negativo")
        except (TypeError, ValueError):
            if _wants_json_response():
                return jsonify({"error": "Informe um valor numérico válido para o preço."}), 400
            flash("Informe um valor numérico válido para o preço.", "error")
            return _redirect_back( fallback)
        message = (
            f"Preço do {noun_l} atualizado para R$ {stored:,.2f}"
            .replace(",", "X").replace(".", ",").replace("X", ".")
        )
    if update_event_product_price(event_id, product_id, stored):
        if _wants_json_response():
            return _json_event_stock_success(message, event_id, product_id)
        flash(message, "success")
    else:
        err = f"Não foi possível atualizar o preço neste {noun_l}."
        if _wants_json_response():
            return jsonify({"error": err}), 400
        flash(err, "error")
    return _redirect_back( fallback)


@app.route("/admin/eventos/<int:event_id>/produtos/<int:product_id>/minimo", methods=["POST"])
@admin_required
@require_event_ops_open
def admin_event_stock_min(event_id: int, product_id: int):
    if _event_or_404(event_id) is None:
        return redirect(url_for("admin_events"))
    min_val = _parse_int(request.form.get("min_stock"), 0)
    fallback = url_for("admin_event_stock_product", event_id=event_id, product_id=product_id)
    update_event_product_stock(
        event_id,
        product_id,
        _get_event_product_stock(event_id, product_id),
        min_val,
    )
    message = f"Estoque mínimo atualizado para {max(0, min_val)} un."
    if _wants_json_response():
        return _json_event_stock_success(message, event_id, product_id)
    flash(message, "success")
    return _redirect_back( fallback)


@app.route(
    "/admin/eventos/<int:event_id>/produtos/<int:product_id>/limite-entrega-pendente",
    methods=["POST"],
)
@admin_required
@require_event_ops_open
def admin_event_stock_backorder_limit(event_id: int, product_id: int):
    if _event_or_404(event_id) is None:
        return redirect(url_for("admin_events"))
    fallback = url_for("admin_event_stock_product", event_id=event_id, product_id=product_id)
    action = (request.form.get("backorder_action") or "set").strip().lower()
    if action == "unlimited":
        limit_val = -1
        message = "Limite de entregas pendentes removido (livre)."
    else:
        limit_val = max(0, _parse_int(request.form.get("backorder_limit"), 0))
        if limit_val == 0:
            message = "Entregas pendentes bloqueadas para este produto neste evento."
        else:
            message = f"Limite de entregas pendentes atualizado para {limit_val} un."
    update_event_product_backorder_limit(event_id, product_id, limit_val)
    if _wants_json_response():
        return _json_event_stock_success(message, event_id, product_id)
    flash(message, "success")
    return _redirect_back( fallback)


def _get_event_product_stock(event_id: int, product_id: int) -> int:
    """Lê o saldo atual de um produto no evento (helper interno)."""
    for ep in list_event_products(event_id):
        if int(ep["product_id"]) == int(product_id):
            return int(ep["stock"] or 0)
    return 0


# ---------------------------------------------------------------------------
# Eventos — sub-página Promoções
# ---------------------------------------------------------------------------

#: Valor de ``?modo=`` que troca a página de Promoções para a lista de brindes.
PROMO_MODE_GIFTS = "brindes"


@app.route("/admin/eventos/<int:event_id>/promocoes")
@admin_required
def admin_event_promotions(event_id: int):
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))
    mode = PROMO_MODE_GIFTS if request.args.get("modo") == PROMO_MODE_GIFTS else "descontos"
    promotions = list_promotions_for_event(event_id)
    gift_rules = list_gift_rules_for_event(event_id)
    gift_items = list_gift_items_for_event(event_id)
    images = {
        int(ep["product_id"]): ep.get("image") or ""
        for ep in list_event_products(event_id)
    }
    return render_template(
        "admin/event_promotions.html",
        event=event,
        mode=mode,
        promotions=promotions,
        gift_rules=gift_rules,
        gift_items=gift_items,
        product_images=images,
        rule_type_labels=RULE_TYPE_LABELS,
        active_event_tab="promocoes",
        **_admin_shell_context(active_section="eventos"),
    )


def _promo_rule_description(promo: dict) -> str:
    """Descrição legível da regra de promoção para planilha."""
    rt = promo.get("rule_type") or ""
    rv = float(promo.get("rule_value") or 0)
    mq = max(1, int(promo.get("min_qty") or 1))
    fq = max(0, int(promo.get("free_qty") or 0))
    label = RULE_TYPE_LABELS.get(rt, rt)
    if rt == "percent":
        pct = int(rv) if abs(rv - int(rv)) < 1e-9 else f"{rv:.1f}".replace(".", ",")
        return f"{label} — {pct}%"
    if rt == "fixed":
        return f"{label} — R$ {rv:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if rt == "bogo":
        buy_sku = (promo.get("bogo_buy_sku") or "").strip()
        free_sku = (promo.get("bogo_free_sku") or "").strip()
        if buy_sku and free_sku and buy_sku != free_sku:
            return f"{label} — Compre {mq} un. SKU {buy_sku}, leve {fq} un. SKU {free_sku} grátis"
        return f"{label} — Compre {mq}, Leve {mq + fq}"
    if rt in ("min_bundle", "exact_bundle"):
        brl = f"R$ {rv:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        if rt == "min_bundle":
            return f"{label} — a partir de {mq} un., {brl} cada"
        return f"{label} — {mq} un. por {brl}"
    return label


@app.route("/admin/eventos/<int:event_id>/promocoes/export.xlsx")
@admin_required
def admin_event_promotions_export_xlsx(event_id: int):
    """Exporta promoções do evento como planilha .xlsx no estilo da Dentsply."""
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))

    promotions = list_promotions_for_event_export(event_id)
    if not promotions:
        flash("Nenhuma promoção para exportar.", "info")
        return redirect(url_for("admin_event_promotions", event_id=event_id))

    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
    except ImportError:
        _pip_install("openpyxl>=3.1.0,<4")
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill, Border, Side

    wb = Workbook()
    ws = wb.active
    ws.title = "Promoções"

    # Estilos
    font_title = Font(name="Calibri", bold=True, size=14, color="FFFFFF")
    fill_title = PatternFill(start_color="0D47A1", end_color="0D47A1", fill_type="solid")
    font_header = Font(name="Calibri", bold=True, size=11, color="FFFFFF")
    fill_header = PatternFill(start_color="1565C0", end_color="1565C0", fill_type="solid")
    font_section = Font(name="Calibri", bold=True, size=12, color="0D47A1")
    fill_section = PatternFill(start_color="E3F2FD", end_color="E3F2FD", fill_type="solid")
    font_data = Font(name="Calibri", size=11)
    font_inactive = Font(name="Calibri", size=11, color="999999", italic=True)
    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")
    thin_border = Border(
        bottom=Side(style="thin", color="DDDDDD"),
    )
    brl_fmt = '#.##0,00'

    col_headers = ["Código", "Descrição", "Preço Evento (R$)"]
    ncols = len(col_headers)

    # Título do evento
    row_num = 1
    ws.merge_cells(start_row=row_num, start_column=1, end_row=row_num, end_column=ncols)
    cell = ws.cell(row=row_num, column=1, value=f"PROMOÇÕES — {(event.get('name') or '').upper()}")
    cell.font = font_title
    cell.fill = fill_title
    cell.alignment = align_center
    for c in range(2, ncols + 1):
        ws.cell(row=row_num, column=c).fill = fill_title
    row_num += 1

    for promo in promotions:
        row_num += 1
        ws.merge_cells(start_row=row_num, start_column=1, end_row=row_num, end_column=ncols)
        promo_title = promo.get("name") or "Promoção"
        rule_desc = _promo_rule_description(promo)
        status = "Ativa" if promo.get("active") else "Inativa"
        cell = ws.cell(row=row_num, column=1, value=f"{promo_title}  —  {rule_desc}  [{status}]")
        cell.font = font_section
        cell.fill = fill_section
        cell.alignment = align_left
        for c in range(2, ncols + 1):
            ws.cell(row=row_num, column=c).fill = fill_section
        row_num += 1

        # Cabeçalho de colunas
        for ci, hdr in enumerate(col_headers, start=1):
            cell = ws.cell(row=row_num, column=ci, value=hdr)
            cell.font = font_header
            cell.fill = fill_header
            cell.alignment = align_center
        row_num += 1

        products = promo.get("products") or []
        if not products:
            ws.merge_cells(start_row=row_num, start_column=1, end_row=row_num, end_column=ncols)
            cell = ws.cell(row=row_num, column=1, value="(sem produtos vinculados)")
            cell.font = font_inactive
            cell.alignment = align_center
            row_num += 1
            continue

        is_active = promo.get("active")
        for prod in products:
            font_row = font_data if is_active else font_inactive

            sku = prod.get("sku") or str(prod.get("product_id", ""))
            ws.cell(row=row_num, column=1, value=sku).font = font_row
            ws.cell(row=row_num, column=1).alignment = align_center

            ws.cell(row=row_num, column=2, value=prod.get("name", "")).font = font_row
            ws.cell(row=row_num, column=2).alignment = align_left

            ev_price = float(prod.get("event_price") or prod.get("library_price") or 0)
            c3 = ws.cell(row=row_num, column=3, value=ev_price)
            c3.font = font_row
            c3.alignment = align_right
            c3.number_format = brl_fmt

            for c in range(1, ncols + 1):
                ws.cell(row=row_num, column=c).border = thin_border

            row_num += 1

    # Ajuste de largura das colunas
    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 60
    ws.column_dimensions["C"].width = 22

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_ev = re.sub(r"[^a-zA-Z0-9_-]+", "_", (event.get("name") or str(event_id)))[:40].strip("_") or str(event_id)
    fname = f"promocoes_{event_id}_{safe_ev}_{ts}.xlsx"

    return Response(
        buf.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            "Cache-Control": "no-store",
        },
    )


def _parse_promotion_form_fields(form) -> tuple[str, float, int, int]:
    """Extrai parâmetros da regra a partir do POST do admin.

    Cada tipo de promoção usa campos com nomes distintos no formulário para evitar
    colisão entre seções ocultas (ex.: ``rule_value`` de percent vs. bundle).
    """
    rule_type = (form.get("rule_type") or "").strip()
    if rule_type in ("percent", "fixed"):
        rule_value = _form_float(form.get("rule_value_pf")) or 0.0
        min_qty = 1
        free_qty = 0
    elif rule_type == "bogo":
        rule_value = 0.0
        min_qty = int(form.get("min_qty_bogo") or 1)
        free_qty = int(form.get("free_qty") or 0)
    elif rule_type in ("min_bundle", "exact_bundle"):
        rule_value = _form_float(form.get("rule_value_bundle")) or 0.0
        min_qty = int(form.get("min_qty_bundle") or 2)
        free_qty = 0
    elif rule_type == "combo_bundle":
        rule_value = _form_float(form.get("rule_value_combo")) or 0.0
        min_qty = 1
        free_qty = 0
    else:
        raise ValueError(f"Tipo de regra inválido: {rule_type}")
    return rule_type, rule_value, min_qty, free_qty


def _parse_bogo_product_ids(form, rule_type: str):
    if rule_type != "bogo":
        return None, None
    try:
        buy_id = int(form.get("bogo_buy_product_id") or 0) or None
    except (TypeError, ValueError):
        buy_id = None
    try:
        free_id = int(form.get("bogo_free_product_id") or 0) or None
    except (TypeError, ValueError):
        free_id = None
    return buy_id, free_id


#: Tipo de regra pré-selecionado numa promoção nova (o mais usado nos eventos).
PROMO_DEFAULT_RULE_TYPE = "exact_bundle"


def _form_float(raw) -> float | None:
    try:
        return float(str(raw).replace(",", ".")) if str(raw or "").strip() else None
    except ValueError:
        return None


def _form_int(raw) -> int | None:
    try:
        return int(str(raw).strip()) if str(raw or "").strip() else None
    except ValueError:
        return None


def _promo_form_state(promo: dict | None = None, form=None) -> dict:
    """Valores que preenchem o formulário de promoção.

    ``form`` (POST recusado) tem prioridade: o admin volta à tela com tudo o que
    digitou. Sem ``form``, usa a promoção salva; sem nenhum, os padrões.
    """
    if form is not None:
        rule_type = (form.get("rule_type") or PROMO_DEFAULT_RULE_TYPE).strip()
        field_by_type = {
            "percent": ("rule_value_pf", None, None),
            "fixed": ("rule_value_pf", None, None),
            "bogo": (None, "min_qty_bogo", "free_qty"),
            "min_bundle": ("rule_value_bundle", "min_qty_bundle", None),
            "exact_bundle": ("rule_value_bundle", "min_qty_bundle", None),
            "combo_bundle": ("rule_value_combo", None, None),
        }
        value_f, min_f, free_f = field_by_type.get(rule_type, (None, None, None))
        return {
            "name": (form.get("name") or "").strip(),
            "rule_type": rule_type,
            "rule_value": _form_float(form.get(value_f)) if value_f else None,
            "min_qty": _form_int(form.get(min_f)) if min_f else None,
            "free_qty": _form_int(form.get(free_f)) if free_f else None,
            "product_ids": [int(p) for p in form.getlist("product_ids") if str(p).isdigit()],
            "bogo_buy_product_id": _form_int(form.get("bogo_buy_product_id")),
            "bogo_free_product_id": _form_int(form.get("bogo_free_product_id")),
        }
    if promo is not None:
        return {
            "name": promo.get("name") or "",
            "rule_type": promo.get("rule_type") or PROMO_DEFAULT_RULE_TYPE,
            "rule_value": promo.get("rule_value"),
            "min_qty": promo.get("min_qty"),
            "free_qty": promo.get("free_qty"),
            "product_ids": list(promo.get("product_ids") or []),
            "bogo_buy_product_id": promo.get("bogo_buy_product_id"),
            "bogo_free_product_id": promo.get("bogo_free_product_id"),
        }
    return {
        "name": "",
        "rule_type": PROMO_DEFAULT_RULE_TYPE,
        "rule_value": None,
        "min_qty": None,
        "free_qty": None,
        "product_ids": [],
        "bogo_buy_product_id": None,
        "bogo_free_product_id": None,
    }


def _promo_picker_catalog(event_id: int, promo: dict | None = None) -> list[dict]:
    """Produtos que a busca do formulário oferece (os do evento).

    Produto da promoção que já saiu do evento continua na lista, para aparecer
    entre os participantes e poder ser removido.
    """
    out: list[dict] = []
    seen: set[int] = set()
    for ep in list_event_products(event_id):
        pid = int(ep["product_id"])
        seen.add(pid)
        price = ep.get("event_price")
        if price is None:
            price = ep.get("price") if ep.get("price") is not None else ep.get("library_price")
        out.append({
            "id": pid,
            "name": ep.get("name") or f"Produto #{pid}",
            "sku": ep.get("sku") or "",
            "category": ep.get("category") or "",
            # Marca do ERP (MARCA): "selecionar todos da marca" no seletor.
            "brand": " ".join(str(ep.get("brand") or "").split()),
            "price": float(price) if price is not None else None,
            "image": ep.get("image") or "",
            "stock": int(ep.get("stock") or 0),
        })
    for prod in (promo or {}).get("products") or []:
        pid = int(prod["product_id"])
        if pid not in seen:
            out.append({
                "id": pid, "name": prod.get("name") or f"Produto #{pid}",
                "sku": prod.get("sku") or "", "category": "", "brand": "", "price": None,
                "image": "", "stock": 0, "outside_event": True,
            })
    return out


def _render_promo_form(event: dict, promo: dict | None, state: dict, status: int = 200):
    """Tela de promoção: nova (``promo`` = None) ou edição."""
    return render_template(
        "admin/event_promotion_detail.html",
        event=event,
        promo=promo,
        state=state,
        picker_catalog=_promo_picker_catalog(int(event["id"]), promo),
        rule_type_labels=RULE_TYPE_LABELS,
        active_event_tab="promocoes",
        **_admin_shell_context(active_section="eventos"),
    ), status


@app.route("/admin/eventos/<int:event_id>/promocoes/nova", methods=["GET"])
@admin_required
def admin_event_promotion_new(event_id: int):
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))
    if not event_ops_open(event):
        flash(EVENT_OPS_CLOSED_MSG, "error")
        return redirect(url_for("admin_event_promotions", event_id=event_id))
    return _render_promo_form(event, None, _promo_form_state())


@app.route("/admin/eventos/<int:event_id>/promocoes/nova", methods=["POST"])
@admin_required
@require_event_ops_open
def admin_event_promotion_create(event_id: int):
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))
    try:
        name = (request.form.get("name") or "").strip()
        rule_type, rule_value, min_qty, free_qty = _parse_promotion_form_fields(request.form)
        buy_id, free_id = _parse_bogo_product_ids(request.form, rule_type)
        product_ids = [int(p) for p in request.form.getlist("product_ids") if p]
        created = create_promotion(
            event_id, name, rule_type,
            rule_value=rule_value, min_qty=min_qty, free_qty=free_qty,
            product_ids=product_ids,
            bogo_buy_product_id=buy_id,
            bogo_free_product_id=free_id,
        )
    except (ValueError, TypeError) as exc:
        # Volta ao formulário com o que foi digitado, em vez de uma tela vazia.
        flash(str(exc), "error")
        return _render_promo_form(event, None, _promo_form_state(form=request.form), 422)
    flash(f"Promoção «{created['name']}» criada.", "success")
    return redirect(url_for("admin_event_promotions", event_id=event_id) + f"#promo-{created['id']}")


@app.route("/admin/eventos/<int:event_id>/promocoes/<int:promo_id>")
@admin_required
def admin_event_promotion_detail(event_id: int, promo_id: int):
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))
    promo = get_promotion(promo_id)
    if promo is None or int(promo["event_id"]) != event_id:
        flash("Promoção não encontrada.", "error")
        return redirect(url_for("admin_event_promotions", event_id=event_id))
    return _render_promo_form(event, promo, _promo_form_state(promo))


@app.route("/admin/eventos/<int:event_id>/promocoes/<int:promo_id>/editar", methods=["POST"])
@admin_required
@require_event_ops_open
def admin_event_promotion_edit(event_id: int, promo_id: int):
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))
    promo = get_promotion(promo_id)
    if promo is None or int(promo["event_id"]) != event_id:
        flash("Promoção não encontrada.", "error")
        return redirect(url_for("admin_event_promotions", event_id=event_id))
    try:
        name = (request.form.get("name") or "").strip()
        rule_type, rule_value, min_qty, free_qty = _parse_promotion_form_fields(request.form)
        buy_id, free_id = _parse_bogo_product_ids(request.form, rule_type)
        # Ativar/desativar é só pelo botão dedicado (admin_event_promotion_toggle).
        active = bool(int(promo.get("active") or 0))
        product_ids = [int(p) for p in request.form.getlist("product_ids") if p]
        update_promotion(
            promo_id, name, rule_type,
            rule_value=rule_value, min_qty=min_qty, free_qty=free_qty,
            active=active, product_ids=product_ids,
            bogo_buy_product_id=buy_id,
            bogo_free_product_id=free_id,
        )
    except (ValueError, TypeError) as exc:
        flash(str(exc), "error")
        return _render_promo_form(event, promo, _promo_form_state(form=request.form), 422)
    flash("Promoção atualizada.", "success")
    return redirect(url_for("admin_event_promotion_detail", event_id=event_id, promo_id=promo_id))


@app.route("/admin/eventos/<int:event_id>/promocoes/<int:promo_id>/ativar", methods=["POST"])
@admin_required
@require_event_ops_open
def admin_event_promotion_toggle(event_id: int, promo_id: int):
    promo = get_promotion(promo_id)
    if promo is None or int(promo["event_id"]) != event_id:
        flash("Promoção não encontrada.", "error")
        return redirect(url_for("admin_event_promotions", event_id=event_id))
    try:
        p = toggle_promotion_active(promo_id)
        status = "ativada" if p["active"] else "desativada"
        flash(f"Promoção «{p['name']}» {status}.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return _redirect_back( url_for("admin_event_promotions", event_id=event_id))


@app.route("/admin/eventos/<int:event_id>/promocoes/<int:promo_id>/excluir", methods=["POST"])
@admin_required
@require_event_ops_open
def admin_event_promotion_delete(event_id: int, promo_id: int):
    promo = get_promotion(promo_id)
    if promo is None or int(promo["event_id"]) != event_id:
        flash("Promoção não encontrada.", "error")
        return redirect(url_for("admin_event_promotions", event_id=event_id))
    try:
        delete_promotion(promo_id)
        flash(f"Promoção «{promo['name']}» excluída.", "warning")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin_event_promotions", event_id=event_id))


# ---------------------------------------------------------------------------
# Eventos — Promoções no modo Brindes
# ---------------------------------------------------------------------------

#: Regra pré-selecionada num brinde novo.
GIFT_DEFAULT_RULE_TYPE = "min_qty"


def _gifts_list_url(event_id: int, rule_id: int | None = None) -> str:
    url = url_for("admin_event_promotions", event_id=event_id, modo=PROMO_MODE_GIFTS)
    return url + (f"#brinde-{rule_id}" if rule_id else "")


#: Valor de ``gift_item_id`` no formulário que cadastra um brinde avulso novo.
GIFT_NEW_ITEM = "novo"


def _parse_gift_form(form) -> dict:
    """Campos do POST do formulário de brinde (``admin/event_gift_detail.html``).

    Cada regra tem o próprio campo de quantidade/valor (``min_qty_min``,
    ``min_qty_kit``, ``min_value``): a seção escondida fica desabilitada e não
    vai no POST. O brinde pode ter produtos do estoque (``gift_product_id``,
    um campo por produto) e brindes avulsos (``gift_item_id``, um campo por
    avulso marcado; ``novo`` com ``new_item_name``/``new_item_stock`` cadastra
    mais um junto) ao mesmo tempo; ``gift_source`` é só a aba aberta.
    """
    item_values = [str(v).strip() for v in form.getlist("gift_item_id")]
    rule_type = (form.get("rule_type") or GIFT_DEFAULT_RULE_TYPE).strip()
    qty_field = "min_qty_kit" if rule_type == "kit" else "min_qty_min"
    source = "avulso" if form.get("gift_source") == "avulso" else "produto"
    return {
        "name": (form.get("name") or "").strip(),
        "rule_type": rule_type,
        "min_qty": _form_int(form.get(qty_field)) if rule_type != "min_total" else None,
        "min_value": _form_float(form.get("min_value")) if rule_type == "min_total" else None,
        "gift_source": source,
        "gift_product_ids": list(dict.fromkeys(
            int(p) for p in form.getlist("gift_product_id") if str(p).isdigit()
        )),
        "gift_item_ids": list(dict.fromkeys(int(v) for v in item_values if v.isdigit())),
        "gift_item_new": GIFT_NEW_ITEM in item_values,
        "new_item_name": (form.get("new_item_name") or "").strip(),
        "new_item_stock": _form_int(form.get("new_item_stock")),
        "gift_qty": _form_int(form.get("gift_qty")),
        "max_per_order": _form_int(form.get("max_per_order")) if rule_type == "kit" else None,
        "product_ids": [int(p) for p in form.getlist("product_ids") if str(p).isdigit()],
    }


def _gift_form_state(rule: dict | None = None, form=None) -> dict:
    """Valores do formulário: POST recusado > brinde salvo > padrões."""
    if form is not None:
        return _parse_gift_form(form)
    if rule is not None:
        return {
            "name": rule.get("name") or "",
            "rule_type": rule.get("rule_type") or GIFT_DEFAULT_RULE_TYPE,
            "min_qty": rule.get("min_qty"),
            "min_value": rule.get("min_value"),
            # Abre na aba do produto, a não ser que o brinde seja só avulso.
            "gift_source": "avulso" if rule.get("gift_item_ids") and not rule.get("gift_product_ids") else "produto",
            "gift_product_ids": list(rule.get("gift_product_ids") or []),
            "gift_item_ids": list(rule.get("gift_item_ids") or []),
            "gift_item_new": False,
            "new_item_name": "",
            "new_item_stock": None,
            "gift_qty": rule.get("gift_qty"),
            "max_per_order": rule.get("max_per_order"),
            "product_ids": list(rule.get("product_ids") or []),
        }
    return {
        "name": "",
        "rule_type": GIFT_DEFAULT_RULE_TYPE,
        "min_qty": None,
        "min_value": None,
        "gift_source": "produto",
        "gift_product_ids": [],
        "gift_item_ids": [],
        "gift_item_new": False,
        "new_item_name": "",
        "new_item_stock": None,
        "gift_qty": None,
        "max_per_order": None,
        "product_ids": [],
    }


def _save_gift_args(state: dict) -> dict:
    args = {
        "min_qty": state["min_qty"] or 0,
        "min_value": state["min_value"],
        "gift_product_ids": state["gift_product_ids"],
        "gift_item_ids": state["gift_item_ids"],
        "gift_qty": state["gift_qty"] or 0,
        "max_per_order": state["max_per_order"],
        "product_ids": state["product_ids"],
    }
    if state["gift_item_new"]:
        args["new_item"] = {"name": state["new_item_name"], "stock": state["new_item_stock"]}
    return args


def _render_gift_form(event: dict, rule: dict | None, state: dict, status: int = 200):
    """Tela de brinde: novo (``rule`` = None) ou edição."""
    picker_extra = dict(rule or {})
    if rule and rule.get("gift_products"):
        # Brinde que saiu do evento continua visível para poder ser trocado.
        picker_extra["products"] = list(rule.get("products") or []) + [
            {"product_id": p["product_id"], "name": p["name"], "sku": p["sku"]}
            for p in rule["gift_products"]
        ]
    return render_template(
        "admin/event_gift_detail.html",
        event=event,
        rule=rule,
        state=state,
        picker_catalog=_promo_picker_catalog(int(event["id"]), picker_extra),
        gift_items=list_gift_items_for_event(int(event["id"])),
        gift_new_item=GIFT_NEW_ITEM,
        gift_rule_labels=GIFT_RULE_LABELS,
        active_event_tab="promocoes",
        **_admin_shell_context(active_section="eventos"),
    ), status


def _gift_rule_of_event(event_id: int, rule_id: int) -> dict | None:
    rule = get_gift_rule(rule_id)
    if rule is None or int(rule["event_id"]) != int(event_id):
        flash("Brinde não encontrado.", "error")
        return None
    return rule


@app.route("/admin/eventos/<int:event_id>/promocoes/brindes/novo", methods=["GET"])
@admin_required
def admin_event_gift_new(event_id: int):
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))
    if not event_ops_open(event):
        flash(EVENT_OPS_CLOSED_MSG, "error")
        return redirect(_gifts_list_url(event_id))
    return _render_gift_form(event, None, _gift_form_state())


@app.route("/admin/eventos/<int:event_id>/promocoes/brindes/novo", methods=["POST"])
@admin_required
@require_event_ops_open
def admin_event_gift_create(event_id: int):
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))
    state = _parse_gift_form(request.form)
    try:
        created = create_gift_rule(event_id, state["name"], state["rule_type"], **_save_gift_args(state))
    except (ValueError, TypeError) as exc:
        flash(str(exc), "error")
        return _render_gift_form(event, None, state, 422)
    flash(f"Brinde «{created['name']}» criado.", "success")
    return redirect(_gifts_list_url(event_id, created["id"]))


@app.route("/admin/eventos/<int:event_id>/promocoes/brindes/<int:rule_id>")
@admin_required
def admin_event_gift_detail(event_id: int, rule_id: int):
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))
    rule = _gift_rule_of_event(event_id, rule_id)
    if rule is None:
        return redirect(_gifts_list_url(event_id))
    return _render_gift_form(event, rule, _gift_form_state(rule))


@app.route("/admin/eventos/<int:event_id>/promocoes/brindes/<int:rule_id>/editar", methods=["POST"])
@admin_required
@require_event_ops_open
def admin_event_gift_edit(event_id: int, rule_id: int):
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))
    rule = _gift_rule_of_event(event_id, rule_id)
    if rule is None:
        return redirect(_gifts_list_url(event_id))
    state = _parse_gift_form(request.form)
    try:
        update_gift_rule(rule_id, state["name"], state["rule_type"], **_save_gift_args(state))
    except (ValueError, TypeError) as exc:
        flash(str(exc), "error")
        return _render_gift_form(event, rule, state, 422)
    flash("Brinde atualizado.", "success")
    return redirect(url_for("admin_event_gift_detail", event_id=event_id, rule_id=rule_id))


@app.route("/admin/eventos/<int:event_id>/promocoes/brindes/<int:rule_id>/ativar", methods=["POST"])
@admin_required
@require_event_ops_open
def admin_event_gift_toggle(event_id: int, rule_id: int):
    rule = _gift_rule_of_event(event_id, rule_id)
    if rule is None:
        return redirect(_gifts_list_url(event_id))
    try:
        r = toggle_gift_rule_active(rule_id)
        flash(f"Brinde «{r['name']}» {'ativado' if r['active'] else 'desativado'}.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return _redirect_back(_gifts_list_url(event_id))


@app.route("/admin/eventos/<int:event_id>/promocoes/brindes/<int:rule_id>/excluir", methods=["POST"])
@admin_required
@require_event_ops_open
def admin_event_gift_delete(event_id: int, rule_id: int):
    rule = _gift_rule_of_event(event_id, rule_id)
    if rule is None:
        return redirect(_gifts_list_url(event_id))
    try:
        delete_gift_rule(rule_id)
        flash(f"Brinde «{rule['name']}» excluído.", "warning")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(_gifts_list_url(event_id))


# --- Brindes avulsos (não são produtos; cadastrados só no Go Sell) ----------

def _gift_items_url(event_id: int, item_id: int | None = None) -> str:
    url = url_for("admin_event_promotions", event_id=event_id, modo=PROMO_MODE_GIFTS)
    return url + (f"#avulso-{item_id}" if item_id else "#avulsos")


def _gift_item_of_event(event_id: int, item_id: int) -> dict | None:
    item = get_gift_item(item_id)
    if item is None or int(item["event_id"]) != int(event_id):
        flash("Brinde avulso não encontrado.", "error")
        return None
    return item


@app.route("/admin/eventos/<int:event_id>/promocoes/brindes/avulsos", methods=["POST"])
@admin_required
@require_event_ops_open
def admin_event_gift_item_create(event_id: int):
    if _event_or_404(event_id) is None:
        return redirect(url_for("admin_events"))
    try:
        item = create_gift_item(
            event_id,
            request.form.get("name") or "",
            _form_int(request.form.get("stock")),
            request.form.get("description"),
        )
        flash(f"Brinde avulso «{item['name']}» cadastrado.", "success")
        return redirect(_gift_items_url(event_id, item["id"]))
    except (ValueError, TypeError) as exc:
        flash(str(exc), "error")
    return redirect(_gift_items_url(event_id))


@app.route("/admin/eventos/<int:event_id>/promocoes/brindes/avulsos/<int:item_id>/editar", methods=["POST"])
@admin_required
@require_event_ops_open
def admin_event_gift_item_edit(event_id: int, item_id: int):
    if _gift_item_of_event(event_id, item_id) is None:
        return redirect(_gift_items_url(event_id))
    try:
        item = update_gift_item(
            item_id,
            request.form.get("name") or "",
            _form_int(request.form.get("stock")),
            request.form.get("description"),
        )
        flash(f"Brinde avulso «{item['name']}» atualizado.", "success")
    except (ValueError, TypeError) as exc:
        flash(str(exc), "error")
    return redirect(_gift_items_url(event_id, item_id))


@app.route("/admin/eventos/<int:event_id>/promocoes/brindes/avulsos/<int:item_id>/excluir", methods=["POST"])
@admin_required
@require_event_ops_open
def admin_event_gift_item_delete(event_id: int, item_id: int):
    item = _gift_item_of_event(event_id, item_id)
    if item is None:
        return redirect(_gift_items_url(event_id))
    try:
        delete_gift_item(item_id)
        flash(f"Brinde avulso «{item['name']}» excluído.", "warning")
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(_gift_items_url(event_id, item_id))
    return redirect(_gift_items_url(event_id))


# ---------------------------------------------------------------------------
# Eventos — sub-página Transações
# ---------------------------------------------------------------------------

def _admin_event_transactions_page_data(event_id: int) -> dict:
    """Filtros, lista e paginação das transações do evento (página + polling)."""
    pedido = (request.args.get("pedido") or "").strip()
    status_raw = (request.args.get("status") or "").strip().lower()
    valid_status = frozenset({"todos", "confirmado", "pendente", "cancelado", "estornado", "entregue"})
    status_norm = status_raw if status_raw in valid_status else "todos"

    entrega_raw = (request.args.get("entrega") or "").strip().lower()
    entrega_norm = entrega_raw if entrega_raw == "parcial" else "todos"
    delivery_api = None if entrega_norm == "todos" else entrega_norm

    date_arg_raw = (request.args.get("data") or "").strip()
    on_date = _parse_tx_filter_date_arg(date_arg_raw)
    filter_date_display = on_date or ""

    event_sellers_rows = list_event_sellers(event_id)
    event_seller_ids = {int(s["id"]) for s in event_sellers_rows}
    seller_raw = _parse_int(request.args.get("vendedor"), 0)
    seller_filter = seller_raw if seller_raw > 0 and seller_raw in event_seller_ids else None
    if seller_raw > 0 and seller_filter is None:
        seller_raw = 0

    per_page = _parse_int(request.args.get("per_page"), DEFAULT_ADMIN_MOVEMENTS_PER_PAGE)
    if per_page not in ALLOWED_ADMIN_STOCK_PER_PAGE:
        per_page = DEFAULT_ADMIN_MOVEMENTS_PER_PAGE
    page = max(1, _parse_int(request.args.get("page"), 1))
    sort, sort_dir = _sortable_table_params(EVENT_TX_SORT_FIELDS)

    status_api = None if status_norm == "todos" else status_norm

    total = count_transactions_for_event(
        event_id,
        seller_id=seller_filter,
        order_search=pedido or None,
        status=status_api,
        on_date=on_date,
        delivery=delivery_api,
    )
    total_pages = max(1, (total + per_page - 1) // per_page) if total > 0 else 1
    page = min(page, total_pages)
    offset = (page - 1) * per_page

    transactions = list_transactions_for_event(
        event_id,
        seller_id=seller_filter,
        order_search=pedido or None,
        status=status_api,
        on_date=on_date,
        delivery=delivery_api,
        limit=per_page,
        offset=offset,
        sort=sort,
        direction=sort_dir,
    )

    showing_from = offset + 1 if total > 0 else 0
    showing_to = min(offset + len(transactions), total) if total > 0 else 0

    return {
        "transactions": transactions,
        "movement_filter_sellers": event_sellers_rows,
        "filters": {
            "pedido": pedido,
            "status": status_norm,
            "entrega": entrega_norm,
            "vendedor": seller_raw,
            "per_page": per_page,
            "data": filter_date_display,
            "sort": sort,
            "dir": sort_dir,
        },
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "has_prev": page > 1,
            "has_next": page < total_pages,
            "showing_from": showing_from,
            "showing_to": showing_to,
        },
        "allowed_per_page": ALLOWED_ADMIN_STOCK_PER_PAGE,
    }


@app.route("/admin/eventos/<int:event_id>/transacoes")
@admin_required
def admin_event_transactions(event_id: int):
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))

    page_data = _admin_event_transactions_page_data(event_id)
    return render_template(
        "admin/event_transactions.html",
        event=event,
        active_event_tab="transacoes",
        **page_data,
        **_admin_shell_context(active_section="eventos"),
    )


@app.route("/admin/eventos/<int:event_id>/transacoes/<int:tx_id>/pdf")
@admin_required
def admin_event_transaction_pdf(event_id: int, tx_id: int):
    """Renderiza a versão para impressão/PDF de um pedido do evento."""
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))
    tx = get_transaction(tx_id)
    if tx is None or tx.get("event_id") is None or int(tx["event_id"]) != int(event_id):
        flash("Pedido não encontrado neste evento.", "error")
        return redirect(url_for("admin_event_transactions", event_id=event_id))
    back_args = {"pedido": tx["order_number"]} if tx.get("order_number") else {}
    return render_template(
        "admin/transaction_pdf.html",
        tx=tx,
        event=event,
        back_url=url_for(
            "admin_event_transactions", event_id=event_id, _anchor=f"tx-{tx_id}", **back_args
        ),
        now=datetime.now(),
    )


@app.route(
    "/admin/eventos/<int:event_id>/transacoes/<int:tx_id>/estornar",
    methods=["POST"],
)
@admin_required
@require_event_ops_open
def admin_event_transaction_refund(event_id: int, tx_id: int):
    if _event_or_404(event_id) is None:
        flash("Evento não encontrado.", "error")
        return redirect(url_for("admin_events"))
    try:
        result = refund_transaction(
            tx_id,
            created_by=_current_admin_user(),
            expected_event_id=event_id,
        )
        order_label = result.get("order_number") or f"#{tx_id}"
        erp_note = {
            "retirado": " O pedido ainda não tinha ido ao ERP e saiu da fila.",
            "cancelamento": " O cancelamento no ERP entrou na fila de envio.",
        }.get(result.get("erp_action") or "", "")
        flash(
            f"Pedido {order_label} estornado. Estoque reposto e totais de vendas atualizados."
            + erp_note,
            "warning",
        )
        erp_sync.notify_worker()
    except ValueError as exc:
        flash(str(exc), "error")
    return _redirect_back(url_for("admin_event_transactions", event_id=event_id))


@app.route(
    "/admin/eventos/<int:event_id>/transacoes/<int:tx_id>/observacao-nota",
    methods=["POST"],
)
@admin_required
@require_event_ops_open
def admin_event_transaction_note(event_id: int, tx_id: int):
    if _event_or_404(event_id) is None:
        flash("Evento não encontrado.", "error")
        return redirect(url_for("admin_events"))
    note = request.form.get("receipt_note") or ""
    try:
        result = update_transaction_receipt_note(
            tx_id,
            note,
            expected_event_id=event_id,
        )
        order_label = result.get("order_number") or f"#{tx_id}"
        if result.get("receipt_note"):
            flash(f"Observação da nota do pedido {order_label} salva.", "success")
        else:
            flash(f"Observação da nota do pedido {order_label} removida.", "warning")
    except ValueError as exc:
        flash(str(exc), "error")
    return _redirect_back(
        url_for("admin_event_transactions", event_id=event_id),
        fragment=f"tx-{tx_id}",
    )


@app.route(
    "/admin/eventos/<int:event_id>/transacoes/<int:tx_id>/cancelar-pendente",
    methods=["POST"],
    endpoint="admin_event_cancel_pending_transaction",
)
@admin_required
def admin_event_cancel_pending_transaction(event_id: int, tx_id: int):
    """Descarta pedido pendente do evento (marca como cancelado); não altera estoque."""
    if _event_or_404(event_id) is None:
        flash("Evento não encontrado.", "error")
        return redirect(url_for("admin_events"))
    try:
        cancel_pending_transaction(tx_id, expected_event_id=event_id)
        flash("Pedido pendente descartado.", "error")
    except ValueError as exc:
        flash(str(exc), "error")
    return _redirect_back(url_for("admin_event_transactions", event_id=event_id))


@app.route(
    "/admin/transacoes/<int:tx_id>/itens/<int:item_id>/alterar",
    methods=["POST"],
)
@admin_required
def admin_transaction_replace_item(tx_id: int, item_id: int):
    """Substitui o produto de um item confirmado (SKU + quantidade). Somente admin."""
    blocked = _reject_if_tx_event_ops_closed(tx_id)
    if blocked is not None:
        return blocked
    sku = (request.form.get("sku") or "").strip()
    qty_raw = request.form.get("quantity") or ""
    try:
        new_qty = int(qty_raw)
    except (TypeError, ValueError):
        flash("Informe uma quantidade válida.", "error")
        return _redirect_back( url_for("admin_events"))
    if not sku:
        flash("Informe o SKU do novo produto.", "error")
        return _redirect_back( url_for("admin_events"))

    product, from_erp, lookup_err = _find_or_fetch_product(sku)
    if product is None:
        flash(_product_lookup_failed_message(sku, lookup_err), "error")
        return _redirect_back( url_for("admin_events"))

    expected_event = _parse_int(request.form.get("event_id") or "", 0)
    try:
        result = replace_transaction_item_product(
            tx_id,
            item_id,
            int(product["id"]),
            new_qty,
            created_by=_current_admin_user(),
            expected_event_id=expected_event or None,
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return _redirect_back( url_for("admin_events"))

    suffix = " (importado do ERP)" if from_erp else ""
    msg = (
        f"Item alterado no pedido {result.get('order_number') or '#' + str(tx_id)}: "
        f"«{result['old_product_name']}» → «{result['new_product_name']}» "
        f"(SKU {result['new_sku']}, {result['quantity']} un.){suffix}."
    )
    if result.get("pending"):
        msg += f" {result['pending']} un. ficaram pendentes de retirada (sem estoque)."
    flash(msg, "success")
    if result.get("erp_warning"):
        flash(result["erp_warning"], "warning")
    return _redirect_back( url_for("admin_events"))


@app.route(
    "/admin/eventos/<int:event_id>/transacoes/<int:tx_id>/itens/<int:item_id>/entregar",
    methods=["POST"],
)
@admin_required
@require_event_ops_open
def admin_event_confirm_item_delivery(event_id: int, tx_id: int, item_id: int):
    """Confirma a retirada de um item pendente (baixa o estoque na entrega)."""
    if _event_or_404(event_id) is None:
        flash("Evento não encontrado.", "error")
        return redirect(url_for("admin_events"))
    try:
        result = confirm_item_delivery(
            tx_id,
            item_id,
            expected_event_id=event_id,
            created_by=_current_admin_user(),
        )
        msg = (
            f"Entrega confirmada: {result['delivered_now']} un. de "
            f"'{result['product_name']}'."
        )
        if result["still_pending"] > 0:
            msg += f" Ainda pendente: {result['still_pending']} un."
        flash(msg, "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return _redirect_back(url_for("admin_event_transactions", event_id=event_id))


@app.route(
    "/admin/eventos/<int:event_id>/transacoes/<int:tx_id>/entregar",
    methods=["POST"],
)
@admin_required
@require_event_ops_open
def admin_event_confirm_items_delivery(event_id: int, tx_id: int):
    """Confirma a retirada de vários itens pendentes de um pedido."""
    if _event_or_404(event_id) is None:
        flash("Evento não encontrado.", "error")
        return redirect(url_for("admin_events"))
    item_ids = request.form.getlist("item_ids")
    try:
        result = confirm_items_delivery(
            tx_id,
            item_ids,
            expected_event_id=event_id,
            created_by=_current_admin_user(),
        )
        msg = (
            f"Entrega confirmada: {result['items_count']} produto(s), "
            f"{result['units_delivered']} un."
        )
        if result.get("errors"):
            msg += " Alguns itens não puderam ser entregues (verifique o estoque)."
            flash(msg, "warning")
            for err in result["errors"][:5]:
                flash(err, "error")
        else:
            flash(msg, "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return _redirect_back(url_for("admin_event_transactions", event_id=event_id))


@app.route(
    "/admin/eventos/<int:event_id>/transacoes/<int:tx_id>/entregue",
    methods=["POST"],
)
@admin_required
@require_event_ops_open
def admin_event_confirm_transaction_handover(event_id: int, tx_id: int):
    """Marca o pedido inteiro como entregue/retirado pelo cliente."""
    if _event_or_404(event_id) is None:
        flash("Evento não encontrado.", "error")
        return redirect(url_for("admin_events"))
    try:
        confirm_transaction_handover(tx_id, expected_event_id=event_id)
        flash("Pedido marcado como entregue.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return _redirect_back(url_for("admin_event_transactions", event_id=event_id))


# ---------------------------------------------------------------------------
# Eventos — API de polling (estoque / transações)
# ---------------------------------------------------------------------------

@app.route("/admin/api/eventos/<int:event_id>/transacoes")
@admin_required
def admin_api_event_transactions(event_id: int):
    """Fragmento HTML da listagem de transações (polling ~20s no admin)."""
    event = _event_or_404(event_id)
    if event is None:
        return ("", 404)
    page_data = _admin_event_transactions_page_data(event_id)
    return render_template(
        "admin/partials/event_transactions_live.html",
        event=event,
        **page_data,
    )


@app.route("/admin/api/eventos/<int:event_id>/estoque")
@admin_required
def admin_api_event_stock(event_id: int):
    if _event_or_404(event_id) is None:
        return jsonify({"error": "Evento não encontrado."}), 404
    products, _filters, pagination = _admin_event_stock_page_view(event_id)
    promo_ids = product_ids_with_active_promotions_for_event(event_id)
    promo_tooltips = active_promotion_tooltip_by_product_id(event_id)
    promo_names = active_promotion_names_by_product_id(event_id)
    return jsonify({
        "stats": get_event_stock_stats(event_id),
        "pagination": {
            "page": pagination["page"],
            "per_page": pagination["per_page"],
            "total": pagination["total"],
            "total_pages": pagination["total_pages"],
        },
        "products": [
            {
                **p,
                "id": p["product_id"],
                "preco": float(p.get("price") or 0),
                "preco_biblioteca": float(p.get("library_price") or 0),
                "preco_evento_override": p.get("event_price") is not None,
                "estoque": p["stock"],
                "estoque_minimo": p["min_stock"],
                "status": _event_product_status(p),
                "active_promo": int(p["product_id"]) in promo_ids,
                "promo_tooltip": promo_tooltips.get(int(p["product_id"]), ""),
                "promo_name": promo_names.get(int(p["product_id"]), ""),
                "pending_delivery_units": int(p.get("pending_delivery_units") or 0),
                "units_sold": int(p.get("units_sold") or 0),
            }
            for p in products
        ],
    })


@app.route("/admin/api/eventos/<int:event_id>/estoque/<int:product_id>")
@admin_required
def admin_api_event_stock_product(event_id: int, product_id: int):
    if _event_or_404(event_id) is None:
        return jsonify({"error": "Evento não encontrado."}), 404
    try:
        return jsonify(_event_stock_product_payload(event_id, product_id))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404


# ---------------------------------------------------------------------------
# Nota não fiscal
# ---------------------------------------------------------------------------

def _receipt_public_url(order_number: str, *, autoprint: bool = False) -> str:
    """URL assinada da nota de retirada (``/nota/OM...?t=...``)."""
    on = (order_number or "").strip()
    if not on:
        return ""
    token = sign_receipt_token(on, app.secret_key)
    extra = {"print": "1"} if autoprint else {}
    return url_for("receipt", order_number=on, t=token, **extra)


app.add_template_global(_receipt_public_url, "receipt_url")


@app.route("/nota/<path:order_number>")
def receipt(order_number: str):
    """Exibe a nota não fiscal (requer parâmetro ``t`` com assinatura válida)."""
    token = (request.args.get("t") or "").strip()
    if not verify_receipt_token(order_number, token, app.secret_key):
        return render_template(
            "nota.html",
            tx=None,
            order_number=order_number,
            autoprint=False,
            receipt_error="invalid_link",
        ), 403

    tx = get_transaction_by_order_number(order_number)
    autoprint = request.args.get("print", "").lower() in ("1", "true", "yes")
    if tx is None:
        return render_template(
            "nota.html",
            tx=None,
            order_number=order_number,
            autoprint=False,
            receipt_error="not_found",
        ), 404
    return render_template(
        "nota.html",
        tx=tx,
        order_number=order_number,
        autoprint=autoprint,
        receipt_error=None,
    )


# ---------------------------------------------------------------------------
# Filtros Jinja
# ---------------------------------------------------------------------------

@app.template_filter("brl")
def brl_filter(value):
    return _format_brl(value)


@app.template_filter("pct")
def pct_filter(value):
    """Percentual em pt-BR com até 1 casa (``6,5``, ``100``) — mesma régua dos gráficos."""
    try:
        n = round(float(value or 0), 1)
    except (TypeError, ValueError):
        return "—"
    text = f"{n:.1f}".replace(".", ",")
    return text[:-2] if text.endswith(",0") else text


@app.template_filter("milhar")
def milhar_filter(value):
    """Inteiro com separador de milhar pt-BR (``4.473``)."""
    try:
        n = int(round(float(value or 0)))
    except (TypeError, ValueError):
        return "0"
    return f"{n:,}".replace(",", ".")


@app.template_filter("brl_compacto")
def brl_compacto_filter(value):
    """Valor curto para rótulos de gráfico (``R$ 33,8 mil``)."""
    try:
        n = float(value or 0)
    except (TypeError, ValueError):
        n = 0.0
    for limit, suffix in ((1_000_000, "mi"), (1_000, "mil")):
        if abs(n) >= limit:
            return f"R$ {n / limit:.1f} {suffix}".replace(".", ",")
    return f"R$ {n:.0f}"


_WEEKDAYS_SHORT = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")


@app.template_filter("dia_semana")
def dia_semana_filter(value):
    """Dia da semana abreviado de uma data ISO (``2026-08-21`` → ``sex``)."""
    try:
        return _WEEKDAYS_SHORT[date.fromisoformat(str(value)[:10]).weekday()]
    except ValueError:
        return ""


@app.template_filter("datahora")
def datahora_filter(value):
    if not value:
        return "-"
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return value
    return dt.strftime("%d/%m/%Y %H:%M")


@app.template_filter("paymethod")
def paymethod_filter(value, installments=None):
    return _payment_method_label(value, installments)


@app.template_filter("cro_pedido")
def cro_pedido_filter(row):
    """Extrai UF e número do registro CRO da linha de movimentação / transação."""
    return _cro_pedido_fields(row if isinstance(row, dict) else None)


@app.template_filter("signed")
def signed_filter(value):
    try:
        n = int(value or 0)
    except (TypeError, ValueError):
        n = 0
    if n > 0:
        return f"+{n}"
    return str(n)


@app.template_filter("movlabel")
def mov_label_filter(value):
    return {
        "entrada": "Entrada",
        "saida": "Saída",
        "venda": "Venda",
        "pendente": "Pendente",
        "ajuste": "Correção",
        "inicial": "Entrada",
    }.get(value, value or "-")


@app.template_filter("periodicidade")
def periodicidade_filter(value):
    """Intervalo entre compras em texto (``None`` = cliente de compra única).

    Vendas de evento acontecem em horas, não em meses: abaixo de um dia o
    número em dias ("0,13 dia") não comunica nada, então cai para horas.
    """
    if value is None:
        return "Compra única"
    try:
        days = float(value)
    except (TypeError, ValueError):
        return "—"
    if days < 0:
        return "—"
    if days < 1:
        hours = days * 24
        if hours < 1:
            minutes = max(1, int(round(hours * 60)))
            return f"{minutes} min"
        return f"{hours:.1f}h".replace(".", ",")
    if days < 60:
        n = int(round(days))
        return "1 dia" if n == 1 else f"{n} dias"
    months = days / 30.0
    return f"{months:.1f} meses".replace(".", ",")


@app.template_filter("desdeontem")
def desdeontem_filter(value):
    """Tempo desde a última compra, em linguagem de painel."""
    if value is None:
        return "—"
    try:
        days = float(value)
    except (TypeError, ValueError):
        return "—"
    if days < 1:
        return "hoje"
    if days < 2:
        return "ontem"
    if days < 60:
        return f"{int(days)} dias"
    months = days / 30.0
    return f"{months:.0f} meses"


# ---------------------------------------------------------------------------
# Painel administrativo — Clientes
# ---------------------------------------------------------------------------

# Teto de linhas para relatórios de impressão/PDF (evita PDFs gigantes; o
# conjunto completo continua disponível via exportação em planilha .xlsx).
ADMIN_EXPORT_PDF_ROWS_CAP = 2000

ALLOWED_ADMIN_CUSTOMERS_PER_PAGE = (10, 25, 50, 100)
DEFAULT_ADMIN_CUSTOMERS_PER_PAGE = 10

# Rótulos das ordenações expostas na tela (chaves validadas por CUSTOMER_SORTS).
ADMIN_CUSTOMER_SORT_LABELS = (
    ("revenue", "Maior faturamento"),
    ("orders", "Mais pedidos"),
    ("ticket", "Maior ticket médio"),
    ("frequency", "Compra mais frequente"),
    ("recent", "Comprou mais recentemente"),
    ("oldest", "Cliente mais antigo"),
    ("name", "Nome (A–Z)"),
)


@app.route("/admin/eventos/<int:event_id>/clientes")
@admin_required
def admin_event_customers(event_id: int):
    """Carteira de clientes do evento: quem comprou, quanto e com que frequência."""
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))

    q, sort = _admin_event_customers_filters()

    per_page = _parse_int(
        request.args.get("per_page"), DEFAULT_ADMIN_CUSTOMERS_PER_PAGE
    )
    if per_page not in ALLOWED_ADMIN_CUSTOMERS_PER_PAGE:
        per_page = DEFAULT_ADMIN_CUSTOMERS_PER_PAGE
    page = max(1, _parse_int(request.args.get("page"), 1))

    def fetch(target_page: int):
        return list_customers(
            query=q or None,
            event_id=event_id,
            sort=sort,
            limit=per_page,
            offset=(target_page - 1) * per_page,
        )

    result = fetch(page)
    total = int(result["total"])
    total_pages = max(1, (total + per_page - 1) // per_page) if total > 0 else 1
    if page > total_pages:
        page = total_pages
        result = fetch(page)

    offset = (page - 1) * per_page
    customers = result["rows"]
    pagination = {
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "showing_from": offset + 1 if total > 0 else 0,
        "showing_to": min(offset + len(customers), total) if total > 0 else 0,
    }
    portfolio = get_portfolio_analytics(event_id)
    for row in portfolio["top"]:
        row["url"] = url_for(
            "admin_event_customer_detail", event_id=event_id, kind=row["kind"], ident=row["ident"]
        )
    return render_template(
        "admin/customers.html",
        customers=customers,
        portfolio=portfolio,
        summary=result["summary"],
        pagination=pagination,
        filters={"q": q, "sort": sort, "per_page": per_page},
        sort_options=ADMIN_CUSTOMER_SORT_LABELS,
        event=event,
        active_event_tab="clientes",
        allowed_per_page=ALLOWED_ADMIN_CUSTOMERS_PER_PAGE,
        **_admin_shell_context(active_section="clientes"),
    )


def _customer_event_filter_arg() -> int | None:
    """``?evento=<id>`` do perfil do cliente; inválido ou vazio = todos os eventos."""
    raw = (request.args.get("evento") or "").strip()
    return int(raw) if raw.isdigit() else None


@app.route("/admin/eventos/<int:event_id>/clientes/<kind>/<ident>")
@admin_required
def admin_event_customer_detail(event_id: int, kind: str, ident: str):
    """Perfil analítico de um cliente (histórico, periodicidade, top produtos)."""
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))
    customer = get_customer_profile(kind, ident, _customer_event_filter_arg())
    if customer is None:
        flash("Cliente não encontrado.", "error")
        return redirect(url_for("admin_event_customers", event_id=event_id))
    # Séries dos gráficos d3; rótulos de pagamento vivem na apresentação.
    # Com filtro de evento, os gráficos descrevem só aquele evento; a previsão da
    # próxima compra é do cliente como um todo e não cabe no recorte.
    filtered = bool(customer["event_filter"])
    scoped_orders = customer["orders"]
    charts = {
        "intervals": customer["intervals"],
        "next_purchase_expected_at": None if filtered else customer["next_purchase_expected_at"],
        "first_purchase_at": (
            scoped_orders[-1]["created_at"] if filtered and scoped_orders
            else customer["first_purchase_at"]
        ),
        "payment_methods": [
            {**p, "label": _payment_method_label(p["method"])} for p in customer["payments"]
        ],
        "products": customer["all_products"],
        "customer_revenue": customer["scope"]["revenue"],
    }
    return render_template(
        "admin/customer_detail.html",
        customer=customer,
        charts=charts,
        event=event,
        active_event_tab="clientes",
        **_admin_shell_context(active_section="clientes"),
    )


@app.route("/admin/eventos/<int:event_id>/clientes/<kind>/<ident>/pdf")
@admin_required
def admin_event_customer_pdf(event_id: int, kind: str, ident: str):
    """Renderiza a versão para impressão/PDF do perfil completo do cliente."""
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))
    customer = get_customer_profile(kind, ident, _customer_event_filter_arg())
    if customer is None:
        flash("Cliente não encontrado.", "error")
        return redirect(url_for("admin_event_customers", event_id=event_id))
    return render_template(
        "admin/customer_detail_pdf.html",
        customer=customer,
        event=event,
        back_url=url_for(
            "admin_event_customer_detail", event_id=event_id, kind=kind, ident=ident,
            evento=customer["event_filter"],
        ),
        now=datetime.now(),
    )


def _admin_event_customers_filters() -> tuple[str, str]:
    """Lê GET (``q``/``sort``) da carteira de clientes, compartilhado entre listagem e exportações."""
    q = (request.args.get("q") or "").strip()
    sort = (request.args.get("sort") or DEFAULT_CUSTOMER_SORT).strip().lower()
    if sort not in CUSTOMER_SORTS:
        sort = DEFAULT_CUSTOMER_SORT
    return q, sort


@app.route("/admin/eventos/<int:event_id>/clientes/export.xlsx")
@admin_required
def admin_event_customers_export_xlsx(event_id: int):
    """Exporta a carteira de clientes do evento em planilha .xlsx, respeitando os filtros ativos."""
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))

    q, sort = _admin_event_customers_filters()
    result = list_customers(
        query=q or None, event_id=event_id, sort=sort, limit=EXPORT_STOCK_CSV_CAP, offset=0
    )
    customers = result["rows"]
    if not customers:
        flash("Nenhum cliente para exportar.", "info")
        return redirect(url_for("admin_event_customers", event_id=event_id, q=q or None, sort=sort))

    header = [
        "Cliente", "CPF", "E-mail", "Telefone", "Cidade/UF",
        "Pedidos", "Unidades", "Faturamento (R$)", "% do Total",
        "Ticket Médio (R$)", "Periodicidade", "Última Compra",
    ]
    rows = []
    for c in customers:
        city = c.get("city") or ""
        state = c.get("state") or ""
        city_state = f"{city}/{state}".strip("/") if (city or state) else ""
        last = c.get("last_purchase_at")
        last_str = f"{_csv_fmt_date(last)} {_csv_fmt_time(last)}".strip()
        rows.append([
            _csv_cell(c.get("name")),
            _csv_cell(c.get("cpf")),
            _csv_cell(c.get("email")),
            _csv_cell(c.get("phone")),
            _csv_cell(city_state),
            int(c.get("orders_count") or 0),
            int(c.get("units") or 0),
            _csv_fmt_brl(c.get("revenue")),
            _csv_cell(c.get("revenue_share")),
            _csv_fmt_brl(c.get("avg_ticket")),
            _csv_cell(
                periodicidade_filter(c.get("avg_interval_days")) if c.get("is_recurring") else "Compra única"
            ),
            _csv_cell(last_str),
        ])

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_ev = re.sub(r"[^a-zA-Z0-9_-]+", "_", (event.get("name") or str(event_id)))[:40].strip("_") or str(event_id)
    fname = f"clientes_evento_{event_id}_{safe_ev}_{ts}.xlsx"
    return _xlsx_attachment_response(fname, header, rows, sheet_title="Clientes")


@app.route("/admin/eventos/<int:event_id>/clientes/pdf")
@admin_required
def admin_event_customers_pdf(event_id: int):
    """Renderiza a versão para impressão/PDF da carteira de clientes do evento."""
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))

    q, sort = _admin_event_customers_filters()
    result = list_customers(
        query=q or None, event_id=event_id, sort=sort, limit=ADMIN_EXPORT_PDF_ROWS_CAP, offset=0
    )
    customers = result["rows"]
    total = int(result["total"])
    sort_label = dict(ADMIN_CUSTOMER_SORT_LABELS).get(sort, sort)

    return render_template(
        "admin/customers_pdf.html",
        event=event,
        customers=customers,
        summary=result["summary"],
        filters={"q": q, "sort": sort, "sort_label": sort_label},
        capped=total > len(customers),
        total=total,
        back_url=url_for("admin_event_customers", event_id=event_id, q=q or None, sort=sort),
        now=datetime.now(),
    )


# ---------------------------------------------------------------------------
# Rotas restauradas: os templates as chamam por url_for (ver HEAD)
# ---------------------------------------------------------------------------

def _parse_event_revenue_goal(raw) -> float | None:
    """Interpreta meta de faturamento em formato BR (1.250,50) ou ponto decimal. Vazio = sem meta."""
    s = (raw or "").strip()
    if not s:
        return None
    s = s.replace("R$", "").replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        value = round(float(s), 2)
    except (TypeError, ValueError):
        raise ValueError("Informe um valor numérico válido para a meta de faturamento.")
    if value < 0:
        raise ValueError("A meta de faturamento não pode ser negativa.")
    return value


def _parse_event_volume_goal(raw) -> int | None:
    """Interpreta meta de volume em unidades. Vazio = sem meta."""
    s = (raw or "").strip()
    if not s:
        return None
    s = s.replace(".", "").replace(" ", "")
    try:
        value = int(float(s.replace(",", ".")))
    except (TypeError, ValueError):
        raise ValueError("Informe um número inteiro válido para a meta de volume.")
    if value < 0:
        raise ValueError("A meta de volume não pode ser negativa.")
    return value


@app.route("/admin/eventos/<int:event_id>/metas", methods=["POST"])
@admin_required
@require_event_ops_open
def admin_event_goals(event_id: int):
    """Grava metas de faturamento e volume do evento (Financeiro ou dashboard)."""
    event = _event_or_404(event_id)
    if (request.form.get("next") or "").strip() == "dashboard":
        fallback = url_for("admin_event_detail", event_id=event_id)
    else:
        fallback = url_for("admin_financeiro", evento=event_id)
    if event is None:
        return redirect(url_for("admin_events"))
    try:
        revenue_goal = _parse_event_revenue_goal(request.form.get("revenue_goal"))
        volume_goal = _parse_event_volume_goal(request.form.get("volume_goal"))
    except ValueError as exc:
        flash(str(exc), "error")
        return _redirect_back( fallback)
    if not update_event_goals(event_id, revenue_goal=revenue_goal, volume_goal=volume_goal):
        flash("Não foi possível salvar as metas deste evento.", "error")
        return _redirect_back( fallback)
    parts = []
    if revenue_goal:
        brl = (
            f"R$ {revenue_goal:,.2f}"
            .replace(",", "X").replace(".", ",").replace("X", ".")
        )
        parts.append(f"faturamento {brl}")
    if volume_goal:
        parts.append(f"volume {volume_goal} un.")
    if parts:
        flash(f"Metas do evento atualizadas: {', '.join(parts)}.", "success")
    else:
        flash("Metas do evento removidas.", "warning")
    ret_from = (request.form.get("ret_de") or "").strip()
    ret_to = (request.form.get("ret_ate") or "").strip()
    kw = {"evento": event_id}
    if ret_from:
        kw["de"] = ret_from
    if ret_to:
        kw["ate"] = ret_to
    next_page = (request.form.get("next") or "").strip()
    if next_page == "dashboard":
        return redirect(url_for("admin_event_detail", event_id=event_id))
    return redirect(url_for("admin_financeiro", **kw))


_ADMIN_MOVEMENT_TYPE_FILTERS = frozenset({"todos", "entrada", "saida", "venda"})


def _admin_movements_list_filters(*, event_id: int) -> dict:
    """Lê GET da listagem de movimentações de um evento."""
    q = (request.args.get("q") or "").strip()
    tipo_raw = (request.args.get("tipo") or "todos").strip().lower()
    tipo = tipo_raw if tipo_raw in _ADMIN_MOVEMENT_TYPE_FILTERS else "todos"
    pedido = (request.args.get("pedido") or "").strip()
    seller_raw = _parse_int(request.args.get("vendedor"), 0)
    event_seller_ids = {int(s["id"]) for s in list_event_sellers(event_id)}
    seller_id = seller_raw if seller_raw > 0 and seller_raw in event_seller_ids else 0
    date_from = _parse_tx_filter_date_arg(request.args.get("de"))
    date_to = _parse_tx_filter_date_arg(request.args.get("ate"))
    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from
    per_page = _parse_int(request.args.get("per_page"), DEFAULT_ADMIN_MOVEMENTS_PER_PAGE)
    if per_page not in ALLOWED_ADMIN_STOCK_PER_PAGE:
        per_page = DEFAULT_ADMIN_MOVEMENTS_PER_PAGE
    page = max(1, _parse_int(request.args.get("page"), 1))
    return {
        "q": q,
        "tipo": tipo,
        "pedido": pedido,
        "vendedor": seller_id,
        "de": date_from or "",
        "ate": date_to or "",
        "per_page": per_page,
        "page": page,
    }


def _admin_movements_query_kwargs(filters: dict, *, event_id: int) -> dict:
    tipo = filters["tipo"]
    seller_id = filters["vendedor"] or None
    if seller_id and tipo not in ("todos", "venda"):
        seller_id = None
    return {
        "product_search": filters["q"] or None,
        "movement_type": None if tipo == "todos" else tipo,
        "reference": filters["pedido"] or None,
        "event_id": int(event_id),
        "seller_id": seller_id,
        "date_from": filters["de"] or None,
        "date_to": filters["ate"] or None,
    }


def _admin_movements_page_data(event_id: int):
    filters = _admin_movements_list_filters(event_id=event_id)
    kwargs = _admin_movements_query_kwargs(filters, event_id=event_id)
    total = count_stock_movements(**kwargs)
    per_page = filters["per_page"]
    total_pages = max(1, (total + per_page - 1) // per_page) if total > 0 else 1
    page = min(filters["page"], total_pages)
    filters["page"] = page
    offset = (page - 1) * per_page
    movements = list_stock_movements(**kwargs, limit=per_page, offset=offset)
    showing_from = offset + 1 if total > 0 else 0
    showing_to = min(offset + len(movements), total) if total > 0 else 0
    pagination = {
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "showing_from": showing_from,
        "showing_to": showing_to,
    }
    return filters, movements, pagination


def _admin_movements_has_active_filters(filters: dict) -> bool:
    return bool(
        filters.get("q")
        or filters.get("tipo") not in (None, "", "todos")
        or filters.get("pedido")
        or int(filters.get("vendedor") or 0) > 0
        or filters.get("de")
        or filters.get("ate")
    )


@app.route("/admin/eventos/<int:event_id>/movimentacoes", endpoint="admin_event_movements")
@admin_required
def admin_event_movements(event_id: int):
    """Histórico de estoque do evento: vendas, entradas e saídas de todos os produtos."""
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))
    filters, movements, pagination = _admin_movements_page_data(event_id)
    live = pagination["page"] == 1
    return render_template(
        "admin/movements.html",
        event=event,
        movements=movements,
        filters=filters,
        pagination=pagination,
        allowed_per_page=ALLOWED_ADMIN_STOCK_PER_PAGE,
        movement_filter_sellers=list_event_sellers(event_id),
        movements_live=live,
        filters_active=_admin_movements_has_active_filters(filters),
        active_event_tab="movimentacoes",
        **_admin_shell_context(active_section="eventos"),
    )


def _admin_movements_export_qargs(filters: dict) -> dict:
    """Query args de filtro (sem paginação) para os links de exportação/voltar."""
    return {
        "q": filters.get("q") or None,
        "tipo": filters.get("tipo") or None,
        "pedido": filters.get("pedido") or None,
        "vendedor": filters.get("vendedor") or None,
        "de": filters.get("de") or None,
        "ate": filters.get("ate") or None,
    }


@app.route("/admin/eventos/<int:event_id>/movimentacoes/export.xlsx")
@admin_required
def admin_event_movements_export_xlsx(event_id: int):
    """Exporta as movimentações de estoque do evento em planilha .xlsx, respeitando os filtros ativos."""
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))

    filters = _admin_movements_list_filters(event_id=event_id)
    kwargs = _admin_movements_query_kwargs(filters, event_id=event_id)
    movements = list_stock_movements(**kwargs, limit=EXPORT_STOCK_CSV_CAP, offset=0)

    header = [
        "Data", "Hora", "Produto", "SKU", "Categoria", "Tipo",
        "Delta", "Saldo Após", "Referência", "Motivo", "Usuário",
    ]
    rows = []
    for m in movements:
        rows.append([
            _csv_fmt_date(m.get("created_at")),
            _csv_fmt_time(m.get("created_at")),
            _csv_cell(m.get("product_name")),
            _csv_cell(m.get("product_sku")),
            _csv_cell(m.get("product_category")),
            mov_label_filter(m.get("movement_type")),
            int(m.get("delta") or 0),
            int(m.get("balance_after") or 0),
            _csv_cell(m.get("reference")),
            _csv_cell(m.get("reason")),
            _csv_cell(_display_created_by(m.get("created_by")) or m.get("seller_name") or ""),
        ])

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_ev = re.sub(r"[^a-zA-Z0-9_-]+", "_", (event.get("name") or str(event_id)))[:40].strip("_") or str(event_id)
    fname = f"movimentacoes_evento_{event_id}_{safe_ev}_{ts}.xlsx"
    return _xlsx_attachment_response(fname, header, rows, sheet_title="Movimentações")


@app.route("/admin/eventos/<int:event_id>/movimentacoes/pdf")
@admin_required
def admin_event_movements_pdf(event_id: int):
    """Renderiza a versão para impressão/PDF das movimentações de estoque do evento."""
    event = _event_or_404(event_id)
    if event is None:
        return redirect(url_for("admin_events"))

    filters = _admin_movements_list_filters(event_id=event_id)
    kwargs = _admin_movements_query_kwargs(filters, event_id=event_id)
    total = count_stock_movements(**kwargs)
    movements = list_stock_movements(**kwargs, limit=ADMIN_EXPORT_PDF_ROWS_CAP, offset=0)

    summary = {
        "total": total,
        "shown": len(movements),
        "sales_count": sum(1 for m in movements if m.get("movement_type") == "venda"),
        "units_sold": sum(
            -int(m.get("delta") or 0) for m in movements if m.get("movement_type") == "venda"
        ),
        "entries_count": sum(
            1 for m in movements if m.get("movement_type") in ("entrada", "inicial")
        ),
        "units_entered": sum(
            int(m.get("delta") or 0)
            for m in movements
            if m.get("movement_type") in ("entrada", "inicial")
        ),
        "exits_count": sum(1 for m in movements if m.get("movement_type") == "saida"),
        "units_exited": sum(
            -int(m.get("delta") or 0) for m in movements if m.get("movement_type") == "saida"
        ),
    }

    seller_name = ""
    if filters["vendedor"]:
        seller_row = get_seller(filters["vendedor"])
        seller_name = (seller_row or {}).get("name", "")

    return render_template(
        "admin/movements_pdf.html",
        event=event,
        movements=movements,
        filters=filters,
        filters_active=_admin_movements_has_active_filters(filters),
        summary=summary,
        seller_name=seller_name,
        capped=total > len(movements),
        back_url=url_for(
            "admin_event_movements", event_id=event_id, **_admin_movements_export_qargs(filters)
        ),
        now=datetime.now(),
    )


@app.route("/admin/produtos/adicionar-ao-evento", methods=["POST"])
@admin_required
def admin_products_bulk_add_to_event():
    """Adiciona vários produtos da biblioteca a um evento, cada um com a própria quantidade."""
    event_id = _parse_int(request.form.get("event_id") or "", 0)
    if event_id <= 0:
        return jsonify({"error": "Selecione um evento."}), 400
    event = get_event(event_id)
    if event is None:
        return jsonify({"error": "Evento não encontrado."}), 400
    blocked = _reject_if_event_ops_closed(event)
    if blocked is not None:
        return blocked
    shared_raw = request.form.get("initial_stock")
    shared_qty = max(0, _parse_int(shared_raw, 0)) if shared_raw not in (None, "") else None
    min_stock = max(0, _parse_int(request.form.get("min_stock") or "", DEFAULT_MIN_STOCK))
    link_note = (request.form.get("link_note") or "").strip()
    if not link_note:
        return jsonify({"error": "Informe Motivo / Ref."}), 400

    seen: set[int] = set()
    product_ids: list[int] = []
    for raw in request.form.getlist("product_ids"):
        pid = _parse_int(raw, 0)
        if pid <= 0 or pid in seen:
            continue
        seen.add(pid)
        product_ids.append(pid)
    if not product_ids:
        return jsonify({"error": "Selecione ao menos um produto."}), 400

    admin_user = _current_admin_user()
    added_ids: list[int] = []
    errors: list[str] = []
    for product_id in product_ids:
        product = get_product(product_id)
        if product is None:
            errors.append(f"#{product_id}: produto não encontrado.")
            continue
        label = (product.get("name") or f"#{product_id}").strip()
        raw_qty = request.form.get(f"qty_{product_id}")
        if raw_qty not in (None, ""):
            qty = _parse_int(raw_qty, -1)
        elif shared_qty is not None:
            qty = shared_qty
        else:
            qty = -1
        if qty <= 0:
            errors.append(f"«{label}»: informe uma quantidade maior que zero.")
            continue
        try:
            add_product_to_event(
                event_id,
                product_id,
                qty,
                min_stock,
                link_audit_reason=link_note,
                link_audit_reference=None,
                created_by=admin_user,
            )
            added_ids.append(product_id)
        except ValueError as exc:
            errors.append(f"«{label}»: {exc}")

    ok_count = len(added_ids)
    event_name = event.get("name") or f"#{event_id}"
    if ok_count and not errors:
        return jsonify({
            "ok": True,
            "message": f"{ok_count} produto(s) adicionado(s) ao evento «{event_name}».",
            "added": ok_count,
            "added_ids": added_ids,
        })
    if ok_count:
        short = errors[:4]
        tail = f" (+{len(errors) - 4})" if len(errors) > 4 else ""
        return jsonify({
            "ok": True,
            "message": (
                f"{ok_count} adicionado(s) a «{event_name}». "
                f"Falha em {len(errors)}: " + "; ".join(short) + tail
            ),
            "added": ok_count,
            "added_ids": added_ids,
            "errors": errors,
        })
    return jsonify({
        "error": errors[0] if len(errors) == 1 else (
            "Nenhum produto adicionado. " + "; ".join(errors[:5])
        ),
    }), 409


# ---------------------------------------------------------------------------
# Rotas restauradas: os templates as chamam por url_for (ver HEAD)
# ---------------------------------------------------------------------------

@app.route("/admin/api/eventos/<int:event_id>/movimentacoes", endpoint="admin_api_event_movements")
@admin_required
def admin_api_event_movements(event_id: int):
    """Polling da listagem de movimentações do evento (mesmos filtros da página)."""
    if _event_or_404(event_id) is None:
        return jsonify({"error": "Evento não encontrado."}), 404
    filters, movements, pagination = _admin_movements_page_data(event_id)
    return jsonify({
        "ok": True,
        "filters": filters,
        "pagination": pagination,
        "movements": [_admin_movement_list_payload(m, event_id=event_id) for m in movements],
    })


_ADMIN_MOVEMENT_LIST_KEYS = (
    "id",
    "product_id",
    "product_name",
    "product_variant",
    "product_sku",
    "movement_type",
    "delta",
    "balance_after",
    "reason",
    "reference",
    "transaction_id",
    "event_id",
    "event_name",
    "event_badge_color",
    "created_by",
    "created_at",
    "seller_id",
    "seller_name",
    "order_number",
)


def _admin_movement_list_payload(movement: dict, *, event_id: int | None = None) -> dict:
    """Payload da listagem do evento: sem dados de cliente da transação."""
    slim = {key: movement.get(key) for key in _ADMIN_MOVEMENT_LIST_KEYS}
    payload = _movement_payload(slim)
    try:
        pid_int = int(movement.get("product_id") or 0)
    except (TypeError, ValueError):
        pid_int = 0
    if event_id and pid_int > 0:
        payload["product_url"] = url_for(
            "admin_event_stock_product",
            event_id=int(event_id),
            product_id=pid_int,
        )
    if event_id:
        ref = str(movement.get("reference") or "").strip()
        try:
            tid_int = int(movement.get("transaction_id") or 0)
        except (TypeError, ValueError):
            tid_int = 0
        if ref and tid_int > 0:
            payload["tx_url"] = (
                url_for("admin_event_transactions", event_id=int(event_id), pedido=ref)
                + f"#tx-{tid_int}"
            )
    return payload


if __name__ == "__main__":
    debug = _env_flag("TOTEM_DEBUG")
    bind_host = (os.environ.get("TOTEM_BIND") or "0.0.0.0").strip()
    port = int(os.environ.get("PORT") or "5000")
    if debug and bind_host not in ("127.0.0.1", "localhost", "::1"):
        # O debugger do Werkzeug executa código Python pelo navegador: aberto na
        # rede do evento, qualquer aparelho conectado controlaria o servidor.
        print(
            f"TOTEM_DEBUG=1 com TOTEM_BIND={bind_host}: o debugger só abre em "
            "127.0.0.1. Para atender a rede, desligue o TOTEM_DEBUG."
        )
        bind_host = "127.0.0.1"
    app.run(host=bind_host, port=port, debug=debug, use_reloader=debug)
