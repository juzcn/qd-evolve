"""AgentLauncher — start/stop Agent subprocesses."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from qd_evolve.agents.loader import discover_agents
from qd_evolve.agents.registry import Topology
from qd_evolve.core.logger import logger


class AgentLauncher:
    """Launch and manage Agent subprocesses."""

    def __init__(self, topology: Topology | None = None) -> None:
        self.topology = topology or Topology()
        self._processes: dict[str, subprocess.Popen] = {}

    def _is_local(self, url: str) -> bool:
        """Check if a URL points to localhost."""
        host = url.split("//", 1)[-1].split("/")[0].split(":")[0]
        return host in ("localhost", "127.0.0.1", "0.0.0.0", "::1")

    def launch(self, name: str) -> subprocess.Popen | None:
        """Launch a single Agent subprocess."""
        url = self.topology.agents.get(name, {}).get("url", "")
        if url and not self._is_local(url):
            logger.info("Launcher: skipping remote agent '%s' at %s", name, url)
            return None

        proc = subprocess.Popen(
            [sys.executable, "-m", "qd_evolve", "--agent", name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._processes[name] = proc
        logger.info("Launcher: started agent '%s' (pid=%d)", name, proc.pid)
        return proc

    def launch_all(self) -> list[subprocess.Popen]:
        """Launch all local Agents from topology."""
        processes = []
        for name in self.topology.agents:
            proc = self.launch(name)
            if proc:
                processes.append(proc)
        return processes

    def stop(self, name: str) -> None:
        """Stop a specific Agent subprocess."""
        proc = self._processes.pop(name, None)
        if proc and proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=10)
            logger.info("Launcher: stopped agent '%s'", name)

    def stop_all(self) -> None:
        """Stop all running Agent subprocesses."""
        for name in list(self._processes):
            self.stop(name)

    def status(self) -> dict[str, str]:
        """Return status of all launched agents."""
        result = {}
        for name, proc in self._processes.items():
            if proc.poll() is None:
                result[name] = "running"
            else:
                result[name] = f"exited({proc.returncode})"
        return result