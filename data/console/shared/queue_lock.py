"""Per-token locks for the catch-up queues' cached payloads.

A queue's payload is mutated in place across requests and Flask's dev server is
threaded, so the routes that check the queue and then act on it (every US queue
route; Westminster's finish) hold that queue's lock for the whole request.
Without it a double submit could pass the check twice and, say, commit the same
poll or run the model twice. The US and Westminster queues share one namespace
of tokens, so they share this registry.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from console.shared.preview import get_preview

# One lock per queue token, created on the token's first request and dropped
# once the token has left the preview cache. The guard protects the dict itself;
# each queue's lock serialises that queue's requests.
QUEUE_LOCKS: dict[str, threading.Lock] = {}
_QUEUE_LOCKS_GUARD = threading.Lock()


@contextmanager
def queue_lock(token: str) -> Iterator[None]:
    """Hold one queue's lock for the duration of a request.

    The per-token lock is looked up (or created) under the module guard, which
    also drops the locks of tokens no longer in the preview cache, so the map
    never outgrows the cache. Dropping a lock is safe even if a request still
    holds it: its token is gone, so every request for it ends as expired.

    Args:
        token: Token from the request path; need not be live.
    """
    with _QUEUE_LOCKS_GUARD:
        for stale in [known for known in QUEUE_LOCKS if get_preview(known) is None]:
            del QUEUE_LOCKS[stale]
        lock = QUEUE_LOCKS.setdefault(token, threading.Lock())
    with lock:
        yield
