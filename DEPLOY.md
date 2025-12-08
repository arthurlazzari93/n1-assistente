# Deploy de Produção

Este documento descreve como preparar e publicar o Assistente N1 em um servidor Ubuntu 24.04 já equipado com Docker, Docker Compose e Jenkins.

## 1. Pré-requisitos

- Docker 24+ e Docker Compose Plugin (`docker compose version`).
- Usuário com permissão de `sudo` ou acesso ao grupo `docker`.
- Arquivo `.env` completo (com todas as chaves listadas em `.env.sample`).
- DNS/Reverse proxy (opcional) caso deseje expor os serviços publicamente.

## 2. Estrutura de arquivos esperada no servidor

```
/opt/apps/n1agent
├── .env                      # cópia do arquivo preenchido (NÃO commitar)
├── docker-compose.prod.yml   # este repositório
├── Dockerfile
├── frontend/Dockerfile
├── frontend/docker/nginx.conf
├── data/                     # diretório novo para o SQLite persistente
│   └── (será criado em tempo de execução)
└── ...
```

> Observação: o diretório `data/` é ignorado pelo git. Crie-o manualmente (`mkdir -p data`) antes de subir os containers. Para iniciar com **banco limpo**, garanta que `data/` esteja vazio (remova `data/n1agent.db` antigo, se existir).

## 3. Variáveis de ambiente úteis

Além das chaves funcionais (OpenAI, Movidesk, Teams, etc.), o deploy usa algumas variáveis opcionais para customizar binding de portas:

| Variável               | Default        | Descrição                                           |
| ---------------------- | -------------- | --------------------------------------------------- |
| `API_BIND_ADDRESS`     | `127.0.0.1`    | Interface local exposta pelo backend. Troque para `0.0.0.0` se quiser expor externamente. |
| `API_PORT`             | `8001`         | Porta pública do backend.                           |
| `SANDBOX_BIND_ADDRESS` | `0.0.0.0`      | Interface usada pelo frontend (sandbox).            |
| `SANDBOX_PORT`         | `8300`         | Porta pública do frontend (use outra se 8300 estiver ocupada). |
| `N1AGENT_IMAGE`        | `tecnogera/n1agent:latest` | Tag usada pelo serviço `backend`. Override para testar builds específicos. |
| `N1AGENT_FRONTEND_IMAGE` | `tecnogera/n1agent-frontend:latest` | Tag para o serviço `frontend`. |

Declare essas variáveis no shell ou no arquivo `.env` do servidor antes de executar o compose.

## 4. Subindo os containers manualmente

```bash
cd /opt/apps/n1agent
cp .env.sample .env        # apenas na primeira vez; depois edite com os segredos
mkdir -p data              # cria diretório para o SQLite
docker compose -f docker-compose.prod.yml pull   # opcional se já existir imagem publicada
docker compose -f docker-compose.prod.yml up -d --build
```

Checar status:

```bash
docker compose -f docker-compose.prod.yml ps
docker logs -f n1agent-backend
docker logs -f n1agent-frontend
```

## 5. Atualizando versões

1. Execute o pipeline no Jenkins (já configurado para produzir e subir `tecnogera/n1agent:<build>`).
2. No servidor, sincronize o repositório (`git pull` ou receba artefatos via pipeline).
3. Reaplique `docker compose -f docker-compose.prod.yml up -d`.

## 6. Desligando ou limpando o ambiente

```bash
docker compose -f docker-compose.prod.yml down
rm -rf data/*   # cuidado! remove o SQLite (útil para resetar o ambiente)
```

## 7. Endereços finais

- Backend FastAPI/Teams Bot: `http://<host>:API_PORT` (por padrão, fica acessível apenas via localhost).
- Sandbox React (somente para testes internos): `http://<host>:SANDBOX_PORT`
  - O Nginx do frontend já encaminha chamadas `GET/POST /debug/*` para o backend na rede interna do compose.

## 8. Logs e observabilidade

- `docker logs -f n1agent-backend` mostra stdout/stderr do FastAPI, incluindo `app.log`.
- `docker logs -f n1agent-frontend` mostra acessos Nginx.
- Endpoints úteis:
  - `GET /healthz` (backend)
  - `GET /debug/metrics`
  - `GET /debug/kb/articles`

Documente qualquer override local (como novas portas) dentro do Jenkins ou inventário do servidor para manter o time alinhado.
