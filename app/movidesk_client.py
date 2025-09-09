# app/movidesk_client.py
import os
from http import HTTPStatus
from typing import List, Dict

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential
from dotenv import load_dotenv

load_dotenv()

MOVIDESK_BASE = "https://api.movidesk.com/public/v1"



class MovideskError(Exception):
    pass


def _get_token() -> str:
    token = os.getenv("MOVIDESK_TOKEN", "").strip()
    if not token:
        raise MovideskError("MOVIDESK_TOKEN não configurado no .env")
    return token


def _raise_http_error(resp: httpx.Response, context: str):
    try:
        detail = resp.text[:1200]
    except Exception:
        detail = "<sem corpo>"
    raise MovideskError(
        f"[{context}] HTTP {resp.status_code} ao chamar {resp.request.method} {resp.request.url}. "
        f"Resposta: {detail}"
    )


def _ensure_ok(resp: httpx.Response, context: str) -> bool:
    # 429 -> deixa estourar para o tenacity fazer retry/backoff
    if resp.status_code == 429:
        _raise_http_error(resp, context)
    if resp.status_code >= 400:
        return False
    return True


def _pick_first(items):
    return items[0] if isinstance(items, list) and items else None


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _contains_any(haystack: str, needles: List[str]) -> bool:
    h = _norm(haystack)
    for n in needles:
        nrm = _norm(n)
        if not nrm:
            continue
        if h == nrm or (nrm in h):
            return True
    return False


# ---------------- Ticket por ID ----------------

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def get_ticket_by_id(ticket_id: int) -> dict:
    """
    Busca robusta por ID com várias tentativas ($select/$expand variam por tenant).
    """
    token = _get_token()
    headers = {"Accept": "application/json"}
    select_safe = "id,subject,origin,originEmailAccount,createdDate,status,category,urgency"

    with httpx.Client(timeout=25, headers=headers) as client:
        # 1) direta simples
        r = client.get(f"{MOVIDESK_BASE}/tickets/{ticket_id}", params={"token": token})
        if r.status_code == 200:
            return r.json()
        if r.status_code not in (404, 400):
            _raise_http_error(r, "tickets/{id} (simples)")

        # 2) direta com select/expand
        params = {"token": token, "$select": select_safe, "$expand": "owner,clients"}
        r = client.get(f"{MOVIDESK_BASE}/tickets/{ticket_id}", params=params)
        if r.status_code == 200:
            return r.json()
        if r.status_code not in (404, 400):
            _raise_http_error(r, "tickets/{id} (select+expand)")

        # 3) lista com filtro + expand
        params = {
            "token": token,
            "$filter": f"id eq {ticket_id}",
            "$select": select_safe,
            "$expand": "owner,clients",
            "$top": 1,
        }
        r = client.get(f"{MOVIDESK_BASE}/tickets", params=params)
        if _ensure_ok(r, "tickets (filter+select+expand)"):
            data = r.json()
            t = _pick_first(data)
            if t:
                return t

        # 4) lista com filtro sem expand
        params = {"token": token, "$filter": f"id eq {ticket_id}", "$select": select_safe, "$top": 1}
        r = client.get(f"{MOVIDESK_BASE}/tickets", params=params)
        if _ensure_ok(r, "tickets (filter+select)"):
            data = r.json()
            t = _pick_first(data)
            if t:
                return t

        # 5) past
        params = {"token": token, "$filter": f"id eq {ticket_id}", "$select": select_safe, "$top": 1}
        r = client.get(f"{MOVIDESK_BASE}/tickets/past", params=params)
        if _ensure_ok(r, "tickets/past (filter+select)"):
            data = r.json()
            t = _pick_first(data)
            if t:
                return t

    raise MovideskError(f"Ticket {ticket_id} não encontrado em nenhuma rota suportada.")


# ---------------- Listagens para varrer recentes / “último da TI” ----------------

def _list_tickets(path: str, params: dict, context: str) -> List[dict]:
    token = _get_token()
    headers = {"Accept": "application/json"}
    p = dict(params)
    p["token"] = token
    with httpx.Client(timeout=25, headers=headers) as client:
        r = client.get(f"{MOVIDESK_BASE}/{path}", params=p)
        if not _ensure_ok(r, f"{path} ({context})"):
            return []
        data = r.json()
        return data if isinstance(data, list) else []


def _list_recent_batch(limit: int = 100, use_past: bool = False, with_orderby: bool = True, skip: int = 0) -> List[dict]:
    """
    Busca um lote de tickets recentes (qualquer canal). Quem chama filtra por origin==3 e por conta.
    """
    select_safe = "id,subject,origin,originEmailAccount,createdDate,status,category,urgency"
    filter_expr = "lastUpdate ge 2000-01-01T00:00:00Z"
    path = "tickets/past" if use_past else "tickets"

    params = {
        "$select": select_safe,
        "$expand": "owner,clients",
        "$top": limit,
        "$skip": skip,
        "$filter": filter_expr,
    }
    if with_orderby:
        params["$orderby"] = "id desc"

    return _list_tickets(path, params, f"_list_recent_batch {path} skip={skip}")


def sample_email_channel(max_items: int = 300) -> List[dict]:
    """
    Retorna amostra de tickets com origin==3 para inspecionar originEmailAccount (debug).
    """
    results: List[dict] = []
    checked = 0
    page_size = 100

    for use_past in (False, True):
        for with_orderby in (True, False):
            skip = 0
            while checked < max_items:
                take = min(page_size, max_items - checked)
                batch = _list_recent_batch(limit=take, use_past=use_past, with_orderby=with_orderby, skip=skip)
                if not batch:
                    break
                for t in batch:
                    try:
                        if int(t.get("origin", 0)) == 3:
                            results.append({
                                "id": t.get("id"),
                                "subject": t.get("subject"),
                                "originEmailAccount": t.get("originEmailAccount") or "",
                            })
                    except Exception:
                        pass
                checked += len(batch)
                skip += take

    # dedup preservando ordem
    seen = set()
    unique = []
    for r in results:
        k = r["id"]
        if k in seen:
            continue
        seen.add(k)
        unique.append(r)
    return unique


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def get_latest_ticket_for_email_account_multi(allowed_accounts: List[str], max_take: int = 50) -> List[dict]:
    """
    Retorna até 'max_take' tickets mais recentes (origin==3) cuja originEmailAccount
    combine com algum item de 'allowed_accounts'. Mantém ordem recente e remove duplicados.
    Compatível com /debug/latest-ti.
    """
    allowed_accounts = [a for a in allowed_accounts if a and a.strip()]
    if not allowed_accounts:
        raise MovideskError("Lista de contas vazia para filtro")

    results: List[dict] = []
    seen_ids: set[int] = set()

    checked = 0
    page_size = 100
    # alcance total proporcional ao que o caller pediu
    max_items = max(500, max_take * 20)

    for use_past in (False, True):
        for with_orderby in (True, False):
            skip = 0
            while checked < max_items and len(results) < max_take:
                take = min(page_size, max_items - checked)
                batch = _list_recent_batch(limit=take, use_past=use_past, with_orderby=with_orderby, skip=skip)
                if not batch:
                    break

                for t in batch:
                    try:
                        if int(t.get("origin", 0)) != 3:
                            continue
                    except Exception:
                        continue

                    acc = (t.get("originEmailAccount") or "")
                    if not _contains_any(acc, allowed_accounts):
                        continue

                    tid = t.get("id")
                    if tid in seen_ids:
                        continue
                    seen_ids.add(tid)
                    results.append(t)

                    if len(results) >= max_take:
                        break

                checked += len(batch)
                skip += take

            if len(results) >= max_take:
                break
        if len(results) >= max_take:
            break

    return results


# ---------------- Texto do chamado (assunto + corpo) ----------------

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def get_ticket_text_bundle(ticket_id: int) -> dict:
    """
    Retorna:
      - subject
      - first_action_text (texto plano)
      - first_action_html (se disponível)
    """
    token = _get_token()
    headers = {"Accept": "application/json"}

    def _clean_html(html: str) -> str:
        if not html:
            return ""
        try:
            import re
            txt = re.sub(r"(?i)<br\s*/?>", "\n", html)
            txt = re.sub(r"(?s)<style.*?>.*?</style>", " ", txt)
            txt = re.sub(r"(?s)<script.*?>.*?</script>", " ", txt)
            txt = re.sub(r"<[^>]+>", " ", txt)
            txt = re.sub(r"\s+", " ", txt)
            return txt.strip()
        except Exception:
            return html

    subject = ""
    first_text = ""
    first_html = ""

    # Fallback imediato: tenta obter o assunto
    try:
        base = get_ticket_by_id(ticket_id)
        subject = base.get("subject") or ""
    except Exception:
        pass

    def _take_from_actions(actions: List[dict]):
        nonlocal first_text, first_html
        for a in actions or []:
            first_html = a.get("htmlDescription") or a.get("description") or ""
            first_text = a.get("description") or _clean_html(first_html)
            if first_text or first_html:
                return True
        return False

    with httpx.Client(timeout=25, headers=headers) as client:
        # B) expand com orderby asc
        params = {"token": token, "$select": "id,subject", "$expand": "actions($orderby=id asc;$top=5)"}
        r = client.get(f"{MOVIDESK_BASE}/tickets/{ticket_id}", params=params)
        if _ensure_ok(r, "tickets/{id} expand actions+orderby"):
            if r.status_code == 200:
                data = r.json()
                subject = subject or (data.get("subject") or "")
                if _take_from_actions(data.get("actions") or []):
                    return {"subject": subject, "first_action_text": first_text, "first_action_html": first_html}

        # C) expand sem orderby
        params = {"token": token, "$select": "id,subject", "$expand": "actions($top=5)"}
        r = client.get(f"{MOVIDESK_BASE}/tickets/{ticket_id}", params=params)
        if _ensure_ok(r, "tickets/{id} expand actions"):
            if r.status_code == 200:
                data = r.json()
                subject = subject or (data.get("subject") or "")
                if _take_from_actions(data.get("actions") or []):
                    return {"subject": subject, "first_action_text": first_text, "first_action_html": first_html}

        # D) actions com orderby asc
        params = {"token": token, "$top": 5, "$orderby": "id asc"}
        r = client.get(f"{MOVIDESK_BASE}/tickets/{ticket_id}/actions", params=params)
        if _ensure_ok(r, "tickets/{id}/actions orderby asc"):
            if r.status_code == 200 and isinstance(r.json(), list):
                if _take_from_actions(r.json()):
                    return {"subject": subject, "first_action_text": first_text, "first_action_html": first_html}

        # E) actions sem orderby
        params = {"token": token, "$top": 5}
        r = client.get(f"{MOVIDESK_BASE}/tickets/{ticket_id}/actions", params=params)
        if _ensure_ok(r, "tickets/{id}/actions"):
            if r.status_code == 200 and isinstance(r.json(), list):
                if _take_from_actions(r.json()):
                    return {"subject": subject, "first_action_text": first_text, "first_action_html": first_html}

        # F) html do ticket
        params = {"token": token}
        r = client.get(f"{MOVIDESK_BASE}/tickets/{ticket_id}/htmldescription", params=params)
        if _ensure_ok(r, "tickets/{id}/htmldescription"):
            if r.status_code == 200:
                html = r.text or ""
                first_html = html
                first_text = _clean_html(html)

    return {
        "subject": subject,
        "first_action_text": first_text,
        "first_action_html": first_html,
    }


# ---------------- Ações/Notas públicas + fechamento ----------------

def _ok_response(r: httpx.Response) -> dict:
    try:
        data = r.json()
    except Exception:
        data = {"status": r.status_code}
    return data


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def add_public_note(ticket_id: int, note_text: str) -> dict:
    """
    Cria uma AÇÃO pública na timeline do ticket.
    Tenta nas seguintes ordens (todas com ?token=...&id=... na URL):
      1) POST /tickets  (X-HTTP-Method-Override: PATCH)  -> actions[id=0]
      2) PATCH /tickets                                  -> actions[id=0]
      3) POST /tickets/{id}/actions                      -> fallback
      4) POST /tickets  (Override)                       -> notes[id=0]
      5) PATCH /tickets                                  -> notes[id=0]
    """
    token = _get_token()
    headers_json = {"Accept": "application/json", "Content-Type": "application/json"}
    params = {"token": token, "id": str(int(ticket_id))}

    text = (note_text or "").strip()
    if not text:
        raise MovideskError("Texto da nota/ação vazio.")

    def _ok(r: httpx.Response) -> bool:
        return r.status_code in (HTTPStatus.OK, HTTPStatus.CREATED, HTTPStatus.ACCEPTED, HTTPStatus.NO_CONTENT)

    def _resp(r: httpx.Response) -> dict:
        try:
            return r.json()
        except Exception:
            return {"status": r.status_code}

    # payload preferencial: ACTION pública
    body_action = {
        "actions": [
            {
                "id": 0,                   # id=0 => inserir
                "description": text,
                "isPublic": True,
                "isHtmlDescription": False,
                "origin": 4,               # Gatilho do sistema
                "type": 1                  # Pública
            }
        ]
    }

    # 1) POST override PATCH /tickets (actions)
    headers_override = dict(headers_json)
    headers_override["X-HTTP-Method-Override"] = "PATCH"
    with httpx.Client(timeout=30, headers=headers_override) as c1:
        r1 = c1.post(f"{MOVIDESK_BASE}/tickets", params=params, json=body_action)
        if _ok(r1):
            return {"ok": True, "status": r1.status_code, "attempt": "POST override PATCH /tickets (actions)", "response": _resp(r1)}

    # 2) PATCH /tickets (actions)
    with httpx.Client(timeout=30, headers=headers_json) as c2:
        r2 = c2.patch(f"{MOVIDESK_BASE}/tickets", params=params, json=body_action)
        if _ok(r2):
            return {"ok": True, "status": r2.status_code, "attempt": "PATCH /tickets (actions)", "response": _resp(r2)}

    # 3) POST /tickets/{id}/actions (fallback)
    body_post_action = {
        "description": text,
        "isPublic": True,
        "isHtmlDescription": False,
        "origin": 4,
        "type": 1,
    }
    with httpx.Client(timeout=30, headers=headers_json) as c3:
        r3 = c3.post(f"{MOVIDESK_BASE}/tickets/{ticket_id}/actions", params={"token": token}, json=body_post_action)
        if _ok(r3):
            return {"ok": True, "status": r3.status_code, "attempt": "POST /tickets/{id}/actions", "response": _resp(r3)}

    # 4) NOTES (override primeiro)
    body_note = {"notes": [{"id": 0, "description": text, "isPublic": True}]}
    with httpx.Client(timeout=30, headers=headers_override) as c4:
        r4 = c4.post(f"{MOVIDESK_BASE}/tickets", params=params, json=body_note)
        if _ok(r4):
            return {"ok": True, "status": r4.status_code, "attempt": "POST override PATCH /tickets (notes)", "kind": "note", "response": _resp(r4)}

    # 5) PATCH /tickets (notes)
    with httpx.Client(timeout=30, headers=headers_json) as c5:
        r5 = c5.patch(f"{MOVIDESK_BASE}/tickets", params=params, json=body_note)
        if _ok(r5):
            return {"ok": True, "status": r5.status_code, "attempt": "PATCH /tickets (notes)", "kind": "note", "response": _resp(r5)}

        try:
            last_detail = r5.text[:1200]
        except Exception:
            last_detail = f"HTTP {r5.status_code} (sem corpo)"

    raise MovideskError(
        f"[tickets] Falha ao anexar ação/nota pública no ticket {ticket_id}. Última resposta: {last_detail}"
    )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def close_ticket(ticket_id: int, status_name: str = "Resolvido", justification: str | None = None) -> dict:
    """
    Ajusta o status via PATCH /tickets (&id na query) ou override por POST,
    com 'justification' opcional (se sua base exigir motivo).
    """
    token = _get_token()
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    params = {"token": token, "id": str(int(ticket_id))}
    body = {"status": status_name}
    if justification:
        body["justification"] = justification

    # PATCH direto
    with httpx.Client(timeout=20, headers=headers) as c1:
        r1 = c1.patch(f"{MOVIDESK_BASE}/tickets", params=params, json=body)
        if r1.status_code in (HTTPStatus.OK, HTTPStatus.ACCEPTED, HTTPStatus.NO_CONTENT, HTTPStatus.CREATED):
            return {"ok": True, "status": r1.status_code, "attempt": "PATCH /tickets (status)", "response": _ok_response(r1)}

    # Override (POST com X-HTTP-Method-Override: PATCH)
    headers2 = dict(headers)
    headers2["X-HTTP-Method-Override"] = "PATCH"
    with httpx.Client(timeout=20, headers=headers2) as c2:
        r2 = c2.post(f"{MOVIDESK_BASE}/tickets", params=params, json=body)
        if r2.status_code in (HTTPStatus.OK, HTTPStatus.ACCEPTED, HTTPStatus.NO_CONTENT, HTTPStatus.CREATED):
            return {"ok": True, "status": r2.status_code, "attempt": "POST override PATCH /tickets (status)", "response": _ok_response(r2)}

    _raise_http_error(r1, "tickets (close_ticket)")


# ---------------- Utilitários de auditoria ----------------

def list_actions(ticket_id: int, top: int = 10) -> List[dict]:
    token = _get_token()
    headers = {"Accept": "application/json"}
    with httpx.Client(timeout=20, headers=headers) as client:
        r = client.get(
            f"{MOVIDESK_BASE}/tickets/{ticket_id}/actions",
            params={"token": token, "$orderby": "id desc", "$top": top},
        )
        if not _ensure_ok(r, "tickets/{id}/actions"):
            return []
        data = r.json()
        return data if isinstance(data, list) else []


def list_actions_expand(ticket_id: int, top: int = 10) -> List[dict]:
    token = _get_token()
    headers = {"Accept": "application/json"}
    with httpx.Client(timeout=20, headers=headers) as client:
        r = client.get(
            f"{MOVIDESK_BASE}/tickets/{ticket_id}",
            params={"token": token, "$select": "id", "$expand": f"actions($orderby=id desc;$top={top})"},
        )
        if not _ensure_ok(r, "tickets/{id} expand actions"):
            return []
        data = r.json() or {}
        return data.get("actions") or []


def list_notes(ticket_id: int, top: int = 10) -> List[dict]:
    token = _get_token()
    headers = {"Accept": "application/json"}
    with httpx.Client(timeout=20, headers=headers) as client:
        r = client.get(
            f"{MOVIDESK_BASE}/tickets/{ticket_id}",
            params={"token": token, "$select": "id", "$expand": f"notes($orderby=id desc;$top={top})"},
        )
        if not _ensure_ok(r, "tickets/{id} expand notes"):
            return []
        data = r.json() or {}
        return data.get("notes") or []
