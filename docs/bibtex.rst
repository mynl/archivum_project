BibTeX Format
=============

Archivum stores bibliographic metadata in a deliberately narrow
BibTeX-compatible shape. Normalizing entry types and fields keeps imports
predictable, keeps Feather tables stable, and reduces synchronization noise.

Entry Types
-----------

Use one of these types for new or edited records:

.. code-block:: text

   article, book, techreport, misc, incollection, unpublished,
   inproceedings, phdthesis

Unknown entry types are not useful inside Archivum. Normalize them to the
closest supported type, or use ``misc`` when there is no clear match.

Supported Fields
----------------

Restrict BibTeX fields to the following set unless a one-off import requires a
temporary exception:

.. code-block:: text

   tag, type, title, year, author, journal, volume, number,
   month, pages, booktitle, editor, edition, doi, isbn,
   publisher, institution, address, url,
   archivePrefix, arxivId, eprint

Conventions
-----------

The BibTeX key is the Archivum tag. Keep tags stable after import because
document links, citations, reports, and read history use them as reference
identity.

Store the entry type without the leading ``@`` in editable metadata. For
example, use ``article`` rather than ``@article``.

Use BibTeX ``and`` between authors or editors. Keep ``year`` as a four-digit
year when known, and use ``month`` only when it is bibliographically meaningful.
Prefer DOI, ISBN, URL, and arXiv fields over embedding identifiers in titles or
notes.

Example:

.. code-block:: bibtex

   @article{Wang2024,
     author = {Wang, Ruodu and Smith, Jane},
     title = {Spectral Risk Measures},
     year = {2024},
     journal = {Insurance Mathematics and Economics},
     doi = {10.0000/example}
   }

Exports
-------

Standard BibTeX export writes citation metadata only.

BibTeX+ export adds Archivum document hashes and Mendeley-style ``file`` fields
when a matched document path is available. The file field uses the form
``:C\:/path/to/document.pdf:pdf``. Do not add these fields manually to normal
library metadata; Archivum maintains document links through the ref-doc table
and export workflow.
