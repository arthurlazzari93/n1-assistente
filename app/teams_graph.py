# app/teams_graph.py
from __future__ import annotations

import os
from typing import Optional, Dict, Any

import requests


class TeamsGraphError(RuntimeError):
    pass


GRAPH = "https://graph.microsoft.com/v1.0"


# ---------------- Utils/env ----------------

def _env(*keys: str, default: str = "") -> str:
    for k in keys:
        v = os.getenv(k)
        if v is not None and str(v).strip() != "":
            return v
    return default

# ----- Getters dinâmicos (evita cache no import) -----

def _get_tenant_id() -> str:
    return _env("MS_TENANT_ID", "MICROSOFT_APP_TENANT_ID", "TENANT_ID", "AZURE_TENANT_ID")

def _get_graph_client_id() -> str:
    return _env("MS_CLIENT_ID", "TEAMS_CLIENT_ID", "BOT_APP_ID", "MICROSOFT_APP_ID")

def _get_graph_client_secret() -> str:
    return _env("MS_CLIENT_SECRET", "TEAMS_CLIENT_SECRET", "BOT_APP_PASSWORD", "MICROSOFT_APP_PASSWORD")

def _get_bot_app_id() -> str:
    return _env("BOT_APP_ID", "MICROSOFT_APP_ID", "MS_CLIENT_ID")

def _get_bot_app_password() -> str:
    return _env("BOT_APP_PASSWORD", "MICROSOFT_APP_PASSWORD", "MS_CLIENT_SECRET")

def _get_teams_app_id() -> str:
    return (os.getenv("TEAMS_APP_ID") or "").strip()

def _get_service_url() -> str:
    return os.getenv("TEAMS_SERVICE_URL", "https://smba.trafficmanager.net/teams/")

def _get_oauth_scope() -> str:
    return os.getenv("MICROSOFT_OAUTH_SCOPE", "https://api.botframework.com/.default")

def _get_app_type() -> str:
    at = (os.getenv("MICROSOFT_APP_TYPE") or os.getenv("BOT_APP_TYPE") or "").strip()
    if at:
        return at
    return "SingleTenant" if _get_tenant_id() else "MultiTenant"

def _get_bot_authority() -> str:
    app_type = _get_app_type().lower()
    if app_type == "singletenant":
        tid = _get_tenant_id()
        if not tid:
            raise TeamsGraphError("MICROSOFT_APP_TYPE=SingleTenant, mas MS_TENANT_ID está vazio.")
        return f"https://login.microsoftonline.com/{tid}/oauth2/v2.0/token"
    return "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token"


# ---------------- HTTP helpers ----------------

def _token(scope: str = "https://graph.microsoft.com/.default") -> str:
    tenant_id = _get_tenant_id()
    client_id = _get_graph_client_id()
    client_secret = _get_graph_client_secret()

    if not tenant_id or not client_id or not client_secret:
        raise TeamsGraphError(
            "Credenciais do Graph ausentes. Defina MS_TENANT_ID, MS_CLIENT_ID e MS_CLIENT_SECRET "
            "(ou equivalentes BOT_APP_ID/BOT_APP_PASSWORD/MICROSOFT_APP_*)."
        )
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": scope,
        "grant_type": "client_credentials",
    }
    url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    r = requests.post(url, data=data, timeout=30)
    if r.status_code != 200:
        raise TeamsGraphError(f"Falha ao obter token: {r.status_code} {r.text}")
    return r.json()["access_token"]


def _g(method: str, url: str, token: str, **kwargs) -> requests.Response:
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {token}"
    headers.setdefault("Content-Type", "application/json")
    return requests.request(method, url, headers=headers, timeout=30, **kwargs)


# ---------------- Usuários (Graph) ----------------

def get_user_id_by_mail(email: str) -> Optional[str]:
    t = _token()
    r1 = _g("GET", f"{GRAPH}/users?$filter=mail eq '{email}'&$select=id", t)
    if r1.status_code != 200:
        raise TeamsGraphError(f"Falha ao buscar usuário (mail): {r1.status_code} {r1.text}")
    v1 = r1.json().get("value", [])
    if v1:
        return v1[0].get("id")

    r2 = _g("GET", f"{GRAPH}/users/{email}?$select=id", t)
    if r2.status_code == 200:
        return r2.json().get("id")

    return None


def get_user_by_email(email: str) -> Dict[str, Any]:
    t = _token()
    r1 = _g("GET", f"{GRAPH}/users?$filter=mail eq '{email}'&$select=id,mail,userPrincipalName,displayName,accountEnabled", t)
    if r1.status_code == 200:
        v = r1.json().get("value", [])
        if v:
            u = v[0]
            return {
                "id": u.get("id"),
                "mail": u.get("mail") or email,
                "userPrincipalName": u.get("userPrincipalName") or email,
                "displayName": u.get("displayName"),
                "accountEnabled": u.get("accountEnabled"),
            }
    r2 = _g("GET", f"{GRAPH}/users/{email}?$select=id,mail,userPrincipalName,displayName,accountEnabled", t)
    if r2.status_code == 200:
        u = r2.json()
        return {
            "id": u.get("id"),
            "mail": u.get("mail") or email,
            "userPrincipalName": u.get("userPrincipalName") or email,
            "displayName": u.get("displayName"),
            "accountEnabled": u.get("accountEnabled"),
        }
    return {"id": None, "mail": email, "userPrincipalName": email, "displayName": None, "accountEnabled": None}


# ---------------- Instalação do app pessoal (Graph) ----------------

def ensure_app_installed_for_user(user_ref: str, by: str = "id") -> None:
    teams_app_id = _get_teams_app_id()
    if not teams_app_id:
        return

    t = _token()
    if by not in ("id", "upn"):
        by = "id"

    target = user_ref if by == "id" else user_ref
    url = f"{GRAPH}/users/{target}/teamwork/installedApps"
    if by == "upn":
        url = f"{GRAPH}/users/{user_ref}/teamwork/installedApps"

    r = _g("GET", f"{url}?$expand=teamsApp", t)
    if r.status_code == 200:
        for it in r.json().get("value", []):
            app = (it.get("teamsApp") or {})
            if app.get("id") == teams_app_id:
                return

    body = {"teamsApp@odata.bind": f"{GRAPH}/appCatalogs/teamsApps/{teams_app_id}"}
    r2 = _g("POST", url, t, json=body)
    if r2.status_code not in (200, 201, 202, 204):
        raise TeamsGraphError(f"Falha ao instalar app pessoal para o usuário {user_ref}: {r2.status_code} {r2.text}")


# ---------------- Diagnóstico ----------------

def diag_token_info() -> Dict[str, Any]:
    try:
        t = _token()
        r = _g("GET", f"{GRAPH}/organization?$select=id,displayName", t)
        org = None
        if r.status_code == 200 and r.json().get("value"):
            org = r.json()["value"][0]
        cid = _get_graph_client_id()
        return {
            "tenant_id": _get_tenant_id(),
            "client_id": (cid[:6] + "…") if cid else None,
            "scopes": "Graph /.default",
            "org": org,
            "ok": True,
        }
    except Exception as e:
        cid = _get_graph_client_id()
        return {
            "tenant_id": _get_tenant_id(),
            "client_id": (cid[:6] + "…") if cid else None,
            "error": str(e),
            "ok": False,
        }

def diag_resolve_app() -> Dict[str, Any]:
    try:
        _ = _token()
        mode = "bot_proactive"
        note = "Mensagens proativas enviadas via Bot Framework; Graph usado para resolver usuário/instalar app."
        if _get_teams_app_id():
            note += " TEAMS_APP_ID definido: app será instalada (escopo pessoal) se faltar."
        bot_ok = bool(_get_bot_app_id() and _get_bot_app_password())
        return {
            "ok": True,
            "mode": mode,
            "note": note,
            "bot_creds_present": bot_ok,
            "bot_app_type": _get_app_type(),
            "bot_authority": _get_bot_authority(),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}

def diag_user(email: str) -> Dict[str, Any]:
    return get_user_by_email(email)

def diag_user_installed_apps(email: str) -> Dict[str, Any]:
    t = _token()
    uid = get_user_id_by_mail(email)
    if not uid:
        return {"ok": False, "error": f"Usuário não encontrado para {email}"}
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


# ---------------- Envio proativo via Bot Framework ----------------

try:
    from botbuilder.schema import Activity, ConversationParameters, ChannelAccount, ActivityTypes
    from botframework.connector.aio import ConnectorClient  # type: ignore
    from botframework.connector.auth import MicrosoftAppCredentials  # type: ignore
    _BOTFRAMEWORK_OK = True
except Exception:
    _BOTFRAMEWORK_OK = False

def _ensure_bot_creds():
    if not _get_bot_app_id() or not _get_bot_app_password():
        raise TeamsGraphError("BOT_APP_ID/BOT_APP_PASSWORD ausentes no ambiente.")

async def send_proactive_via_bot(aad_object_id: str, tenant_id: str, text: str) -> str:
    """
    Cria uma conversa 1:1 e envia a primeira mensagem usando o Bot Framework (Teams).
    Alguns tenants não entregam a 'activity' enviada dentro do create_conversation.
    Por isso, após criar a conversa, enviamos explicitamente um segundo Activity.
    """
    if not _BOTFRAMEWORK_OK:
        raise TeamsGraphError(
            "Pacote 'botframework-connector' ausente. Adicione 'botframework-connector==4.14.5' ao requirements."
        )
    _ensure_bot_creds()

    service_url = _get_service_url()
    bot_app_id = _get_bot_app_id()
    bot_app_password = _get_bot_app_password()
    app_type = _get_app_type().lower()

    # confiar no serviceUrl quando proativo
    try:
        MicrosoftAppCredentials.trust_service_url(service_url)  # type: ignore
    except Exception:
        pass

    channel_auth_tenant = _get_tenant_id() if app_type == "singletenant" else None
    creds = MicrosoftAppCredentials(
        bot_app_id,
        bot_app_password,
        channel_auth_tenant=channel_auth_tenant,
        oauth_scope=_get_oauth_scope(),
    )
    connector = ConnectorClient(credentials=creds, base_url=service_url)

    # Variantes para identificar o membro (evita "User id can't be null")
    member_variants = [
        ChannelAccount(id=f"8:orgid:{aad_object_id}"),
        ChannelAccount(aad_object_id=aad_object_id),
        ChannelAccount(id=aad_object_id),
    ]

    # Atividade que vamos reenviar explicitamente após criar a conversa
    followup_activity = Activity(
        type=ActivityTypes.message,
        text=text,
        channel_data={"tenant": {"id": tenant_id}},
        from_property=ChannelAccount(id=bot_app_id),
    )

    last_err: Exception | None = None
    for m in member_variants:
        params = ConversationParameters(
            is_group=False,
            bot=ChannelAccount(id=bot_app_id),
            members=[m],
            tenant_id=tenant_id,
            channel_data={"tenant": {"id": tenant_id}},
            # ainda mandamos uma 'activity' aqui, mas alguns tenants ignoram;
            # o envio garantido virá com send_to_conversation logo após.
            activity=Activity(
                type=ActivityTypes.message,
                text=text,
                channel_data={"tenant": {"id": tenant_id}},
                from_property=ChannelAccount(id=bot_app_id),
            ),
        )
        try:
            convo = await connector.conversations.create_conversation(params)
            conv_id = getattr(convo, "id", "") or ""
            if not conv_id:
                # tenta próxima variante
                continue

            # 🚀 envio explícito (garante entrega)
            await connector.conversations.send_to_conversation(conv_id, followup_activity)
            return conv_id
        except Exception as e:
            last_err = e
            continue

    raise TeamsGraphError(f"Falha ao criar/enviar no chat 1:1 do Teams. Último erro: {last_err!r}")



# ---------------- API pública principal ----------------

def notify_user_for_ticket(user_email: str, ticket_id: int, subject: str, preview_text: Optional[str] = None) -> None:
    user = get_user_by_email(user_email)
    user_id = user.get("id")
    if not user_id:
        raise TeamsGraphError(f"Usuário não encontrado no Graph para: {user_email}")

    try:
        ensure_app_installed_for_user(user_id, by="id")
    except Exception:
        pass  # app já pode estar instalada

    tenant_id = _get_tenant_id() or os.getenv("AZURE_TENANT_ID") or os.getenv("TENANT_ID")
    if not tenant_id:
        raise TeamsGraphError("MS_TENANT_ID ausente no ambiente.")

    first_name = (user_email.split("@", 1)[0]).split(".")[0].title()
    text = preview_text or f"Olá {first_name}! Recebemos seu chamado #{ticket_id} sobre “{subject}”. Posso ajudar agora?"

    import asyncio
    asyncio.run(send_proactive_via_bot(user_id, tenant_id, text))


def send_proactive_message(user_email: str, text: str) -> bool:
    try:
        notify_user_for_ticket(user_email, 0, "Assistente N1", preview_text=text)
        return True
    except Exception:
        return False


# ---------------- Diagnóstico do token do Bot ----------------

def _get_bot_token_or_die() -> Dict[str, Any]:
    app_id = _get_bot_app_id()
    app_pw = _get_bot_app_password()
    if not app_id or not app_pw:
        return {"ok": False, "error": "BOT_APP_ID/BOT_APP_PASSWORD ausentes no ambiente."}

    data = {
        "grant_type": "client_credentials",
        "client_id": app_id,
        "client_secret": app_pw,
        "scope": _get_oauth_scope(),
    }
    url = _get_bot_authority()
    r = requests.post(url, data=data, timeout=30)
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text}

    if r.status_code != 200 or "access_token" not in body:
        return {
            "ok": False,
            "status": r.status_code,
            "authority": url,
            "app_type": _get_app_type(),
            "body": body,
        }
    return {
        "ok": True,
        "status": r.status_code,
        "authority": url,
        "app_type": _get_app_type(),
        "has_token": True,
    }

def diag_bot_token() -> dict:
    try:
        return _get_bot_token_or_die()
    except Exception as e:
        return {"ok": False, "error": str(e)}
