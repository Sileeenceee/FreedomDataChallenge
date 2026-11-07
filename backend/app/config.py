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

    # Evaluator: "qwen" (team model served from Colab), "anthropic" (Claude) or "mock" (offline heuristic).
    # "qwen" without QWEN_URL falls back to mock. "auto" = qwen if QWEN_URL, anthropic if key, else mock.
    evaluator: str = "qwen"
    qwen_model: str = "Qwen/Qwen3-4B-Instruct-2507"
    qwen_url: str = ""  # public tunnel URL printed by colab/qwen_server.py
    qwen_token: str = ""  # optional shared secret, must match TOKEN in the Colab cell
    qwen_timeout: int = 1800  # seconds to wait for one dialogue (includes queueing on the single GPU)
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
            return "qwen" if self.qwen_url else "anthropic" if self.anthropic_api_key else "mock"
        if self.evaluator == "qwen" and not self.qwen_url:
            return "mock"
        return self.evaluator

    @property
    def model_name(self) -> str:
        return {"qwen": self.qwen_model, "anthropic": self.cqa_model}.get(self.evaluator_mode, "mock-heuristic")


settings = Settings()
