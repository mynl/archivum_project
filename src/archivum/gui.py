"""
STEADFAST guis

with GPT.
"""

import tkinter as tk
from tkinter import ttk
from rapidfuzz import process

def edit_fields_from_df(df, i, mandatory=(), title="Edit Row", known_values=None):
    result = None
    rowcount = len(df)
    fields = df.iloc[i].to_dict()
    for key in mandatory:
        fields.setdefault(key, "")

    known_values = known_values or {}
    vars_ = {}

    def load_row(idx):
        for k in fields:
            val = str(df.iloc[idx].get(k, ""))
            vars_[k].set(val)

    def save_row():
        return {k: vars_[k].get() for k in fields}

    def on_done():
        nonlocal result
        result = (save_row(), current[0])
        root.destroy()

    def on_cancel():
        root.destroy()

    def on_prev():
        if current[0] > 0:
            current[0] -= 1
            load_row(current[0])

    def on_next():
        if current[0] < rowcount - 1:
            current[0] += 1
            load_row(current[0])

    def setup_fuzzy_combobox(box, values):
        def on_key(event):
            current = box.get()
            if current:
                match, score, _ = process.extractOne(current, values, score_cutoff=70)
                if match and match != current:
                    box.set(match)
                    box.icursor(tk.END)
        box.bind('<KeyRelease>', on_key)

    current = [i]  # mutable index holder
    root = tk.Tk()
    root.title(title)
    root.geometry("800x500")

    for row, key in enumerate(fields):
        tk.Label(root, text=key).grid(row=row, column=0, sticky="e", padx=5, pady=4)
        var = tk.StringVar()
        vars_[key] = var

        if key in known_values:
            box = ttk.Combobox(root, textvariable=var, values=known_values[key], width=80)
            box.grid(row=row, column=1, padx=5, pady=4)
            setup_fuzzy_combobox(box, known_values[key])
        else:
            tk.Entry(root, textvariable=var, width=80).grid(row=row, column=1, padx=5, pady=4)

    load_row(i)

    row += 1
    btn_frame = tk.Frame(root)
    btn_frame.grid(row=row, column=0, columnspan=2, pady=20)
    tk.Button(btn_frame, text="Previous", width=12, command=on_prev).pack(side="left", padx=5)
    tk.Button(btn_frame, text="Next", width=12, command=on_next).pack(side="left", padx=5)
    tk.Button(btn_frame, text="Cancel", width=12, command=on_cancel).pack(side="left", padx=20)
    tk.Button(btn_frame, text="Done", width=12, command=on_done).pack(side="left", padx=5)

    root.mainloop()
    return result
