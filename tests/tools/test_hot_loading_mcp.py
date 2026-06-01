"""Tests for qd_evolve.tools.hot_loading_mcp — hot_loading_mcp handler."""

from unittest.mock import MagicMock, patch


class TestHotLoadingMcp:
    def test_hot_load_connects_and_discovers_tools(self):
        mock_registry = MagicMock()
        mock_bridge = MagicMock()
        mock_bridge.tool_names = ["tool1", "tool2"]
        mock_bridge_class = MagicMock(return_value=mock_bridge)

        with patch("qd_evolve.tools.hot_loading_mcp.get_registry", return_value=mock_registry):
            with patch("qd_evolve.tools.hot_loading_mcp.MCPToolBridge", mock_bridge_class):
                from qd_evolve.tools.hot_loading_mcp import _hot_loading_mcp
                result = _hot_loading_mcp(
                    name="myserver",
                    config={"command": "npx", "args": ["-y", "my-server"]},
                )
                assert "hot-loaded" in result
                assert "tool1" in result

    def test_pip_install_failure(self):
        mock_registry = MagicMock()
        with patch("qd_evolve.tools.hot_loading_mcp.get_registry", return_value=mock_registry):
            with patch("subprocess.check_call", side_effect=__import__("subprocess").CalledProcessError(1, "pip")):
                from qd_evolve.tools.hot_loading_mcp import _hot_loading_mcp
                result = _hot_loading_mcp(
                    name="myserver",
                    config={"command": "npx", "args": ["-y", "my-server"]},
                    pip_packages=["nonexistent"],
                )
                assert "Error" in result
                assert "package install failed" in result

    def test_bridge_connect_failure(self):
        mock_registry = MagicMock()
        mock_bridge = MagicMock()
        mock_bridge.connect.side_effect = RuntimeError("connection failed")
        mock_bridge_class = MagicMock(return_value=mock_bridge)

        with patch("qd_evolve.tools.hot_loading_mcp.get_registry", return_value=mock_registry):
            with patch("qd_evolve.tools.hot_loading_mcp.MCPToolBridge", mock_bridge_class):
                from qd_evolve.tools.hot_loading_mcp import _hot_loading_mcp
                result = _hot_loading_mcp(
                    name="myserver",
                    config={"command": "npx", "args": ["-y", "my-server"]},
                )
                assert "Error" in result
                assert "connect failed" in result

    def test_pip_packages_none_skips_install(self):
        mock_registry = MagicMock()
        mock_bridge = MagicMock()
        mock_bridge.tool_names = []
        mock_bridge_class = MagicMock(return_value=mock_bridge)

        with patch("qd_evolve.tools.hot_loading_mcp.get_registry", return_value=mock_registry):
            with patch("qd_evolve.tools.hot_loading_mcp.MCPToolBridge", mock_bridge_class):
                with patch("subprocess.check_call") as mock_call:
                    from qd_evolve.tools.hot_loading_mcp import _hot_loading_mcp
                    result = _hot_loading_mcp(
                        name="myserver",
                        config={"command": "npx", "args": ["-y", "my-server"]},
                    )
                    mock_call.assert_not_called()
                    assert "hot-loaded" in result

    def test_pip_packages_empty_list_skips_install(self):
        """Empty pip_packages list should be treated same as None."""
        mock_registry = MagicMock()
        mock_bridge = MagicMock()
        mock_bridge.tool_names = ["tool1"]
        mock_bridge_class = MagicMock(return_value=mock_bridge)

        with patch("qd_evolve.tools.hot_loading_mcp.get_registry", return_value=mock_registry):
            with patch("qd_evolve.tools.hot_loading_mcp.MCPToolBridge", mock_bridge_class):
                with patch("subprocess.check_call") as mock_call:
                    from qd_evolve.tools.hot_loading_mcp import _hot_loading_mcp
                    result = _hot_loading_mcp(
                        name="myserver",
                        config={"command": "npx", "args": ["-y", "my-server"]},
                        pip_packages=[],
                    )
                    mock_call.assert_not_called()
                    assert "hot-loaded" in result

    def test_empty_tool_list(self):
        """Bridge connects but discovers zero tools."""
        mock_registry = MagicMock()
        mock_bridge = MagicMock()
        mock_bridge.tool_names = []
        mock_bridge_class = MagicMock(return_value=mock_bridge)

        with patch("qd_evolve.tools.hot_loading_mcp.get_registry", return_value=mock_registry):
            with patch("qd_evolve.tools.hot_loading_mcp.MCPToolBridge", mock_bridge_class):
                from qd_evolve.tools.hot_loading_mcp import _hot_loading_mcp
                result = _hot_loading_mcp(
                    name="myserver",
                    config={"command": "npx", "args": ["-y", "my-server"]},
                )
                assert "hot-loaded" in result

    def test_uses_uv_when_available(self):
        """When uv is found on PATH, use uv pip install instead of pip."""
        mock_registry = MagicMock()
        mock_bridge = MagicMock()
        mock_bridge.tool_names = ["tool1"]
        mock_bridge_class = MagicMock(return_value=mock_bridge)

        with patch("qd_evolve.tools.hot_loading_mcp.get_registry", return_value=mock_registry):
            with patch("qd_evolve.tools.hot_loading_mcp.MCPToolBridge", mock_bridge_class):
                with patch("shutil.which", return_value="/usr/bin/uv") as mock_which:
                    with patch("subprocess.check_call") as mock_call:
                        from qd_evolve.tools.hot_loading_mcp import _hot_loading_mcp
                        _hot_loading_mcp(
                            name="myserver",
                            config={"command": "npx", "args": ["-y", "my-server"]},
                            pip_packages=["requests"],
                        )
                        mock_which.assert_called_once_with("uv")
                        # Should use uv, not pip
                        mock_call.assert_called_once()
                        call_args = mock_call.call_args[0][0]
                        assert call_args[0] == "/usr/bin/uv"

    def test_falls_back_to_pip_when_no_uv(self):
        """When uv is not found, fall back to pip."""
        mock_registry = MagicMock()
        mock_bridge = MagicMock()
        mock_bridge.tool_names = ["tool1"]
        mock_bridge_class = MagicMock(return_value=mock_bridge)

        with patch("qd_evolve.tools.hot_loading_mcp.get_registry", return_value=mock_registry):
            with patch("qd_evolve.tools.hot_loading_mcp.MCPToolBridge", mock_bridge_class):
                with patch("shutil.which", return_value=None) as mock_which:
                    with patch("subprocess.check_call") as mock_call:
                        with patch("sys.executable", "python"):
                            from qd_evolve.tools.hot_loading_mcp import _hot_loading_mcp
                            _hot_loading_mcp(
                                name="myserver",
                                config={"command": "npx", "args": ["-y", "my-server"]},
                                pip_packages=["requests"],
                            )
                            mock_which.assert_called_once_with("uv")
                            mock_call.assert_called_once()
                            call_args = mock_call.call_args[0][0]
                            assert call_args[0] == "python"
