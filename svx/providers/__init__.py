"""
Provider registry for SuperVoxtral.

This module centralizes provider discovery and retrieval so the CLI (and other
consumers) can instantiate providers by name without importing their concrete modules.

Design goals:
- Simple API: register_provider(name, factory) and get_provider(name)
- Lazy imports: default providers are registered with factories that import on demand
- Friendly errors: list available providers on unknown name
"""

from __future__ import annotations

from typing import Callable, Dict, List

from .base import Provider, ProviderError, TranscriptionResult

__all__ = [
    "Provider",
    "ProviderError",
    "TranscriptionResult",
    "register_provider",
    "get_provider",
    "available_providers",
    "register_default_providers",
]

# Factory callable that returns a Provider instance when called.
ProviderFactory = Callable[[], Provider]

# Internal registry mapping provider name -> factory
_registry: Dict[str, ProviderFactory] = {}


def register_provider(name: str, factory: ProviderFactory) -> None:
    """
    Register a provider factory by a short, lowercase name.
    If the name already exists, it will be overwritten.
    """
    key = name.strip().lower()
    if not key:
        raise ValueError("Provider name cannot be empty.")
    _registry[key] = factory


def get_provider(name: str) -> Provider:
    """
    Retrieve a Provider instance by name.

    Raises:
        KeyError: if no provider is registered under that name.
    """
    register_default_providers()
    key = name.strip().lower()
    try:
        factory = _registry[key]
    except KeyError as e:
        raise KeyError(
            f"Unknown provider '{name}'. Available: {', '.join(sorted(_registry.keys())) or '(none)'}"
        ) from e
    return factory()


def available_providers() -> List[str]:
    """
    Return the list of available provider names (sorted).
    """
    register_default_providers()
    return sorted(_registry.keys())


def register_default_providers() -> None:
    """
    Register built-in providers with lazy imports to avoid hard dependencies at import time.
    Safe to call multiple times (idempotent).
    """
    # Mistral (voxtral) provider
    if "mistral" not in _registry:

        def _mistral_factory() -> Provider:
            # Lazy import to avoid requiring 'mistralai' until the provider is actually used.
            from .mistral import MistralProvider

            return MistralProvider()

        register_provider("mistral", _mistral_factory)
