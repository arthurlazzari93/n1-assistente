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
