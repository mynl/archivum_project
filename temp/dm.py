# coding: utf-8
get_ipython().run_line_magic('load_ext', 'IPython.extensions.autoreload')
get_ipython().run_line_magic('autoreload', '2')
get_ipython().run_line_magic('config', 'InlineBackend.figure_format = "retina"')
get_ipython().run_line_magic('load_ext', 'autotime')
import pandas as pd 
from pathlib import Path 
import yaml
# logging 
from importlib.resources import files
import logging
import logging.config

with (files("archivum.configurations") / "logging-default.yaml").open("r") as f:
    cfg = yaml.safe_load(f)
logging.config.dictConfig(cfg)

logger = logging.getLogger('archivum.TEST')
logger.info("Hello Steve")
import archivum as arc
import archivum.mendeley_port as arcmp
import archivum.library as arcl
import archivum.utilities as arcu
import archivum.document as arcd
import archivum.crossref as arcc
import archivum.gui as arcg
import archivum.parser as arcp
import archivum.reference as arcr
import archivum.cli as cli 
import archivum.config as con
import archivum.import_bibtex as aibt

qd = arcu.make_partial_GT()
import archivum as arc
import archivum.mendeley_port as arcmp
import archivum.library as arcl
import archivum.utilities as arcu
import archivum.document as arcd
import archivum.crossref as arcc
import archivum.gui as arcg
import archivum.parser as arcp
import archivum.reference as arcr
import archivum.cli as cli 
import archivum.config as con
import archivum.import_bibtex as aibt

qd = arcu.make_partial_GT()
lib_name = 'books'
lib_name = 'uber-library'
lib = arcl.Library(lib_name)
qd(lib.ref_df.querex('top 4 recent !Mildenhall'))
get_ipython().system('dir ..\\new-papers\\*.bib')
p = Path('..\\new-papers\\new-test.bib')
assert p.exists()
lib.import_bibtex(p)
get_ipython().run_line_magic('pinfo', '%save')
