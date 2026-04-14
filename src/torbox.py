import asyncio

from torbox_api import TorboxApi
from torbox_api.net.transport.request_error import RequestError


class TorboxCacheService:
    def __init__(self, api_key: str, api_version: str = "v1"):
        self._client = TorboxApi(access_token=api_key)
        self._api_version = api_version

    async def get_cached_hashes(self, hashes: list[str]) -> set[str]:
        if not hashes:
            return set()

        normalized = []
        seen = set()
        for item in hashes:
            key = item.strip().lower()
            if key and key not in seen:
                seen.add(key)
                normalized.append(key)

        if not normalized:
            return set()

        return await asyncio.to_thread(self._get_cached_hashes_sync, normalized)

    def _get_cached_hashes_sync(self, hashes: list[str]) -> set[str]:
        response = self._client.torrents.get_torrent_cached_availability(
            self._api_version,
            hash=",".join(hashes),
            format="object",
        )
        return self._extract_cached_hashes(response.data, hashes)

    @staticmethod
    def _extract_cached_hashes(data, requested_hashes: list[str]) -> set[str]:
        if data is None:
            return set()

        requested = {item.lower() for item in requested_hashes}
        cached = set()

        if isinstance(data, dict):
            for key, value in data.items():
                key_lower = str(key).lower()
                if key_lower in requested and value:
                    cached.add(key_lower)
            return cached

        if isinstance(data, list):
            for item in data:
                if isinstance(item, str):
                    item_lower = item.lower()
                    if item_lower in requested:
                        cached.add(item_lower)
                elif isinstance(item, dict):
                    for candidate_key in ("hash", "torrent_hash", "id"):
                        candidate = item.get(candidate_key)
                        if isinstance(candidate, str) and candidate.lower() in requested:
                            if item.get("cached", True):
                                cached.add(candidate.lower())
                            break

        return cached


__all__ = ["RequestError", "TorboxCacheService"]
