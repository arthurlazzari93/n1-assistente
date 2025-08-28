# app/movidesk_client.py

import os
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


def _ensure_ok(resp: httpx.Response, context: str):
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


def _contains_any(haystack: str, needles: list[str]) -> bool:
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

def _list_tickets(path: str, params: dict, context: str) -> list[dict]:
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


def _list_recent_batch(limit: int = 100, use_past: bool = False, with_orderby: bool = True, skip: int = 0) -> list[dict]:
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


def sample_email_channel(max_items: int = 300) -> list[dict]:
    """
    Retorna amostra de tickets com origin==3 para inspecionar originEmailAccount (debug).
    """
    results: list[dict] = []
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
def get_latest_ticket_for_email_account_multi(allowed_accounts: list[str]) -> dict:
    """
    Retorna o ticket mais recente com origin==3 e originEmailAccount combinando com alguma conta da lista.
    """
    allowed_accounts = [a for a in allowed_accounts if a and a.strip()]
    if not allowed_accounts:
        raise MovideskError("Lista de contas vazia para filtro")

    checked = 0
    page_size = 100
    max_items = 1500  # alcance maior para ambientes com muito volume

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
                        if int(t.get("origin", 0)) != 3:
                            continue
                    except Exception:
                        continue
                    acct = t.get("originEmailAccount") or ""
                    if _contains_any(acct, allowed_accounts):
                        return t
                checked += len(batch)
                skip += take

    raise MovideskError(
        f"Nenhum ticket via e-mail encontrado para as contas: {', '.join(allowed_accounts)}. "
        f"Tente aumentar o alcance ou confirme o valor da conta conforme aparece na API."
    )


# ---------------- Texto do chamado (assunto + corpo) ----------------

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def get_ticket_text_bundle(ticket_id: int) -> dict:
    """
    Retorna:
      - subject
      - first_action_text (texto plano)
      - first_action_html (se disponível)

    Estratégia:
      A) Sempre tenta pegar "subject" via get_ticket_by_id como fallback inicial.
      B) /tickets/{id}?$select=id,subject&$expand=actions($orderby=id asc;$top=5)
      C) /tickets/{id}?$select=id,subject&$expand=actions($top=5)
      D) /tickets/{id}/actions?$orderby=id asc&$top=5
      E) /tickets/{id}/actions?$top=5
      F) /tickets/{id}/htmldescription
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

    # Fallback imediato: tenta obter o assunto pela rota “segura”
    try:
        base = get_ticket_by_id(ticket_id)
        subject = base.get("subject") or ""
    except Exception:
        pass

    def _take_from_actions(actions: list[dict]):
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
