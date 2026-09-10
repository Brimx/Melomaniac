"""
╔══════════════════════════════════════════════════════════════════════╗
║                    MelomaniacPass v3.3.1                               ║
║                    Paquete Utils                                     ║
╚══════════════════════════════════════════════════════════════════════╝

Paquete: utils
Descripción: Utilidades transversales y helpers compartidos por toda la
            aplicación. Incluye patrones de diseño como Circuit Breaker
            y funciones auxiliares reutilizables.

Módulos:
    - circuit_breaker: Patrón Circuit Breaker para protección contra rate limiting

Autor: MelomaniacPass Team
Versión: 3.3.1
Fecha: 2026
"""

from utils.circuit_breaker import CircuitBreaker, RateLimitError

__all__ = [
    'CircuitBreaker',
    'RateLimitError',
]
