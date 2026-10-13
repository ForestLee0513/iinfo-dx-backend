"""배포 환경파일의 필수 Supabase 설정을 확인한다."""

from pathlib import Path
import os
import re
import sys
from urllib.parse import urlparse


def check(path: Path) -> str:
    values = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value.strip().strip('"')
    url = urlparse(values.get("SUPABASE_URL", ""))
    if url.scheme != "https" or not re.fullmatch(r"[a-z0-9]{20}\.supabase\.co", url.hostname or ""):
        raise ValueError("SUPABASE_URL must be a Supabase HTTPS project URL")
    expected_ref = os.environ.get("EXPECTED_SUPABASE_REF")
    if expected_ref and url.hostname != f"{expected_ref}.supabase.co":
        raise ValueError("SUPABASE_URL does not match the deployment environment")
    key = values.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not key or key.startswith("your-"):
        raise ValueError("SUPABASE_SERVICE_ROLE_KEY is missing")
    domain = values.get("DEPLOY_DOMAIN", "")
    if not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", domain):
        raise ValueError("DEPLOY_DOMAIN must be a valid hostname")
    return domain


if __name__ == "__main__":
    try:
        domain = check(Path(sys.argv[1]))
        if len(sys.argv) > 2 and sys.argv[2] == "--print-domain":
            print(domain)
    except (IndexError, OSError, ValueError) as exc:
        print(f"Invalid deployment environment: {exc}", file=sys.stderr)
        sys.exit(1)
