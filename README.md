# 🤖 AgentLab — MCP Backend + AI Agent

A professional backend system that simulates real enterprise agent infrastructure. Natural language in, business actions out.

```
User: "Create an order for 5 units of SKU-001"
  └─► Agent API → LLM decides tools → MCP Backend → PostgreSQL
        └─► "Order created and stock reserved successfully."
```

### What's inside

- **MCP Backend** — Exposes business tools (products, stock, orders) via Model Context Protocol
- **Agent API** — Receives natural language, uses an LLM to call the right tools automatically
- **n8n** — Orchestrates workflows (order processing, payment callbacks)
- **PostgreSQL** — Persistent storage with transactions and row-level locking
- **Bearer Auth** — All MCP tool calls require a valid API key

---

## 🚀 Getting started

### Prerequisites

- Docker Desktop running
- A `.env` file in the root (copy from `.env.example` and fill in the values)

```bash
cp .env.example .env
# Edit .env with your values
```

### Start everything

```bash
docker compose up -d --build
```

| Service     | URL                       |
| ----------- | ------------------------- |
| n8n         | http://localhost:5678     |
| MCP Backend | http://localhost:8000/mcp |
| Agent API   | http://localhost:9000     |

### First time setup — create the database schema

```bash
# Windows (PowerShell)
Get-Content db/schema.sql | docker exec -i agentlab_postgres psql -U n8n -d n8n

# Mac / Linux
docker exec -i agentlab_postgres psql -U n8n -d n8n < db/schema.sql
```

### Stop everything

```bash
docker compose down
```

> ⚠️ Never use `docker compose down -v` — it deletes all data including the database.

---

## 🧪 Test the agent

```bash
POST http://localhost:9000/chat
Content-Type: application/json

{ "message": "Create a product SKU-001 with 50 units of stock" }
{ "message": "How much stock does SKU-001 have?" }
{ "message": "Create an order for 3 units of SKU-001 and reserve the stock" }
```

Check the `trace` field in the response to see every tool call the agent made.

---

## 📁 Project structure

```
MCPServer/
├── services/
│   ├── mcp-backend/        # MCP server (FastMCP + asyncpg)
│   │   └── src/
│   │       ├── app/        # Config, DB pool, MCP app
│   │       └── tools/      # Business tools (products, stock, orders)
│   └── agent-api/          # AI agent (FastAPI + Groq/Llama)
│       └── src/
├── workflows/              # n8n workflow exports (JSON)
├── db/
│   └── schema.sql          # Database schema
├── docker-compose.yml
└── .env.example
```

---

## 🔧 Environment variables

Copy `.env.example` to `.env` and fill in:

| Variable             | Description                             |
| -------------------- | --------------------------------------- |
| `POSTGRES_DB`        | Database name                           |
| `POSTGRES_USER`      | Database user                           |
| `POSTGRES_PASSWORD`  | Database password                       |
| `MCP_API_KEY`        | Bearer token for MCP authentication     |
| `GROQ_API_KEY`       | Groq API key (free at console.groq.com) |
| `N8N_ENCRYPTION_KEY` | Random string for n8n encryption        |
