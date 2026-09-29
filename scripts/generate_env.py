"""Generate a local .env with strong random secrets from .env.example.

Never writes over an existing .env (use --force to override that). This is
for local/dev use — a real deploy should pull JWT_SECRET/POSTGRES_PASSWORD
from a secrets manager (AWS Secrets Manager, Vault, Docker/Kubernetes
secrets, ...), not a checked-out .env file.
"""

from __future__ import annotations

import argparse
import secrets
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = REPO_ROOT / ".env.example"
TARGET = REPO_ROOT / ".env"

GENERATED = {
    "POSTGRES_PASSWORD": lambda: secrets.token_urlsafe(24),
    "JWT_SECRET": lambda: secrets.token_urlsafe(48),
}


def render(example_text: str) -> str:
    lines = []
    for line in example_text.splitlines():
        if "=" not in line or line.strip().startswith("#"):
            lines.append(line)
            continue
        key, _, _ = line.partition("=")
        if key in GENERATED:
            lines.append(f"{key}={GENERATED[key]()}")
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="overwrite an existing .env")
    args = parser.parse_args()

    if TARGET.exists() and not args.force:
        print(f"{TARGET} already exists — pass --force to overwrite it")
        return 1

    TARGET.write_text(render(EXAMPLE.read_text()))
    print(f"wrote {TARGET} with freshly generated POSTGRES_PASSWORD and JWT_SECRET")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
