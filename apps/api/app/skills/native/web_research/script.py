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


ANGLE_TEMPLATES = {
    "overview": "{topic}",
    "news": "{topic} latest news 2024 2025 2026",
    "reviews": "{topic} reviews opinions",
    "pricing": "{topic} pricing cost",
    "competitors": "{topic} competitors alternatives vs",
    "problems": "{topic} problems issues complaints",
    "leadership": "{topic} founder CEO leadership team",
}

def _gen_angles(topic, custom):
    if custom:
        names = [a.strip().lower() for a in custom.split(",") if a.strip()]
        return {n: ANGLE_TEMPLATES.get(n, "{topic} " + n).format(topic=topic) for n in names}
    tl = topic.lower()
    angles = {"overview": topic}
    if any(k in tl for k in ["company", "inc", "corp", "ltd", "startup", "saas"]):
        for a in ["news", "reviews", "pricing", "competitors", "leadership"]:
            angles[a] = ANGLE_TEMPLATES[a].format(topic=topic)
    elif any(k in tl for k in ["vs", "versus", "compare"]):
        angles["comparison"] = topic
        angles["reviews"] = f"{topic} reviews"
    else:
        angles["news"] = f"{topic} latest news"
        angles["details"] = f"{topic} overview explained"
        angles["opinions"] = f"{topic} reviews opinions reddit"
    return angles

def execute(inputs: dict) -> dict:
    topic = inputs.get("topic", "").strip()
    if not topic: return {"error": "Missing required input: topic"}
    angles = _gen_angles(topic, inputs.get("angles"))
    max_per = min(int(inputs.get("max_results_per_angle", 3)), 5)
    findings, urls = {}, set()
    for name, query in angles.items():
        res = _search(query, max_per)
        findings[name] = {"query": query, "results": res, "result_count": len(res)}
        for r in res: urls.add(r["url"])
    return {"topic": topic, "angles": list(angles.keys()), "findings": findings,
            "total_sources": len(urls), "key_urls": list(urls)[:15]}

if __name__ == "__main__":
    print(json.dumps(execute(json.loads(sys.stdin.read())), indent=2))
