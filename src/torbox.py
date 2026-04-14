import logging
import time

import httpx

_BASE = "https://api.torbox.app"
_VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".m4v"}
_log = logging.getLogger(__name__)



class TorboxCacheService:
    def __init__(self, api_key: str, api_version: str = "v1"):
        self._api_key = api_key
        self._api_version = api_version

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._api_key}"}

    def _url(self, path: str) -> str:
        return f"{_BASE}/{self._api_version}{path}"

    async def get_cached_hashes(self, hashes: list[str]) -> set[str]:
        if not hashes:
            return set()

        normalized = list({h.strip().lower() for h in hashes if h.strip()})
        if not normalized:
            return set()

        async with httpx.AsyncClient(timeout=30.0) as client:
            t = time.monotonic()
            resp = await client.get(
                self._url("/api/torrents/checkcached"),
                params={"hash": ",".join(normalized), "format": "object"},
                headers=self._headers(),
            )
            resp.raise_for_status()

        _log.debug("%s %s %s (%.0fms)", resp.request.method, resp.request.url, resp.status_code, (time.monotonic() - t) * 1000)
        data = resp.json().get("data")
        return self._extract_cached_hashes(data, normalized)

    @staticmethod
    def _extract_cached_hashes(data, requested_hashes: list[str]) -> set[str]:
        if not data:
            return set()

        requested = {h.lower() for h in requested_hashes}
        cached = set()

        if isinstance(data, dict):
            for key, value in data.items():
                if key.lower() in requested and value:
                    cached.add(key.lower())
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, str) and item.lower() in requested:
                    cached.add(item.lower())
                elif isinstance(item, dict):
                    for field in ("hash", "torrent_hash", "id"):
                        candidate = item.get(field)
                        if isinstance(candidate, str) and candidate.lower() in requested:
                            if item.get("cached", True):
                                cached.add(candidate.lower())
                            break

        return cached

    async def add_magnet(self, magnet: str) -> int:
        async with httpx.AsyncClient(timeout=30.0) as client:
            t = time.monotonic()
            resp = await client.post(
                self._url("/api/torrents/createtorrent"),
                data={"magnet": magnet},
                headers=self._headers(),
            )
            resp.raise_for_status()

        _log.debug("%s %s %s (%.0fms)", resp.request.method, resp.request.url, resp.status_code, (time.monotonic() - t) * 1000)
        body = resp.json()
        torrent_id = (body.get("data") or {}).get("torrent_id")
        if torrent_id is None:
            raise ValueError(f"No torrent_id in response: {body}")
        return int(torrent_id)

    async def get_video_file_id(self, torrent_id: int) -> int:
        async with httpx.AsyncClient(timeout=30.0) as client:
            t = time.monotonic()
            resp = await client.get(
                self._url("/api/torrents/mylist"),
                params={"id": torrent_id, "bypass_cache": "true"},
                headers=self._headers(),
            )
            resp.raise_for_status()

        _log.debug("%s %s %s (%.0fms)", resp.request.method, resp.request.url, resp.status_code, (time.monotonic() - t) * 1000)
        body = resp.json()
        torrent = body.get("data")
        if not torrent:
            raise ValueError(f"Torrent {torrent_id} not found")

        files = torrent.get("files") or []
        if not files:
            raise ValueError(f"No files found for torrent {torrent_id}")

        video_files = [
            f for f in files
            if (f.get("name") or "").lower().endswith(tuple(_VIDEO_EXTENSIONS))
        ]
        candidates = video_files if video_files else files
        best = max(candidates, key=lambda f: f.get("size") or 0)
        fid = best.get("id")
        if fid is None:
            raise ValueError(f"No file id found for torrent {torrent_id}")
        return int(fid)


__all__ = ["TorboxCacheService"]
