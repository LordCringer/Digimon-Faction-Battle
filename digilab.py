import aiohttp
import logging
from urllib.parse import urljoin

import config

log = logging.getLogger("digilab")


class DigiLabError(Exception):
    pass


class DigiLabClient:
    def __init__(self, api_key: str = None, session: aiohttp.ClientSession = None):
        self.api_key = api_key or config.DIGILAB_API_KEY
        self._session = session
        self._owns_session = session is None

    async def __aenter__(self):
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *exc):
        if self._owns_session and self._session:
            await self._session.close()

    async def _get(self, path: str, params: dict = None):
        if self._session is None:
            self._session = aiohttp.ClientSession()
        url = f"{config.DIGILAB_BASE_URL}{path}"
        headers = {"X-API-Key": self.api_key} if self.api_key else {}

        # Some endpoints (observed on /api/search) 302-redirect from
        # api.digilab.cards to the bare digilab.cards domain, and that
        # redirect's Location header drops the original query string.
        # aiohttp's default auto-follow would silently hit the query-less
        # URL and return an empty result. So we disable auto-follow and
        # re-attach our own params to whatever host we're redirected to.
        resp = await self._request_no_redirect(url, headers, params)
        redirect_hops = 0
        current_url = url
        while resp.status in (301, 302, 307, 308) and redirect_hops < 3:
            location = resp.headers.get("Location")
            resp.release()
            if not location:
                break
            current_url = urljoin(current_url, location)
            redirect_hops += 1
            resp = await self._request_no_redirect(current_url, headers, params)

        return await self._handle_response(resp)

    async def _request_no_redirect(self, url: str, headers: dict, params: dict):
        return await self._session.get(url, headers=headers, params=params, allow_redirects=False)

    async def _handle_response(self, resp):
        async with resp:
            if resp.status == 429:
                retry_after = resp.headers.get("Retry-After", "unknown")
                raise DigiLabError(f"Rate limited by DigiLab API, retry after {retry_after}s")
            if resp.status in (401, 403):
                raise DigiLabError("DigiLab API key missing or invalid")
            if resp.status == 404:
                return None
            if resp.status >= 400:
                text = await resp.text()
                raise DigiLabError(f"DigiLab API error {resp.status}: {text}")
            return await resp.json()

    async def search(self, query: str):
        """Cross-entity search; used to resolve a Discord user's DigiLab player."""
        return await self._get("/api/search", {"q": query})

    async def decklists(self, scene: str = None, event_type: list[str] = None,
                         date_from: str = None, page: int = 1, per_page: int = 50,
                         sort: str = "date", sort_dir: str = "asc"):
        params = {"page": page, "per_page": per_page, "sort": sort, "sort_dir": sort_dir}
        if scene:
            params["scene"] = scene
        if date_from:
            params["date_from"] = date_from
        # event_type is repeatable; aiohttp handles list values as repeated params
        if event_type:
            params["event_type"] = event_type
        return await self._get("/api/decklists", params)

    async def scenes(self):
        return await self._get("/api/scenes")

    async def scene(self, slug: str):
        return await self._get(f"/api/scene/{slug}")
