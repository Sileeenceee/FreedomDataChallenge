from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(ROOT / ".env", ".env"), extra="ignore")

    # Single-file SQLite storage, zero setup. Any SQLAlchemy URL works if a server DB is ever needed.
    database_url: str = f"sqlite:///{ROOT / 'backend' / 'data' / 'cqa.db'}"
    cors_origins: list[str] = ["*"]
    frontend_dir: Path = ROOT / "frontend"

    # Organisers' temporary API. Full GET URL with any group_id; we substitute the requested group.
    cqa_api_url: str = ""

    # Evaluator: "anthropic" (Claude) or "mock" (offline heuristic, for UI development without a key).
    # "auto" = anthropic when ANTHROPIC_API_KEY is set, otherwise mock.
    evaluator: str = "auto"
    anthropic_api_key: str = ""
    cqa_model: str = "claude-opus-5-5"
    cqa_effort: str = "medium"  # low | medium | high | xhigh | max
    eval_concurrency: int = 4

    # USD per 1M tokens, used to report the cost of every evaluation.
    price_input: float = 4.0
    price_output: float = 20.0
    price_cache_write: float = 5.0
    price_cache_read: float = 0.20

    @property
    def evaluator_mode(self) -> str:
        if self.evaluator == "auto":
            return "anthropic" if self.anthropic_api_key else "mock"
        return self.evaluator


settings = Settings()
