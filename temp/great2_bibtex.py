# functions related to bibtex
# exactly great2.bibtex.py
from bibtexparser.bibdatabase import BibDatabase
from bibtexparser.bwriter import BibTexWriter
import click
from collections import namedtuple
from itertools import count
import numpy as np
from pathlib import Path
from tinydb import TinyDB, Query
from tinydb.storages import MemoryStorage
import bibtexparser
import logging
import pandas as pd
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import FuzzyCompleter, NestedCompleter
from prompt_toolkit.formatted_text import HTML
import re
import subprocess

from datetime import datetime, timedelta
import os
import pytz


LIBRARY_DIR = '.'
LIBRARY_BIB = 'library.bib'
BOOKS_BIB = 'books.bib'

SUMATRA = SUMATRA = 'C:\\Program Files\\SumatraPDF\\SumatraPDF.exe'
if not Path(SUMATRA).exists():
    SUMATRA = 'C:\\Users\\steve\\AppData\\Local\\SumatraPDF\\SumatraPDF.exe'

logger = logging.getLogger(__name__)
logging.getLogger().setLevel(logging.INFO)

# cache last search results
LAST_SEARCH_DF = None


# __all__ = ['bibtex_to_links']


def bibtex_template(regex):
    """
    Return a template for the markdown file used by bibtex_to_links.
    """
    return f"""---
fontsize: 12pt
geometry: margin=1in
colorlinks: true
linkcolor: blue
filecolor: magenta
urlcolor: blue
mainfont: "Garamond"
sansfont: "Arial"
monofont: "Courier New"
---

# Generic BibTex Export

Source tag regex: `{regex}`.

Created: {grt_now()}

# Bibliography entries

{{{{ contents }}}}

# References

\\medskip

"""


def bibtex_to_links(base_dir,
                    bibfile='',
                    outfile='',
                    template='',
                    outdir='',
                    hard=False,
                    execute=True):
    """
    WHY DOES THIS EXIST? WHERE IS IT USED?
    YOU PROBABLY WANT bibdb_to_links

    Create a set of hard links to papers and a biblio file
    for references in bibfile.
    Note: this is a bit of a kludge.

    From Mendeley, select all papers and then export to bibtex.

    :param base_dir: base directory for all files, input and output
    :param bibfile: name of bibtex input file; if '' then use extract.bib
    :param outfile: name of markdown output file; if '' then use allrefs.md
    :param template: name of markdown template file; if '' then use allrefs_template.md
    :param outdir: name of directory for hard links; if '' then use papers
    :param execute: whether to execute and make the links; the markdown file is always written
    :return: list of files
    """

    base_dir = Path(base_dir)
    if bibfile == '':
        bibfile = base_dir / 'extract.bib'
    if outfile == '':
        outfile = base_dir / 'allrefs.md'
    if outdir == '':
        outdir = base_dir / 'papers'
    if template == '':
        template = bibtex_template(bibfile)
    else:
        template = base_dir / 'allrefs_template.md'
        template = Path(template).read_text()

    f = Path(bibfile)
    assert f.exists(), f'bibfile {f} does not exist.'

    s = f.read_bytes().split(b'\n')

    # write a markdown file with all the refs
    it = count(1)
    blob = ''.join([f'{next(it)}. @' + i.decode().split('{')[1][:-1] + '\n'
                    for i in s
                    if len(i) and i[0] == ord('@')])
    template = template.replace('{{ contents }}', blob)
    Path(outfile).write_text(template)

    # now extract the pdf file links
    fn = [i.decode()[12:-6] for i in s if i[:6] == b'file =']
    good = [i for i in fn if Path(i).exists()]
    bad = [i for i in fn if not Path(i).exists()]

    # TODO: fix issues caused by multiple reference files...
    bad = [i.replace('{\\"{u}}', 'ü') for i in bad]
    bad = [i if Path(i).exists() else i.split(';')[-1][4:] for i in bad]
    fn = good + bad
    logger.info(f'Found {len(fn)} bibtex entries')

    if not execute:
        return fn

    outdir = Path(outdir)
    outdir.mkdir(exist_ok=True)
    errs = []
    for x in fn:
        x = Path(x)
        new = outdir / x.name
        if not new.exists():
            try:
                if hard:
                    new.hardlink_to(x)
                else:
                    new.symlink_to(x)
            except OSError:
                errs.append(x)
                logger.error(
                    f'OSError making {x.name}. Ignoring and continuing.')

    return fn


def bibtex_read(bibfile=LIBRARY_BIB):
    """
    Read a bibtex file and return a list of entries.
    """
    f = Path(bibfile)
    assert f.exists(), f'bibfile {f} does not exist.'
    with open(bibfile, encoding='utf-8') as bibtex_file:
        bib_database = bibtexparser.load(bibtex_file)
    return bib_database


def bibtex_create_db(bibfile='all', create_new=False, close=True):
    """
    Read bibtex file and create a tinyDB database.
    Adjust author, de-LaTex names, etc. as needed.

    Database name is .json file with same name as bibfile.

    :param bibfile:
    :param create_new: whether to create a new database (overwrite existing)
    :param close: whether to close the database
    :return:
    """

    db_path = Path(bibfile).with_suffix('.json')

    # determine if update needed
    if db_path.exists():
        db_date = db_path.stat().st_mtime
        lib_bdate = [Path(p).stat().st_mtime < db_date
                     for p in [LIBRARY_BIB, BOOKS_BIB]]
        if np.all(lib_bdate):
            print('Database up to date...exiting.')
            return

    if create_new and db_path.exists():
        try:
            db_path.unlink()
        except PermissionError:
            logger.error(f'Could not delete {db_path}. Will overwrite with ""')
            db_path.write_text('')

    # Initialize TinyDB
    db = TinyDB(db_path)

    # Insert data
    for b in [LIBRARY_BIB, BOOKS_BIB]:
        print(f'Reading bibfile {b}')
        # read bibtex file
        bib_database = bibtex_read(b)
        for entry in bib_database.entries:
            db.insert(entry)
    logger.info(
        f'Created database {db_path} with {len(bib_database.entries)} entries.')

    # tidy up the database ===============================
    # de {} titles
    def replace_braces_in_title(doc):
        brace_pattern = re.compile(r'\{([^{}]+)\}')
        if 'title' in doc:
            doc['title'] = brace_pattern.sub(r'\1', doc['title'])

    db.update(replace_braces_in_title)

    # de latex names
    _fix_unicode(db)

    # remove unwanted elements
    def remove_elements(doc):
        for elt in ['abstract', 'keywords', 'isbn', 'archiveprefix', 'arxivid',
                    'eprint', 'issn', 'pmid']:
            doc.pop(elt, None)
        return doc

    db.update(remove_elements)

    if close:
        db.close()
        db = None

    return db


def bibdb_read(bibfile=LIBRARY_BIB):
    """
    Read a bibtex tinyDB file and return a tinyDB database.
    """
    db_path = Path(bibfile).with_suffix('.json')
    db = TinyDB(Path(LIBRARY_DIR) / db_path)
    return db


def bibdb_all(db, field):
    """
    Find all unique values for a field in the database.

    :param db:
    :param field:
    :return:
    """
    x = []
    for ref in db.all():
        if field in ref:
            x.append(ref[field])
    return sorted(set(x))


def bibdb_fields(db):
    """
    Return set of all fields in db
    :param db:
    :return:
    """
    f = []
    for ref in db.all():
        f.extend(list(ref.keys()))
    f = sorted(set(f))
    return f


def bibdb_frequency(db, field):
    """
    Return a frequency table for a field in the database.

    Eg use::

        bibdb_frequency(db, 'year').sort_index().plot.bar()
        bibdb_frequency(db, 'author').sort_values().plot.barh()
        bibdb_frequency(db, 'ID') # to find duplicates

    :param db:
    :param field:
    :return:
    """
    x = []
    for ref in db.all():
        if field in ref:
            x.append(ref[field])
    return pd.Series(x).value_counts()


def bibdb_find(db, field, value):
    """
    Find all records with a given field value. Exact match only.
    Eg::

        bt.bibdb_find(db, 'ID', 'Angelsberg2011')

    :param db:
    :param field:
    :param value:
    :return:
    """
    return db.search(Query()[field] == value)


def bibdb_find_regex(db, field, regex):
    """
    Find all records with a given field regex. Regex match.
    Eg::

        bt.bibdb_find_regex(db, 'author', 'Mildenhall')

    :param db:
    :param field:
    :param regex:
    :return:
    """
    return db.search(Query()[field].matches(regex))


def bibdb_test(db, field, test_function):
    """
    Find all records with a given field value. Test function.
    Eg::

        bt.bibdb_test(db, 'author', lambda x: 'Mildenhall' in x)
        bt.bibdb_test(db, 'author', lambda x: 'Coherent' in x)
        bt.bibdb_test(db, 'author', lambda x: 'Eber' in x)

    :param db:
    :param field:
    :param test_function:
    :return:
    """
    return db.search(Query()[field].test(test_function))


def bibdb_missing_field(db, field):
    """
    Find all records with a missing field.
    Eg::

        bt.bibdb_missing_field(db, 'year')

    :param db:
    :param field:
    :return:
    """
    return db.search(~Query()[field].exists())


def bibdb_files_all(db, verify=True):
    """
    Find all files in the database.

    :param db or list of records
    :return:
    """
    if isinstance(db, list):
        dbn = TinyDB(storage=MemoryStorage)
        for i in db:
            dbn.insert(i)
        db = dbn
    files = [i['file'] for i in db.all() if 'file' in i]
    no_file = [i for i in db.all() if 'file' not in i]
    all_files = []
    missing_files = []
    for f in files:
        all_files.extend(re.split(':', f)[2:3])
    all_files = sorted(set(all_files))
    if verify:
        for f in all_files:
            if not Path(f).exists():
                missing_files.append(f)
    ans = namedtuple('FileInfo', ['all', 'missing', 'no_file'])
    ans = ans(all_files, missing_files, no_file)
    return ans


def bibdb_files_parse(record):
    """
    Extract the files from a record.

    :param record:
    :return:
    """
    files = []
    if 'file' in record:
        files = [i for i in re.split(':', record['file'])[2:3]]
    return files


def bibdb_info(db):
    """
    Database info (written by copilot).
    """
    ans = {}
    ans['entries'] = f'{len(db)}'
    ans['fields'] = f'{bibdb_fields(db)}'
    ans['years'] = f'{bibdb_all(db, "year")}'
    ans['authors'] = f'{bibdb_all(db, "author")}'
    ans['journals'] = f'{bibdb_all(db, "journal")}'
    ans['year_Freq'] = bibdb_frequency(db, "year")
    ans['author_freq'] = bibdb_frequency(db, "author")
    ans['journal_freq'] = bibdb_frequency(db, "journal")
    ans['ID_freq'] = bibdb_frequency(db, "ID")

    # Define the namedtuple type
    Ans = namedtuple('Info', ans.keys())
    ans = Ans(**ans)

    return ans


def bibdb_to_links(db, tag_regex,
                   stem='',
                   out_dir='.',
                   template='',
                   hard=False,
                   execute=True):
    """
    Create a set of hard links to papers, a biblio file, and
    the bibtex extract for references with given tag.

    Create appropriate tags in Mendeley.

    :param db: database
    :param tag_regex: regex for mendeley tag; need .* since it may not be
      at the start.
    :param out_dir: directory for all files, input and output; papers in
      subdir papers. Defaults to current directory.
    :param stem: stem name of outputs (creates stem.bib and stem.md,
      a markdown output file) if '' then use allrefs
    :param template: (findable) name of markdown template file; if ''
      then uses bibtex_template() function
    :param hard: whether to use sym links (default) or hard links
    :param execute: whether to execute and make the links; the markdown
      file is always written
    :return: list of files
    """

    subset = bibdb_find_regex(db, 'mendeley-tags', tag_regex)
    # sort by year and then ID tag (proxy for author)
    subset = sorted(subset, key=lambda x: f'{x["year"]}-{x["ID"]}')
    print(f'Found {len(subset)} entries with tag {tag_regex}')
    if len(subset) == 0:
        return

    # sort out files and folders
    out_dir = Path(out_dir)
    out_dir.mkdir(exist_ok=True)
    (out_dir / 'papers').mkdir(exist_ok=True)
    (out_dir / 'pdf').mkdir(exist_ok=True)

    if stem == '':
        out_md = out_dir / 'allrefs.md'
        out_bib = out_dir / 'allrefs.bib'
    else:
        out_md = out_dir / f'{stem}.md'
        out_bib = out_dir / f'{stem}.bib'

    if template == '' or not Path(template).exists():
        template = bibtex_template(tag_regex)
    else:
        template = Path(template).read_text()

    # Create a BibDatabase instance
    bib_database = BibDatabase()
    bib_database.entries = subset

    # Use BibTexWriter to convert the BibDatabase to a string
    writer = BibTexWriter()
    bibtex_str = writer.write(bib_database)
    # write bibtex extract file
    out_bib.write_text(bibtex_str, encoding='utf-8')

    # write a markdown file with all the refs
    it = count(1)
    blob = '\n'.join([f'{n}. @{rid}' for n, rid in
                      zip(it, [i['ID'] for i in subset])])
    template = template.replace('{{ contents }}', blob)
    out_md.write_text(template, encoding='utf-8')

    # now extract the pdf file links, parser returns a list
    fn = {(i['year'], i['ID']): bibdb_files_parse(i)
          for i in subset if 'file' in i}

    # audits (exist, unique)
    good = {k: v[0]
            for k, v in fn.items() if len(v) == 1 and Path(v[0]).exists()}
    not_unique = {k: v for k, v in fn.items() if len(v) != 1}
    bad = {k: v for k, v in fn.items() if len(
        v) != 1 or not Path(v[0]).exists()}

    if len(bad):
        logger.warning(f'Found {len(bad)} bad files, pdf does not exist.')
        print(bad)

    if len(not_unique):
        logger.warning(f'Found {len(not_unique)} not unique files.')
        print(not_unique)
        # TODO select somehow?

    if not execute:
        return

    errs = []
    out_dir = out_dir / 'papers'
    print('Output directory exists and name:', out_dir.exists(),
          out_dir.resolve())
    print(f'Writing {len(good)} entries.')
    # logger.info(out_dir.exists(), 'out_dir exists?', out_dir.resolve())
    # already sorted by year, then key
    for k, v in good.items():
        # for k, v in sorted(good.items(), key=lambda x: x[0][0] + '-' + x[0][1]):
        x = Path(v).resolve()
        new = (out_dir / x.name).resolve()
        # print(new, '==>', x)
        # print(new.name)
        if not new.exists():
            try:
                if hard:
                    new.hardlink_to(x)
                else:
                    new.symlink_to(x)
            except OSError as e:
                errs.append(x)
                print(new.name, e)
                # logger.error(f'OSError making {x.name}. Ignoring and continuing.')
        else:
            print('Already exists', new.name, '\n')
    if len(errs):
        print(f'Found {len(errs)} errors making hard links.')
    return fn


def bt_search_work(case_sensitive, tablefmt, debug=False):
    """
    Run search bibtex file for a pattern loop.
    End on ; or q.
    a = Author search, t = title, j = journal, y = year, id = id(reference id).
    By default searches ignore case, set -c for case sensitive.

    Examples:

        a Mildenhall
        y 2023
        cls
        t PMIR

    """

    global LAST_SEARCH_DF
    authors = ['Artzner', 'Barndorff-Nielsen', 'Bauer', 'Bellini', 'Bodoff',
               'Boonen', 'Borwein', 'Brockett', 'Bühlmann', 'Carr',
               'Cerreia-Vioglio', 'Chateauneuf', 'Cheridito', 'Cherny', 'Cummins',
               "D'Arcy", 'Delbaen', 'Denuit', 'Dhaene', 'Diaconis',
               'Dionne', 'Doherty', 'Duffie', 'Eber', 'Eeckhoudt',
               'Eling', 'Embrechts', 'Epstein', 'Feldblum', 'Filipovi',
               'Fishburn', 'Frittelli', 'Froot', 'Föllmer', 'Garven',
               'Gerber', 'Gilboa', 'Gollier', 'Goovaerts', 'Harrington',
               'Harrison', 'Hewitt', 'Ibragimov', 'Ingram', 'Jaffee',
               'Jouini', 'Jørgensen', 'Kaas', 'Klein', 'Koch-Medina',
               'Kreps', 'Kunreuther', 'Laeven', 'Landsman', 'Maccheroni',
               'Madan', 'Major', 'Mango', 'Marinacci', 'Mildenhall',
               'Millossovich', 'Montrucchio', 'Muermann', 'Müller', 'Panjer',
               'Pichler', 'Puccetti', 'Rockafellar', 'Saez', 'Schachermayer',
               'Schied', 'Schlesinger', 'Schmeidler', 'Shapiro', 'Shiu',
               'Svindland', 'Tankov', 'Tasche', 'Tsanakas', 'Uryasev',
               'Venter', 'Wang', 'Willmot', 'Wüthrich', 'Zanjani']

    commands = ['author', 'title', 'journal', 'year', 'ID', 'mendeley-tags',
                'debug', 'help', '?', 'quit', ';']
    # word_completer = WordCompleter(commands)
    dcommands = {c: None for c in commands}
    dcommands['author'] = {a: None for a in authors}
    word_completer = NestedCompleter.from_nested_dict(dcommands)
    fuzzy_completer = FuzzyCompleter(word_completer)
    session = PromptSession(completer=fuzzy_completer)

    db = bibdb_read()

    prompt = HTML('<ansired>g.uber</ansired><ansigreen>.bibtex > </ansigreen>')

    while True:
        try:
            pattern_in = session.prompt(prompt)
            # process
            if pattern_in in ('q', ';', 'x', 'quit', '..'):
                return
            field, *reg = pattern_in.split(' ')
            reg = ' '.join(reg)
            field = field.lower()
            if field == 'cls':
                clear_screen()
                continue
            elif field == 'a':
                field = 'author'
            elif field == 't':
                field = 'title'
            elif field == 'j':
                field = 'journal'
            elif field == 'y':
                field = 'year'
            elif field == 'i':
                field = 'id'
            elif field == 'g':
                field = 'mendeley-tags'
            elif len(field) and field[0] == 'o':
                bt_search_open(field, reg)
                continue
            elif field == 'debug':
                # toggle debug mode
                debug = not debug
                continue
            elif field in ('h', "?", 'help'):
                bt_search_help()
                continue
            else:
                print('Command not recognized.\n; or x or q to quit.')
                continue
        except KeyboardInterrupt:
            continue
        except EOFError:
            break

        # fields to display and default widths, None = as needed
        display = ['ID', 'n', 'year', 'author', 'title', 'journal']
        width = [None, None, None, 30, 40, 15]
        col_widths = dict(zip(display, width))
        # if field not in ('ID', 'title') and field in display:
        #     display.remove(field)
        if reg[0] != '^':
            # match anywhere unless requested not to
            reg = '.*' + reg
        try:
            r = re.compile(reg, 0 if case_sensitive else re.IGNORECASE)
        except Exception as e:
            print(f'Error in regex, compiler returned "{e}"')
        else:
            a = bibdb_find_regex(db, field, r)
            if len(a):
                df = pd.DataFrame(a)
                # cache results
                if debug:
                    colw = {c: 15 for c in df.columns}
                    for c in ['title', 'file', 'author']:
                        if c in df:
                            colw[c] = 40
                    print(df.head().to_markdown(
                        tablefmt=tablefmt,
                        maxcolwidths=[colw.get(i) for i in df.columns]))
                if field != 'year':
                    df = df.sort_values(['year', field], ascending=[0, 1])
                else:
                    df = df.sort_values(field)
                LAST_SEARCH_DF = df.set_index('ID')
                # reflect the new order
                df = df.reset_index(drop=True)
                df.index.name = 'n'
                # get n in the mix, for opening
                df = df.reset_index(drop=False)
                display = [i for i in display if i in df]
                df = df[display].set_index('ID').fillna('')
                print()
                print(f'Query returned {len(df)} items.\n')
                print(df.to_markdown(tablefmt=tablefmt,
                                     maxcolwidths=[col_widths.get(i) for i in display]))
                print()
            else:
                print(f'Query {reg} in field {field} returned no records')
        print()


def bt_search_open(field, reg):
    """
    Manage opening files from bt_search_work
    """
    global LAST_SEARCH_DF
    print(f'OPEN: {field = } {reg = }')
    if field[-1] == 'a':
        open_all_mode = True
    else:
        open_all_mode = False
    # open mode
    if LAST_SEARCH_DF is None:
        print('Must execute a search before opening file.')
    else:
        try:
            idx = int(reg)
        except ValueError:
            # must be a str
            idx = reg
        except Exception as e:
            print(f'Error with input {reg}, {e}.')
            return
        if isinstance(idx, int):
            if idx > len(LAST_SEARCH_DF):
                print(f'Requested item {idx} > length of last search.')
                return
            fns = [LAST_SEARCH_DF.iloc[idx].file]
        else:
            pattern = re.compile(reg)
            matches = [
                idx for idx in LAST_SEARCH_DF.index if pattern.search(idx)]
            if len(matches):
                fns = LAST_SEARCH_DF.loc[matches, 'file']
            else:
                print("No matches found.")
                return
        if len(fns) > 1 and not open_all_mode:
            print(
                "Multiple matches found: "
                f"{fns}\n"
                "Please provide a more specific regex "
                "or request oa for open all matches.")
            return
        try:
            fns = [fn.split(':')[2] for fn in fns]
        except IndexError:
            print(
                f'Error extracting from split {fns}. Expected at index 2.')
        except Exception as e:
            print(f'Error with splitting filename {fn}, {e}.')
        if len(fns) > 4:
            print(f'Opening first four matches')
            fns = fns[:4]
        for fname in fns:
            # print(f'Would open {fname}')
            subprocess.Popen([SUMATRA, fname])


def bt_search_help():
    h = f'''
Bibtex Search Help
==================

{help_base()}

[CMD] regex search phrase

CMD:
    a      =  author
    t      =  title
    j      =  journal
    y      =  year
    ID     =  ID tag
    g      =  Mendeley tag
    d      =  toggle debug mode
    o reg  =  open papers with index number matching reg
    oa     =  open all papers
    oa reg =  open all papers matching reg
    h  ?   =  print help
    q ; x  =  quit
    cls    =  clear screen 😁


'''
    print(h)


def _fix_unicode(db):
    """
    Map LaTex to Unicode in author name field
    """

    # mapping dictionary
    latex_to_unicode = {
        "\\'{o}": 'ó', "\\'{e}": 'é', "\\'{a}": 'á', "\\'{i}": 'í', "\\'{u}": 'ú',
        "\\'{A}": 'Á', "\\'{E}": 'É', "\\'{I}": 'Í', "\\'{O}": 'Ó', "\\'{U}": 'Ú',
        "\\`{a}": 'à', "\\`{e}": 'è', "\\`{i}": 'ì', "\\`{o}": 'ò', "\\`{u}": 'ù',
        "\\`{A}": 'À', "\\`{E}": 'È', "\\`{I}": 'Ì', "\\`{O}": 'Ò', "\\`{U}": 'Ù',
        '\\"{a}': 'ä', '\\"{e}': 'ë', '\\"{i}': 'ï', '\\"{o}': 'ö', '\\"{u}': 'ü',
        '\\"{A}': 'Ä', '\\"{E}': 'Ë', '\\"{I}': 'Ï', '\\"{O}': 'Ö', '\\"{U}': 'Ü',
        "\\c{c}": 'ç', "\\c{C}": 'Ç', "\\~{n}": 'ñ', "\\~{N}": 'Ñ', '\o': 'ø'
        # ... add more mappings as needed
    }
    latex_to_unicode = {f'{{{k}}}': v for k, v in latex_to_unicode.items()}

    regex = re.compile('|'.join(re.escape(key)
                                for key in latex_to_unicode.keys()))

    # Function to modify the author field
    def modify(x):
        return regex.sub(lambda x: latex_to_unicode[x[0]], x)

    # Retrieve all records and iterate through them
    for record in db.all():
        for f in ['author', 'journal', 'title', 'file']:
            if f in record:
                a = record[f]
                new_f = modify(a)
                if a != new_f:
                    k = record.doc_id
                    db.update({f: new_f}, doc_ids=[k])


def clear_screen():
    os.system('cls')


def help_base():
    """
    Common help elements across all scripts
    """
    h = '''
; q x ..        Exit to next level
? h help        Help
--help          Always available
'''


def grt_now():
    # Timezone for London
    london_tz = pytz.timezone('Europe/London')
    now_in_london = datetime.now(london_tz)

    # Format as an ISO standard date-time string
    iso_formatted_datetime = now_in_london.isoformat()
    return iso_formatted_datetime
