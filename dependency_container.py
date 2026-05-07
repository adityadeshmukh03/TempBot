from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DependencyContainer:
    kite: object
    builder: object
    order_manager: object
    state_store: object
    logger: object = None

