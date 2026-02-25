"""
Application settings for the agent-api service.

Uses pydantic-settings to load and validate configuration values from
environment variables (or a .env file). These settings control which
LLM provider and model to use, how to connect to the MCP backend,
and which port the API listens on.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    Validated application settings loaded from environment variables.

    Attributes:
        GROQ_API_KEY: API key for authenticating with the Groq LLM service.
        GROQ_MODEL:   Name of the LLM model to use (default: llama-3.3-70b-versatile).
        MCP_URL:      URL of the MCP backend's HTTP endpoint.
        MCP_API_KEY:  API key sent to the MCP backend for authentication.
        PORT:         Port number the agent-api server listens on (default: 9000).
    """
    GROQ_API_KEY: str
    GROQ_MODEL: str = "llama-3.3-70b-versatile"

    MCP_URL: str = "http://mcp-backend:8000/mcp"
    MCP_API_KEY: str

    PORT: int = 9000

    class Config:
        env_file = ".env"       # Load variables from a .env file if present
        extra = "ignore"        # Ignore extra env vars not listed above


# Singleton instance used throughout the application
settings = Settings()