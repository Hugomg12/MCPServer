# agent-lab

Mini sistema backend para agentes de IA usando:

- MCP (Model Context Protocol)
- Python + FastMCP
- PostgreSQL
- n8n (workflows)

## Arquitectura

- MCP server expone tools de negocio (productos, stock, pedidos).
- n8n orquesta procesos (order API, payment callback).
- Pensado para ser usado por agentes de IA o APIs externas.

## Cómo arrancar

```bash
docker compose up -d
cd services/mcp-backend
python src/server.py
```
