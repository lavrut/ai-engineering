"""Put desk_core, the MCP agent module and the contract generator on sys.path (no packaging), as mcp/python/tests does."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "agents" / "desk" / "python"))
sys.path.insert(0, str(ROOT / "agents" / "fixtures"))
sys.path.insert(0, str(ROOT / "mcp" / "python"))
