"""
Application configuration module.

Loads environment variables (from a .env file or the system environment)
and exposes them as simple Python constants used by the rest of the
mcp-backend service — mainly database connection details and the
optional API key for request authentication.
"""

import os
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

# --- Database connection settings ---
# Each variable falls back to a sensible default for local development.
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "5433"))
DB_NAME = os.getenv("DB_NAME", "n8n")
DB_USER = os.getenv("DB_USER", "n8n")
DB_PASSWORD = os.getenv("DB_PASSWORD", "hugo1234")


# API key used by the authentication middleware in main.py.
# If empty, authentication is effectively disabled.
MCP_API_KEY = os.getenv("MCP_API_KEY", "").strip()
