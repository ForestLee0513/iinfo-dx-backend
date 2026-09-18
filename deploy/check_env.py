"""배포 환경파일의 필수 Supabase 설정을 확인한다."""

from pathlib import Path
import sys
from urllib.parse import urlparse


def check(path: Path, expected_ref: str) -> None:
    values = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value.strip().strip('"')
    actual_ref = urlparse(values.get("SUPABASE_URL", "")).hostname
    if actual_ref != f"{expected_ref}.supabase.co":
        raise ValueError(f"SUPABASE_URL must use project {expected_ref}")
    key = values.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not key or key.startswith("your-"):
        raise ValueError("SUPABASE_SERVICE_ROLE_KEY is missing")


if __name__ == "__main__":
    try:
        check(Path(sys.argv[1]), sys.argv[2])
    except (IndexError, OSError, ValueError) as exc:
        print(f"Invalid deployment environment: {exc}", file=sys.stderr)
        sys.exit(1)
