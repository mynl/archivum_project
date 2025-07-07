import pandas as pd
from pathlib import Path
import archivum.document as arcd
import archivum.crossref as arcc
import archivum.reference as arcr
from greater_tables import GT  # Assuming GT is installed


def bibtex_from_series(ser: pd.Series) -> str:
    key = ser.get("tag", "missing_key")
    entry_type = ser.get("type", "unknown")

    lines = [f"@{entry_type}{{{key},"]
    for field in ser.index:
        if field in {"tag", "type"}:
            continue
        value = ser[field]
        if pd.notna(value) and str(value).strip():
            lines.append(f"    {field} = {{{value}}},")
    lines[-1] = lines[-1].rstrip(",")  # remove trailing comma from last field
    lines.append("}")
    return "\n".join(lines)


def process_new_pdf(pdf_path: Path, library_instance) -> tuple[str, str]:
    d = arcd.Document(pdf_path, library_instance)
    titles = d.uber()
    if isinstance(titles, str):
        title = titles
        titles = (titles, 'none')
    else:
        title = titles[0]
        if len(titles) == 1:
            titles = (titles[0], 'none')
    td = arcc.search_by_title(title)
    r = arcr.Reference.from_crossref(td, library_instance)

    df = pd.Series(r.to_dict().values(), index=r.to_dict().keys()).to_frame()
    df.index.name = 'field'
    df.columns = ['value']

    metadata_html = GT(df).html
    bibtex = bibtex_from_series(r.to_ref_ser())

    return metadata_html, bibtex, titles
