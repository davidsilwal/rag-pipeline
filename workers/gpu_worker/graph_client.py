#!/usr/bin/env python3
"""workers/gpu_worker/graph_client.py — Microsoft Graph API client (OAuth2, delta sync)."""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from typing import Any

import aiohttp

MS_GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class GraphClient:
    def __init__(self, client_id: str, client_secret: str, tenant_id: str, drive_id: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.tenant_id = tenant_id
        self.drive_id = drive_id
        self._token: str | None = None
        self._token_expiry: float = 0.0

    async def _get_token(self) -> str:
        now = time.time()
        if self._token and now < self._token_expiry - 60:
            return self._token

        async with aiohttp.ClientSession() as session:
            token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
            payload = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            }
            async with session.post(token_url, data=payload) as resp:
                data = await resp.json()
                self._token = data.get("access_token", "")
                self._token_expiry = now + data.get("expires_in", 3600)
                return self._token

    async def _delta_request(self, session: aiohttp.ClientSession, page_token: str | None) -> dict:
        url = f"{MS_GRAPH_BASE}/drives/{self.drive_id}/root/delta"
        headers = {"Authorization": f"Bearer {await self._get_token()}"}
        params = {}
        if page_token:
            params["token"] = page_token
        async with session.get(url, headers=headers, params=params) as resp:
            if resp.status == 429:
                # Respect Retry-After
                retry = int(resp.headers.get("Retry-After", "30"))
                await asyncio.sleep(retry)
                return await self._delta_request(session, page_token)
            resp.raise_for_status()
            return await resp.json()

    async def sync(self, since_token: str | None = None) -> dict:
        """Full + incremental delta sync. Returns merged driveItem list + new delta_token."""
        async with aiohttp.ClientSession() as session:
            result = await self._delta_request(session, since_token)
            items = result.get("value", [])
            new_token = result.get("@odata.deltaLink", None)
            next_link = result.get("@odata.nextLink", None)

            # Paginate nextLink if present
            while next_link:
                async with session.get(next_link, headers={"Authorization": f"Bearer {await self._get_token()}"}) as resp:
                    resp.raise_for_status()
                    more = await resp.json()
                    items.extend(more.get("value", []))
                    next_link = more.get("@odata.nextLink")

            # Also do initial full crawl if no token was supplied
            if since_token is None:
                # Reset: the initial full crawl establishes the first delta token.
                # Subsequent runs will use this token with /delta.
                pass

            return {"items": items, "delta_token": new_token}

    async def download_url(self, session: aiohttp.ClientSession, item_id: str) -> str:
        """Get a short-lived pre-authenticated download URL for a file."""
        url = f"{MS_GRAPH_BASE}/drives/{self.drive_id}/items/{item_id}"
        headers = {"Authorization": f"Bearer {await self._get_token()}"}
        async with session.get(url, headers=headers) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data.get("@microsoft.graph.downloadUrl", "")