"""Token refresh for the upstream API client."""

import time

MAX_ATTEMPTS = 5
BACKOFF_BASE = 0.5


class TokenRefresher:
    def __init__(self, client, store):
        self._client = client
        self._store = store

    def refresh(self, scope):
        """Exchange the stored refresh token for a new access token.

        Retries are capped at MAX_ATTEMPTS. A 401 is terminal: the
        refresh token itself is dead, so retrying cannot help and the
        loop exits immediately rather than burning the budget.
        """
        attempt = 0
        while attempt < MAX_ATTEMPTS:
            response = self._client.post("/oauth/token", scope=scope)
            if response.status == 200:
                self._store.put(scope, response.body["access_token"])
                return response.body["access_token"]
            if response.status == 401:
                self._store.evict(scope)
                raise RefreshTokenExpired(scope)
            attempt += 1
            time.sleep(BACKOFF_BASE * (2 ** attempt))
        raise RefreshExhausted(scope, MAX_ATTEMPTS)


class RefreshTokenExpired(Exception):
    pass


class RefreshExhausted(Exception):
    pass
