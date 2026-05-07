from __future__ import annotations

from pathlib import Path
import sqlite3
import sys

import config


def main() -> int:
    db = Path(getattr(config, "STATE_DB_PATH", "state/bot_state.sqlite"))
    if db.exists():
        with sqlite3.connect(db) as conn:
            conn.execute("SELECT 1").fetchone()
    log_dir = Path(getattr(config, "LOG_DIR", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

