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

    async def tournaments(self, scene: str = None, event_type: list[str] = None,
                           date_from: str = None, date_to: str = None,
                           page: int = 1, per_page: int = 50,
                           sort: str = "date", sort_dir: str = "asc"):
        """Listing endpoint — winner only, but tells us which tournament_ids exist."""
        params = {"page": page, "per_page": per_page, "sort": sort, "sort_dir": sort_dir}
        if scene:
            params["scene"] = scene
        if date_from:
            params["date_from"] = date_from
        if date_to:
            params["date_to"] = date_to
        if event_type:
            params["event_type"] = event_type
        return await self._get("/api/tournaments", params)

    async def tournament_detail(self, tournament_id: int):
        """Full standings for one tournament — every placement, no decklist required."""
        return await self._get(f"/api/tournament/{tournament_id}")

    async def leaderboard(self, scene: str = None, page: int = 1, per_page: int = 100,
                           sort: str = "rating", sort_dir: str = "desc"):
        params = {"page": page, "per_page": per_page, "sort": sort, "sort_dir": sort_dir}
        if scene:
            params["scene"] = scene
        return await self._get("/api/leaderboard", params)

    async def find_players_by_name(self, name: str, scene: str = None, max_pages: int = 3):
        """
        DigiLab removed /api/search (2026-07-20 changelog), so player lookup
        now goes through the leaderboard instead — scoped to a scene when
        possible, since that's both faster and more relevant than a global
        scan. Client-side substring match on display_name.
        """
        name_lower = name.strip().lower()
        matches = []
        for page in range(1, max_pages + 1):
            resp = await self.leaderboard(scene=scene, page=page, per_page=100)
            if not resp or not resp.get("data"):
                break
            for row in resp["data"]:
                if name_lower in (row.get("display_name") or "").lower():
                    matches.append(row)
            pagination = resp.get("pagination", {})
            if page >= pagination.get("total_pages", 1):
                break
        return matches

    async def decklists(self, scene: str = None, event_type: list[str] = None,
                         date_from: str = None, page: int = 1, per_page: int = 50,
                         sort: str = "date", sort_dir: str = "asc"):
        """
        Kept for reference/meta features, but no longer used for points —
        /api/tournament/{id} gives full standings without needing a
        decklist, which is strictly better for scoring purposes.
        """
        params = {"page": page, "per_page": per_page, "sort": sort, "sort_dir": sort_dir}
        if scene:
            params["scene"] = scene
        if date_from:
            params["date_from"] = date_from
        if event_type:
            params["event_type"] = event_type
        return await self._get("/api/decklists", params)

    async def scenes(self):
        return await self._get("/api/scenes")

    async def scene(self, slug: str):
        return await self._get(f"/api/scene/{slug}")
