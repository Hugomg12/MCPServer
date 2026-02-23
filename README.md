# MCPServer

## Overview

MCPServer is a minimal but realistic backend system designed for agent-based platforms. It demonstrates how enterprise systems expose business capabilities through **Model Context Protocol (MCP)** tools, orchestrate workflows with **n8n**, and interact with a **PostgreSQL** database using transactional logic.

The goal of this project is to simulate a production-style architecture where:

- Business logic is exposed as secure MCP tools.
- Workflows are orchestrated externally (n8n).
- A future AI agent can call tools via structured tool-calling.
- Database access is safe, transactional, and concurrency-aware.

This project runs on Windows with Docker Desktop.

---

## Architecture

The system consists of:

- **mcp-backend (Python)**
  Exposes business tools using FastMCP.
  Handles authentication, transactions, and database access.

- **PostgreSQL (Docker)**
  Stores products, stock, orders, and reservations.

- **n8n (Docker)**
  Orchestrates workflows that call MCP tools.

### Key Features

- API key authentication (fail-closed) using Bearer token
- Transaction-safe stock reservation with row-level locking
- Order lifecycle management (PENDING → RESERVED → PAID / FAILED / CANCELLED)
- Read-only query tool with SELECT enforcement
- Modular backend structure

---

## Project Structure

```
services/
  mcp-backend/
    src/
      app/
      tools/
      main.py

docker-compose.yml
.env.example
workflows/
```

---

## Environment Configuration

Create a `.env` file in the root of the repository based on `.env.example`.

Example:

```env
# PostgreSQL
POSTGRES_USER=n8n
POSTGRES_PASSWORD=your_password
POSTGRES_DB=n8n

# MCP Backend Auth
MCP_API_KEY=your_secure_key
```

---

## Running the Project (Full Docker Setup)

From the root of the repository:

### 1. Build and start all services

```bash
docker compose up -d --build
```

This starts:

- PostgreSQL
- n8n ([http://localhost:5678](http://localhost:5678))
- MCP backend ([http://localhost:8000/mcp](http://localhost:8000/mcp))

### 2. Stop the project

```bash
docker compose down
```

---

## Running MCP Backend Locally (Optional)

If you prefer running the backend outside Docker:

```bash
cd services/mcp-backend
.venv\Scripts\activate
python src/main.py
```

MCP endpoint:

```
http://localhost:8000/mcp
```

---

## Authentication

All MCP calls require:

```
Authorization: Bearer <MCP_API_KEY>
```

If the header is missing or invalid, the request is rejected.

---

## Example Workflow

1. A webhook in n8n receives `{ sku, qty }`.
2. n8n calls `create_order` via MCP.
3. n8n calls `reserve_for_order`.
4. A payment callback updates order state (`mark_paid` or `mark_failed`).

---

## Purpose

This project serves as a foundation for:

- Tool-calling AI agents
- Multi-agent orchestration experiments
- Backend architecture demonstrations
- Interview or portfolio projects simulating real-world systems

It is intentionally small but structured in a production-oriented way.
