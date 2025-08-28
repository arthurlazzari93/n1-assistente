# app/classifier.py
import re
from dataclasses import dataclass

@dataclass
class Classification:
    n1_candidate: bool
    n1_reason: str
    suggested_service: str | None
    suggested_category: str | None
    suggested_urgency: str | None

RULES = [
    (r"\b(senha|password|bloquead[ao]|expirad[ao]|redefini(r|ç)[aã]o)\b",
     ("Acesso e Autenticação", "Redefinição de Senha", True)),
    (r"\b(2fa|mfa|duo|verifica(c|ç)[aã]o em duas etapas|token)\b",
     ("Acesso e Autenticação", "MFA/2FA", True)),
    (r"\b(outlook|email|e-mail|caixa (cheia|lotad[ao])|quota|quot[aã])\b",
     ("Correio Eletrônico", "Outlook/Quota/Envio", True)),
    (r"\b(onedrive|sharepoint|teams)\b",
     ("Colaboração Microsoft 365", "Sincronização/Aplicativo", True)),
    (r"\b(impressora|impress[aã]o|scanner)\b",
     ("Periféricos", "Impressora/Scanner", True)),
    (r"\b(vpn|globalprotect|anyconnect|forticlient)\b",
     ("Conectividade", "VPN - Acesso Remoto", True)),
    (r"\b(lento|travando|trav[aã]o|desempenho|espaço em disco)\b",
     ("Estações de Trabalho", "Desempenho/Manutenção", True)),
    (r"\b(permiss[aã]o|acesso (a|à|na|no) (pasta|compartilhamento)|liberar|compartilhar)\b",
     ("Arquivos e Permissões", "Acesso/Permissões", False)),
    (r"\b(cria(r|ç)[aã]o de usu[aá]rio|novo usu[aá]rio|alterar perfil|ad|active directory)\b",
     ("Identidade e Diretório", "Usuários/AD", False)),
    (r"\b(instala(r|ç)[aã]o|instalar|deploy|licen(c|ç)a)\b",
     ("Software", "Instalação/Licenciamento", False)),
    (r"\b(servidor|switch|roteador|wi-?fi|rede (caiu|fora|down)|storage|backup)\b",
     ("Infraestrutura", "Rede/Servidores", False)),
]

URGENCY_HINTS = [
    (r"\b(parad[ao]|parou|inoperante|n[aã]o consigo trabalhar|parou a produ[cç][aã]o)\b", "Alta"),
    (r"\b(urgente|urg[êe]ncia|imediat[oa])\b", "Alta"),
    (r"\b(lento|intermitente)\b", "Média"),
]

DEFAULT_URGENCY = "Média"

def classify_from_subject(subject: str) -> Classification:
    text = (subject or "").lower()
    suggested_urgency = next((u for pat, u in URGENCY_HINTS if re.search(pat, text)), DEFAULT_URGENCY)
    for pat, (service, category, is_n1) in RULES:
        if re.search(pat, text):
            return Classification(is_n1, f"Regra: '{pat}'", service, category, suggested_urgency)
    return Classification(True, "N1 preliminar (assunto genérico)", "Triagem", "Análise Inicial", suggested_urgency)
