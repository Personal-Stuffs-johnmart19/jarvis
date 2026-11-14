"""Passive startup routing notice; network/model checks require an explicit request."""
from __future__ import annotations
from threading import Event
from typing import Callable
from local_mode.messages import OwnedText


def start_network_check(stop: Event, report: Callable[[str], None], connected: Callable[[bool], None], *, initialize: bool = True) -> None:
    """Preserve the legacy call boundary without probing, retries or preference writes.

    Routing is resolved lazily by the next requested model operation. The connected
    callback is retained for callers, but a passive startup cannot verify connectivity.
    Recovery calls intentionally do nothing; an actual request owns its own routing.
    """
    if stop.is_set() or not initialize:
        return None
    report(OwnedText("Network checks are manual. Saved routing is used for the next model request."))
    return None
