"""
WebSocket terminal component for interactive shell sessions.

This module provides a WebSocket endpoint for xterm.js integration,
supporting PTY-based interactive shell sessions with terminal resize.
Each WebSocket connection creates a new PTY; disconnection destroys it.
"""

import asyncio
import os
import struct
import fcntl
import termios
import logging
import json
from typing import Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from .user_manager import start_interactive_shell

logger = logging.getLogger(__name__)

router = APIRouter()


class TerminalSession:
    """Represents an active terminal session"""

    def __init__(self, master_fd: int, pid: int):
        self.master_fd = master_fd
        self.pid = pid
        self.websocket: Optional[WebSocket] = None
        self._read_task: Optional[asyncio.Task] = None
        self._closed = False

    async def start_reader(self, websocket: WebSocket):
        """Start reading from PTY and sending to WebSocket"""
        self.websocket = websocket
        loop = asyncio.get_event_loop()

        def read_pty():
            """Blocking read from PTY"""
            try:
                return os.read(self.master_fd, 4096)
            except OSError:
                return b""

        while not self._closed:
            try:
                # Read from PTY in a thread to avoid blocking
                data = await loop.run_in_executor(None, read_pty)
                if not data:
                    logger.info("PTY closed")
                    break
                # Send to WebSocket as text (xterm expects text)
                await websocket.send_text(data.decode("utf-8", errors="replace"))
            except WebSocketDisconnect:
                logger.info("WebSocket disconnected")
                break
            except Exception as e:
                logger.error(f"Error reading from PTY: {e}")
                break

    async def write(self, data: str):
        """Write data to PTY"""
        try:
            os.write(self.master_fd, data.encode("utf-8"))
        except OSError as e:
            logger.error(f"Error writing to PTY: {e}")

    def resize(self, cols: int, rows: int):
        """Resize the terminal"""
        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
            logger.debug(f"Resized terminal to {cols}x{rows}")
        except Exception as e:
            logger.error(f"Error resizing terminal: {e}")

    def close(self):
        """Close the terminal session"""
        if self._closed:
            return
        self._closed = True

        try:
            os.close(self.master_fd)
        except OSError:
            pass

        # Try to terminate the child process
        try:
            os.kill(self.pid, 9)  # SIGKILL
            os.waitpid(self.pid, os.WNOHANG)
        except OSError:
            pass

        logger.info("Closed terminal session")


@router.websocket("/ws")
async def websocket_terminal(
    websocket: WebSocket,
    cols: int = Query(80),
    rows: int = Query(24),
):
    """
    WebSocket endpoint for interactive terminal.

    Query parameters:
    - cols: Terminal columns (default 80)
    - rows: Terminal rows (default 24)

    Messages from client:
    - Text: Input data to send to PTY
    - JSON: {"type": "resize", "cols": <int>, "rows": <int>}
    """
    await websocket.accept()

    terminal: Optional[TerminalSession] = None

    try:
        # Start interactive shell
        master_fd, pid = await start_interactive_shell(cols=cols, rows=rows)

        terminal = TerminalSession(master_fd, pid)

        logger.info("Terminal session started")

        # Start PTY reader in background
        read_task = asyncio.create_task(terminal.start_reader(websocket))

        # Handle incoming WebSocket messages
        while True:
            try:
                message = await websocket.receive()

                if message["type"] == "websocket.disconnect":
                    break

                if "text" in message:
                    text = message["text"]
                    # Check if it's a control message
                    if text.startswith("{"):
                        try:
                            data = json.loads(text)
                            if data.get("type") == "resize":
                                terminal.resize(
                                    data.get("cols", 80), data.get("rows", 24)
                                )
                                continue
                        except json.JSONDecodeError:
                            pass
                    # Regular input data
                    await terminal.write(text)

                elif "bytes" in message:
                    # Binary data (also valid input)
                    await terminal.write(
                        message["bytes"].decode("utf-8", errors="replace")
                    )

            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.error(f"Error handling WebSocket message: {e}")
                break

    except Exception as e:
        logger.error(f"Error in terminal WebSocket: {e}")
        try:
            await websocket.close(code=1011, reason=str(e))
        except Exception:
            pass

    finally:
        # Cleanup
        if terminal:
            terminal.close()
