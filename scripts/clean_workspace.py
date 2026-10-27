"""Dry-run/apply the workspace cleanup plan (AGENT_UPDATE_6.md task 5).

    py scripts/clean_workspace.py            # dry run only
    py scripts/clean_workspace.py --apply --all-regenerable

The full logic lives in `dubtool.app.cleanup` so `dubtool clean` in the CLI
and the tests use exactly the same code. Dry run is always safe: it deletes
nothing and only prints the plan.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dubtool.app.cleanup import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
