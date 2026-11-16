#!/usr/bin/env python3
"""
100B Automated YouTube Shorts Pipeline — Local Dashboard
Entrypoint script binding strictly to 127.0.0.1 on port 8765.
"""

import sys
import uvicorn
from pathlib import Path

# Ensure project root is in sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def main():
    print("=" * 60)
    print("  🚀 Starting 100B Local Pipeline Dashboard")
    print("  Binding: http://127.0.0.1:8765 (Localhost only)")
    print("=" * 60)

    uvicorn.run(
        "dashboard.backend.app:app",
        host="127.0.0.1",
        port=8765,
        log_level="info",
        access_log=True,
    )


if __name__ == "__main__":
    main()
