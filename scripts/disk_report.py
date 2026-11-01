"""Print the read-only disk report (AGU10 task 7.1); also `dubtool doctor --disk`.

Read-only: it deletes nothing and never reads the content of .env or
settings files. The report is printed and written to <build root>/logs/.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dubtool.disk_report import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
