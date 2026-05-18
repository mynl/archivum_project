Quick Start
===========

Requirements
------------

Archivum is developed for Python 3.13 and local document libraries. The normal
development and operating environment uses ``uv`` for dependency management and
``ripgrep`` for full-text search.

Install or refresh the Python environment:

.. code-block:: powershell

   uv sync --extra test

Check the installed command line interface:

.. code-block:: powershell

   uv run archivum --help

Start the Uber Shell
--------------------

The normal interactive CLI workflow is the Uber shell. Start it with the default
library already open:

.. code-block:: powershell

   uv run archivum uber -a

The prompt shows the active library. From there, run library-state commands
without the outer ``uv run archivum`` prefix:

.. code-block:: text

   stats
   status
   library-config
   q title ~ /risk measure/ top 20
   rg "spectral risk measure"

To choose a specific library when starting the shell:

.. code-block:: powershell

   uv run archivum uber -l uber-library

You can also start Uber without auto-opening a library and then open one from
the prompt:

.. code-block:: powershell

   uv run archivum uber

.. code-block:: text

   list
   open uber-library

Launch the Web UI
-----------------

Start the Flask web interface directly. Unlike stateful shell commands, the
server command opens the configured default library for the web process:

.. code-block:: powershell

   uv run archivum serve -b

By default the server listens on ``http://127.0.0.1:9124``. Use ``--port``,
``--address``, ``--debug``, and ``--prod`` for alternate launch modes:

.. code-block:: powershell

   uv run archivum serve uber-library --port 9124 --address 127.0.0.1 -b
   uv run archivum serve uber-library --prod --address 0.0.0.0

Safety Notes
------------

Archivum is designed for a real local research library, so prefer dry runs
before executing write operations. Many commands require ``-x`` or
``--execute`` before they mutate library data. Be especially deliberate around
document deletion, sharded document storage, Feather files, and BibTeX
synchronization.
