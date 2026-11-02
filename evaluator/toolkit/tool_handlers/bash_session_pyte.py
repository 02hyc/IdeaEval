"""
Multi-window bash session manager using pexpect with PTY support.
Implements tmux-like functionality without actually using tmux.
"""

import asyncio
import json
import os
import re
import shlex
import signal
import termios
import threading
import tty
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import psutil
import pexpect
from pexpect import EOF, TIMEOUT
import pyte
import copy
import fcntl

DEBUG_FLAG = False  # set to True to print debug stream for bash

def sanitize_utf8_string(s: str) -> str:
    """
    Sanitize UTF-8 string by replacing invalid Unicode sequences (e.g., lone surrogates) with replacement characters.
    """
    if not isinstance(s, str):
        s = str(s)
    return s.encode('utf-8', errors='replace').decode('utf-8')


@dataclass
class WindowInfo:
    """Information about a bash window"""
    index: str
    name: str
    active: bool
    flags: str
    current_command: str
    current_path: str
    pane_pid: str


class IncrementalScreen(pyte.Screen):
    def __init__(self, columns: int, lines: int) -> None:
        super().__init__(columns, lines)


    def read_output(self) -> List[str]:
        """
        Read the output from the screen and return the output as a list of strings.
        """
        output = []
        display = self.display
        for y in self.dirty:
            output.append(display[y])
        self._clear_current_screen()
        return output


    def _clear_current_screen(self) -> None:
        """
        Clear the current screen
        """
        # NOTE:
        # This screen is used for incremental reads. After returning "dirty" lines,
        # we need to clear the buffer so future reads only include new output.
        #
        # Previous implementation moved the preserved cursor line to the top of
        # the screen via cursor_to_line(0). That corrupts cursor position and
        # causes subsequent prompts (PS1="$ ") to accumulate horizontally,
        # producing outputs like "$ $ $ $ ..." after no-output commands (e.g., mkdir).
        #
        # Fix: preserve the current cursor line *in place* and restore cursor
        # coordinates after clearing.
        self.dirty.clear()

        preserved_y = self.cursor.y
        preserved_x = self.cursor.x
        preserved_line = copy.deepcopy(self.buffer[preserved_y])

        blank = self.cursor.attrs  # Char(data=' ', ...)
        for y in range(self.lines):
            line = self.buffer[y]
            # Ensure we clear the full visible width, not just existing keys.
            for x in range(self.columns):
                line[x] = blank

        # Restore cursor line and cursor position.
        self.buffer[preserved_y] = preserved_line
        self.cursor.y = preserved_y
        self.cursor.x = preserved_x



class BashWindow:
    """A single bash session window using pexpect with PTY"""

    N_ROWS = 40     # only for display in bash process
    N_COLUMNS = 240     # columns for bash process must be the same as the frontend
    N_ROWS_SHOW = 500
    N_COLUMNS_SHOW = 240

    def __init__(self, name: str, working_dir: str):
        self.name = name
        self.working_dir = working_dir

        # Use system default shell from environment, fallback to /bin/bash
        self.shell_path = os.environ.get('SHELL', '/bin/bash')

        # Process management
        self.process: Optional[pexpect.spawn] = None
        self.pyte_screen = IncrementalScreen(self.N_COLUMNS_SHOW, self.N_ROWS_SHOW)
        self.pyte_stream = pyte.Stream(self.pyte_screen)

        self.screen_lock = asyncio.Lock()   # pyte screen is not thread-safe

        self.current_status = 'idle'
        self.last_cmd = None
        self.monitor_task = None


    def _build_process_env(self) -> Dict[str, str]:
        """Build a clean environment for the persistent shell process.
        Does not inherit parent process environment (virtualenv/conda etc).
        Instead, relies on shell loading user's config files (.bashrc, .zshrc etc).
        """
        # Create a minimal clean environment
        env: Dict[str, str] = {
            'HOME': os.path.expanduser('~'),
            'SHELL': self.shell_path,
            'PATH': '$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',  # Basic PATH, will be overridden by rc files
            'TERM': 'xterm-256color', 
            'LANG': 'en_US.UTF-8',
            'USER': os.environ.get('USER', os.environ.get('LOGNAME', 'user')),
            'LOGNAME': os.environ.get('LOGNAME', os.environ.get('USER', 'user')),
            'GIT_EDITOR': 'true',
            'PYTHONUNBUFFERED': '1',
            'PS1': '$ ',  # Simple prompt for bash
            'PS2': ' > ',  # Simple continuation prompt
            'PROMPT': '$ ',  # For zsh compatibility
        }

        env.update({
            'HISTFILE': '/dev/null',    # do not write a history file
            'HISTSIZE': '0',            # keep no in-memory history
            'HISTFILESIZE': '0',
        })
        
        return env

    def pid(self) -> int:
        if self.process is None:
            return -1
        return int(self.process.pid)

    async def start(self):
        """Start the bash process with PTY"""
        if self.process is not None:
            return  # Already started

        # Start shell with PTY using pexpect
        loop = asyncio.get_event_loop()
        
        def _spawn_process():
            # Spawn the shell process with PTY
            # Disable history for different shells
            if "zsh" in self.shell_path:
                cmd = self.shell_path + " -il"
                init_cmd = "unset HISTFILE; unset SAVEHIST; setopt NO_HIST_SAVE NO_HIST_IGNORE NO_SHARE_HISTORY"
            elif "fish" in self.shell_path:
                cmd = self.shell_path + " --private"
                init_cmd = ""
            else:
                cmd = self.shell_path + " -i"
                init_cmd = "unset HISTFILE; HISTSIZE=0; HISTFILESIZE=0; set +o history"
            
            proc = pexpect.spawn(
                cmd,
                cwd=self.working_dir,
                env=self._build_process_env(),
                encoding='utf-8',  # return strings from reads
                echo=False,  # Disable echo to avoid duplicate output
                timeout=None,  # No timeout on reads
                dimensions=(self.N_ROWS, self.N_COLUMNS),  # Set PTY dimensions
            )
            # proc.delaybeforesend = None
            proc.sendline(init_cmd)
            return proc
        
        self.process = await loop.run_in_executor(None, _spawn_process)

        self._set_nonblocking()

        await self._initialize_shell()

        self.reader_task = asyncio.create_task(self._read_output_loop())

        await asyncio.sleep(0.01)



    async def _initialize_shell(self):
        """Initialize shell with simple prompt after startup"""
        shell_name = os.path.basename(self.shell_path)

        # set PATH
        await self._write_command('export PATH="$HOME/.local/bin:$PATH"')

        await self._write_command('export PYTHONUNBUFFERED=1; export PYTHONIOENCODING=utf-8; export GIT_EDITOR=true')
        await self._write_command('export LANG=en_US.UTF-8; export LC_ALL=en_US.UTF-8')
        await self._write_command(f'export COLUMNS={self.N_COLUMNS}; export LINES={self.N_ROWS}')
        
        # Set simple prompt based on shell type
        if 'zsh' in shell_name:
            # For zsh, set PROMPT
            await self._write_command('export PROMPT="$ "')
            await self._write_command('export PS1="$ "')
        else:
            # For bash and other shells, set PS1
            await self._write_command('export PS1="$ "')
        
        # Set continuation prompt
        await self._write_command('export PS2=" > "')
        
        # Disable any prompt command that might override PS1
        await self._write_command('unset PROMPT_COMMAND')
        
        # For zsh, disable precmd hooks that might change prompt
        if 'zsh' in shell_name:
            await self._write_command('precmd() { : ; }')

        await self._wait_for_initialization()


    async def _wait_for_initialization(self):
        loop = asyncio.get_event_loop()
        try:
            await self._write_command('echo "Shell initialized for IdeaEval: $HOME"')

            home_dir = os.environ.get('HOME', 'unknown')

            await loop.run_in_executor(
                None, self.process.expect, f"Shell initialized for IdeaEval: {home_dir}", 5.0)
            # await loop.run_in_executor(
            #     None, self.process.expect, "$", 1.0)
        except TIMEOUT:
            print(f"Bash (PID:{self.pid()}) Error waiting for initialization: TIMEOUT")
            pass

    def _set_nonblocking(self):
        """Set PTY file descriptor to non-blocking mode and configure for minimal buffering."""
        fd = self.process.fileno()
        
        # Set non-blocking I/O
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        
        # # Configure PTY terminal attributes to reduce buffering
        # # This is critical for reading progress bars and other unbuffered output
        # try:
        #     # Get current terminal attributes
        #     attrs = termios.tcgetattr(fd)
            
        #     # Disable canonical (line-buffered) mode
        #     # In canonical mode, data is only available after a newline
        #     # Disabling it allows us to read data character-by-character
        #     attrs[3] = attrs[3] & ~termios.ICANON
            
        #     # Keep echo disabled (already set by pexpect, but make sure)
        #     attrs[3] = attrs[3] & ~termios.ECHO
            
        #     # Set VMIN=1 and VTIME=0 for immediate character availability
        #     # VMIN=1: read() returns as soon as at least 1 byte is available
        #     # VTIME=0: no timeout, return immediately with available data
        #     attrs[6][termios.VMIN] = 1
        #     attrs[6][termios.VTIME] = 0
            
        #     # Apply the new attributes immediately
        #     termios.tcsetattr(fd, termios.TCSANOW, attrs)
            
        # except Exception as e:
        #     # If termios configuration fails, log but continue
        #     # The process will still work, just might have buffering issues
        #     print(f"Warning: Could not configure PTY attributes for minimal buffering: {e}")

    async def _read_output_loop(self):
        """Background task to continuously read output from the process with carriage return handling (strings)."""
        # if self.process and self.process.isalive():
        #     self.process.read_nonblocking(size=65536, timeout=0)
        #     async with self.screen_lock:
        #         self.pyte_stream.feed('$ ')
        loop = asyncio.get_event_loop()
        while self.process and self.process.isalive():
            try:
                data = None
                data = await loop.run_in_executor(None, self.process.read_nonblocking, 65536, 0.1)   # size, timeout
            except TIMEOUT:
                pass
            except EOF:
                pass
            except Exception as e:
                if DEBUG_FLAG:
                    print(f"Bash(PID:{self.pid()}) Error reading output: {e}")
                # raise RuntimeError(f"Bash (PID:{self.pid()}) Error reading output: {e}")
                break
            if data:
                async with self.screen_lock:
                    if DEBUG_FLAG:
                        print(f'bash ({self.name}) -> {json.dumps(data, ensure_ascii=False)}')
                    self.pyte_stream.feed(data)

        try:
            if self.process and self.process.isalive():
                self.process.terminate(force=True)
        except Exception:
            pass

    async def _write_command(self, cmd: str):
        """Write a command to the bash process"""
        if self.process is None or not self.process.isalive():
            raise RuntimeError("Process not started")

        loop = asyncio.get_event_loop()
        
        def _send_command():
            data = cmd
            try:
                encoding = getattr(self.process, 'encoding', None)
            except Exception:
                encoding = 'utf-8'
            if encoding is None and isinstance(data, str):
                data = data.encode('utf-8')
            self.process.send(data)
            self.process.send('\n')
        
        await loop.run_in_executor(None, _send_command)

        # pexpect is spawned with echo=False, so the newline we send is not echoed
        # to the PTY and the shell's next prompt can appear on the same line as the
        # previous prompt (e.g., "$ $ $ ...") for commands that produce no output.
        #
        # To keep prompts on separate lines without echoing the full command text,
        # we manually advance the terminal emulator by one newline.
        async with self.screen_lock:
            self.pyte_stream.feed("\n")

    async def run_command(self, command: str, timeout: float = 30.0) -> Dict[str, object]:
        """Run a command and wait for completion or timeout.

        Returns:
            Dict with keys: 'status' ('completed'|'running'|'error'), 'output', 'session_id'
        """
        # Check if command is a background command (ends with &)
        command_stripped = command.strip()
        if command_stripped.endswith('&'):
            error_msg = (
                "Error: Background commands (ending with '&') are not allowed.\n"
                "Please remove the '&' to run the command in foreground.\n"
                "Command rejected."
            )
            return {
                'status': 'error',
                'output': error_msg,
                'session_id': self.name
            }
        
        # Validate command syntax using shlex
        try:
            shlex.split(command_stripped)
        except ValueError as e:
            error_msg = (
                f"Error: Invalid command syntax: {str(e)}. Command rejected."
            )
            return {
                'status': 'error',
                'output': error_msg,
                'session_id': self.name
            }
        
        # Update state (but don't set status to running yet - let monitor task do it)
        self.last_cmd = command

        # Send actual command (no marker needed)
        await self._write_command(command)

        # Start async monitoring task that will handle status changes
        result = await self._wait_for_completion(timeout)

        return result

    async def _wait_for_completion(self, timeout: float) -> Dict[str, object]:
        """Wait for command completion with timeout.
        
        Creates an async monitoring task that updates status throughout execution.
        The monitoring task continues running in background even if timeout occurs.
        """

        # Cancel any existing monitor task
        if self.monitor_task and not self.monitor_task.done():
            await asyncio.sleep(3.0)
            # Return the last output after 3 seconds
            output = await self._read_output()

            return {
                'status': "running" if self.current_status == "running" else "completed",
                'output': output,
                'session_id': self.name
            }

        # Start background monitoring task that will manage status
        self.monitor_task = asyncio.create_task(self._monitor_command_execution())

        # Use asyncio.wait instead of wait_for to avoid cancelling the task on timeout
        done, pending = await asyncio.wait(
            [self.monitor_task],
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED
        )

        await asyncio.sleep(0.1)   # wait for the result feed to the screen

        output = await self._read_output()

        if self.current_status == "idle" and not output.strip().endswith('$'):
            # command may not run at all, with unclosed quotes, etc.
            await self.interrupt()
            output += await self._read_output()

        return {
            'status': "running" if self.current_status == "running" else "completed",
            'output': output,
            'session_id': self.name
        }

    async def _monitor_command_execution(self):
        """Background async task that monitors command execution and updates status.
        
        This task:
        1. Sets status to 'running' when command starts executing
        2. Monitors execution progress
        3. Sets status to 'idle' when command completes
        """

        # Set status to running at the start
        self.current_status = 'running'

        # Give bash a moment to start executing
        await asyncio.sleep(0.1)

        while True:
            # Check if command completed (no child processes)
            has_children = self._has_child_processes()

            if has_children:
                # Command still running
                await asyncio.sleep(0.5)
            else:
                # Command completed
                self.current_status = 'idle'
                return

    async def _read_output(self) -> str:
        async with self.screen_lock:
            output = self.pyte_screen.read_output()
        output = [line.strip() for line in output]
        output = '\n'.join(output).strip()
        return sanitize_utf8_string(output)

    def _set_read_pointer(self, read_pointer: int):
        # TODO not supported yet
        pass

    def _get_read_pointer(self) -> int:
        # TODO not supported yet
        return self.pyte_screen.cursor.y

    async def _full_output(self) -> str:
        # TODO not supported yet
        """Extract command output from buffer, starting from cmd_start_line"""
        return await self._read_output()

    def _has_child_processes(self) -> bool:
        """Check if the bash process has any child processes (indicating command is running)"""

        if self.pid() == -1:
            return False

        try:
            # Use psutil to check for child processes
            parent = psutil.Process(self.pid())
            children = parent.children(recursive=True)
            return len(children) > 0

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

    async def interrupt(self):
        """Send interrupt signal (Ctrl-C) to the running process"""

        if self.process is None or not self.process.isalive():
            return

        loop = asyncio.get_event_loop()
        
        def _send_interrupt():
            try:
                # Send Ctrl-C using pexpect (this works with PTY)
                self.process.sendintr()
                
                # Also send SIGINT to child processes for redundancy
                if self.pid() != -1:
                    try:
                        parent = psutil.Process(self.pid())
                        children = parent.children(recursive=True)
                        
                        # Send SIGINT to children first
                        for child in children:
                            try:
                                child.send_signal(signal.SIGINT)
                            except (psutil.NoSuchProcess, psutil.AccessDenied):
                                pass
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
            except Exception:
                pass
        
        await loop.run_in_executor(None, _send_interrupt)


    async def close(self):
        """Close the bash process and clean up"""

        # Cancel background tasks with timeout protection
        if self.monitor_task and not self.monitor_task.done():
            self.monitor_task.cancel()
            try:
                await asyncio.wait_for(self.monitor_task, timeout=0.1)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        if self.reader_task and not self.reader_task.done():
            self.reader_task.cancel()
            try:
                await asyncio.wait_for(self.reader_task, timeout=0.1)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        # Terminate process
        if self.process:
            loop = asyncio.get_event_loop()
            
            def _terminate_process():
                try:
                    if self.process.isalive():
                        self.process.terminate(force=False)
                        # Give it a moment to terminate gracefully
                        try:
                            self.process.wait(timeout=0.5)
                        except TIMEOUT:
                            # Force kill if terminate didn't work
                            self.process.terminate(force=True)
                            try:
                                self.process.wait(timeout=0.5)
                            except TIMEOUT:
                                pass
                    self.process.close()
                except Exception:
                    pass
            
            await loop.run_in_executor(None, _terminate_process)

            self.process = None


class BashSessionManager:
    """Manages multiple bash session windows (tmux-like functionality without tmux)"""

    def __init__(self, task_id: str, working_dir: str):
        self.task_id = task_id
        self.working_dir = working_dir

        # Window management
        self.windows: Dict[str, BashWindow] = {}  # {window_name: BashWindow}
        self.active_window: Optional[str] = None

        # Thread safety
        self._lock = threading.RLock()


    async def status(self, window_name: str) -> str:
        """Get the status of a window.

        Args:
            window_name: Name of window to get status of

        Returns:
            String representation of the window status string
        """

        with self._lock:
            if window_name not in self.windows:
                return "error"

            window = self.windows[window_name]
            return window.current_status

    async def create_window(self, name: Optional[str] = None) -> str:
        """Create a new bash window.

        Args:
            name: Optional window name. Will be sanitized.

        Returns:
            The created window name
        """

        with self._lock:
            # Determine window name
            if name:
                window_name = self._sanitize_name(name)
                if not window_name:
                    raise ValueError(f"Invalid window name '{name}': sanitization resulted in empty string")
            else:
                if not self.windows:
                    window_name = "main"  # First window is always "main"
                else:
                    window_name = f"ideaeval-{uuid.uuid4().hex[:8]}"

            # Check if window already exists
            if window_name in self.windows:
                raise RuntimeError(f"Window with name '{window_name}' already exists")

            # Create window
            window = BashWindow(window_name, self.working_dir)

            # Start the window
            await window.start()

            # Register window
            self.windows[window_name] = window

            # Set as active if first window
            if self.active_window is None:
                self.active_window = window_name

            return window_name

    async def switch_window(self, window_name: str) -> bool:
        """Switch to a different window.

        Args:
            window_name: Name of window to switch to

        Returns:
            True if successful, False if window doesn't exist
        """

        with self._lock:
            if window_name not in self.windows:
                return False

            self.active_window = window_name
            return True

    def set_read_pointer(self, window_name: str, read_pointer: int):
        with self._lock:
            if window_name not in self.windows:
                raise ValueError(f"Window not found: {window_name}")

            self.windows[window_name]._set_read_pointer(read_pointer)

    def get_read_pointer(self, window_name: str) -> int:
        with self._lock:
            if window_name not in self.windows:
                raise ValueError(f"Window not found: {window_name}")

            return self.windows[window_name]._get_read_pointer()

    async def run_command(
        self,
        command: str,
        window_name: Optional[str] = None,
        timeout: float = 30.0
    ) -> Dict[str, object]:
        """Run a command in a window.

        Args:
            command: Command to run
            window_name: Window to run in (None = active or create new)
            timeout: Timeout in seconds

        Returns:
            Dict with 'status', 'output', 'session_id'
        """

        # Get or create target window
        target_name = await self._get_or_create_window(window_name)

        with self._lock:
            window = self.windows[target_name]

        # Run command (outside lock to allow concurrency)
        result = await window.run_command(command, timeout)
        return result

    async def _get_or_create_window(self, window_name: Optional[str]) -> str:
        """Get existing window or create new one"""
        if not window_name:
            window_name = 'main'
        
        if window_name in self.windows:
            window = self.windows[window_name]
            if window.process is None or not window.process.isalive():
                await window.close()
                del self.windows[window_name]
                if self.active_window == window_name:
                    self.active_window = None
                return await self.create_window(window_name)
            return window_name
        
        return await self.create_window(window_name)

    async def read_output(
        self,
        window_name: str,
        incremental: bool = True
    ) -> Tuple[str, str]:
        """Capture output from a window.

        Args:
            window_name: Window to capture from
            filter_regex: Optional regex to filter lines
            incremental: If True, return only new output

        Returns:
            Tuple of (output, status)
        """

        with self._lock:
            if window_name not in self.windows:
                return "", "unknown"

            window = self.windows[window_name]
            if incremental:
                output = await window._read_output()
            
            else:
                output = await window._full_output()

            status = "running" if window.current_status == "running" else "completed"
            return output, status

    async def list_windows(self) -> List[WindowInfo]:
        """List all windows.

        Returns:
            List of WindowInfo objects
        """
        # Ensure at least one window exists
        if not self.windows:
            _ = await self._get_or_create_window(None)

        with self._lock:
            result = []

            for idx, name in enumerate(self.windows.keys()):
                window = self.windows[name]

                is_active = (name == self.active_window)
                flags = "*" if is_active else ""

                # Get current command/status
                current_cmd = "bash"
                if window.current_status == 'running' and window.last_cmd:
                    current_cmd = window.last_cmd[:30]  # Truncate long commands

                result.append(WindowInfo(
                    index=str(idx),
                    name=name,
                    active=is_active,
                    flags=flags,
                    current_command=current_cmd,
                    current_path=window.working_dir,
                    pane_pid=str(window.pid()) if window.pid() != -1 else ""
                ))

            return result

    async def kill(self, window_name: str, end_session: bool = False) -> Tuple[bool, str]:
        """Kill a window or interrupt running command.

        Args:
            window_name: Window to kill/interrupt
            end_session: If True, kill entire window. If False, just interrupt.

        Returns:
            Tuple of (success, message)
        """

        with self._lock:
            if window_name not in self.windows:
                return False, f"Window not found: {window_name}"

            # Prevent ending "main" window
            if end_session and window_name == "main":
                return False, "Cannot end 'main' window. Please set end_session to False to interrupt instead."

            window = self.windows[window_name]

            if end_session:
                # Kill entire window (close outside lock)
                window_to_close = window

                # Remove from management
                del self.windows[window_name]

                # Update active window if needed
                if self.active_window == window_name:
                    self.active_window = None

        # Perform I/O operations outside the lock
        if end_session:
            await window_to_close.close()
            return True, "Window killed"
        else:
            await window.interrupt()
            return True, "Interrupt signal sent"

    async def terminate(self) -> bool:
        """Terminate all windows and clean up.

        Returns:
            True if successful
        """

        with self._lock:
            windows_to_close = list(self.windows.values())
            self.windows.clear()
            self.active_window = None

        # Close all windows (outside lock)
        for window in windows_to_close:
            try:
                await window.close()
            except Exception:
                pass

        return True

    def _sanitize_name(self, name: str) -> str:
        """Sanitize window name to be safe.

        Only allows alphanumeric characters, dash, and underscore.
        """
        # Replace whitespace with dash
        cleaned = re.sub(r"\s+", "-", name).strip("-")
        # Replace special chars with dash
        cleaned = re.sub(r"[^a-zA-Z0-9_-]", "-", cleaned)
        # Remove consecutive dashes
        cleaned = re.sub(r"-+", "-", cleaned)
        # Strip leading/trailing dashes
        cleaned = cleaned.strip("-")
        # Truncate if too long
        if len(cleaned) > 48:
            cleaned = cleaned[:48].rstrip("-")

        return cleaned or "bash"


# Global manager registry
_MANAGERS: Dict[str, BashSessionManager] = {}
_MANAGERS_LOCK = threading.RLock()


def get_bash_manager(task_id: str, working_dir: str) -> BashSessionManager:
    """Get or create BashSessionManager for a task.

    Args:
        task_id: Unique task identifier
        working_dir: Working directory for bash sessions

    Returns:
        BashSessionManager instance
    """

    with _MANAGERS_LOCK:
        mgr = _MANAGERS.get(task_id)
        if mgr is None:
            mgr = BashSessionManager(task_id=task_id, working_dir=working_dir)
            _MANAGERS[task_id] = mgr
        else:
            # Update working directory for new windows
            if working_dir and mgr.working_dir != working_dir:
                mgr.working_dir = working_dir
        return mgr


async def cleanup_bash_manager(task_id: str) -> bool:
    """Clean up and remove bash manager for a task.

    Args:
        task_id: Task identifier

    Returns:
        True if successful
    """

    with _MANAGERS_LOCK:
        mgr = _MANAGERS.get(task_id)
        if mgr is not None:
            _MANAGERS.pop(task_id, None)

    if mgr is not None:
        success = await mgr.terminate()
        return success
    return True


async def cleanup_all_bash_managers() -> None:
    """Clean up all bash managers (for global shutdown)"""

    with _MANAGERS_LOCK:
        managers = list(_MANAGERS.values())
        _MANAGERS.clear()

    # Clean up outside lock
    for mgr in managers:
        try:
            await mgr.terminate()
        except Exception:
            pass
