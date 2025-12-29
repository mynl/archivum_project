"""
Definitive path to creating the new library.

Steps
======

1. Open test-library
2. Clear
3. Populate with library, books, scans, plus five days of additional files
4. Rationalize duplicates
5. Rationalize hash duplicates
6. Rename and move files

Code from
==========

* 2025-12-13-quality-audit.ipynb: loading files


Running
=======

```
python -m archivum.definitive_loader

"""
from functools import partial
from importlib.resources import files
from pathlib import Path
import argparse
import logging
import logging.config
import yaml

import numpy as np

import archivum.library as arcl
import archivum.utilities as arcu
import archivum.cli as cli
import archivum.enhancements as arce


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
    print()
    print(line)
    print(ul * len(line))


def setup_logging():
    # set up logging
    logger_config = "logging-debug-file-only.yaml"
    logger_config = "logging-warning.yaml"
    logger_config = "logging-info-file-only.yaml"
    with (files("archivum.configurations") / logger_config).open("r") as f:
        cfg = yaml.safe_load(f)
    logging.config.dictConfig(cfg)

    logger = logging.getLogger("archivum.TEST")
    logger.debug("debug - test")
    logger.info("info - test")
    logger.warning("warning - test")
    logger.error("error - test")


def main():
    """Main control loop - just turn things on / off."""

    parser = argparse.ArgumentParser(description="Archivum Load Script")
    parser.add_argument("-s", "--show-stats", action="store_true", help="Show stats of all libraries")
    parser.add_argument("-w", "--make-raw", action="store_true", help="Rebuilt raw library - default: no build")
    parser.add_argument("-t", "--make-test", action="store_true", help="Rebuilt test library - default: no build")
    parser.add_argument("-r", "--enhance-refs", action="store_true", help="Enhance refs in test library")

    args = parser.parse_args()

    # see what you have
    ulprint('Existing libraries')

    if args.show_stats:
        qd(arcl.Library.list_stats(), vrule_widths=(1,0,0))

    # discover folders
    source_root = Path(r"C:\temp\temp-arc\original-sources")
    all_base_files = [
       source_root  / "Library",
       source_root  / "Books",
       source_root  / "Book_scans",
    ]
    new_base_files = [f for f in Path("C:\\temp\\temp-arc\\new-sources\\").glob("*") if f.is_dir()]

    full_build = all_base_files + new_base_files
    print("Found directories")
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
    ulprint('Expected output')
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



if __name__ == "__main__":
    main()
