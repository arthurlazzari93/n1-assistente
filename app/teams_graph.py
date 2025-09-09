# app/teams_graph.py
from __future__ import annotations

import os
import time
from typing import Optional, Dict, Any

import requests


class TeamsGraphError(RuntimeError):
    pass


GRAPH = "https://graph.microsoft.com/v1.0"


# ---------------- Credenciais (via ambiente, com fallbacks) ----------------

def _env(*keys: str, default: str = "") -> str:
    for k in keys:
        v = os.getenv(k)
        if v:
            return v
    return default

# Preferência: MS_* ; depois TEAMS_* ; por fim BOT_* (mesma App Registration)
TENANT_ID = _env("MS_TENANT_ID", "TEAMS_TENANT_ID")
CLIENT_ID = _env("MS_CLIENT_ID", "TEAMS_CLIENT_ID", "BOT_APP_ID")
CLIENT_SECRET = _env("MS_CLIENT_SECRET", "TEAMS_CLIENT_SECRET", "BOT_APP_PASSWORD")


# ---------------- HTTP helpers ----------------

def _token(scope: str = "https://graph.microsoft.com/.default") -> str:
    """
    App-only token (Client Credentials). A App Registration precisa de permissões de APLICAÇÃO:
      - Chat.ReadWrite.All
      - User.Read.All
      - (Opcional) TeamsAppInstallation.ReadForUser.All para listar apps instalados
    """
    if not TENANT_ID or not CLIENT_ID or not CLIENT_SECRET:
        raise TeamsGraphError(
            "Credenciais do Graph ausentes. Defina MS_TENANT_ID, MS_CLIENT_ID e MS_CLIENT_SECRET "
            "(ou equivalentes TEAMS_* / BOT_*)."
        )
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope": scope,
        "grant_type": "client_credentials",
    }
    url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    r = requests.post(url, data=data, timeout=30)
    if r.status_code != 200:
        raise TeamsGraphError(f"Falha ao obter token: {r.status_code} {r.text}")
    return r.json()["access_token"]


def _g(method: str, url: str, token: str, **kwargs) -> requests.Response:
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {token}"
    headers.setdefault("Content-Type", "application/json")
    return requests.request(method, url, headers=headers, timeout=30, **kwargs)


# ---------------- Usuários / Chats ----------------

def get_user_id_by_mail(email: str) -> Optional[str]:
    """
    Busca o usuário pelo campo 'mail' e, se não achar, tenta 'userPrincipalName'.
    """
    t = _token()
    # 1) procurar por mail
    r1 = _g("GET", f"{GRAPH}/users?$filter=mail eq '{email}'", t)
    if r1.status_code != 200:
        raise TeamsGraphError(f"Falha ao buscar usuário (mail): {r1.status_code} {r1.text}")
    v1 = r1.json().get("value", [])
    if v1:
        return v1[0].get("id")

    # 2) tentar por userPrincipalName
    r2 = _g("GET", f"{GRAPH}/users/{email}", t)
    if r2.status_code == 200:
        return r2.json().get("id")

    return None


def get_user_by_email(email: str) -> Dict[str, Any]:
    """
    Retorna dict com 'id', 'mail', 'displayName' (quando possível).
    """
    t = _token()
    # tenta por mail
    r1 = _g("GET", f"{GRAPH}/users?$filter=mail eq '{email}'", t)
    if r1.status_code == 200:
        v = r1.json().get("value", [])
        if v:
            u = v[0]
            return {"id": u.get("id"), "mail": u.get("mail") or email, "displayName": u.get("displayName")}
    # tenta por UPN
    r2 = _g("GET", f"{GRAPH}/users/{email}", t)
    if r2.status_code == 200:
        u = r2.json()
        return {"id": u.get("id"), "mail": u.get("mail") or email, "displayName": u.get("displayName")}
    return {"id": None, "mail": email, "displayName": None}


def _get_or_create_one_on_one_chat(user_id: str, token: str) -> str:
    """
    Obtém um chat 1:1 existente; se não houver, cria um novo.
    """
    # tenta pegar um chat existente do usuário (mais recente)
    r1 = _g("GET", f"{GRAPH}/users/{user_id}/chats?$top=1", token)
    if r1.status_code == 200:
        items = r1.json().get("value", [])
        if items:
            return items[0]["id"]

    # cria um chat 1:1
    payload = {
        "chatType": "oneOnOne",
        "members": [
            {
                "@odata.type": "#microsoft.graph.aadUserConversationMember",
                "roles": ["owner"],
                "user@odata.bind": f"{GRAPH}/users('{user_id}')",
            }
        ],
    }
    r2 = _g("POST", f"{GRAPH}/chats", token, json=payload)
    if r2.status_code not in (201, 409):
        raise TeamsGraphError(f"Falha ao criar chat: {r2.status_code} {r2.text}")
    if r2.status_code == 201:
        return r2.json()["id"]

    # conflito — aguarda e busca de novo
    time.sleep(1.0)
    r3 = _g("GET", f"{GRAPH}/users/{user_id}/chats?$top=1", token)
    if r3.status_code == 200 and r3.json().get("value"):
        return r3.json()["value"][0]["id"]
    raise TeamsGraphError("Não foi possível obter/criar chat 1:1.")


def _send_chat_message(chat_id: str, text: str, token: str) -> None:
    body = {"body": {"contentType": "text", "content": text}}
    r = _g("POST", f"{GRAPH}/chats/{chat_id}/messages", token, json=body)
    if r.status_code not in (201, 200):
        raise TeamsGraphError(f"Falha ao enviar mensagem: {r.status_code} {r.text}")


# ---------------- Diagnóstico & Compat ----------------

def diag_token_info() -> Dict[str, Any]:
    """
    Retorna informações básicas de diagnóstico do token e tenant.
    """
    try:
        t = _token()
        # tenta pegar dados mínimos da organização só para validar o token
        r = _g("GET", f"{GRAPH}/organization?$select=id,displayName", t)
        org = None
        if r.status_code == 200 and r.json().get("value"):
            org = r.json()["value"][0]
        return {
            "tenant_id": TENANT_ID,
            "client_id": CLIENT_ID[:6] + "…" if CLIENT_ID else None,
            "scopes": "Graph /.default",
            "org": org,
            "ok": True,
        }
    except Exception as e:
        return {
            "tenant_id": TENANT_ID,
            "client_id": CLIENT_ID[:6] + "…" if CLIENT_ID else None,
            "error": str(e),
            "ok": False,
        }


def diag_resolve_app() -> Dict[str, Any]:
    """
    Compat com versões antigas: antes havia um 'app catalog'. Nesta abordagem
    usamos chat 1:1 direto, então não há catálogo a resolver.
    """
    try:
        _ = _token()  # valida credenciais
        return {
            "ok": True,
            "mode": "direct_chat",
            "note": "Usando chat 1:1 via Graph; catálogo de app não é necessário.",
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def diag_user(email: str) -> Dict[str, Any]:
    """
    Retorna dados essenciais do usuário (id, UPN, displayName, mail, enabled).
    """
    t = _token()
    # tenta por mail
    r1 = _g("GET", f"{GRAPH}/users?$filter=mail eq '{email}'", t)
    if r1.status_code == 200:
        v = r1.json().get("value", [])
        if v:
            u = v[0]
            return {
                "ok": True,
                "id": u.get("id"),
                "userPrincipalName": u.get("userPrincipalName"),
                "displayName": u.get("displayName"),
                "mail": u.get("mail"),
                "accountEnabled": u.get("accountEnabled"),
            }
    # tenta por UPN (email como UPN)
    r2 = _g("GET", f"{GRAPH}/users/{email}", t)
    if r2.status_code == 200:
        u = r2.json()
        return {
            "ok": True,
            "id": u.get("id"),
            "userPrincipalName": u.get("userPrincipalName"),
            "displayName": u.get("displayName"),
            "mail": u.get("mail"),
            "accountEnabled": u.get("accountEnabled"),
        }
    return {"ok": False, "error": f"Usuário não encontrado para {email}", "status": r2.status_code}


def diag_user_installed_apps(email: str) -> Dict[str, Any]:
    """
    Lista (quando permitido) os apps instalados para o usuário.
    Requer permissões específicas. Se 403/404, retorna erro amigável (não lança).
    """
    t = _token()
    # obter id
    uid = get_user_id_by_mail(email)
    if not uid:
        return {"ok": False, "error": f"Usuário não encontrado para {email}"}

    # endpoint de installed apps do usuário
    # Doc: GET /users/{id}/teamwork/installedApps?$expand=teamsApp
    url = f"{GRAPH}/users/{uid}/teamwork/installedApps?$expand=teamsApp"
    r = _g("GET", url, t)
    if r.status_code == 200:
        vals = r.json().get("value", [])
        apps = []
        for it in vals:
            app = (it.get("teamsApp") or {})
            apps.append({"id": app.get("id"), "displayName": app.get("displayName")})
        return {"ok": True, "count": len(apps), "apps": apps}
    else:
        return {
            "ok": False,
            "status": r.status_code,
            "error": r.text,
            "hint": "Verifique permissões de aplicação: TeamsAppInstallation.ReadForUser.All",
        }


def ensure_app_installed_and_current(user_id: str, wait_seconds: int = 0, interval: float = 0.0) -> None:
    """
    Stub compatível com versões antigas do código.
    Como estamos enviando mensagem 1:1 por chat, não é necessário instalar App personalizada.
    """
    return None


def send_activity_notification_to_user(user_id: str, ticket_id: int, subject: str, preview_text: Optional[str] = None):
    """
    Compatibilidade com chamadas antigas de 'activity notification':
    aqui enviamos uma mensagem de chat 1:1 com o mesmo texto.
    """
    text = preview_text or f"Olá! Recebemos seu chamado #{ticket_id} sobre \"{subject}\". Podemos iniciar o atendimento agora?"
    t = _token()
    chat_id = _get_or_create_one_on_one_chat(user_id, t)
    _send_chat_message(chat_id, text, t)


# ---------------- API pública principal ----------------

def notify_user_for_ticket(user_email: str, ticket_id: int, subject: str, preview_text: Optional[str] = None) -> None:
    """
    Envia uma mensagem proativa 1:1 no Teams para o usuário do e-mail informado.
    """
    text = preview_text or f"Olá! Recebemos seu chamado #{ticket_id} sobre \"{subject}\". Podemos iniciar o atendimento agora?"
    token = _token()
    user = get_user_by_email(user_email)
    user_id = user.get("id")
    if not user_id:
        raise TeamsGraphError(f"Usuário não encontrado no Graph para: {user_email}")

    # Compat: algumas versões pedem "instalar app". Aqui é no-op.
    ensure_app_installed_and_current(user_id, wait_seconds=0, interval=0.0)

    chat_id = _get_or_create_one_on_one_chat(user_id, token)
    _send_chat_message(chat_id, text, token)


def send_proactive_message(user_email: str, text: str) -> bool:
    """
    Wrapper simples para enviar mensagem 1:1.
    """
    try:
        token = _token()
        user = get_user_by_email(user_email)
        user_id = user.get("id")
        if not user_id:
            raise TeamsGraphError(f"Usuário não encontrado: {user_email}")
        chat_id = _get_or_create_one_on_one_chat(user_id, token)
        _send_chat_message(chat_id, text, token)
        return True
    except Exception:
        return False
