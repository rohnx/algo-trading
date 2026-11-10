"""Optional access to the journal's standalone daily-risk policy.

The checkout discovers its sibling journal directory. An installed ORB can
use TRADING_RISK_HOME to locate the same directory without importing the journal
or requiring its process to be running.
"""

from functools import lru_cache
import hashlib
import importlib.util
import os
from pathlib import Path
import sys


@lru_cache(maxsize=8)
def _policy_class(path):
    name = "_orb_daily_risk_" + hashlib.sha256(str(path).encode()).hexdigest()[:16]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        return module.RiskPolicy
    except Exception:
        sys.modules.pop(name, None)
        raise


class DailyRisk:
    def __init__(self, *, root=None, state_path=None):
        selected = root if root is not None else (
            os.environ.get("TRADING_RISK_HOME") or Path(__file__).resolve().parents[1] / "journal")
        self.root = Path(selected).expanduser().resolve()
        self.state_path = state_path
        self._policy = None

    def record_first_orb(self, timestamp):
        """Return the shared decision, or None if this optional feature is unavailable."""
        try:
            if self._policy is None:
                factory = _policy_class(self.root / "daily_risk.py")
                self._policy = factory(root=self.root, state_path=self.state_path)
            return self._policy.record_first_orb(timestamp)
        except Exception:
            # A missing checkout, invalid CSV, or unavailable state cannot stop
            # the independent monitor or its existing Telegram delivery.
            return None


def launch_journal():
    """Launch the standalone journal from the checkout or configured source directory."""
    script = Path(__file__).resolve().parents[1] / "journal" / "trade.py"
    if not script.is_file():
        script = DailyRisk().root / "trade.py"
    if not script.is_file():
        raise SystemExit("Journal not found; set TRADING_RISK_HOME to its source directory")
    os.execv(sys.executable, [sys.executable, str(script), *sys.argv[1:]])


def launch_erv():
    """Launch the checkout's calculator with the same arguments and prompts."""
    script = Path(__file__).resolve().parents[1] / "erv" / "erv.py"
    if not script.is_file():
        raise SystemExit("ERV not found; run uv run erv from the project checkout")
    os.execv(sys.executable, [sys.executable, str(script), *sys.argv[1:]])
