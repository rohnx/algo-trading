"""Keep installed assets separate from user configuration and writable data."""

import os
from pathlib import Path
import tomllib

PACKAGE_DIR = Path(__file__).resolve().parent


def _checkout_root():
    root = PACKAGE_DIR.parent
    try:
        with (root / "pyproject.toml").open("rb") as stream:
            project = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    match project:
        case {"tool": {"uv": {"build-backend": {"module-name": "orb", "module-root": ""}}}}:
            return root
    return None


_CHECKOUT_ROOT = _checkout_root()


def config_dir():
    """Use the checkout's credentials, or the launch directory after installation."""
    override = os.environ.get("ORB_CONFIG_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return _CHECKOUT_ROOT or Path.cwd()


def data_dir():
    """Retain checkout caches; installed monitors write to the user's data directory."""
    override = os.environ.get("ORB_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if _CHECKOUT_ROOT is not None:
        return _CHECKOUT_ROOT / "orb" / "data"
    user_data = os.environ.get("XDG_DATA_HOME")
    base = Path(user_data).expanduser().resolve() if user_data else Path.home() / ".local" / "share"
    return base / "algo-trading" / "orb"
