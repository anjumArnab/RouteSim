"""Settings read from environment variables."""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

MIN_TOKEN_LENGTH = 16
_AGENT_NAME = re.compile(r"[A-Za-z0-9_-]+")


class ConfigError(ValueError):
    pass


def parse_api_keys(raw: str) -> dict[str, str]:
    """Parse "name:token,name2:token2" into a name to token mapping."""
    keys: dict[str, str] = {}
    for item in filter(None, (part.strip() for part in raw.split(","))):
        name, separator, token = item.partition(":")
        name, token = name.strip(), token.strip()
        # Never echo the item itself in errors: it may contain a secret.
        if not separator or not _AGENT_NAME.fullmatch(name):
            raise ConfigError("Each ROUTESIM_API_KEYS entry must look like name:token")
        if len(token) < MIN_TOKEN_LENGTH or not token.isascii() or " " in token:
            raise ConfigError(
                f"Token for '{name}' must be at least {MIN_TOKEN_LENGTH} "
                "ASCII characters without spaces"
            )
        if name in keys or token in keys.values():
            raise ConfigError("Agent names and tokens must be unique")
        keys[name] = token
    if not keys:
        raise ConfigError("ROUTESIM_API_KEYS is not set. Example: claude:<random token>")
    return keys


@dataclass(frozen=True)
class Settings:
    api_keys: dict[str, str]
    host: str = "127.0.0.1"
    port: int = 8000

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Settings":
        raw_port = env.get("ROUTESIM_PORT") or "8000"
        if not raw_port.isdigit():
            raise ConfigError("ROUTESIM_PORT must be a number")
        return cls(
            api_keys=parse_api_keys(env.get("ROUTESIM_API_KEYS", "")),
            host=env.get("ROUTESIM_HOST") or "127.0.0.1",
            port=int(raw_port),
        )
