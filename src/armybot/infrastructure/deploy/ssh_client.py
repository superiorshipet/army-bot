"""Async SSH client with minimal safety guardrails.

Only truly catastrophic commands are blocked (disk wipes, fork bombs).
The AI agent has full control to install packages, configure services,
manage files, and do whatever is needed for deployment.
"""

from __future__ import annotations

import asyncio
import re

import asyncssh
import structlog

logger = structlog.get_logger()

# Only block commands that could destroy the entire server.
# Everything else (rm, chmod, chown, systemctl, etc.) is allowed.
_BLOCKED_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bdd\b.*\bof=/dev/[a-z]"),        # dd of=/dev/sda (disk wipe)
    re.compile(r"\bmkfs\b.*\b/dev/[a-z]"),          # mkfs /dev/sda (format disk)
    re.compile(r":\(\)\{.*\|.*&\s*\}\s*;"),          # fork bomb
    re.compile(r">\s*/dev/sd[a-z]"),                 # write to raw disk device
    re.compile(r"\binit\s+0\b"),                     # init 0
]


class CommandBlockedError(Exception):
    """Raised when a command is blocked by the safety filter."""


class SSHClient:
    """Async SSH wrapper. The AI has full server access except disk-destructive ops."""

    def __init__(self, command_timeout: int = 120) -> None:
        self.command_timeout = command_timeout
        self._conn: asyncssh.SSHClientConnection | None = None

    async def connect(
        self,
        host: str,
        username: str,
        key_path: str,
        port: int = 22,
    ) -> None:
        logger.info("ssh.connecting", host=host, username=username)
        self._conn = await asyncssh.connect(
            host,
            port=port,
            username=username,
            client_keys=[key_path],
            known_hosts=None,
        )
        logger.info("ssh.connected", host=host)

    async def run(self, command: str) -> tuple[str, str, int]:
        """Execute *command* and return ``(stdout, stderr, exit_code)``."""
        self._validate(command)
        if self._conn is None:
            raise RuntimeError("SSH not connected. Call connect() first.")

        logger.info("ssh.run", command=command[:120])
        try:
            result = await asyncio.wait_for(
                self._conn.run(command, check=False),
                timeout=self.command_timeout,
            )
        except asyncio.TimeoutError:
            return "", f"Command timed out after {self.command_timeout}s", 1

        stdout = (result.stdout or "")[-4000:]
        stderr = (result.stderr or "")[-2000:]
        exit_code = result.exit_status or 0
        logger.info("ssh.result", exit_code=exit_code, stdout_len=len(stdout))
        return stdout, stderr, exit_code

    async def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            logger.info("ssh.disconnected")

    @staticmethod
    def _validate(command: str) -> None:
        lowered = command.strip().lower()
        for pattern in _BLOCKED_PATTERNS:
            if pattern.search(lowered):
                raise CommandBlockedError(
                    f"Blocked catastrophic command: {command!r}"
                )
