"""Read-only endpoint probe. Never uses credentials or records cookies."""

import json
import socket
import sys
from pathlib import Path
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

TARGETS = ["https://bbs.byr.cn/index", "https://bbs.byr.cn/"]


def probe(url):
    host = urlsplit(url).hostname
    result = {"requested": url, "dns": [], "status": "network_failed"}
    try:
        result["dns"] = sorted({item[4][0] for item in socket.getaddrinfo(host, None)})
        response = requests.get(url, timeout=(8, 15))
        soup = BeautifulSoup(response.content, "html.parser")
        result.update(
            status="reachable",
            http=response.status_code,
            final=response.url.split("?")[0],
            title=soup.title.get_text() if soup.title else "",
            redirects=[{"http": r.status_code, "url": r.url.split("?")[0]} for r in response.history],
            forms=[
                {"action": f.get("action", "").split("?")[0], "inputs": [i.get("name") for i in f.select("input")]}
                for f in soup.select("form")
            ],
        )
    except Exception as exc:
        result["error_type"] = type(exc).__name__
    return result


if __name__ == "__main__":
    data = [probe(url) for url in TARGETS]
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("network-probe.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2))
