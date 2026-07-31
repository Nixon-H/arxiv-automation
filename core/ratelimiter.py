import time

from core.database import Database
from core.logger import AppLogger


class RateLimiter:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._providers: dict[str, tuple[int, int]] = {}

    def register_provider(self, provider: str, max_per_hour: int, max_per_day: int) -> None:
        self._providers[provider] = (max_per_hour, max_per_day)
        self.db.init_rate_limit(provider, max_per_hour, max_per_day)
        AppLogger.debug(f"Rate limit: {provider} = {max_per_hour}/h, {max_per_day}/d")

    def check(self, provider: str) -> tuple[bool, str]:
        ok, msg = self.db.check_rate_limit(provider)
        return ok, msg

    def increment(self, provider: str) -> None:
        self.db.increment_rate_limit(provider)

    def get_adaptive_delay(self, provider: str, base_range: tuple[float, float]) -> float:
        limits = self._providers.get(provider, (20, 200))
        per_hour_used = self._get_hourly_usage(provider)
        ratio = per_hour_used / max(limits[0], 1)

        if ratio > 0.8:
            delay = base_range[1] * 2.0
        elif ratio > 0.5:
            delay = base_range[0] + (base_range[1] - base_range[0]) * ratio
        else:
            delay = base_range[0]

        jitter = delay * 0.2
        return delay + (time.time() % 1 * jitter)

    def _get_hourly_usage(self, provider: str) -> int:
        rl = self.db.fetchone(
            "SELECT sent_this_hour FROM rate_limits WHERE provider = ?", (provider,)
        )
        return rl["sent_this_hour"] if rl else 0

    def get_limits(self, provider: str) -> tuple[int, int]:
        return self._providers.get(provider, (20, 200))
