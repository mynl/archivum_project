Command Line Interface
======================

Archivum exposes its command line interface through the ``archivum`` console
script. For library-aware work, use the Uber shell so the active library lives
in the shell state.

Start Uber
----------

Open the default library:

.. code-block:: powershell

   uv run archivum uber -a

Open a named library:

.. code-block:: powershell

   uv run archivum uber -l uber-library

Once the prompt is open, run commands directly:

.. code-block:: text

   stats
   status
   library-config
   q title ~ /risk measure/ top 20
   rg "spectral risk measure"
   tag Wang2024 -o
   stage-docs "C:\S\PDFs\Batch6"
   import-bibtex "C:\S\PDFs\Batch6\bibtex-import.bib" -x

Direct Launchers
----------------

Some commands are still useful as one-shot launchers from PowerShell. The web
server opens its own library context:

.. code-block:: powershell

   uv run archivum serve -b
   uv run archivum serve uber-library --port 9124 --address 127.0.0.1 -b

Full Command Reference
----------------------

.. click:: archivum.cli:entry
   :prog: archivum
   :nested: full
