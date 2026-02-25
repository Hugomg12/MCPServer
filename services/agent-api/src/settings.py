from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    GROQ_API_KEY: str
    GROQ_MODEL: str = "llama-3.3-70b-versatile"

    MCP_URL: str = "http://mcp-backend:8000/mcp"
    MCP_API_KEY: str

    PORT: int = 9000

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()