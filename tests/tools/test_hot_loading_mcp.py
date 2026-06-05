"""Tests for qd_evolve.tools.hot_loading_mcp — hot_loading_mcp handler."""

import json
from unittest.mock import MagicMock, patch


class TestHotLoadingMcp:
    def test_hot_load_connects_and_discovers_tools(self):
        mock_registry = MagicMock()
        mock_bridge = MagicMock()
        mock_bridge.tool_names = ["tool1", "tool2"]
        mock_bridge_class = MagicMock(return_value=mock_bridge)

        config_json = json.dumps({"command": "npx", "args": ["-y", "my-server"]})
        with patch("qd_evolve.tools.hot_loading_mcp.get_registry", return_value=mock_registry):
            with patch("qd_evolve.tools.hot_loading_mcp.MCPToolBridge", mock_bridge_class):
                with patch("pathlib.Path.read_text", return_value=config_json):
                    from qd_evolve.tools.hot_loading_mcp import _hot_loading_mcp
                    result = _hot_loading_mcp(
                        name="myserver",
                        config_path="tools/mcp/myserver.json",
                    )
                    assert "hot-loaded" in result
                    assert "tool1" in result

    def test_bridge_connect_failure(self):
        mock_registry = MagicMock()
        mock_bridge = MagicMock()
        mock_bridge.connect.side_effect = RuntimeError("connection failed")
        mock_bridge_class = MagicMock(return_value=mock_bridge)

        config_json = json.dumps({"command": "npx", "args": ["-y", "my-server"]})
        with patch("qd_evolve.tools.hot_loading_mcp.get_registry", return_value=mock_registry):
            with patch("qd_evolve.tools.hot_loading_mcp.MCPToolBridge", mock_bridge_class):
                with patch("pathlib.Path.read_text", return_value=config_json):
                    from qd_evolve.tools.hot_loading_mcp import _hot_loading_mcp
                    result = _hot_loading_mcp(
                        name="myserver",
                        config_path="tools/mcp/myserver.json",
                    )
                    assert "Error" in result
                    assert "connect failed" in result

    def test_empty_tool_list(self):
        """Bridge connects but discovers zero tools."""
        mock_registry = MagicMock()
        mock_bridge = MagicMock()
        mock_bridge.tool_names = []
        mock_bridge_class = MagicMock(return_value=mock_bridge)

        config_json = json.dumps({"command": "npx", "args": ["-y", "my-server"]})
        with patch("qd_evolve.tools.hot_loading_mcp.get_registry", return_value=mock_registry):
            with patch("qd_evolve.tools.hot_loading_mcp.MCPToolBridge", mock_bridge_class):
                with patch("pathlib.Path.read_text", return_value=config_json):
                    from qd_evolve.tools.hot_loading_mcp import _hot_loading_mcp
                    result = _hot_loading_mcp(
                        name="myserver",
                        config_path="tools/mcp/myserver.json",
                    )
                    assert "hot-loaded" in result

    def test_config_file_not_found(self):
        """Returns error when config_path points to a missing file."""
        mock_registry = MagicMock()
        with patch("qd_evolve.tools.hot_loading_mcp.get_registry", return_value=mock_registry):
            with patch("pathlib.Path.read_text", side_effect=FileNotFoundError):
                from qd_evolve.tools.hot_loading_mcp import _hot_loading_mcp
                result = _hot_loading_mcp(
                    name="myserver",
                    config_path="nonexistent.json",
                )
                assert "Error" in result
                assert "not found" in result

    def test_config_file_invalid_json(self):
        """Returns error when config file contains invalid JSON."""
        mock_registry = MagicMock()
        with patch("qd_evolve.tools.hot_loading_mcp.get_registry", return_value=mock_registry):
            with patch("pathlib.Path.read_text", return_value="{not valid"):
                from qd_evolve.tools.hot_loading_mcp import _hot_loading_mcp
                result = _hot_loading_mcp(
                    name="myserver",
                    config_path="tools/mcp/bad.json",
                )
                assert "Error" in result
                assert "invalid JSON" in result

    def test_mcp_servers_wrapper_format(self):
        """Config file uses the mcpServers wrapper format."""
        mock_registry = MagicMock()
        mock_bridge = MagicMock()
        mock_bridge.tool_names = ["tool_a"]
        mock_bridge_class = MagicMock(return_value=mock_bridge)

        config_json = json.dumps({
            "mcpServers": {
                "myserver": {"command": "npx", "args": ["-y", "my-server"]}
            }
        })
        with patch("qd_evolve.tools.hot_loading_mcp.get_registry", return_value=mock_registry):
            with patch("qd_evolve.tools.hot_loading_mcp.MCPToolBridge", mock_bridge_class):
                with patch("pathlib.Path.read_text", return_value=config_json):
                    from qd_evolve.tools.hot_loading_mcp import _hot_loading_mcp
                    result = _hot_loading_mcp(
                        name="myserver",
                        config_path="tools/mcp/myserver.json",
                    )
                    assert "hot-loaded" in result
                    assert "tool_a" in result

    def test_name_not_in_config(self):
        """Returns error when name doesn't match any key in the config."""
        mock_registry = MagicMock()
        config_json = json.dumps({
            "mcpServers": {
                "other-server": {"command": "npx", "args": ["-y", "other"]}
            }
        })
        with patch("qd_evolve.tools.hot_loading_mcp.get_registry", return_value=mock_registry):
            with patch("pathlib.Path.read_text", return_value=config_json):
                from qd_evolve.tools.hot_loading_mcp import _hot_loading_mcp
                result = _hot_loading_mcp(
                    name="myserver",
                    config_path="tools/mcp/other.json",
                )
                assert "Error" in result
                assert "not found in config" in result
