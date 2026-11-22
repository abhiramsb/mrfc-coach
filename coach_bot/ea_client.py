"""Minimal client for EA's unofficial Pro Clubs API.

EA blocks plain HTTP clients (403/timeouts), so we use curl_cffi to send a
real Chrome TLS/HTTP fingerprint, with retries and a small cache.
"""
import asyncio
import time

from curl_cffi import requests as cffi

BASE = "https://proclubs.ea.com/api/fc"
MATCH_TYPES = ("leagueMatch", "playoffMatch", "friendlyMatch")


class EAClient:
    def __init__(self, platform="common-gen5", cache_seconds=300):
        self.platform = platform
        self.cache_seconds = cache_seconds
        self._cache = {}

    def _get_sync(self, path, params):
        last = None
        for attempt in range(4):
            try:
                r = cffi.get(
                    f"{BASE}/{path}",
                    params=params,
                    impersonate="chrome",
                    timeout=20,
                    headers={"Accept": "application/json"},
                )
                if r.status_code == 200:
                    return r.json()
                last = f"HTTP {r.status_code}"
            except Exception as e:  # network / TLS errors
                last = repr(e)
            time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"EA API failed for {path}: {last}")

    async def _get(self, path, **params):
        key = (path, tuple(sorted(params.items())))
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < self.cache_seconds:
            return hit[1]
        data = await asyncio.to_thread(self._get_sync, path, params)
        self._cache[key] = (time.time(), data)
        return data

    async def search_club(self, name):
        return await self._get(
            "allTimeLeaderboard/search", platform=self.platform, clubName=name
        )

    async def club_record(self, club_id):
        res = await self._get(
            "allTimeLeaderboard/search", platform=self.platform, clubName=str(club_id)
        )
        return res

    async def member_stats(self, club_id):
        return await self._get("members/stats", platform=self.platform, clubId=club_id)

    async def matches(self, club_id, match_type):
        return await self._get(
            "clubs/matches",
            matchType=match_type,
            platform=self.platform,
            clubIds=club_id,
        )

    async def all_matches(self, club_id):
        out = []
        for mt in MATCH_TYPES:
            try:
                out.extend(await self.matches(club_id, mt))
            except RuntimeError:
                continue
        out.sort(key=lambda m: m.get("timestamp", 0), reverse=True)
        return out
