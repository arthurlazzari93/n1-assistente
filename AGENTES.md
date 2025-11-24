# Instruções para agentes que editam este repositório

Este projeto é o **Assistente N1 da Tecnogera**, um backend em **FastAPI** com bot do **Microsoft Teams** que integra com o **Movidesk** e usa **OpenAI** para classificar e orientar atendimentos N1.

Estas instruções são para qualquer agente (humano ou IA) que for modificar o código.

## Estrutura do projeto

- Código principal da API: `app/main.py`
- Bot do Teams: `app/bot.py`
- Cliente Movidesk: `app/movidesk_client.py`
- Camada de banco de dados (SQLite): `app/db.py` (arquivo físico: `n1agent.db`)
- Lógica de IA / LLM:
  - `app/llm.py` (classificação com OpenAI)
  - `app/ai/triage_agent.py` (fluxo de triagem)
- Base de conhecimento (artigos em Markdown): `app/knowledge/`
- Dados de feedback/aprendizado: `app/data/`
- Artefatos do Teams App: `teams_app/`

## Convenções de código

- Linguagem principal: **Python 3**.
- Mantenha o estilo atual de código (imports explícitos, funções pequenas, logs com `loguru`).
- Preserve os textos de logs e mensagens em **português**.
- Evite criar novas dependências no `requirements.txt` sem necessidade clara.
- Não altere arquivos dentro de `.venv` nem os arquivos `.zip` em `teams_app/`.
- Prefira mudanças pequenas e focadas, explicando no PR ou na mensagem o que foi feito e por quê.

## Execução local

- Usar ambiente virtual (exemplo):
  - `python -m venv .venv`
  - `.\.venv\Scripts\activate` (Windows)
  - `pip install -r requirements.txt`
- Configurar variáveis de ambiente via `.env` (base em `.env.sample`).
- Rodar a API localmente, por exemplo:
  - `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`
- Endpoint de health-check: `GET /healthz`.

## Boas práticas para modificações

- Sempre que possível, corrija a **causa raiz** em vez de adicionar remendos.
- Não adicione comentários extensos ou desnecessários; use nomes claros de variáveis e funções.
- Mantenha compatibilidade com os endpoints existentes (não quebre contratos de API sem necessidade).
- Se precisar criar novos endpoints:
  - Documente brevemente no `README.md`.
  - Siga o padrão de rotas e respostas usado em `app/main.py`.

## Segurança e segredos

- **Nunca** versione arquivos `.env` reais ou credenciais sensíveis.
- Use apenas variáveis de ambiente e o arquivo `.env.sample` como referência.
- Tenha cuidado ao logar erros para não expor tokens ou chaves de API.

