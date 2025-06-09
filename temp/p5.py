"""Good start on edit for new doc import..."""

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import FuzzyWordCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, HSplit, VSplit, Window
from prompt_toolkit.layout.containers import VSplit
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import D
from prompt_toolkit.styles import Style

import numpy as np
import pandas as pd

import os
import shutil


def interactive_edit_df_prompt(df: pd.DataFrame) -> pd.DataFrame:
    original = df.copy()
    current = df.copy()
    row_idx = [0]
    col_idx = [0]
    total_rows = len(df)
    buf = Buffer()

    style = Style.from_dict({
        "fieldname": "bold cyan",
        "header": "bold",
    })

    field_col_width = max(len(str(c)) for c in df.columns) + 2
    terminal_width = shutil.get_terminal_size().columns
    value_col_width = max(20, terminal_width - field_col_width - 5)

    def get_visible_keys(row: pd.Series) -> list[str]:
        return [k for k in row.index if k in minimal_fields or pd.notnull(row[k]) and row[k] != ""]

    def make_row_display(row: pd.Series, rownum: int) -> FormattedText:
        keys = get_visible_keys(row)
        lines = [
            ("class:header", f"[Row {rownum + 1} of {total_rows}]\n"),
            ("class:header", f"{'Field':<{field_col_width}}| Value\n"),
            ("class:header", "-" * terminal_width + "\n")
        ]
        for key in keys:
            val = str(row[key]) if pd.notnull(row[key]) else ""
            lines.append(("class:fieldname", f"{key:<{field_col_width}}"))
            lines.append(("", f"| {val}\n"))
        return lines, keys

    os.system("cls")

    row = current.iloc[row_idx[0]]
    display_lines, visible_keys = make_row_display(row, row_idx[0])
    key = visible_keys[col_idx[0]]
    val = str(row[key]) if pd.notnull(row[key]) else ""

    row_display = FormattedTextControl(text=display_lines)
    prefix_display = FormattedTextControl(text=[("class:fieldname", f"{key}: ")])

    completer = FuzzyWordCompleter(["Active", "Inactive", "Analyst", "Manager", "On leave", "London", "Paris", "Berlin"], WORD=True)
    buf = Buffer(completer=completer, complete_while_typing=True)
    buf.document = Document(text=val, cursor_position=len(val))

    def load_field():
        row = current.iloc[row_idx[0]]
        nonlocal visible_keys
        new_lines, visible_keys = make_row_display(row, row_idx[0])
        display_lines[:] = new_lines
        col_idx[0] = col_idx[0] % len(visible_keys)
        key = visible_keys[col_idx[0]]
        val = str(row[key]) if pd.notnull(row[key]) else ""
        prefix_display.text = [("class:fieldname", f"{key}: ")]
        buf.document = Document(text=val, cursor_position=len(val))

    def save_field():
        key = visible_keys[col_idx[0]]
        val = buf.text.strip()
        current.at[row_idx[0], key] = val

    kb = KeyBindings()

    @kb.add("tab")
    def _(event):
        save_field()
        col_idx[0] = (col_idx[0] + 1) % len(visible_keys)
        load_field()

    @kb.add("s-tab")
    def _(event):
        save_field()
        col_idx[0] = (col_idx[0] - 1) % len(visible_keys)
        load_field()

    @kb.add("down")
    @kb.add("pagedown")
    def _(event):
        save_field()
        row_idx[0] = (row_idx[0] + 1) % total_rows
        load_field()

    @kb.add("up")
    @kb.add("pageup")
    def _(event):
        save_field()
        row_idx[0] = (row_idx[0] - 1) % total_rows
        load_field()

    @kb.add("enter")
    def _(event):
        save_field()
        row_idx[0] = (row_idx[0] + 1) % total_rows
        load_field()

    @kb.add("c-s")
    def _(event):
        save_field()
        event.app.exit(result=current)

    @kb.add("escape")
    def _(event):
        event.app.exit(result=original)

    load_field()

    layout = Layout(
        HSplit([
            Window(content=row_display, height=D(min=6)),
            Window(height=1, char="-"),
            VSplit([
                Window(content=prefix_display, width=field_col_width),
                Window(BufferControl(buffer=buf), width=value_col_width)
            ])
        ])
    )

    app = Application(layout=layout, key_bindings=kb, full_screen=False, style=style)
    return app.run()


if __name__ == "__main__":

    import archivum.library as arcl

    lib = arcl.Library('uber-library')

    minimal_fields = ['tag', 'year', "author", "title", 'journal']

    df = lib.ref_df.head(20)

    # df = pd.DataFrame([
    #     {"name": "Alice", "role": "Analyst", "city": "London", "status": "Active"},
    #     {"name": "Bob", "role": "Manager", 'level': 3, "city": "Paris", "status": "Inactive", "notes": "On leave"},
    #     {"name": "Carol", "role": np.nan, "city": "Berlin", "status": "Active", "notes": ""},
    # ])

    revised_df = interactive_edit_df_prompt(df)
    print("\nFinal result:\n", revised_df)
