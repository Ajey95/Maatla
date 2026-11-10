"""Approved local knowledge first; optional web search limited to allowlisted domains."""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup


def is_current(entry: dict) -> bool:
    expiry = entry.get("expires_at")
    if not expiry:
        return True
    try:
        moment = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment > datetime.now(timezone.utc)
    except ValueError:
        return False


def find_local(query: str, entries: list[dict], limit: int = 3) -> list[dict]:
    terms = {w for w in re.findall(r"[\w]+", query.lower()) if len(w) > 2}
    scored = []
    for entry in entries:
        if not entry.get("approved") or not is_current(entry):
            continue
        hay = (entry["title"] + " " + entry["body"]).lower()
        score = sum(1 for term in terms if term in hay)
        if score:
            scored.append((score, entry))
    return [x for _, x in sorted(scored, key=lambda x: -x[0])[:limit]]


def _allowed(url: str) -> bool:
    host = urlparse(url).hostname or ""
    allowed = {x.strip().lower() for x in os.getenv("MAATLA_ALLOWED_DOMAINS", "").split(",") if x.strip()}
    return url.startswith("https://") and any(host == domain or host.endswith("." + domain) for domain in allowed)


async def fetch_authorized(url: str) -> dict:
    if not _allowed(url):
        raise ValueError("URL is outside the authorized HTTPS domains")
    async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
        result = await client.get(url)
        result.raise_for_status()
        if "text/html" not in result.headers.get("content-type", ""):
            raise ValueError("Only HTML pages can be fetched")
    soup = BeautifulSoup(result.text[:500_000], "html.parser")
    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()
    return {"url": url, "title": soup.title.get_text(" ", strip=True) if soup.title else url,
            "text": soup.get_text(" ", strip=True)[:6000]}


async def search_authorized(query: str) -> list[dict]:
    """Search only configured domains through Brave when a key is present."""
    key = os.getenv("BRAVE_SEARCH_API_KEY", "")
    domains = [x.strip() for x in os.getenv("MAATLA_ALLOWED_DOMAINS", "").split(",") if x.strip()]
    if not key or not domains:
        return []
    q = query + " " + " OR ".join("site:" + d for d in domains)
    async with httpx.AsyncClient(timeout=8) as client:
        result = await client.get("https://api.search.brave.com/res/v1/web/search",
                                  headers={"X-Subscription-Token": key}, params={"q": q, "count": 5})
        result.raise_for_status()
    return [{"title": x.get("title", ""), "url": x.get("url", ""),
             "snippet": x.get("description", "")} for x in result.json().get("web", {}).get("results", [])
            if _allowed(x.get("url", ""))][:3]
