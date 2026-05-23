"""Tests for qd_evolve.tools.install_mcp — install_mcp handler."""

import json
from unittest.mock import MagicMock, patch

import pytest


class TestInstallMcp:
    def test_install_creates_staging_file(self, tmp_path, monkeypatch):
        import qd_evolve.tools.install_mcp  # ensure submodule attr exists for patch()
        monkeypatch.setattr("qd_evolve.tools.staging._staging_base", lambda: tmp_path / ".qd_evolve" / "staging")
        from qd_evolve.tools.staging import ensure_staging_dirs
        ensure_staging_dirs()

        mock_registry = MagicMock()
        mock_bridge = MagicMock()
        mock_bridge.tool_names = ["tool1", "tool2"]
        mock_bridge_class = MagicMock(return_value=mock_bridge)

        with patch("qd_evolve.tools.install_mcp.get_registry", return_value=mock_registry):
            with patch("qd_evolve.tools.install_mcp.MCPToolBridge", mock_bridge_class):
                from qd_evolve.tools.install_mcp import _install_mcp
                result = _install_mcp(
                    name="myserver",
                    config={"command": "npx", "args": ["-y", "my-server"]},
                )
                assert "installed and hot-loaded" in result
                assert "tool1" in result
                staging_file = tmp_path / ".qd_evolve" / "staging" / "mcp" / "myserver.json"
                assert staging_file.exists()

    def test_pip_install_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr("qd_evolve.tools.staging._staging_base", lambda: tmp_path / ".qd_evolve" / "staging")

        mock_registry = MagicMock()
        with patch("qd_evolve.tools.install_mcp.get_registry", return_value=mock_registry):
            with patch("subprocess.check_call", side_effect=__import__("subprocess").CalledProcessError(1, "pip")):
                from qd_evolve.tools.install_mcp import _install_mcp
                result = _install_mcp(
                    name="myserver",
                    config={"command": "npx", "args": ["-y", "my-server"]},
                    pip_packages=["nonexistent"],
                )
                assert "Error" in result
                assert "package install failed" in result

    def test_bridge_connect_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr("qd_evolve.tools.staging._staging_base", lambda: tmp_path / ".qd_evolve" / "staging")
        from qd_evolve.tools.staging import ensure_staging_dirs
        ensure_staging_dirs()

        mock_registry = MagicMock()
        mock_bridge = MagicMock()
        mock_bridge.connect.side_effect = RuntimeError("connection failed")
        mock_bridge_class = MagicMock(return_value=mock_bridge)

        with patch("qd_evolve.tools.install_mcp.get_registry", return_value=mock_registry):
            with patch("qd_evolve.tools.install_mcp.MCPToolBridge", mock_bridge_class):
                from qd_evolve.tools.install_mcp import _install_mcp
                result = _install_mcp(
                    name="myserver",
                    config={"command": "npx", "args": ["-y", "my-server"]},
                )
                assert "Error" in result
                assert "connect failed" in result

    def test_pip_packages_none_skips_install(self, tmp_path, monkeypatch):
        monkeypatch.setattr("qd_evolve.tools.staging._staging_base", lambda: tmp_path / ".qd_evolve" / "staging")
        from qd_evolve.tools.staging import ensure_staging_dirs
        ensure_staging_dirs()

        mock_registry = MagicMock()
        mock_bridge = MagicMock()
        mock_bridge.tool_names = []
        mock_bridge_class = MagicMock(return_value=mock_bridge)

        with patch("qd_evolve.tools.install_mcp.get_registry", return_value=mock_registry):
            with patch("qd_evolve.tools.install_mcp.MCPToolBridge", mock_bridge_class):
                with patch("subprocess.check_call") as mock_call:
                    from qd_evolve.tools.install_mcp import _install_mcp
                    result = _install_mcp(
                        name="myserver",
                        config={"command": "npx", "args": ["-y", "my-server"]},
                    )
                    mock_call.assert_not_called()
                    assert "installed" in result


class TestSetStagedBridges:
    def test_set_staged_bridges(self):
        from qd_evolve.tools.install_mcp import set_staged_bridges, _staged_bridges
        mock_bridge = MagicMock()
        set_staged_bridges([mock_bridge])
        from qd_evolve.tools import install_mcp as mod
        assert mock_bridge in mod._staged_bridges
        mod._staged_bridges.clear()
