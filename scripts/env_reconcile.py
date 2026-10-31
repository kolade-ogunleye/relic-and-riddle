"""Brings this checkout's .env up to date with .env.example, without showing a secret (src/env_sync.py).

    python scripts/env_reconcile.py                              # report only, by name
    python scripts/env_reconcile.py --apply                      # add the missing settings that change nothing
    python scripts/env_reconcile.py --apply --include-changes    # also those whose template value differs
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.env_sync import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
