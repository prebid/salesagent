"""Listen ports of runner-owned signals origins behind the shared TLS fronts."""

SHARED_SIGNALS_PORT = 9100


def signals_origin_port(worker_index: int | None = None) -> int:
    return SHARED_SIGNALS_PORT if worker_index is None else SHARED_SIGNALS_PORT + 1 + worker_index
