# app/teams_graph.py
import os, re, time
import httpx
from urllib.parse import quote, urlencode
from loguru import logger

MS_TENANT_ID = os.getenv("MS_TENANT_ID", "")
MS_CLIENT_ID = os.getenv("MS_CLIENT_ID", "")
MS_CLIENT_SECRET = os.getenv("MS_CLIENT_SECRET", "")
TEAMS_APP_ID_ENV = os.getenv("TEAMS_APP_ID", "")  # GUID do app no catálogo (ou externalId)

AUTH_URL = f"https://login.microsoftonline.com/{MS_TENANT_ID}/oauth2/v2.0/token"
GRAPH = "https://graph.microsoft.com/v1.0"

class TeamsGraphError(Exception):
    pass

_GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

def _looks_like_guid(s: str) -> bool:
    return bool(_GUID_RE.match(s or ""))

def _get_app_token() -> str:
    if not (MS_TENANT_ID and MS_CLIENT_ID and MS_CLIENT_SECRET):
        raise TeamsGraphError("Credenciais MS_* ausentes no .env")
    data = {
        "grant_type": "client_credentials",
        "client_id": MS_CLIENT_ID,
        "client_secret": MS_CLIENT_SECRET,
        "scope": "https://graph.microsoft.com/.default",
    }
    with httpx.Client(timeout=20) as c:
        r = c.post(AUTH_URL, data=data)
        if r.status_code != 200:
            raise TeamsGraphError(f"Falha ao obter token: {r.status_code} {r.text}")
        return r.json()["access_token"]

def _g(method: str, url: str, token: str, **kwargs):
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {token}"
    headers["Content-Type"] = "application/json"
    timeout = kwargs.pop("timeout", 30)
    with httpx.Client(timeout=timeout, headers=headers) as c:
        return c.request(method, url, **kwargs)

# ---------- resolver App ID do catálogo sem ler catálogo (evita AppCatalog.Read.All) ----------
_CATALOG_ID_CACHE: str | None = None

def _resolve_catalog_app_id(token: str) -> str:
    global _CATALOG_ID_CACHE
    if _CATALOG_ID_CACHE:
        return _CATALOG_ID_CACHE

    if not TEAMS_APP_ID_ENV:
        raise TeamsGraphError("TEAMS_APP_ID não definido no .env")

    if _looks_like_guid(TEAMS_APP_ID_ENV):
        _CATALOG_ID_CACHE = TEAMS_APP_ID_ENV
        return _CATALOG_ID_CACHE

    # Se TEAMS_APP_ID_ENV for externalId, isso exige AppCatalog.Read.All:
    url_filter = f"{GRAPH}/appCatalogs/teamsApps?$filter=externalId eq '{TEAMS_APP_ID_ENV}'"
    r = _g("GET", url_filter, token)
    if r.status_code != 200:
        raise TeamsGraphError(f"Falha ao filtrar externalId: {r.status_code} {r.text}")
    items = r.json().get("value", [])
    if not items:
        raise TeamsGraphError("App não encontrado no catálogo pelo externalId.")
    _CATALOG_ID_CACHE = items[0]["id"]
    return _CATALOG_ID_CACHE

# ---------------- helpers -----------------
def _build_teams_deeplink_to_app(catalog_app_id: str, ticket_id: int) -> str:
    # Deep link para a aba pessoal "home" definida no manifest
    from urllib.parse import urlencode
    label = f"Ticket {ticket_id}"
    qs = urlencode({"label": label})
    return f"https://teams.microsoft.com/l/entity/{catalog_app_id}/home?{qs}"


# ---------------- operações de usuário/app -----------------
def get_user_by_email(email: str) -> dict:
    token = _get_app_token()
    url = f"{GRAPH}/users/{email}"
    r = _g("GET", url, token)
    if r.status_code != 200:
        raise TeamsGraphError(f"GET user por email falhou: {r.status_code} {r.text}")
    return r.json()

def list_user_installed_apps(user_id: str) -> list[dict]:
    """
    Lista os apps instalados no usuário (inclui teamsApp e teamsAppDefinition p/ ver versão).
    """
    token = _get_app_token()
    url = f"{GRAPH}/users/{user_id}/teamwork/installedApps?$expand=teamsApp,teamsAppDefinition"
    r = _g("GET", url, token)
    if r.status_code != 200:
        raise TeamsGraphError(f"Listar apps instalados falhou: {r.status_code} {r.text}")
    items = []
    for it in r.json().get("value", []):
        app = it.get("teamsApp") or {}
        defn = it.get("teamsAppDefinition") or {}
        items.append({
            "installationId": it.get("id"),
            "teamsAppId": app.get("id"),
            "teamsAppExternalId": app.get("externalId"),
            "displayName": app.get("displayName"),
            "version": defn.get("version"),
            "publishingState": defn.get("publishingState"),
        })
    return items

def _get_installation_id_for_app(user_id: str, catalog_id: str) -> str | None:
    token = _get_app_token()
    url = f"{GRAPH}/users/{user_id}/teamwork/installedApps?$expand=teamsApp"
    r = _g("GET", url, token)
    if r.status_code != 200:
        raise TeamsGraphError(f"Verificar apps instalados falhou: {r.status_code} {r.text}")
    for item in r.json().get("value", []):
        app = item.get("teamsApp") or {}
        if app.get("id") == catalog_id:
            return item.get("id")  # installationId
    return None

def is_app_installed_for_user(user_id: str) -> bool:
    token = _get_app_token()
    catalog_id = _resolve_catalog_app_id(token)
    return _get_installation_id_for_app(user_id, catalog_id) is not None

def install_app_for_user(user_id: str):
    token = _get_app_token()
    catalog_id = _resolve_catalog_app_id(token)
    url = f"{GRAPH}/users/{user_id}/teamwork/installedApps"
    body = {
        "@odata.type": "#microsoft.graph.userScopeTeamsAppInstallation",
        "teamsApp@odata.bind": f"{GRAPH}/appCatalogs/teamsApps/{catalog_id}"
    }
    r = _g("POST", url, token, json=body, timeout=60)
    if r.status_code not in (201, 202):
        raise TeamsGraphError(f"Instalação do app falhou: {r.status_code} {r.text}")

def upgrade_user_app_installation(user_id: str, installation_id: str):
    token = _get_app_token()
    url = f"{GRAPH}/users/{user_id}/teamwork/installedApps/{installation_id}/upgrade"
    r = _g("POST", url, token, json={}, timeout=30)
    if r.status_code not in (200, 202, 204):
        raise TeamsGraphError(f"Upgrade do app falhou: {r.status_code} {r.text}")

def ensure_app_installed_and_current(user_id: str, wait_seconds: int = 120, interval: float = 3.0):
    """
    - Instala se faltar;
    - Faz upgrade para a última versão publicada (se disponível);
    - Faz polling até o Graph refletir as mudanças.
    """
    token = _get_app_token()
    catalog_id = _resolve_catalog_app_id(token)

    inst_id = _get_installation_id_for_app(user_id, catalog_id)
    if not inst_id:
        logger.info(f"[TEAMS] App não instalado para user {user_id}. Instalando…")
        install_app_for_user(user_id)

        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            time.sleep(interval)
            inst_id = _get_installation_id_for_app(user_id, catalog_id)
            if inst_id:
                logger.info(f"[TEAMS] App instalado e visível para user {user_id}.")
                break
        if not inst_id:
            raise TeamsGraphError("App não ficou disponível para o usuário dentro do tempo de espera.")

    # tenta upgrade para garantir versão mais recente
    try:
        upgrade_user_app_installation(user_id, inst_id)
        logger.info(f"[TEAMS] Upgrade solicitado para instalação {inst_id}.")
        # espera alguns segundos para refletir
        time.sleep(5)
    except TeamsGraphError as e:
        # alguns tenants respondem 400 se já está na última versão — prossegue
        logger.warning(f"[TEAMS] Upgrade não aplicado (pode já estar na última): {e}")

def send_activity_notification_to_user(user_id: str, ticket_id: int, subject: str):
    token = _get_app_token()
    catalog_id = _resolve_catalog_app_id(token)  # necessário p/ deeplink
    url = f"{GRAPH}/users/{user_id}/teamwork/sendActivityNotification"

    topic_web_url = _build_teams_deeplink_to_app(catalog_id, ticket_id)

    body = {
        "topic": {
            "source": "text",
            "value": f"Ticket {ticket_id} - {subject[:180]}",
            "webUrl": topic_web_url
        },
        "activityType": "ticketTriagem",
        "previewText": { "content": f"Olá! Recebi seu chamado #{ticket_id}. Posso te guiar agora." },
        "templateParameters": [
            {"name": "ticketId", "value": str(ticket_id)},
            {"name": "subject", "value": subject or ""}
        ]
    }

    r = _g("POST", url, token, json=body, timeout=30)
    if r.status_code not in (200, 202, 204):
        raise TeamsGraphError(f"Enviar notificação falhou: {r.status_code} {r.text}")

def notify_user_for_ticket(user_email: str, ticket_id: int, subject: str):
    user = get_user_by_email(user_email)
    user_id = user.get("id")
    if not user_id:
        raise TeamsGraphError("user_id não encontrado no Graph")

    # garante instalado e versão corrente (evita 403 “not authorized / expected app”)
    ensure_app_installed_and_current(user_id, wait_seconds=120, interval=3.0)

    send_activity_notification_to_user(user_id, ticket_id, subject)

# --------------- diagnósticos ----------------
def diag_token_info() -> dict:
    token = _get_app_token()
    return {"ok": True, "tenant": MS_TENANT_ID, "client_id_suffix": MS_CLIENT_ID[-6:]}

def diag_resolve_app() -> dict:
    token = _get_app_token()
    catalog_id = _resolve_catalog_app_id(token)
    return {"ok": True, "input": TEAMS_APP_ID_ENV, "catalog_id": catalog_id}

def diag_user(email: str) -> dict:
    u = get_user_by_email(email)
    return {"ok": True, "id": u.get("id"), "displayName": u.get("displayName"), "mail": u.get("mail") or u.get("userPrincipalName")}

def diag_user_installed_apps(email: str) -> dict:
    u = get_user_by_email(email)
    uid = u.get("id")
    items = list_user_installed_apps(uid)
    return {"ok": True, "userId": uid, "installed": items}
