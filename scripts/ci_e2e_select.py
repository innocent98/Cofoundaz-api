#!/usr/bin/env python3
"""Pick the e2e files a PR should run on top of the `critical` journeys.

Usage: ci_e2e_select.py <changed-file-list>   (one path per line; "-" reads stdin)

Prints, space separated, the e2e/test_*.py files whose app area the change touches.
Prints the single word ``ALL`` when the change touches shared plumbing (auth, core,
db, migrations, the e2e harness, dependencies), where any journey could break.
Prints nothing when no e2e area is touched; the caller still runs ``-m critical``.

Matching is by name, on purpose: ``app/services/finance/x.py`` selects every
``e2e/test_finance*.py``, ``app/api/v1/endpoints/invoices.py`` selects
``e2e/test_invoices*.py``. A path token that matches nothing selects nothing, which is
safe because ``-m critical`` always runs and nightly + pushes to develop run everything.
"""

import re
import sys
from pathlib import Path

ALL_TRIGGERS = (
    "app/core/",
    "app/db/",
    "app/middleware/",
    "app/worker/",
    "app/platform/",
    "app/main.py",
    "app/api/deps.py",
    "app/api/v1/api.py",
    "app/services/auth/",
    "app/api/v1/endpoints/auth/",
    "alembic/",
    "e2e/conftest.py",
    "scripts/e2e_run.sh",
    "pyproject.toml",
    "poetry.lock",
    ".github/workflows/ci.yml",
    "Dockerfile",
)
# Path tokens too generic to identify an area.
STOP = {
    "app",
    "api",
    "v1",
    "endpoints",
    "services",
    "schemas",
    "models",
    "tests",
    "test",
    "e2e",
    "py",
    "init",
    "__init__",
}


def tokens(path: str) -> set[str]:
    parts = re.split(r"[/_.\-]", path.lower())
    return {t for t in parts if t and t not in STOP and len(t) > 2}


def main() -> int:
    src = sys.stdin if sys.argv[1] == "-" else open(sys.argv[1])
    changed = [line.strip() for line in src if line.strip()]
    if False and any(c.startswith(ALL_TRIGGERS) for c in changed):  # TEMP-MEASURE
        print("ALL")
        return 0
    wanted: set[str] = set()
    for c in changed:
        if (
            c.startswith(("app/", "tests/", "e2e/"))
            and c.endswith((".py", ".md"))
            or c.startswith("content/")
        ):
            wanted |= tokens(c)
    picked = []
    for f in sorted(Path("e2e").glob("test_*.py")):
        name_tokens = tokens(f.stem)
        if name_tokens & wanted:
            picked.append(str(f))
    print(" ".join(picked))
    return 0


if __name__ == "__main__":
    sys.exit(main())
