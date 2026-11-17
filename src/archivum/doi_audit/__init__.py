"""
DOI audit and citation counts.

Everything collected here is stored in ``<library>/doi-audit/``, apart from the
master data. The only route from there to ``ref.feather`` is
``Library.apply_field_changes``, which writes a changeset file for every commit.
"""

AUDIT_DIR_NAME = "doi-audit"
