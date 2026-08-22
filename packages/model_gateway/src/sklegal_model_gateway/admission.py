"""Bounded per-provider admission control.

Saturation fails fast with ProviderSaturationError; there is no queueing.
Callers retry through the pinned Temporal retry class instead of holding a
slot or waiting inside the gateway.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from .errors import ProviderSaturationError
from .models import Provider


class AdmissionController:
    """Thread-safe bounded admission with one counter per provider."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._in_flight: dict[Provider, int] = {}

    def in_flight(self, provider: Provider) -> int:
        with self._lock:
            return self._in_flight.get(provider, 0)

    @contextmanager
    def acquire(
        self,
        provider: Provider,
        *,
        max_concurrent: int,
    ) -> Iterator[None]:
        with self._lock:
            current = self._in_flight.get(provider, 0)
            if current >= max_concurrent:
                raise ProviderSaturationError(
                    f"provider {provider} is saturated: "
                    f"{current} of {max_concurrent} slots in flight"
                )
            self._in_flight[provider] = current + 1
        try:
            yield
        finally:
            with self._lock:
                remaining = self._in_flight[provider] - 1
                if remaining:
                    self._in_flight[provider] = remaining
                else:
                    del self._in_flight[provider]
