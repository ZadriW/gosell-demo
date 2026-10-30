"""Orquestração da integração ERP: catálogo e fila de pedidos.

- ``sync_catalog``: esvazia as filas de preço e de cadastro do gateway (lotes
  de 20, cada um confirmado depois de gravado) para a biblioteca e atualiza
  os tipos de negociação liberados (``sync_payment_types``).
- ``import_single_product``: traz um ``CODPROD`` que ainda não existe aqui.
- ``lookup_client``: cadastro do parceiro pelo CPF, para o checkout (com teto
  de tempo: o ERP lento não pode segurar o vendedor).
- ``process_outbox_once`` / ``start_worker``: envia clientes e pedidos da fila
  em segundo plano, sem travar o pagamento no estande.
"""
from __future__ import annotations

import base64
import binascii
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import sankhya_api
from database import erp as erp_db
from database.connection import get_conn
from database.products import (
    apply_erp_prices,
    find_local_product_for_codprod,
    get_erp_prices,
    upsert_products_from_erp,
)

log = logging.getLogger(__name__)

WORKER_INTERVAL_SECONDS = 30


# ---------------------------------------------------------------------------
# Catálogo
# ---------------------------------------------------------------------------

def _decode_image(raw) -> Optional[bytes]:
    """``IMAGEM`` em base64 (com ou sem prefixo ``data:``). Vazio = não altera a foto."""
    text = str(raw or "").strip()
    if not text:
        return None
    if text.startswith("data:") and "," in text:
        text = text.split(",", 1)[1]
    try:
        return base64.b64decode(text, validate=False)
    except (binascii.Error, ValueError):
        return None


def _prices_for_company(rows: List[Dict], codemp: int) -> Dict[int, float]:
    prices: Dict[int, float] = {}
    for r in rows:
        try:
            if int(r.get("CODEMP")) != codemp:
                continue
            prices[int(r.get("CODPROD"))] = float(r.get("PRECO"))
        except (TypeError, ValueError):
            continue
    return prices


def _to_items(products: List[Dict], prices: Dict[int, float]) -> List[Dict]:
    items = []
    for p in products:
        try:
            codprod = int(p.get("CODPROD"))
        except (TypeError, ValueError):
            continue
        items.append({
            "codprod": codprod,
            "name": p.get("DESCRPROD"),
            "category": p.get("DESCRGRUPOPROD"),
            "group_code": p.get("CODGRUPOPROD"),
            "brand": p.get("MARCA"),
            "codvol": p.get("CODVOL"),
            "anvisa_code": p.get("CODANVISA"),
            "supplier_ref": p.get("REFFORN"),
            "supplier_name": p.get("FORNECEDOR"),
            "description": p.get("CARACTERISTICAS"),
            "price": prices.get(codprod),
            "image": _decode_image(p.get("IMAGEM")),
        })
    return items


def _codemp() -> int:
    raw = (erp_db.get_erp_settings().get("codemp") or "1").strip()
    return int(raw) if raw.isdigit() else 1


#: Teto de lotes por fila numa sincronização (20 itens cada = 40 mil itens).
MAX_QUEUE_BATCHES = 2000


class QueueStuckError(sankhya_api.SankhyaError):
    """O gateway devolveu de novo um lote já confirmado: a confirmação não pegou."""


def _drain_queue(fetch, ack, handle, label: str, on_batch=None, key: str = "CODPROD") -> int:
    """Esvazia uma fila do gateway: busca um lote, grava aqui, confirma, repete.

    A confirmação só sai depois que o lote foi gravado no banco: se algo cair
    no meio, o lote continua "Aguard. Integração" no ERP e volta na próxima
    sincronização (gravar de novo é inofensivo). Devolve quantos itens vieram.
    ``on_batch(lotes, itens)`` é chamado depois de cada lote confirmado.
    ``key`` é o campo que identifica o item na fila e na confirmação.
    """
    acked: set = set()
    total = 0
    for batch_no in range(1, MAX_QUEUE_BATCHES + 1):
        batch = fetch()
        if not batch:
            return total
        codes = set()
        for row in batch:
            try:
                codes.add(int(row.get(key)))
            except (TypeError, ValueError):
                continue
        if not codes:
            raise QueueStuckError(f"Fila de {label} do ERP devolveu itens sem {key.upper()}.")
        if codes <= acked:
            raise QueueStuckError(
                f"A fila de {label} do ERP devolveu de novo itens já confirmados "
                f"({len(codes)}): a confirmação de integração não foi registrada no gateway."
            )
        handle(batch)
        ack(codes)
        acked |= codes
        total += len(batch)
        if on_batch:
            on_batch(batch_no, total)
    log.warning("Fila de %s do ERP: parou no teto de %d lotes.", label, MAX_QUEUE_BATCHES)
    return total


def payment_types_message(result: Dict) -> str:
    """Resumo de ``sync_payment_types`` para o painel e o histórico."""
    if not result["received"]:
        return "Tipos de negociação: nenhuma alteração aguardando integração no ERP."
    parts = [
        f"{result['received']} tipo(s) de negociação recebido(s) do ERP "
        "e marcado(s) como integrado(s)"
    ]
    for key, label in (("new", "novo(s)"), ("activated", "reativado(s)"),
                       ("deactivated", "desativado(s) no ERP")):
        if result[key]:
            parts.append(f"{label}: " + ", ".join(map(str, result[key])))
    text = "; ".join(parts) + "."
    if result["in_use_inactive"]:
        text += (
            " Atenção: " + ", ".join(map(str, result["in_use_inactive"]))
            + " ainda está(ão) escolhido(s) numa forma de pagamento; pedidos com ele(s) "
            "ficam na fila até você trocar em Tipos de negociação."
        )
    return text


def sync_payment_types() -> Dict:
    """Esvazia a fila de tipos de negociação do ERP (``orders/payment``).

    Cada lote é gravado e só então confirmado em
    ``orders/payment/registrar-cadastro`` (integrador, 13/10/2026). A fila só
    devolve o que mudou: vazia quer dizer "nada novo", não "nenhum liberado".
    Como nas filas do catálogo, quem confirma primeiro leva os itens: só uma
    instância do Go Sell deve sincronizar.
    """
    result: Dict = {"received": 0, "new": [], "activated": [], "deactivated": [],
                    "in_use_inactive": []}

    def handle(rows: List[Dict]) -> None:
        saved = erp_db.save_sankhya_payment_types(rows)
        result["received"] += saved["received"]
        for key in ("new", "activated", "deactivated", "in_use_inactive"):
            result[key] = sorted(set(result[key]) | set(saved[key]))

    _drain_queue(sankhya_api.fetch_payment_types, sankhya_api.ack_payment_types, handle,
                 "tipos de negociação", key="codtipvenda")
    result["message"] = payment_types_message(result)
    return result


def sync_catalog(run_id: Optional[int] = None) -> Dict:
    """Esvazia as filas de preço, cadastro e estoque do ERP. Não mexe no
    estoque dos eventos, em vendas nem em promoções.

    - Preços primeiro: ficam guardados (``erp_prices``) e o produto novo que
      vier depois pela fila de cadastro já entra com preço, em vez de inativo.
    - Estoque: só os lotes (``CONTROLE``) por local, em ``erp_stock``, para o
      pedido informar de onde sai cada item.
    - Cada fila roda mesmo que outra falhe (ex.: rota de confirmação ausente
      no gateway); a sincronização termina com erro listando as que falharam.
    """
    run_id = run_id or erp_db.start_sync_run("catalogo")
    codemp = _codemp()
    stats: Dict = {
        "inserted": 0, "updated": 0, "unchanged": 0, "skipped": 0, "images": 0,
        "activated": 0, "warnings": 0, "messages": [], "inserted_ids": [],
        "received": 0, "prices": 0, "price_changed": 0, "price_orphans": 0,
        "stock_rows": 0,
    }
    stock_replaced: set = set()

    def handle_stock(rows: List[Dict]) -> None:
        stats["stock_rows"] += erp_db.apply_erp_stock(rows, stock_replaced)

    def handle_prices(rows: List[Dict]) -> None:
        result = apply_erp_prices(_prices_for_company(rows, codemp))
        for key in ("prices", "price_changed", "activated", "price_orphans"):
            stats[key] += result[key]

    def handle_products(rows: List[Dict]) -> None:
        cached = get_erp_prices(
            int(p["CODPROD"]) for p in rows if str(p.get("CODPROD") or "").strip().isdigit()
        )
        result = upsert_products_from_erp(_to_items(rows, cached))
        for key in ("inserted", "updated", "unchanged", "skipped", "images", "activated", "warnings"):
            stats[key] += result[key]
        stats["inserted_ids"].extend(result["inserted_ids"])
        room = 50 - len(stats["messages"])
        if room > 0:
            stats["messages"].extend(result["messages"][:room])

    def progress(step: str):
        """Etapa atual gravada no histórico, para o painel acompanhar ao vivo."""
        def report(batch_no: int, items: int) -> None:
            try:
                erp_db.update_sync_progress(
                    run_id, stats, f"{step}: lote {batch_no} ({items} item(ns) recebido(s))…"
                )
            except Exception:  # progresso é só exibição; nunca derruba a sincronização
                log.exception("Falha ao gravar o progresso da sincronização")
        return report

    queues = (
        ("preços", "Preços", sankhya_api.fetch_prices, sankhya_api.ack_prices, handle_prices),
        ("cadastro", "Cadastro de produtos", sankhya_api.fetch_products,
         sankhya_api.ack_products, handle_products),
        ("estoque", "Estoque (lotes)", sankhya_api.fetch_stock, sankhya_api.ack_stock,
         handle_stock),
    )
    errors: List[str] = []
    empty_queues: List[str] = []
    for key, step, fetch, ack, handle in queues:
        erp_db.update_sync_progress(run_id, stats, f"{step}: buscando no ERP…")
        try:
            received = _drain_queue(fetch, ack, handle, key, progress(step))
            if not received:
                empty_queues.append(key)
            if key == "cadastro":
                stats["received"] = received
        except Exception as exc:
            log.exception("Fila de %s do ERP falhou", key)
            errors.append(f"{step}: {exc}")
    erp_db.update_sync_progress(run_id, stats, "Tipos de negociação: buscando no ERP…")
    try:
        stats["messages"].insert(0, sync_payment_types()["message"])
    except Exception as exc:
        log.exception("Tipos de negociação do ERP falharam")
        errors.append(f"Tipos de negociação: {exc}")
    error = "\n".join(errors) or None

    lines = []
    if len(empty_queues) == len(queues):
        # A fila é do ERP, não deste totem: quem confirma primeiro leva os itens.
        lines.append(
            "Nada aguardando integração no ERP (filas de preços, cadastro e "
            "estoque vazias). Itens já confirmados por outra instância do Go Sell "
            "não voltam para esta."
        )
    elif empty_queues:
        lines.append("Fila(s) vazia(s) no ERP: " + ", ".join(empty_queues) + ".")
    if stats["prices"]:
        lines.append(
            f"{stats['prices']} preço(s) recebido(s), {stats['price_changed']} alterado(s)"
            + (f"; {stats['price_orphans']} chegou(aram) antes do cadastro do produto e "
               "foi(ram) guardado(s) para ele" if stats["price_orphans"] else "")
            + "."
        )
    if stats["activated"]:
        lines.append(f"{stats['activated']} produto(s) ativado(s): o preço chegou do ERP.")
    if stats["stock_rows"]:
        lines.append(f"{stats['stock_rows']} linha(s) de estoque (local × lote) recebida(s).")
    lines.extend(stats["messages"])
    message = "\n".join(lines) or None
    # Lotes já confirmados ficam gravados mesmo quando a sincronização para no meio.
    if error:
        erp_db.finish_sync_run(run_id, "erro", stats=stats, message=message, error=error)
        raise sankhya_api.SankhyaError(error)
    erp_db.finish_sync_run(
        run_id, "parcial" if stats["warnings"] else "ok", stats=stats, message=message
    )
    return stats


_sync_lock = threading.Lock()


def start_catalog_sync_async() -> Optional[int]:
    """Roda a sincronização numa thread. ``None`` se já houver uma em andamento."""
    if not _sync_lock.acquire(blocking=False):
        return None
    try:
        # A trava acima é deste processo; com vários workers WSGI, o banco decide.
        if erp_db.other_sync_running("catalogo"):
            _sync_lock.release()
            return None
        run_id = erp_db.start_sync_run("catalogo")
    except Exception:
        _sync_lock.release()
        raise

    def _run() -> None:
        try:
            sync_catalog(run_id)
        except Exception:
            pass  # já registrado em erp_sync_runs
        finally:
            _sync_lock.release()

    threading.Thread(target=_run, name="sankhya-catalog-sync", daemon=True).start()
    return run_id


def catalog_sync_running() -> bool:
    return _sync_lock.locked() or erp_db.other_sync_running("catalogo")


# Teto da consulta de cliente no checkout. A chamada leva ~1,2 s (medido em
# 23/10/2026); passado o teto, o checkout segue com os dados do Go Sell e a
# requisição termina sozinha em segundo plano.
CLIENT_LOOKUP_BUDGET_SECONDS = 4.0
_client_lookup_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="sankhya-cliente")


def lookup_client(cpf: str) -> Tuple[Optional[Dict], str]:
    """Parceiro do ERP com este CPF. Devolve ``(linha, situação)``.

    Situação: ``found``, ``not_found``, ``off`` (consulta não configurada) ou
    ``error`` (falha ou passou do teto). Nunca levanta: é conveniência.
    """
    if not sankhya_api.is_configured() or not sankhya_api.client_filter_param():
        return None, "off"
    future = _client_lookup_pool.submit(
        sankhya_api.fetch_client_by_cpf, cpf, timeout=CLIENT_LOOKUP_BUDGET_SECONDS
    )
    try:
        row = future.result(timeout=CLIENT_LOOKUP_BUDGET_SECONDS)
    except FutureTimeout:
        log.warning("Consulta de cliente no ERP passou de %.0f s; usando o Go Sell.",
                    CLIENT_LOOKUP_BUDGET_SECONDS)
        return None, "error"
    except Exception as exc:  # noqa: BLE001 — qualquer falha cai no Go Sell
        log.warning("Consulta de cliente no ERP falhou: %s", sankhya_api.redact(exc))
        return None, "error"
    return (row, "found") if row else (None, "not_found")


def import_single_product(code: str) -> Optional[int]:
    """Busca um ``CODPROD`` no ERP e cadastra/atualiza aqui. Devolve o ``id`` local.

    Levanta ``SankhyaConfigError`` quando a busca avulsa não está configurada.
    """
    try:
        codprod = int(str(code).strip().lstrip("#"))
    except ValueError:
        return None
    products = [
        p for p in sankhya_api.fetch_products(codprod)
        if str(p.get("CODPROD")).strip() == str(codprod)
    ]
    if not products:
        return None
    # A fila de preços entrega cada preço uma vez: o que vale é o último guardado.
    upsert_products_from_erp(_to_items(products, get_erp_prices([codprod])))
    with get_conn() as conn:
        row = find_local_product_for_codprod(conn, codprod)
    return int(row["id"]) if row else None


# ---------------------------------------------------------------------------
# Fila de pedidos
# ---------------------------------------------------------------------------

def _process_order(job: Dict, settings: Dict[str, str]) -> None:
    with get_conn() as conn:
        tx = erp_db.load_transaction_for_erp(conn, int(job["transaction_id"]))
    if tx is not None and not tx.get("erp_idpedido") and tx.get("status") == "confirmado":
        # Venda que entrou na fila antes do IDPEDIDO numérico: ganha o número
        # agora, antes de qualquer envio, e mantém nas próximas tentativas.
        with get_conn() as conn:
            tx["erp_idpedido"] = erp_db.ensure_idpedido(conn, int(tx["id"]))
    if tx is None or tx.get("status") != "confirmado":
        # Estorno enquanto o pedido saía: ele não chegou ao ERP, então o
        # cancelamento que o estorno enfileirou também não tem o que cancelar.
        erp_db.close_claimed_job(job, "A venda não está mais confirmada.", "nao_enviado")
        return

    with get_conn() as conn:
        codvend, codvend_problem = erp_db.resolve_codvend(conn, tx, settings)
    if codvend_problem:
        erp_db.mark_job_failed(job, codvend_problem, transient=False, final=True)
        return

    codparc = tx.get("erp_codparc") or erp_db.get_cached_codparc(tx.get("client_cpf"))
    if not codparc and not erp_db.has_valid_client_cpf(tx):
        # Demo: venda sem CPF entra como consumidor final no ERP simulado.
        codparc = erp_db.CONSUMIDOR_FINAL_CODPARC
    if not codparc:
        # Cadastro só é exigido quando o cliente ainda não existe no ERP.
        customer, problems = erp_db.build_customer_payload(tx, settings, codparc, codvend)
        if problems:
            erp_db.mark_job_failed(job, "; ".join(problems), transient=False, final=True,
                                   payload={"cliente": customer})
            return
        try:
            result = sankhya_api.create_update_client(customer)
        except sankhya_api.SankhyaConfigError:
            raise  # login/endereço: pausa a fila inteira (process_outbox_once)
        except sankhya_api.SankhyaError as exc:
            erp_db.mark_job_failed(job, f"Cliente: {exc}", transient=exc.transient,
                                   payload={"cliente": customer}, response=exc.response)
            return
        codparc = result["codparc"]
        erp_db.save_customer_codparc(tx.get("client_cpf"), codparc, customer.get("NOMEPARC"))

    with get_conn() as conn:
        payload, problems = erp_db.build_order_payload(conn, tx, settings, codparc, codvend)
    if problems:
        erp_db.mark_job_failed(job, "; ".join(problems), transient=False, final=True,
                               payload=payload)
        return
    try:
        result = sankhya_api.create_order(payload)
    except sankhya_api.SankhyaConfigError:
        raise  # recusado antes de processar: nada entrou no ERP
    except sankhya_api.SankhyaUncertain as exc:
        # Reenviar sozinho poderia duplicar o pedido no ERP. Os lotes ficam
        # reservados: o pedido pode ter entrado e tirado esse saldo.
        erp_db.record_allocations(int(tx["id"]), payload)
        erp_db.mark_job_failed(
            job,
            f"{erp_db.UNCERTAIN_SEND_ERROR} Busque no ERP pelo IDPEDIDO "
            f"{tx.get('erp_idpedido')} (pedido {tx.get('order_number')}). ({exc})",
            transient=False, final=True, payload=payload, response=exc.response,
        )
        return
    except sankhya_api.SankhyaError as exc:
        erp_db.mark_job_failed(job, str(exc), transient=exc.transient,
                               payload=payload, response=exc.response)
        return
    erp_db.mark_job_sent(job, payload=payload, response=result["raw"],
                         erp_order_id=result["order_id"], codparc=codparc)
    log.info("Pedido %s enviado ao ERP (%s)", tx.get("order_number"), result["order_id"])


def _process_cancel(job: Dict) -> None:
    with get_conn() as conn:
        tx = erp_db.load_transaction_for_erp(conn, int(job["transaction_id"]))
        order_job = erp_db.get_order_job(conn, int(job["transaction_id"]))
    reached_erp = order_job is not None and (
        order_job["status"] == "enviado"
        or (order_job["status"] == "erro" and erp_db.is_uncertain_send(order_job))
    )
    if tx is None or not reached_erp:
        if order_job and order_job["status"] in ("pendente", "processando"):
            # Pedido ainda saindo: espera ele terminar antes de cancelar.
            erp_db.mark_job_failed(job, "Aguardando o envio do pedido.", transient=True)
            return
        erp_db.close_claimed_job(job, "O pedido nunca chegou ao ERP.", "nao_enviado")
        return
    # Pelo IDPEDIDO, e não pelo NUNOTA: também cancela o pedido cujo envio
    # ficou incerto (sem NUNOTA) e o pedido que o orçamento gerou no ERP.
    idpedido = tx.get("erp_idpedido")
    with get_conn() as conn:
        motivo = erp_db.cancel_reason(conn, tx)
    sent = {"IDPEDIDO": idpedido, "motivo": motivo}
    try:
        result = sankhya_api.cancel_order(idpedido, motivo)
    except sankhya_api.SankhyaConfigError:
        raise
    except sankhya_api.SankhyaError as exc:
        erp_db.mark_job_failed(job, str(exc), transient=exc.transient,
                               final=not exc.transient, response=exc.response,
                               payload=sent)
        return
    erp_db.mark_job_sent(job, payload=sent, response=result)
    log.info("Pedido %s (IDPEDIDO %s) cancelado no ERP",
             tx.get("order_number"), idpedido)


#: Login recusado ou endereço do gateway errado: a fila inteira espera este
#: tempo. Cada venda tentando por conta própria gastaria as tentativas dela
#: e somaria logins errados no ERP, que pode bloquear o usuário.
PAUSE_ON_CONFIG_ERROR_SECONDS = 300
_paused_until = 0.0
_pause_reason = ""


def resume_queue() -> None:
    """Admin pediu envio agora: tira a pausa de login/configuração."""
    global _paused_until, _pause_reason
    _paused_until = 0.0
    _pause_reason = ""


def queue_pause() -> Optional[Dict]:
    """Pausa em vigor (``{until, reason}``) ou ``None``. Vale para este processo."""
    if time.time() >= _paused_until:
        return None
    return {"until": datetime.fromtimestamp(_paused_until), "reason": _pause_reason}


def _pause_queue(exc: sankhya_api.SankhyaConfigError, jobs: List[Dict]) -> None:
    global _paused_until, _pause_reason
    _paused_until = time.time() + PAUSE_ON_CONFIG_ERROR_SECONDS
    _pause_reason = str(exc)
    retry_at = datetime.now() + timedelta(seconds=PAUSE_ON_CONFIG_ERROR_SECONDS)
    erp_db.release_claimed_jobs(
        jobs, f"Fila pausada até {retry_at:%H:%M}: {exc}", retry_at
    )
    log.warning(
        "Fila do ERP pausada por %d min (%d item(ns) devolvido(s)): %s",
        PAUSE_ON_CONFIG_ERROR_SECONDS // 60, len(jobs), exc,
    )


def process_outbox_once(limit: int = 20) -> Dict[str, int]:
    """Uma passada pela fila. Sem configuração completa, não envia nada."""
    out = {"processed": 0, "skipped": 0}
    if not sankhya_api.is_configured() or time.time() < _paused_until:
        return out
    settings = erp_db.get_erp_settings()
    if erp_db.orders_readiness(settings):
        return out
    erp_db.reset_stale_processing()
    jobs = erp_db.claim_due_jobs(limit)
    for i, job in enumerate(jobs):
        try:
            if job["kind"] == "pedido":
                _process_order(job, settings)
            else:
                _process_cancel(job)
            out["processed"] += 1
        except sankhya_api.SankhyaConfigError as exc:
            # Este item e os que ainda não saíram voltam para a fila intactos.
            _pause_queue(exc, jobs[i:])
            break
        except Exception as exc:  # nunca derruba o worker
            log.exception("Falha inesperada ao processar item %s da fila ERP", job["id"])
            erp_db.mark_job_failed(job, f"Erro interno: {exc}", transient=False)
    return out


_wake = threading.Event()
_worker_started = False
_worker_lock = threading.Lock()


def notify_worker() -> None:
    """Acorda o worker (venda confirmada, reenvio pedido pelo admin)."""
    _wake.set()


def _worker_loop() -> None:
    while True:
        try:
            while process_outbox_once()["processed"]:
                pass
        except Exception:
            log.exception("Worker da fila ERP falhou; tentando de novo no próximo ciclo")
        _wake.wait(WORKER_INTERVAL_SECONDS)
        _wake.clear()


def start_worker() -> bool:
    """Sobe o worker uma vez por processo. Desligue com ``TOTEM_ERP_WORKER=0``."""
    global _worker_started
    if (os.environ.get("TOTEM_ERP_WORKER") or "1").strip() == "0":
        return False
    with _worker_lock:
        if _worker_started:
            return False
        _worker_started = True
    threading.Thread(target=_worker_loop, name="sankhya-outbox", daemon=True).start()
    return True
