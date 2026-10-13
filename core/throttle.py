import hashlib
from functools import wraps

from django.contrib import messages
from django.core.cache import cache
from django.shortcuts import redirect

MESSAGE = 'Too many attempts. Please wait a few minutes and try again.'


def limit_reached(request, scope, limit=10, window=300, ident=None):
    """Count attempts per IP address, or per `ident` (for example an email). True when exceeded."""
    who = (hashlib.sha256(ident.encode()).hexdigest()[:32] if ident
           else request.META.get('REMOTE_ADDR', 'unknown'))
    key = f'throttle:{scope}:{who}'
    cache.add(key, 0, window)
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, window)
        count = 1
    return count > limit


class ThrottledPostMixin:
    """For class-based login views."""
    throttle_scope = 'login'
    throttle_limit = 10

    def post(self, request, *args, **kwargs):
        if limit_reached(request, self.throttle_scope, self.throttle_limit):
            messages.error(request, MESSAGE)
            return redirect(request.path)
        return super().post(request, *args, **kwargs)


def throttle_post(scope, limit=10):
    """For function views."""
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if request.method == 'POST' and limit_reached(request, scope, limit):
                messages.error(request, MESSAGE)
                return redirect(request.path)
            return view(request, *args, **kwargs)
        return wrapper
    return decorator