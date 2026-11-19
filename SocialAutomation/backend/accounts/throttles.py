from hashlib import sha256

from rest_framework.throttling import SimpleRateThrottle


class LoginIPThrottle(SimpleRateThrottle):
    """Limit login attempts from one client IP address."""

    scope = "login_ip"

    def get_cache_key(self, request, view):
        ip_address = self.get_ident(request)
        return self.cache_format % {"scope": self.scope, "ident": ip_address}


class LoginUsernameThrottle(SimpleRateThrottle):
    """Limit repeated attempts for one username without storing it in cache keys."""

    scope = "login_username"

    def get_cache_key(self, request, view):
        username = getattr(request, "login_throttle_username", "")
        if not username:
            return None

        username_hash = sha256(username.casefold().encode("utf-8")).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": username_hash}
