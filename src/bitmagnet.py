import base64
import binascii
from urllib.parse import parse_qs, urlparse
import xml.etree.ElementTree as ET

import httpx


class BitmagnetTorznabClient:
    def __init__(self, torznab_url: str, torbox_cache_service=None):
        self._torznab_url = torznab_url
        self._torbox_cache_service = torbox_cache_service

    async def search_streams(self, slug: str) -> list[dict]:
        query = self._slug_to_query(slug)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                self._torznab_url,
                params={"t": "search", "q": query},
            )
            response.raise_for_status()

        streams = self._parse_streams(response.text, query)
        if not streams or self._torbox_cache_service is None:
            return streams

        cached_hashes = await self._torbox_cache_service.get_cached_hashes(
            [stream["infoHash"] for stream in streams]
        )
        for stream in streams:
            is_cached = stream["infoHash"] in cached_hashes
            stream["cached"] = is_cached
            stream["name"] = self._build_name(stream.get("resolution"), is_cached)

        streams.sort(key=self._stream_sort_key)

        for stream in streams:
            stream.pop("cached", None)
            stream.pop("resolution", None)
        return streams

    def _parse_streams(self, xml_text: str, fallback_query: str) -> list[dict]:
        root = ET.fromstring(xml_text)

        streams = []
        seen_hashes = set()
        for item in root.findall(".//item"):
            title = item.findtext("title", default=fallback_query).strip() or fallback_query
            attrs = self._torznab_attrs(item)
            magnet_url = item.findtext("link", default="").strip()
            if not magnet_url:
                enclosure = item.find("enclosure")
                if enclosure is not None:
                    magnet_url = (enclosure.attrib.get("url") or "").strip()
            if not magnet_url:
                continue

            stream = self._stream_from_magnet(
                magnet_url,
                title,
                size_bytes=self._parse_size(item.findtext("size", default="")),
                seeders=attrs.get("seeders"),
                leechers=attrs.get("leechers"),
                resolution=attrs.get("resolution"),
                video_type=attrs.get("video"),
            )
            if not stream:
                continue

            info_hash = stream["infoHash"]
            if info_hash in seen_hashes:
                continue

            seen_hashes.add(info_hash)
            streams.append(stream)

        for stream in streams:
            stream["name"] = self._build_name(stream.get("resolution"), False)

        return streams

    @staticmethod
    def _slug_to_query(slug: str) -> str:
        return slug.replace("-", " ").strip()

    @staticmethod
    def _normalize_info_hash(raw_hash: str) -> str:
        normalized = raw_hash.strip()
        if len(normalized) == 40:
            return normalized.lower()
        if len(normalized) == 32:
            return base64.b32decode(normalized.upper()).hex()
        raise ValueError("Unsupported btih format")

    @classmethod
    def _stream_from_magnet(
        cls,
        magnet_url: str,
        fallback_name: str,
        size_bytes: int | None = None,
        seeders: str | None = None,
        leechers: str | None = None,
        resolution: str | None = None,
        video_type: str | None = None,
    ) -> dict | None:
        parsed = urlparse(magnet_url)
        if parsed.scheme != "magnet":
            return None

        params = parse_qs(parsed.query)
        xt_values = params.get("xt", [])
        btih = next(
            (
                value.removeprefix("urn:btih:")
                for value in xt_values
                if value.startswith("urn:btih:")
            ),
            "",
        )
        if not btih:
            return None

        try:
            info_hash = cls._normalize_info_hash(btih)
        except (ValueError, binascii.Error):
            return None

        trackers = []
        for tracker in params.get("tr", []):
            if tracker.startswith(("udp://", "http://", "https://")):
                trackers.append(f"tracker:{tracker}")

        display_name = params.get("dn", [fallback_name])[0] or fallback_name
        stream = {
            "name": cls._build_name(resolution, False),
            "description": cls._build_description(
                display_name,
                size_bytes=size_bytes,
                seeders=seeders,
                leechers=leechers,
                resolution=resolution,
                video_type=video_type,
            ),
            "infoHash": info_hash,
            "sources": trackers,
            "resolution": resolution,
            "_magnet": magnet_url,
        }
        if size_bytes is not None:
            stream["behaviorHints"] = {"videoSize": size_bytes}
        return stream

    @staticmethod
    def _torznab_attrs(item: ET.Element) -> dict[str, str]:
        attrs = {}
        for child in item:
            if child.tag.endswith("attr"):
                name = child.attrib.get("name")
                value = child.attrib.get("value")
                if name and value:
                    attrs[name] = value
        return attrs

    @staticmethod
    def _parse_size(raw_size: str) -> int | None:
        raw_size = raw_size.strip()
        if not raw_size:
            return None
        try:
            size = int(raw_size)
        except ValueError:
            return None
        return size if size > 0 else None

    @staticmethod
    def _format_size(size_bytes: int | None) -> str | None:
        if size_bytes is None:
            return None

        units = ["B", "KB", "MB", "GB", "TB"]
        size = float(size_bytes)
        unit_idx = 0
        while size >= 1024 and unit_idx < len(units) - 1:
            size /= 1024
            unit_idx += 1

        if unit_idx == 0:
            return f"{int(size)} {units[unit_idx]}"
        return f"{size:.2f} {units[unit_idx]}"

    @staticmethod
    def _build_name(resolution: str | None, is_cached: bool) -> str:
        parts = ["[TB⚡]" if is_cached else "[TB⏳]", "Bitmagnet"]
        if resolution:
            parts.append(resolution)
        return " ".join(parts)

    @classmethod
    def _build_description(
        cls,
        display_name: str,
        size_bytes: int | None,
        seeders: str | None,
        leechers: str | None,
        resolution: str | None,
        video_type: str | None,
    ) -> str:
        lines = []
        if seeders or leechers:
            lines.append(f"👥 S:{seeders or '?'} L:{leechers or '?'}")

        media_bits = [value for value in [video_type, resolution] if value]
        if media_bits:
            lines.append(f"🎥 {' '.join(media_bits)}")

        formatted_size = cls._format_size(size_bytes)
        if formatted_size:
            lines.append(f"📦 {formatted_size}")

        lines.append(f"📁 {display_name}")
        return "\n".join(lines)

    @staticmethod
    def _resolution_rank(resolution: str | None) -> int:
        if not resolution:
            return -1

        digits = "".join(char for char in resolution if char.isdigit())
        if not digits:
            return -1

        try:
            return int(digits)
        except ValueError:
            return -1

    @classmethod
    def _stream_sort_key(cls, stream: dict) -> tuple[int, int, str]:
        cached_rank = 0 if stream.get("cached") else 1
        resolution_rank = cls._resolution_rank(stream.get("resolution"))
        description = stream.get("description", "")
        return (cached_rank, -resolution_rank, description)
