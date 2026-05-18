Query Syntax
============

Archivum metadata search is powered by querexfuzz. The web Query page adds a
small layer of input interpretation so common searches stay short while explicit
queries remain available.

Live Interpretation
-------------------

The Query page interprets input before sending it to the library query engine.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Input
     - Query sent to Archivum
   * - ``machine learning``
     - ``# machine learning``
   * - ``!Wang``
     - ``recent top 50 !Wang``
   * - ``year > 2020``
     - ``recent top 50 year > 2020``
   * - ``title ~ /risk measure/``
     - ``recent top 50 title ~ /risk measure/``
   * - ``q top 20 recent``
     - ``top 20 recent``

Plain text with no querexfuzz symbols becomes a fuzzy ``#`` search. Text that
contains one of ``#``, ``~``, ``=``, ``<``, ``>``, or ``!`` is treated as an
explicit query expression and wrapped in ``recent top 50``. Prefix an expression
with ``q`` to pass it through unchanged.

Querexfuzz Examples
-------------------

Fuzzy search:

.. code-block:: text

   q # machine learning

Field matching:

.. code-block:: text

   q title ~ /credibility/ and year > 2019

Projection and ordering:

.. code-block:: text

   q select year, author, title author ~ /Wang, R/ order by -year

Limits:

.. code-block:: text

   q top 25 recent

Network Input
-------------

The Network screen uses a related but separate interpretation model:

* Raw text is a raw query universe.
* ``q ...`` is an explicit querexfuzz universe.
* ``rg ...`` is a ripgrep full-text universe.
* ``q ... rg ...`` combines a querexfuzz universe with a ripgrep filter.

Ripgrep Input
-------------

The Ripgrep screen does not reinterpret the search as querexfuzz. The typed
text is the pattern passed to ripgrep. The live feedback line only checks
whether the pattern compiles as regular expression syntax before the search is
run.
