Data Model
==========

Archivum separates bibliographic references, physical documents, and the links
between them.

Core Tables
-----------

Reference metadata is stored in ``ref.feather``. A reference is keyed by a
stable tag such as ``Wang2024`` and contains BibTeX-style fields including
author, title, year, journal, publisher, DOI, ISBN, and URL.

Document metadata is stored in ``doc.feather``. A document is a physical file
identified by content hash and version. Document rows track file paths, sizes,
types, hash information, and related file metadata.

Reference-document links are stored in ``ref-doc.feather``. This junction table
links reference tags to document hashes and versions so reference identity is
not dependent on a particular file location.

Read history tracks document opens, timestamps, and caller URLs. Semantic cache
data stores embeddings and projection inputs for repeated network analysis.

Document Storage
----------------

Documents are stored in a sharded content-addressable document store. Internal
metadata uses relative paths where possible so libraries remain portable across
machines and drive mappings.

Text Extraction
---------------

When documents are imported, Archivum extracts searchable text for supported
formats. Ripgrep searches run against this extracted text store rather than
against PDF binary contents directly.

Configuration
-------------

Global configuration lives under local app data:

.. code-block:: text

   %LOCALAPPDATA%\archivum\global-config.yaml

Library-specific configuration lives in each library directory:

.. code-block:: text

   %LOCALAPPDATA%\archivum\libraries\<library-name>\config.yaml

Important configuration concepts include ``default_library``, ``doc_store_lib``,
``bibtex_file``, query defaults, enhancement settings, timezone settings, table
settings, extractor settings, and tag-mapping defaults.
