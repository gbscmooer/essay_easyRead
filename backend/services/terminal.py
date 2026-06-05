"""
终端服务

通过 WebSocket 提供真实的 PTY 终端。
使用 Python 内置的 pty 模块（仅支持 Unix/macOS）。
"""

import asyncio
import os
import pty
import select
import signal
import struct
import fcntl
import termios
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class TerminalSession:
    """一个 PTY 终端会话"""

    def __init__(self, cwd: str = None):
        self.cwd = cwd or os.path.expanduser("~")
        self.pid: Optional[int] = None
        self.master_fd: Optional[int] = None
        self._running = False

    async def spawn(self):
        """启动 shell 进程"""
        master_fd, slave_fd = pty.openpty()

        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["COLUMNS"] = "120"
        env["LINES"] = "30"

        shell = os.environ.get("SHELL", "/bin/zsh")

        def _preexec():
            os.setsid()
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)

        proc = await asyncio.create_subprocess_exec(
            shell, "--login",
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            env=env,
            cwd=self.cwd,
            preexec_fn=_preexec,
        )

        # 父进程关闭 slave fd
        os.close(slave_fd)
        self.pid = proc.pid
        self.master_fd = master_fd
        self._running = True

        # 设置非阻塞
        import fcntl as fcntl_mod
        flags = fcntl_mod.fcntl(master_fd, fcntl_mod.F_GETFL)
        fcntl_mod.fcntl(master_fd, fcntl_mod.F_SETFL, flags | os.O_NONBLOCK)

    def resize(self, cols: int, rows: int):
        """调整终端大小"""
        if self.master_fd is not None:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)

    def write(self, data: bytes):
        """向终端写入数据"""
        if self.master_fd is not None and self._running:
            try:
                os.write(self.master_fd, data)
            except OSError:
                self._running = False

    def read(self) -> bytes:
        """从终端读取数据（非阻塞）"""
        if self.master_fd is None or not self._running:
            return b""

        try:
            ready, _, _ = select.select([self.master_fd], [], [], 0.05)
            if ready:
                return os.read(self.master_fd, 4096)
        except OSError:
            self._running = False
        return b""

    def close(self):
        """关闭终端"""
        self._running = False
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None

        if self.pid is not None:
            try:
                os.kill(self.pid, signal.SIGTERM)
                os.waitpid(self.pid, os.WNOHANG)
            except (OSError, ChildProcessError):
                pass
            self.pid = None

    @property
    def is_alive(self) -> bool:
        if self.pid is None:
            return False
        try:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            return pid == 0
        except ChildProcessError:
            return False


async def handle_terminal_websocket(websocket, cwd: str = None):
    """处理 WebSocket 终端连接"""
    session = TerminalSession(cwd)
    await session.spawn()

    read_task = None

    async def read_from_pty():
        """从 PTY 读取数据并发送到 WebSocket"""
        loop = asyncio.get_event_loop()
        while session.is_alive:
            try:
                data = await loop.run_in_executor(None, session.read)
                if data:
                    await websocket.send_bytes(data)
                else:
                    await asyncio.sleep(0.01)
            except Exception:
                break

    try:
        read_task = asyncio.create_task(read_from_pty())

        async for message in websocket:
            if message.type == "websocket.receive":
                if message.text:
                    if message.text == "ping":
                        await websocket.send_text("pong")
                        continue
                    if message.text.startswith("\x1b[8;"):
                        try:
                            parts = message.text[4:].rstrip("t").split(";")
                            rows, cols = int(parts[0]), int(parts[1])
                            session.resize(cols, rows)
                        except (ValueError, IndexError):
                            pass
                    else:
                        session.write(message.text.encode("utf-8"))
                elif message.bytes:
                    session.write(message.bytes)
    except Exception as e:
        logger.debug(f"Terminal WebSocket closed: {e}")
    finally:
        if read_task:
            read_task.cancel()
            try:
                await read_task
            except asyncio.CancelledError:
                pass
        session.close()
