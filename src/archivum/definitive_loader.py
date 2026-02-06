"""
Definitive path to creating the new library.

Steps
======

1. Open test-library
2. Clear
3. Populate with library, books, scans, plus six days of additional files
4. Rationalize duplicates
5. Rationalize hash duplicates
6. Rename and move files

Code from
==========

* 2025-12-13-quality-audit.ipynb: loading files


Running
=======

```python
python -m archivum.definitive_loader
```

"""
from functools import partial
from importlib.resources import files
import json
from pathlib import Path
import argparse
import logging
import logging.config
import yaml

import numpy as np
import pandas as pd 

import archivum.library as arcl
import archivum.utilities as arcu
import archivum.cli as cli
import archivum.enhancements as arce
from .enhancements import Ans


logger = None

qd = arcu.make_qd(
        display_func=print,
        max_rows=-1,
        large_warning=200,
        max_string_length=0,
        year_cols='year',
        show_index=True,
        )


errors_mapper = {'Caicedo, Andr´es Eduardo': 'Caicedo, Andrés Eduardo',
                 'Cerreia‐Vioglio, Simone': 'Cerreia‐Vioglio, Simone',
                 'Cerreia–Vioglio, S.': 'Cerreia–Vioglio, S.',
                 'Cireşan, Dan': 'Cireșan, Dan',
                 'J.B., SEOANE-SEP´ULVEDA': 'J.B., Seoane-Sepúlveda',
                 'JIM´ENEZ-RODR´IGUEZ, P.': 'Jiménez-Rodríguez, P.',
                 'Joldeş, Mioara': 'Joldeș, Mioara',
                 'Lesne, Jean‐Philippe ‐P': 'Lesne, Jean‐Philippe ‐P',
                 'MU˜NOZ-FERN´ANDEZ, G.A.': 'Muñoz-Fernández, G.A.',
                 'Naneş, Ana Maria': 'Naneș, Ana Maria',
                 'Paradıs, J': 'Paradís, J',
                 "P{\\'{a}}stor, Ľ": 'Pástor, Ľ',
                 'Uludağ, Muhammed': 'Uludağ, Muhammed',
                 'Ulug{\\"{u}}lyaǧci, Abdurrahman': 'Ulugülyaǧci, Abdurrahman',
                 'Zitikis, Riċardas': 'Zitikis, Riċardas',
                 'de la Pen̄a, Victor H.': 'de la Peña, Victor H.',
                 "{L{\\'{o}}pez\xa0de\xa0Vergara}, Jorge E.": 'López\xa0de\xa0Vergara, Jorge E.'}


def ulprint(line, ul='='):
    """Underlined (ul) print function."""
    print()
    print(line)
    print(ul * len(line))


def setup_logging():
    # set up logging to permanent logging folder
    global logger
    logger_config = "logging-loader.yaml"
    with (files("archivum.configurations") / logger_config).open("r") as f:
        cfg = yaml.safe_load(f)
    logging.config.dictConfig(cfg)

    logger = logging.getLogger(__name__)
    logger.info("definitive_loader logger started")


def discover_sources():
    """Initial load sources only."""
    source_root = Path("C:\\S\\PDFs\\original-sources")
    all_base_files = [
       source_root  / "Library",
       source_root  / "Books",
       source_root  / "Book_scans",
    ]
    # sources from 2025-12-05 to 18
    new_base_files = [f for f in Path("C:\\S\\PDFs\\new-sources\\").glob("*") if f.is_dir()]

    return all_base_files + new_base_files


def load_enhance_audit(base_path: Path, name: str = "Ans") -> Ans:
    """Read back the enhance-audit file."""
    data = {}

    for field in Ans._fields:
        stem = f"{name}-{field}"
        csv_path = base_path / f"{stem}.csv"
        json_path = base_path / f"{stem}.json"

        if field.endswith("df") and csv_path.exists():
            data[field] = pd.read_csv(csv_path, index_col=0, encoding="utf-8")
        elif field.endswith("map") and json_path.exists():
            with json_path.open("r", encoding="utf-8") as f:
                content = json.load(f)
            # Check if this specific JSON represents the Graph
            if field == "G" and isinstance(content, dict) and "nodes" in content:
                data[field] = nx.readwrite.json_graph.node_link_graph(content)
            else:
                data[field] = content
        else:
            data[field] = None

    return Ans(**data)


def main():
    """Main control loop - just turn things on / off."""

    parser = argparse.ArgumentParser(description="Archivum Load Script")
    parser.add_argument("-s", "--show-stats", action="store_true", help="Show stats of all libraries")
    parser.add_argument("-w", "--make-raw", action="store_true", help="Rebuilt raw library - default: no build")
    parser.add_argument("-t", "--make-test", action="store_true", help="Rebuilt test library - default: no build")
    parser.add_argument("-r", "--enhance-refs", action="store_true", help="Enhance refs in test library")
    parser.add_argument("-d", "--organize-docs", action="store_true", help="Organize docs in test library")

    args = parser.parse_args()

    # see what you have
    if args.show_stats:
        ulprint('Existing libraries')
        qd(arcl.Library.list_stats(), vrule_widths=(1,0,0))

    # discover folders
    full_build = discover_sources()
    print(f"Found {len(full_build)} directories")
    print('\n'.join(str(p) for p in full_build))

    # Create raw library
    ulprint('Loading / re-creating raw-library')
    raw_lib = arcl.Library('raw-library')
    if args.make_raw:
        raw_lib.reset_library()

        # load all directories
        raw_lib.initial_import(
                dir_iterable=full_build,
                errors_mapper=errors_mapper,
                qd=qd,
                update=True)
    ulprint('Raw stats')
    qd(raw_lib.stats())

    # expected situation
    ulprint('Expected output -> Raw')
    print("""
┍━━━━━━━━━━━━━┳━━━━━━━┯━━━━━━━━━━━┑
│             ┃ refer │           │
│ index       ┃ ences │ documents │
┝━━━━━━━━━━━━━╋━━━━━━━┿━━━━━━━━━━━┥
│ objects     ┃ 7,035 │     7,138 │
├─────────────╂───────┼───────────┤
│ no children ┃   178 │       259 │
├─────────────╂───────┼───────────┤
│ children    ┃ 6,857 │     6,879 │
├─────────────╂───────┼───────────┤
│ 1 child     ┃ 6,739 │     6,783 │
├─────────────╂───────┼───────────┤
│ 2 children  ┃   108 │        89 │
├─────────────╂───────┼───────────┤
│ 3 children  ┃    10 │         4 │
├─────────────╂───────┼───────────┤
│ 4 children  ┃     0 │         3 │
┕━━━━━━━━━━━━━┻━━━━━━━┷━━━━━━━━━━━┙
""")

    # same with test library
    ulprint('Loading / re-creating test library')
    test_lib = arcl.Library('test-library')
    if args.make_test:
        test_lib.reset_library()

        # load all directories
        test_lib.initial_import(
                dir_iterable=full_build,
                errors_mapper=errors_mapper,
                qd=qd,
                update=True)
        ulprint('Test stats - pre-edits')
        qd(test_lib.stats())

    # enhance references
    kc = ['tag', 'type', 'title', 'author', 'journal', 'cluster_id',
            'source_id', 'mapped_title', 'merge_count']
    qc = ['tag', 'type', 'title', 'author', 'journal', 'merge_count']

    if args.enhance_refs:
        ans = test_lib.enhance_refs(update=True)
        print(f"new num references = {len(ans.ans_df) = } Expected 6816")
        print(f"                   = {len(test_lib.ref_df) = }")
        print(f"dropped {len(ans.dropped_df) = } Expected = 219")
        ulprint('Test stats - post-ref enhancement')
        qd(test_lib.stats())

    # doc organizer
    if args.organize_docs:
        ulprint('Organizing documents into sharded structure')
        # We use the config-defined doc_dir, or provide a specific one
        # For the test library, we probably want them in a 'sharded' subfolder of the library
        # sharded_base = test_lib.config_path / "sharded_docs"
        sharded_base = "C:\\S\\AppData\\archivum\\docs"
        arce.enhance_doc_df(test_lib, base_dir=str(sharded_base), update=True)
        ulprint('Test stats - post-org docs')
        qd(test_lib.stats())


if __name__ == "__main__":
    setup_logging()
    main()
