"""stdio transport: local MCP servers as long-lived child processes.

JSON-RPC over the child's stdin/stdout, one JSON object per line. Sessions are
kept alive and reused per connection id; a dead process is respawned on the
next call.

All subprocess IO runs on a dedicated worker loop, not the app's main loop:
Windows selector loops (which the app needs for psycopg) cannot host
subprocesses, so the worker uses a Proactor loop there.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from typing import Any


class StdioMcpError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _StdioSession:
    def __init__(self, key: str, process: asyncio.subprocess.Process) -> None:
        self.key = key
        self.process = process
        self.pending: dict[int, asyncio.Future] = {}
        self._next_id = 1

    def start_reader(self) -> None:
        asyncio.ensure_future(self._read())

    async def _send(self, message: dict[str, Any]) -> None:
        try:
            assert self.process.stdin is not None
            self.process.stdin.write((json.dumps(message) + "\n").encode())
            await self.process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, AssertionError) as exc:
            raise StdioMcpError(
                "mcp_stdio_error", "MCP stdio server closed its stdin"
            ) from exc

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self._send(
                {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
            )
            return await future
        finally:
            self.pending.pop(request_id, None)

    async def initialize(self) -> None:
        payload = await self.request(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "agent-orchestrator", "version": "1"},
            },
        )
        if "error" in payload:
            raise StdioMcpError("mcp_protocol_error", "MCP stdio initialize failed")
        await self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    async def _respond_method_not_found(self, message: dict[str, Any]) -> None:
        await self._send(
            {
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "error": {"code": -32601, "message": "Client does not accept requests"},
            }
        )

    async def _read(self) -> None:
        assert self.process.stdout is not None
        while True:
            line = await self.process.stdout.readline()
            if not line:
                break
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            if "method" in message and "id" in message:
                # Server-initiated request: decline it without touching our
                # pending ids (the server numbers its own requests).
                await self._respond_method_not_found(message)
                continue
            future = self.pending.get(message.get("id"))
            if future is not None and not future.done():
                future.set_result(message)
        for future in self.pending.values():
            if not future.done():
                future.set_exception(
                    StdioMcpError("mcp_stdio_error", "MCP stdio server exited")
                )
        self.pending.clear()

    async def close(self) -> None:
        if self.process.returncode is None:
            try:
                self.process.kill()
            except ProcessLookupError:
                pass


class StdioMcpSessionManager:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._sessions: dict[str, _StdioSession] = {}

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None or self._thread is None or not self._thread.is_alive():
            if sys.platform == "win32":
                self._loop = asyncio.ProactorEventLoop()
            else:
                self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._run_loop, daemon=True, name="mcp-stdio"
            )
            self._thread.start()
        return self._loop

    def _run_loop(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def call(
        self,
        config: dict[str, Any],
        calls: list[tuple[str, dict[str, Any]]],
        *,
        timeout: float = 30.0,
    ) -> list[dict[str, Any]]:
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(self._rpc(config, calls), loop)
        try:
            return await asyncio.wait_for(asyncio.wrap_future(future), timeout)
        except asyncio.TimeoutError as exc:
            future.cancel()
            raise StdioMcpError(
                "tool_network_error", "MCP stdio server timed out"
            ) from exc

    async def _rpc(
        self, config: dict[str, Any], calls: list[tuple[str, dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        key = str(config["connection_id"])
        session = self._sessions.get(key)
        if session is None or session.process.returncode is not None:
            self._sessions.pop(key, None)
            session = await self._spawn(key, config)
            try:
                await session.initialize()
            except StdioMcpError:
                self._sessions.pop(key, None)
                raise
        return [await session.request(method, params) for method, params in calls]

    async def _spawn(self, key: str, config: dict[str, Any]) -> _StdioSession:
        env = {**os.environ, **(config.get("env") or {})}
        try:
            process = await asyncio.create_subprocess_exec(
                str(config["command"]),
                *[str(arg) for arg in config.get("args", [])],
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=env,
            )
        except (OSError, ValueError, KeyError) as exc:
            raise StdioMcpError(
                "mcp_stdio_error", "Failed to start MCP stdio server"
            ) from exc
        session = _StdioSession(key, process)
        session.start_reader()
        self._sessions[key] = session
        return session

    def kill(self, connection_id: str) -> None:
        session = self._sessions.pop(str(connection_id), None)
        if session is None or self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(session.close(), self._loop)

    def shutdown(self) -> None:
        sessions = [self._sessions.pop(key) for key in list(self._sessions)]
        if self._loop is None:
            return
        for session in sessions:
            asyncio.run_coroutine_threadsafe(session.close(), self._loop)


stdio_manager = StdioMcpSessionManager()
