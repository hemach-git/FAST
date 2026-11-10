"""Config loader. config.yaml is the source of record; env vars override LLM settings."""
from __future__ import annotations

import hashlib
import re
import json
import os
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            v = v.strip()
            if v[:1] in ("'", '"'):                 # quoted value: take what is inside the quotes
                q = v[0]
                end = v.find(q, 1)
                v = v[1:end] if end > 0 else v[1:]
            else:                                   # unquoted: drop an inline "  # comment"
                v = re.split(r"\s+#", v, maxsplit=1)[0].strip()
            os.environ.setdefault(k.strip(), v)


@lru_cache(maxsize=1)
def get_config() -> dict:
    _load_dotenv()
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    cfg["llm"]["provider"] = (os.getenv("LLM_PROVIDER", cfg["llm"]["provider"]).split() or ["mock"])[0].lower()
    cfg["llm"]["model"] = os.getenv("LLM_MODEL", cfg["llm"]["model"])
    if os.getenv("OPENAI_MODEL"):                    # one env var switches every agent's model
        for role in ("router", "specialist", "synthesizer"):
            cfg["agents"][role]["model"] = os.environ["OPENAI_MODEL"].strip()
    if os.getenv("FAST_AUTH", "").strip().lower() in ("off", "false", "0"):   # local development only
        cfg["auth"]["enabled"] = False
    return cfg


def config_hash() -> str:
    return hashlib.sha256(json.dumps(get_config(), sort_keys=True).encode()).hexdigest()[:12]


def model_label() -> str:
    llm = get_config()["llm"]
    return "mock-extractive-v1" if llm["provider"] == "mock" else f"{llm['provider']}:{llm['model']}"


def db_path() -> Path:
    return Path(os.getenv("FAST_DB_PATH", ROOT / "data" / "fast.db"))


def log_dir() -> Path:
    return Path(os.getenv("FAST_LOG_DIR", ROOT / "logs"))
