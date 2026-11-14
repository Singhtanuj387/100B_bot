import sys
from pathlib import Path

# Add scraper root to sys.path so tests can run from anywhere
scraper_root = Path(__file__).parent.parent
if str(scraper_root) not in sys.path:
    sys.path.insert(0, str(scraper_root))

