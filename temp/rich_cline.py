from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.layout.margins import ScrollbarMargin
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Frame

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box

import os
import math
import tempfile

console = Console()


def rich_display_dict(d: dict, title="Edit Mode") -> str:
    keys = list(d.keys())
    half = math.ceil(len(keys) / 2)
    left, right = keys[:half], keys[half:]

    table = Table(box=box.SIMPLE_HEAVY, show_lines=True, expand=True)
    table.add_column("Key", style="cyan", ratio=1)
    table.add_column("Value", style="white", ratio=2)
    table.add_column("Key", style="cyan", ratio=1)
    table.add_column("Value", style="white", ratio=2)

    for i in range(half):
        k1 = left[i]
        v1 = d[k1]
        k2 = right[i] if i < len(right) else ""
        v2 = d[k2] if k2 else ""
        table.add_row(str(k1), str(v1), str(k2), str(v2))

    with tempfile.NamedTemporaryFile(delete=False, mode="w", suffix=".txt") as tmp:
        console.file = tmp
        console.print(Panel(table, title=title, border_style="green"))
        tmp_path = tmp.name

    with open(tmp_path) as f:
        result = f.read()
    os.unlink(tmp_path)
    return result


def interactive_field_editor(d: dict) -> dict:
    current = dict(d)
    fields = list(current.keys())
    index = [0]  # mutable wrapper to allow update inside closure

    buf = Buffer()

    def update_display():
        buf.text = current[fields[index[0]]]

    def apply_change():
        current[fields[index[0]]] = buf.text

    # on ← / → arrows
    kb = KeyBindings()

    @kb.add("left")
    def _(event):
        apply_change()
        index[0] = (index[0] - 1) % len(fields)
        update_display()

    @kb.add("right")
    def _(event):
        apply_change()
        index[0] = (index[0] + 1) % len(fields)
        update_display()

    @kb.add("enter")
    def _(event):
        apply_change()
        update_display()
        app.invalidate()

    @kb.add("c-c")
    @kb.add("escape")
    def _(event):
        apply_change()
        event.app.exit(current)

    # layout
    def get_body_text():
        return rich_display_dict(current)

    buf_display = Buffer(read_only=True, document=Document(get_body_text()))

    body = Window(content=BufferControl(buffer=buf_display),
                # always_hide_cursor=True,
                  # height=console.size.height - 2,
                  wrap_lines=True)

    input_window = Window(BufferControl(buffer=buf), height=1, wrap_lines=False)
    layout = Layout(HSplit([body, Frame(input_window, title=lambda: f"Editing: {fields[index[0]]}")]))

    app = Application(layout=layout, key_bindings=kb, full_screen=True, mouse_support=False)

    update_display()
    result = app.run()
    return result


if __name__ == "__main__":
    sample = {
        "name": "Stephen",
        "role": "actuary",
        "location": "London",
        "book": "Risk Theory",
        "project": "archivum",
        "status": "draft",
    }
    updated = interactive_field_editor(sample)
    print(updated)
