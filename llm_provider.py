"""LLM provider abstraction layer.

Maps provider names to API base URLs and credentials so the same
``openai`` SDK client can drive OpenAI, Xiaomi MiMo V2, and future
OpenAI-compatible services.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from openai import OpenAI


@dataclass(frozen=True)
class LLMProvider:
    name: str
    display_name: str
    api_key_env_var: str
    default_model: str
    base_url: str | None = None
    supports_reasoning_effort: bool = False
    supports_vision: bool = False
    available_models: list[str] = field(default_factory=list)


PROVIDERS: dict[str, LLMProvider] = {
    "openai": LLMProvider(
        name="openai",
        display_name="OpenAI",
        base_url=None,
        api_key_env_var="OPENAI_API_KEY",
        default_model="gpt-4.1",
        supports_reasoning_effort=True,
        supports_vision=True,
        available_models=["gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano", "gpt-4o", "gpt-5"],
    ),
    "mimo": LLMProvider(
        name="mimo",
        display_name="Xiaomi MiMo V2",
        base_url="https://api.xiaomimimo.com/v1",
        api_key_env_var="MIMO_API_KEY",
        default_model="mimo-v2-flash",
        supports_reasoning_effort=False,
        supports_vision=False,
        available_models=["mimo-v2-pro", "mimo-v2-flash"],
    ),
}


def get_provider(name: str) -> LLMProvider:
    """Look up a registered provider by name.

    Raises ``ValueError`` if the name is not in the registry.
    """
    provider = PROVIDERS.get(name)
    if provider is None:
        valid = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"Unknown LLM provider {name!r}. Valid providers: {valid}")
    return provider


def get_llm_client(provider_name: str = "openai", *, api_key: str | None = None) -> OpenAI:
    """Return an ``openai.OpenAI`` client configured for *provider_name*.

    If *api_key* is not given explicitly, the provider's environment
    variable is read instead.
    """
    provider = get_provider(provider_name)
    key = api_key or os.environ.get(provider.api_key_env_var)
    if not key:
        raise ValueError(
            f"{provider.api_key_env_var} is not set "
            f"(required for provider {provider.display_name!r})"
        )
    kwargs: dict[str, str] = {"api_key": key}
    if provider.base_url:
        kwargs["base_url"] = provider.base_url
    return OpenAI(**kwargs)


def provider_names() -> list[str]:
    """Return sorted list of registered provider names."""
    return sorted(PROVIDERS)


def provider_choices() -> list[dict[str, str | list[str]]]:
    """Return provider info dicts for use in UI dropdowns.

    Xiaomi MiMo V2 is listed first so it appears as the default choice in
    dropdowns when no explicit selection applies.
    """
    order = ("mimo", "openai")
    names = [n for n in order if n in PROVIDERS] + sorted(
        n for n in PROVIDERS if n not in order
    )
    return [
        {
            "name": p.name,
            "display_name": p.display_name,
            "default_model": p.default_model,
            "available_models": p.available_models,
        }
        for n in names
        for p in (PROVIDERS[n],)
    ]
