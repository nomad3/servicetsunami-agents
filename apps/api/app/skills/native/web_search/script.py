import sys
import json
import os
import re
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup


def _google_search(query: str, num_results: int = 5, **params) -> list:
    """Search using Google Custom Search JSON API."""
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    cse_id = os.environ.get("GOOGLE_CSE_ID", "")
    if not api_key or not cse_id:
        return []
    req_params = {
        "key": api_key, "cx": cse_id, "q": query,
        "num": min(num_results, 10), **params,
    }
    try:
        resp = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params=req_params, timeout=10,
        )
        if resp.status_code != 200:
            return []
        return [
            {"title": i.get("title", ""), "url": i.get("link", ""),
             "snippet": i.get("snippet", ""), "source": "google"}
            for i in resp.json().get("items", [])
        ]
    except Exception:
        return []


def _duckduckgo_search(query: str, num_results: int = 5) -> list:
    """Fallback search using DuckDuckGo HTML scraping."""
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return []
        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        for div in soup.select(".result"):
            title_tag = div.select_one(".result__title a, .result__a")
            snippet_tag = div.select_one(".result__snippet")
            if not title_tag:
                continue
            title = title_tag.get_text(strip=True)
            href = title_tag.get("href", "")
            snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""
            if "uddg=" in href:
                from urllib.parse import parse_qs, urlparse
                real_url = parse_qs(urlparse(href).query).get("uddg", [href])[0]
            else:
                real_url = href
            if title and real_url:
                results.append({"title": title, "url": real_url,
                                "snippet": snippet, "source": "duckduckgo"})
            if len(results) >= num_results:
                break
        return results
    except Exception:
        return []


def _search(query: str, num_results: int = 5, **google_params) -> list:
    results = _google_search(query, num_results, **google_params)
    if not results:
        results = _duckduckgo_search(query, num_results)
    return results


def execute(inputs: dict) -> dict:
    query = inputs.get("query", "").strip()
    if not query:
        return {"error": "Missing required input: query"}
    num_results = min(int(inputs.get("num_results", 5)), 10)
    search_type = inputs.get("search_type", "general")
    extra = {}
    if search_type == "news":
        query = f"{query} news"
        extra["sort"] = "date"
    elif search_type == "images":
        extra["searchType"] = "image"
    results = _search(query, num_results, **extra)
    engine = results[0]["source"] if results else "none"
    return {"query": query, "results": results,
            "result_count": len(results), "engine": engine}


if __name__ == "__main__":
    print(json.dumps(execute(json.loads(sys.stdin.read())), indent=2))
