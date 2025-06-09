from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.widgets import Frame

import math


def display_dict_as_plaintext(d: dict) -> str:
    keys = list(d.keys())
    half = math.ceil(len(keys) / 2)
    left, right = keys[:half], keys[half:]

    lines = []
    for i in range(half):
        k1 = left[i]
        v1 = d[k1]
        k2 = right[i] if i < len(right) else ""
        v2 = d[k2] if k2 else ""
        lines.append(f"{k1:<15}: {v1:<30}    {k2:<15}: {v2}")
    return "\n".join(lines)


def interactive_field_editor(d: dict) -> dict:
    current = dict(d)
    fields = list(current.keys())
    index = [0]  # mutable so closure can update

    buf = Buffer()

    def update_display():
        buf.text = current[fields[index[0]]]

    def apply_change():
        current[fields[index[0]]] = buf.text

    kb = KeyBindings()

    @kb.add("left")
    def _(event):
        apply_change()
        index[0] = (index[0] - 1) % len(fields)
        update_display()
        refresh_display()

    @kb.add("right")
    def _(event):
        apply_change()
        index[0] = (index[0] + 1) % len(fields)
        update_display()
        refresh_display()

    @kb.add("enter")
    def _(event):
        apply_change()
        update_display()
        refresh_display()

    @kb.add("c-c")
    @kb.add("escape")
    def _(event):
        apply_change()
        event.app.exit(current)

    def refresh_display():
        body_buffer.set_document(Document(get_body_text()), bypass_readonly=True)

    def get_body_text():
        return display_dict_as_plaintext(current)

    # display buffer
    body_buffer = Buffer(read_only=True, document=Document(get_body_text()))
    body = Window(BufferControl(buffer=body_buffer), wrap_lines=True)

    input_window = Window(BufferControl(buffer=buf), height=1, wrap_lines=False)
    layout = Layout(HSplit([
        body,
        Frame(input_window, title=lambda: f"Editing: {fields[index[0]]}")
    ]))

    app = Application(layout=layout, key_bindings=kb, full_screen=True)

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
