Web Interface
=============

The web interface is launched by ``archivum serve`` and is built with Flask,
Bootstrap 5, and incremental HTMX-style updates. It is intended as the primary
workflow for searching, reading, ingesting, and exploring a library.

Query Page
----------

The Query page searches reference metadata through querexfuzz.

* The search bar supports history navigation with the up and down arrows.
* Plain text is interpreted as a fast fuzzy ``#`` query.
* Explicit querexfuzz syntax is wrapped in ``recent top 50`` unless the input
  starts with ``q``.
* The live feedback line below the input shows the exact query expression that
  will run and reports incomplete or invalid syntax.
* Result views include list, verbose, and table modes.
* The Export menu can send results back to Query or download CSV, BibTeX, or
  BibTeX+.

Ripgrep
-------

The Ripgrep screen searches extracted document text.

* Search terms are passed to ``rg`` and results stream back to the browser.
* The small feedback line validates the typed pattern as regular expression
  syntax before execution.
* Context controls choose how many neighboring lines are shown.
* Summary mode groups matches by year and author.
* Export can send hash-limited matches to Query or download CSV, BibTeX, or
  BibTeX+ for the matched documents.

Network and Discovery
---------------------

The Network screen explores query-defined or ripgrep-defined universes.

* A raw string is treated as a query universe.
* ``q ...`` sends an explicit querexfuzz universe.
* ``rg ...`` defines a full-text universe.
* Combined ``q ... rg ...`` input uses querexfuzz first, then filters through
  ripgrep.

Social graph mode maps co-authorship. Author nodes represent authors in the
current universe, edges represent co-authored papers, node size reflects paper
count, and edge thickness reflects repeated collaboration.

Semantic galaxy mode projects papers into two dimensions using transformer
embeddings, UMAP, and HDBSCAN. It can analyze title and metadata, the first
2,000 characters of full text, or the first 4,000 characters of full text.
Embeddings are cached per library and source so repeated analyses avoid
unnecessary encoding work.

Reports
-------

Report Studio creates persistent research reports from selected query or
network results.

* Reports are stored as ``.qmd`` sources.
* HTML fragments are rendered with Pandoc and embedded in the web interface.
* Admin PDF export uses Quarto and Tectonic.
* Rendered HTML, PDFs, and report images are cached in the library export
  directory until the source changes.

Ingest and Edit
---------------

Admin mode exposes write workflows.

The Ingest screen stages a document or BibTeX source, previews the real importer
result, checks duplicate and merge conditions, and commits the final archive
operation. The final commit updates Feather data, writes synchronized BibTeX,
stores the document, and extracts text for supported PDFs.

The Edit screen provides direct BibTeX-backed metadata editing by tag. Use it
for deliberate metadata repair and keep tags stable once references are linked
to reports, citations, or documents.

Status
------

The Status page reports library identity, database counts, file synchronization
state, watcher state, and related operational details.
