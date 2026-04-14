import base64
import binascii
import json
import logging
import os
import xml.etree.ElementTree as ET
from urllib.parse import quote, unquote

logging.basicConfig(level=logging.DEBUG, format="%(name)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.bitmagnet import BitmagnetTorznabClient
from src.torbox import TorboxCacheService

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "HEAD"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="src/templates")

def _template_response(request: Request, context: dict):
    template_context = {"request": request, **context}
    try:
        return templates.TemplateResponse(
            request=request,
            name="configure.html",
            context=template_context,
        )
    except TypeError:
        return templates.TemplateResponse("configure.html", template_context)


def _decode_config(config_b64: str) -> dict:
    padding = "=" * (-len(config_b64) % 4)
    try:
        decoded = base64.urlsafe_b64decode(config_b64 + padding).decode()
        config = json.loads(decoded)
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail="Invalid config") from exc

    if not isinstance(config, dict):
        raise HTTPException(status_code=400, detail="Invalid config")
    return config


def _require_torbox_api_key(config_b64: str) -> str:
    config = _decode_config(config_b64)
    api_key = config.get("torboxApiKey", "")
    if not isinstance(api_key, str) or not api_key.strip():
        raise HTTPException(status_code=400, detail="Missing torboxApiKey")
    return api_key.strip()


def _require_torznab_url() -> str:
    torznab_url = os.environ.get("TORZNAB_ENDPOINT", "").strip()
    if torznab_url:
        return torznab_url
    raise HTTPException(status_code=500, detail=f"TORZNAB_ENDPOINT not configured")


def _get_bitmagnet_client(torbox_api_key: str) -> BitmagnetTorznabClient:
    return BitmagnetTorznabClient(
        _require_torznab_url(),
        torbox_cache_service=TorboxCacheService(torbox_api_key),
    )


def _build_manifest() -> dict:
    return {
        "id": "com.stdin-stderr.pornscraper",
        "version": "1.0.0",
        "name": "PornScraper for Stremio",
        "description": "NSFW Streams with TorBox support.",
        "resources": ["stream"],
        "types": ["movie"],
        "catalogs": [],
        "idPrefixes": ["tpdb_"],
        "behaviorHints": {"adult": True, "configurable": True},
    }


@app.get("/")
async def index():
    return RedirectResponse(url="/configure")


@app.get("/configure")
async def configure(request: Request):
    return _template_response(request, {"torbox_api_key": ""})


@app.get("/{config_b64}/configure")
async def configure_edit(request: Request, config_b64: str):
    config = _decode_config(config_b64)
    api_key = config.get("torboxApiKey", "")
    if not isinstance(api_key, str):
        raise HTTPException(status_code=400, detail="Invalid config")

    return _template_response(request, {"torbox_api_key": api_key})


@app.get("/{config_b64}/manifest.json")
async def manifest(config_b64: str):
    _require_torbox_api_key(config_b64)
    return JSONResponse(_build_manifest())


@app.get("/{config_b64}/stream/movie/{meta_id}.json")
async def stream(request: Request, config_b64: str, meta_id: str):
    torbox_api_key = _require_torbox_api_key(config_b64)

    if not meta_id.startswith("tpdb_"):
        raise HTTPException(status_code=400, detail="Invalid meta id")

    slug = meta_id.removeprefix("tpdb_").strip()
    if not slug:
        raise HTTPException(status_code=400, detail="Invalid meta id")

    client = _get_bitmagnet_client(torbox_api_key)
    try:
        streams = await client.search_streams(slug)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Failed to query Torznab") from exc
    except ET.ParseError as exc:
        raise HTTPException(status_code=502, detail="Invalid Torznab response") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Failed to query TorBox cache") from exc

    base = str(request.base_url).rstrip("/")
    for s in streams:
        magnet = s.pop("_magnet", None)
        if magnet:
            encoded = quote(magnet, safe="")
            s["url"] = f"{base}/{config_b64}/stream/tpdb_{slug}/{encoded}"
        s.pop("infoHash", None)
        s.pop("sources", None)

    return JSONResponse({"streams": streams})


@app.api_route("/{config_b64}/stream/tpdb_{slug}/{encoded_magnet:path}", methods=["GET", "HEAD"])
async def resolve_stream(config_b64: str, slug: str, encoded_magnet: str):
    torbox_api_key = _require_torbox_api_key(config_b64)
    magnet = unquote(encoded_magnet)

    torbox = TorboxCacheService(torbox_api_key)
    try:
        torrent_id = await torbox.add_magnet(magnet)
        file_id = await torbox.get_video_file_id(torrent_id)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="TorBox error") from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    dl_url = (
        f"https://api.torbox.app/v1/api/torrents/requestdl"
        f"?token={torbox_api_key}&torrent_id={torrent_id}&file_id={file_id}&redirect=true"
    )
    return RedirectResponse(url=dl_url, status_code=301)
