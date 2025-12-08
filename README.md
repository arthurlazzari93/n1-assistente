# Assistente N1 – Tecnogera

Backend em **FastAPI** com bot para **Microsoft Teams** que integra com o **Movidesk** e usa **OpenAI** para apoiar o atendimento de primeiro nível (N1).  
O objetivo é classificar automaticamente tickets, sugerir roteiros de solução para o usuário e apoiar a equipe de suporte via Teams.

## Visão geral

- Recebe eventos/tickets via Webhook (Movidesk) e outros canais.
- Usa um modelo LLM (por padrão `gpt-4o-mini`) para:
  - Avaliar se o ticket é resolvível em N1.
  - Sugerir passos objetivos de resolução em PT-BR.
  - Gerar perguntas de triagem.
- Integra com o Movidesk para leitura/escrita de tickets.
- Expõe um bot do Microsoft Teams para conversar com analistas e usuários internos.
- Mantém histórico/estado em um banco **SQLite** (`n1agent.db`).

Principais componentes:

- `app/main.py` – API FastAPI, endpoints HTTP, integrações e follow-ups.
- `app/bot.py` – implementação do bot do Teams (Bot Framework).
- `app/llm.py` – wrapper para chamadas ao OpenAI e classificação de tickets.
- `app/movidesk_client.py` – cliente HTTP para API pública do Movidesk.
- `app/db.py` – inicialização e operações de banco de dados (SQLite).
- `app/ai/triage_agent.py` – lógica de triagem orientada por IA.
- `app/knowledge/` – artigos de base de conhecimento em Markdown.

## Pré‑requisitos

- **Python 3.10+** (recomendado 3.11 ou superior).
- Conta e token de API no **Movidesk**.
- **OpenAI API key** válida (`OPENAI_API_KEY`).
- Credenciais do Azure AD/Teams:
  - `MS_TENANT_ID`
  - `MS_CLIENT_ID`
  - `MS_CLIENT_SECRET`
- Ambiente virtual Python (opcional, mas recomendado).

## Configuração

1. Crie e ative o ambiente virtual:
   - Windows (PowerShell):
     - `python -m venv .venv`
     - `.\.venv\Scripts\activate`
2. Instale as dependências:
   - `pip install -r requirements.txt`
3. Copie o arquivo de exemplo de variáveis de ambiente:
   - `cp .env.sample .env` (ou copie manualmente no Windows).
4. Preencha o `.env` com:
   - `OPENAI_API_KEY`
   - `MOVIDESK_TOKEN`
   - `MS_TENANT_ID`, `MS_CLIENT_ID`, `MS_CLIENT_SECRET`
   - Demais chaves conforme necessidade (veja `app/main.py` e `app/movidesk_client.py`).

### Ajustando tempos de sessão e follow-ups

Alguns tempos críticos são controlados por variáveis de ambiente para facilitar ajustes sem alterar código:

- `SESSION_REMINDER_MINUTES` – minutos para um lembrete de sessão em andamento (default 15).
- `SESSION_TIMEOUT_MINUTES` – minutos para encerrar uma sessão inativa automaticamente (default 60).
- `FOLLOWUP_NUDGE1_MINUTES` – atraso do primeiro lembrete pró-ativo no Teams (default 10).
- `FOLLOWUP_NUDGE2_MINUTES` – atraso do segundo lembrete (default 25).
- `FOLLOWUP_FINAL_CLOSE_MINUTES` – atraso para a mensagem final/encerramento pró-ativo (default 85).
- `ENABLE_SESSION_WATCHDOG` / `SESSION_WATCHDOG_POLL_SECONDS` – habilitam o monitoramento de sessões chat_driven e definem o intervalo (s) entre verificações (default 1 minuto / 60 s).
- `SESSION_REMINDER_MESSAGE` – texto opcional do lembrete enviado antes do timeout (se vazio, usamos o padrão amigável do código).

Edite esses valores no `.env` antes de subir a API. O arquivo `.env.sample` já traz todos os campos para referência.

## Executando a API localmente

Após configurar o ambiente e o `.env`:

- Ative o virtualenv (se ainda não estiver ativo).
- Execute:
  - `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`
- Endpoints úteis:
  - `GET /healthz` – verificação simples de saúde.
  - `GET /debug/routes` – lista rotas registradas (para inspeção).
  - Endpoints de debug Movidesk – ver `app/main.py` (ex.: `/debug/movidesk/audit`).

> Atenção: alguns endpoints de debug podem chamar a API do Movidesk e/ou OpenAI.  
> Use com cuidado em ambientes de produção para evitar custos e efeitos indesejados.

## Estrutura de pastas (resumo)

- `app/`
  - `main.py` – aplicação FastAPI.
  - `bot.py` – bot do Teams.
  - `llm.py` – integração com OpenAI.
  - `db.py` – acesso ao SQLite.
  - `movidesk_client.py` – cliente Movidesk.
  - `knowledge/` – artigos da base de conhecimento (Markdown).
  - `ai/` – agentes e fluxos de IA.
  - `data/` – dados de feedback/aprendizado (ex.: `feedback_kb.jsonl`).
- `frontend/` – projeto React + Vite para testar o agente via tela de chat.
- `teams_app/` – manifestos, ícones e pacotes ZIP do app do Teams.
- `n1agent.db` – banco SQLite usado pela aplicação.
- `docker-compose.yml` / `Dockerfile` – artefatos para containerização.

## Docker (resumo rápido)

Há arquivos `Dockerfile` e `docker-compose.yml` para execução em container.  
O fluxo típico é:

- Ajustar variáveis de ambiente no compose (ou usar `.env` compartilhado).
- Subir os serviços:
  - `docker-compose up --build`

Consulte seu pipeline (ex.: `Jenkinsfile`) ou documentação interna para detalhes específicos de deploy.
### Deploy de produ??o

- `docker-compose.prod.yml` sobe backend (FastAPI/Bot) + frontend (sandbox React servido por Nginx) usando o mesmo `.env`.
- O backend grava o SQLite em `./data/` (diret?rio ignorado pelo git) para facilitar reset/backup.
- Ports padr?o:
  - Backend: `127.0.0.1:8001` (mant?m compatibilidade com o agente antigo / t?nel).
  - Frontend sandbox: `0.0.0.0:8300` (troque se preciso; porta 9443 continua livre).
- Veja `DEPLOY.md` para o passo a passo completo (limpeza de DB, cuidados com containers antigos e comandos sugeridos para Ubuntu 24.04 + Jenkins).


## Frontend (React + Vite)

O diretório `frontend/` contém um projeto em **React** criado com **Vite**, com uma tela de chat para testar o agente N1 via HTTP.

### Rodando o frontend em desenvolvimento

1. Instale as dependências (na primeira vez):
   - `cd frontend`
   - `npm install`
2. Com o backend já rodando em `http://localhost:8000`:
   - `npm run dev`
3. Acesse no navegador:
   - `http://localhost:5173`

O Vite está configurado para fazer proxy das rotas que começam com `/debug` para `http://localhost:8000`, então o frontend conversa com o endpoint `/debug/chat/triage` sem precisar configurar CORS.

### Build de produção do frontend

Para gerar os artefatos estáticos:

- `cd frontend`
- `npm run build`

O resultado ficará em `frontend/dist/`. Em um passo posterior, você pode integrar esse build ao FastAPI (por exemplo, servindo os arquivos estáticos a partir de `app/static/` ou de um CDN/reverso).

## Base de conhecimento em Markdown

Os arquivos em `app/knowledge/` são artigos de ajuda usados pela IA e pelo bot.  
Para criar novos artigos:

- Use sempre **português claro**, com passos numerados.
- Inclua um cabeçalho (front‑matter) com título, tags e sinônimos (ver `markdown.md` para um modelo).
- Evite copiar dados sensíveis ou informação confidencial.

## Segurança

- Nunca versione `.env` reais ou tokens.
- Verifique logs (`app.log`) para garantir que erros não exponham segredos.
- Restrinja o acesso aos endpoints de debug em ambientes de produção (via rede, gateway ou auth).
