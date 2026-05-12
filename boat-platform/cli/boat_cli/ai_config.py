"""User-level AI configuration stored in ~/.config/boat/ai.toml."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

_CONFIG_PATH = Path.home() / ".config" / "boat" / "ai.toml"

_DEFAULTS = {
    "endpoint": "http://localhost:11434/v1",
    "model": "qwen2.5-coder:3b",
    "timeout": 120,
}


@dataclass
class AiConfig:
    endpoint: str
    model: str
    timeout: int

    @property
    def config_path(self) -> Path:
        return _CONFIG_PATH


def load() -> AiConfig:
    cfg = dict(_DEFAULTS)
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, "rb") as fh:
            data = tomllib.load(fh)
        cfg.update(data.get("ai", {}))
    return AiConfig(
        endpoint=str(cfg["endpoint"]),
        model=str(cfg["model"]),
        timeout=int(cfg["timeout"]),
    )


def save(endpoint: str | None, model: str | None, timeout: int | None) -> AiConfig:
    current = load()
    updated = AiConfig(
        endpoint=endpoint or current.endpoint,
        model=model or current.model,
        timeout=timeout if timeout is not None else current.timeout,
    )
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_CONFIG_PATH, "w", encoding="utf-8") as fh:
        fh.write("[ai]\n")
        fh.write(f'endpoint = "{updated.endpoint}"\n')
        fh.write(f'model    = "{updated.model}"\n')
        fh.write(f'timeout  = {updated.timeout}\n')
    return updated
