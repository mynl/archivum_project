from flask import render_template, current_app
from pathlib import Path 
import pandas as pd
from . import grid_bp

def _load_grid_data(library_path: Path) -> pd.DataFrame:
    # This function is now responsible for loading data for the grid
    df = pd.read_feather(library_path) #, dtype_backend="pyarrow")
    cols = ['tag', 'author', 'title', 'journal']
    df = df[cols]
    df = df.reset_index(drop=True)
    df["id"] = df.index
    df["search_blob"] = df[cols].fillna("").agg(" ".join, axis=1) # Still useful for internal grid filtering if needed
    return df

@grid_bp.route("/grid")
def grid():
    return render_template("grid.html", active_page="grid")

@grid_bp.route("/grid-data")
def grid_data_route():
    library_path = current_app.config['LIBRARY_PATH']
    df = _load_grid_data(library_path)

    cols = ['tag', 'author', 'title', 'journal']
    widths = [1, 2, 4, 1]

    data = df.to_dict(orient="records")
    columns = [{"title": col, "field": col, 'widthGrow': w} for col, w in zip(cols, widths)]

    return {"data": data, "columns": columns}
