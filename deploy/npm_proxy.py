"""ForestLee의 Nginx Proxy Manager 프록시 대상을 blue/green 슬롯으로 전환한다."""

import json
import os
from pathlib import Path
import stat
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:81/api"
DOMAIN = "iinfo-dx-api.forestlee.me"
ACCESS_RULES = """# iinfo-dx-api public-domain restrictions
if ($request_uri ~ "^/internal(/|$)") { return 403; }
if ($request_uri ~ "^/api/v1/(admin|iidx/admin)(/|$)") { return 403; }
if ($request_uri ~ "^/api/v1/iidx/crawl/(jobs|schedules)(/|$)") { return 403; }
if ($request_uri ~ "^/api/v1/iidx/crawl/targets/") { return 403; }
"""


def request(method: str, path: str, token: str = "", payload: dict | None = None):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(BASE + path, data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=15) as response:
            return json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"NPM API {method} {path} failed: HTTP {exc.code}") from exc


def credentials() -> dict:
    path = Path(os.environ.get("NPM_CREDENTIALS", ""))
    if not path.is_file():
        raise RuntimeError("NPM_CREDENTIALS must point to a JSON credentials file")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise RuntimeError("NPM credentials file must have mode 600")
    data = json.loads(path.read_text())
    if not data.get("identity") or not data.get("secret"):
        raise RuntimeError("NPM credentials need identity and secret")
    return data


def wildcard_certificate(token: str) -> int:
    certificates = request("GET", "/nginx/certificates", token)
    for certificate in certificates:
        if "*.forestlee.me" in certificate.get("domain_names", []) and certificate.get("provider") == "letsencrypt":
            return certificate["id"]
    raise RuntimeError("No Let's Encrypt *.forestlee.me certificate found in NPM")


def set_target(slot: str) -> None:
    if slot not in {"blue", "green"}:
        raise RuntimeError("Slot must be blue or green")
    auth = request("POST", "/tokens", payload=credentials())
    token = auth["token"]
    hosts = request("GET", "/nginx/proxy-hosts", token)
    matches = [host for host in hosts if DOMAIN in host.get("domain_names", [])]
    if len(matches) > 1:
        raise RuntimeError(f"Multiple NPM proxy hosts match {DOMAIN}")
    target = f"iinfo-production-{slot}"
    if matches:
        host = matches[0]
        advanced = host.get("advanced_config") or ""
        if "# iinfo-dx-api public-domain restrictions" not in advanced:
            advanced += "\n" + ACCESS_RULES
        request("PUT", f"/nginx/proxy-hosts/{host['id']}", token, {
            "forward_scheme": "http",
            "forward_host": target,
            "forward_port": 8000,
            "advanced_config": advanced,
        })
    else:
        request("POST", "/nginx/proxy-hosts", token, {
            "domain_names": [DOMAIN],
            "forward_scheme": "http",
            "forward_host": target,
            "forward_port": 8000,
            "certificate_id": wildcard_certificate(token),
            "ssl_forced": True,
            "http2_support": True,
            "block_exploits": True,
            "allow_websocket_upgrade": True,
            "advanced_config": ACCESS_RULES,
        })
    print(f"NPM {DOMAIN} -> {target}")


if __name__ == "__main__":
    try:
        set_target(sys.argv[1])
    except (IndexError, KeyError, ValueError, RuntimeError) as exc:
        print(f"Proxy switch failed: {exc}", file=sys.stderr)
        sys.exit(1)
