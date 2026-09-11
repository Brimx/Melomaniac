"""External API, authentication and resilience services."""

__all__ = [
    "MusicApiService",
    "CircuitBreaker",
    "RateLimitError",
]


def __getattr__(name: str):
    """Lazily expose services so authentication imports remain acyclic."""
    if name == "MusicApiService":
        from services.api_service import MusicApiService

        return MusicApiService
    if name in {"CircuitBreaker", "RateLimitError"}:
        from services.circuit_breaker import CircuitBreaker, RateLimitError

        return {"CircuitBreaker": CircuitBreaker, "RateLimitError": RateLimitError}[name]
    raise AttributeError(f"module 'services' has no attribute {name!r}")
