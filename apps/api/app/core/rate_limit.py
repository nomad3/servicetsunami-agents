"""Shared slowapi Limiter instance.

Kept in its own module to avoid circular imports: route modules that use
`@limiter.limit(...)` must import from here, not from `app.main` (which
itself imports the route modules).
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
