# script.py
import sys
import json

import requests
from bs4 import BeautifulSoup


def execute(inputs):
    url = inputs.get("url")
    if not url:
        return {"error": "Missing required input: url"}

    try:
        response = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if response.status_code != 200:
            return {"error": f"Failed to fetch URL: HTTP {response.status_code}"}
    except requests.exceptions.RequestException as e:
        return {"error": f"Failed to fetch URL: {e}"}

    soup = BeautifulSoup(response.text, "html.parser")

    title = soup.title.get_text(strip=True) if soup.title else None

    meta_desc_tag = soup.find("meta", attrs={"name": lambda v: v and v.lower() == "description"})
    meta_description = meta_desc_tag.get("content") if meta_desc_tag else None

    h1_tags = [tag.get_text(strip=True) for tag in soup.find_all("h1")]
    h2_tags = [tag.get_text(strip=True) for tag in soup.find_all("h2")]

    return {
        "title": title,
        "meta_description": meta_description,
        "h1_tags": h1_tags,
        "h2_tags": h2_tags,
    }


if __name__ == "__main__":
    input_str = sys.stdin.read()
    inputs = json.loads(input_str)
    result = execute(inputs)
    print(json.dumps(result))
