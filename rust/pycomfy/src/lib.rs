use pyo3::prelude::*;
use comfy_table::{Table, presets::UTF8_FULL};

#[pyfunction]
fn render_table(rows: Vec<Vec<String>>) -> PyResult<String> {
    let mut table = Table::new();
    table.load_preset(UTF8_FULL);
    for row in rows {
        table.add_row(row);
    }
    Ok(table.to_string())
}

#[pymodule]
fn pycomfy(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(render_table, m)?)?;
    Ok(())
}
