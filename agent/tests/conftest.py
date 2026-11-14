import sys
from pathlib import Path

agent_root = Path(__file__).parent.parent.resolve()

if str(agent_root) in sys.path:
    sys.path.remove(str(agent_root))
sys.path.insert(0, str(agent_root))

# Clear any previously cached 'core' package from scraper or root
if "core" in sys.modules and not hasattr(sys.modules["core"], "frames"):
    for key in list(sys.modules.keys()):
        if key == "core" or key.startswith("core."):
            del sys.modules[key]
