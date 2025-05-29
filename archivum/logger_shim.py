"""Drop in to avoid logging frustrations."""
import inspect
from datetime import datetime
from enum import IntEnum

try:
    import click
except ImportError:
    click = None


class LogLevel(IntEnum):
    """Enumeration for log levels with numeric severity."""

    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40
    CRITICAL = 50
    TODO = 100


class LoggerShim:
    """
    A lightweight, configurable logger that mimics the standard logging API.

    Supports color output via click, plain printing, timestamping, contextual
    function/module labels, and log level filtering.

    Parameters:
        level (LogLevel or str): Minimum log level to display.
        use_colors (bool): Whether to use colorized output (click required).
        show_context (bool): Whether to include module.function in output.
        show_time (bool): Whether to include HH:MM:SS timestamp.
        use_click (bool): Use click.echo/secho or plain print.
        name (str): Identifier to show as source of messages.
    """

    def __init__(
        self,
        level=LogLevel.INFO,
        use_colors=True,
        show_context=True,
        show_time=True,
        use_click=False,
        name=None,
    ):
        """Create LoggerShim object."""
        self.level = LogLevel[level.upper()] if isinstance(level, str) else level
        self.use_colors = use_colors
        self.show_context = show_context
        self.show_time = show_time
        self.use_click = use_click and click is not None
        self.name = name or "logger"

    def set_level(self, level):
        """
        Update the logger's minimum active log level.

        Args:
            level (str or LogLevel): New threshold (e.g. 'debug', LogLevel.WARNING).
        """
        self.level = LogLevel[level.upper()] if isinstance(level, str) else level

    def _should_log(self, level: LogLevel) -> bool:
        """Determine whether a message at the given level should be shown."""
        return level >= self.level

    @staticmethod
    def _get_caller_frame(skip_modules=(__name__,)):
        for frame_info in inspect.stack():
            mod = inspect.getmodule(frame_info.frame)
            modname = mod.__name__ if mod else ""
            if not any(modname.startswith(skip) for skip in skip_modules):
                return frame_info
        return inspect.stack()[2]  # fallback

    def _prefix(self, level_name: str) -> str:
        """
        Construct the aligned prefix with optional time, context, and level.

        Returns:
            str: e.g. "14:32:10 | archivum.cli.query_library | INFO     "
        """
        parts = []
        if self.show_time:
            parts.append(datetime.now().strftime("%H:%M:%S"))

        label = self.name
        if self.show_context:
            caller = self._get_caller_frame()
            func = caller.function
            mod = inspect.getmodule(caller.frame)
            modname = mod.__name__ if mod else '<unknown>'
            label = f"{modname}.{func}"

        parts.append(f"{label[-30:].strip():<30}")
        parts.append(f"{level_name.upper():<8}")
        return " | ".join(parts)

    def _log(self, level, msg, args, color=None, err=False, bold=False):
        """
        Unified internal log handler.

        Args:
            level (LogLevel): Severity level of the message.
            msg (str): Format string message.
            args (tuple): Arguments to interpolate into msg.
            color (str): Optional color for click.secho.
            err (bool): Write to stderr instead of stdout.
            bold (bool): Bold text if supported.
        """
        if not self._should_log(level):
            return
        text = msg % args if args else msg
        full = f"{self._prefix(level.name)} | {text}"

        if self.use_click:
            if color and self.use_colors:
                click.secho(full, fg=color, err=err, bold=bold)
            else:
                click.echo(full, err=err)
        else:
            print(full)

    def debug(self, msg, *args):
        """Log a DEBUG-level message."""
        self._log(LogLevel.DEBUG, msg, args, color="blue")

    def info(self, msg, *args):
        """Log an INFO-level message."""
        self._log(LogLevel.INFO, msg, args)

    def warning(self, msg, *args):
        """Log a WARNING-level message."""
        self._log(LogLevel.WARNING, msg, args, color="yellow")

    def error(self, msg, *args):
        """Log an ERROR-level message."""
        self._log(LogLevel.ERROR, msg, args, color="red", err=True)

    def critical(self, msg, *args):
        """Log a CRITICAL-level message."""
        self._log(LogLevel.CRITICAL, msg, args, color="bright_red", err=True, bold=True)

    def todo(self, msg, *args):
        """Log a TODO-level message."""
        self._log(LogLevel.TODO, msg, args, color="cyan")  # or magenta cyan, your choice
