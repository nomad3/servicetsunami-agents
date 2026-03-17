import sys, json, re
import requests
from bs4 import BeautifulSoup, Comment

REMOVE_TAGS = ["script", "style", "noscript", "iframe", "svg", "canvas",
               "nav", "footer", "header", "aside", "form"]
BOILERPLATE_RE = re.compile(
    r"(cookie|banner|popup|modal|sidebar|footer|nav|menu|social|share|comment|advert|promo|signup|subscribe)",
    re.IGNORECASE)

def _clean(soup):
    for t in REMOVE_TAGS:
        for tag in soup.find_all(t): tag.decompose()
    for c in soup.find_all(string=lambda t: isinstance(t, Comment)): c.extract()
    for el in soup.find_all(True):
        cls = " ".join(el.get("class", []))
        eid = el.get("id", "")
        if BOILERPLATE_RE.search(cls) or BOILERPLATE_RE.search(eid):
            el.decompose()
    return soup

def _main_content(soup):
    for sel in ["main", "article", '[role="main"]', "#content", ".content", ".post"]:
        m = soup.select_one(sel)
        if m and len(m.get_text(strip=True)) > 100:
            return m.get_text(separator="\n", strip=True)
    body = soup.find("body")
    return body.get_text(separator="\n", strip=True) if body else soup.get_text(separator="\n", strip=True)

def execute(inputs: dict) -> dict:
    url = inputs.get("url", "").strip()
    if not url: return {"error": "Missing required input: url"}
    if not url.startswith(("http://", "https://")): url = "https://" + url
    extract_links = inputs.get("extract_links", False)
    max_length = int(inputs.get("max_length", 5000))
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    try:
        resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        if resp.status_code != 200: return {"error": f"HTTP {resp.status_code}"}
    except requests.exceptions.RequestException as e:
        return {"error": str(e)}
    soup = BeautifulSoup(resp.text, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else None
    meta_tag = soup.find("meta", attrs={"name": lambda v: v and v.lower() == "description"})
    meta_desc = meta_tag.get("content") if meta_tag else None
    h1s = [t.get_text(strip=True) for t in soup.find_all("h1")]
    h2s = [t.get_text(strip=True) for t in soup.find_all("h2")]
    links = []
    if extract_links:
        for a in soup.find_all("a", href=True):
            h, t = a["href"], a.get_text(strip=True)
            if h.startswith(("http://", "https://")) and t:
                links.append({"text": t[:100], "url": h})
                if len(links) >= 20: break
    soup = _clean(soup)
    content = _main_content(soup)
    content = re.sub(r"\n{3,}", "\n\n", content)
    if len(content) > max_length: content = content[:max_length] + "\n\n[... truncated]"
    result = {"url": url, "title": title, "meta_description": meta_desc,
              "headings": {"h1": h1s[:5], "h2": h2s[:10]},
              "content": content, "word_count": len(content.split())}
    if extract_links: result["links"] = links
    return result

if __name__ == "__main__":
    print(json.dumps(execute(json.loads(sys.stdin.read())), indent=2))
