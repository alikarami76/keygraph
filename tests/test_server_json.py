"""server.json (MCP registry metadata) stays in step with the package."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    return re.search(r'^version = "([^"]+)"', text, re.MULTILINE).group(1)


def test_server_json_versions_match_the_package():
    data = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    v = _pyproject_version()
    assert data["version"] == v
    (pkg,) = data["packages"]
    assert pkg["registryType"] == "pypi" and pkg["identifier"] == "keygraph"
    assert pkg["version"] == v
    assert {"type": "named", "name": "--from", "value": f"keygraph[mcp]=={v}"} in pkg["runtimeArguments"]
    assert pkg["packageArguments"][0] == {"type": "positional", "value": "mcp"}
    assert len(data["description"]) <= 100


def test_readme_carries_the_registry_ownership_marker():
    name = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))["name"]
    assert f"<!-- mcp-name: {name} -->" in (ROOT / "README.md").read_text(encoding="utf-8")
