"""The Audit page: DOI proposals for a subset, reviewed and committed row by row. Admin only."""

from __future__ import annotations

import json
import logging

from flask import abort, render_template, request

from ...cli import LibraryContext
from ...doi_audit import changesets as cs
from ...doi_audit import journal
from ...doi_audit.doi import MALFORMED
from ...doi_audit.proposals import LOCAL_CLASSES, local_proposals, subset_rows, summarize
from ...search.universe import resolve_universe_tags
from ..decorators import admin_required
from ..services.search_history import note_search
from .shared import bp

logger = logging.getLogger(__name__)

PAGE_ROWS = 50
CHANGESETS_SHOWN = 20


def _subset(lib, raw_query: str):
    """Return the references a query selects; every reference for an empty query."""
    tags = resolve_universe_tags(lib, raw_query) if raw_query else None
    return subset_rows(lib.ref_df, tags)


def _changeset_rows(lib) -> list[dict]:
    """The newest changesets, each with its counts and whether Revert is offered."""
    listed = [(p, cs.read_changeset(p)) for p in cs.list_changesets(lib.config_path)]
    reverted = {c["reverts"] for _, c in listed if c["reverts"] and c["outcome"] == cs.APPLIED}
    rows = []
    for path, content in listed[:CHANGESETS_SHOWN]:
        applied = sum(r["outcome"] == cs.APPLIED for r in content["rows"])
        rows.append({
            "stem": path.stem,
            "created": content["created"].replace("T", " ")[:19],
            "outcome": content["outcome"],
            "rows": len(content["rows"]),
            "applied": applied,
            "reverts": content["reverts"].removesuffix(".json"),
            "reverted": path.name in reverted,
            "can_revert": content["outcome"] == cs.APPLIED and applied > 0 and path.name not in reverted,
        })
    return rows


def _render_body(lib, raw_query: str, cls: str = "", page: int = 1, message: str = "", level: str = "success"):
    """
    Render everything under the query box.

    Parameters
    ----------
    lib : Library
        The open library.
    raw_query : str
        The subset query; empty is the whole library.
    cls : str, optional
        The class whose review table is shown; none when empty.
    page : int, optional
        The page of the review table, from 1.
    message, level : str, optional
        A status line and its Bootstrap level.
    """
    try:
        rows = _subset(lib, raw_query)
    except ValueError as e:
        return render_template("components/alert.html", level="danger", message=f"Query not understood: {e}")

    review = None
    if cls in LOCAL_CLASSES:
        found = local_proposals(rows, cls, journal.rejected_proposals(lib.config_path))
        pages = max(1, -(-len(found) // PAGE_ROWS))
        page = min(max(page, 1), pages)
        shown = found[(page - 1) * PAGE_ROWS: page * PAGE_ROWS]
        review = {
            "cls": cls, "label": LOCAL_CLASSES[cls], "total": len(found), "page": page, "pages": pages,
            "rows": [{"p": p, "old_json": json.dumps(p.current)} for p in shown],
            "can_commit": cls == MALFORMED,
        }
    return render_template(
        "components/audit_body.html",
        q=raw_query, summary=summarize(rows), review=review, classes=LOCAL_CLASSES,
        changesets=_changeset_rows(lib), message=message, level=level,
    )


def _form_state():
    """Return the query, class and page a review form was showing."""
    values = request.values
    try:
        page = int(values.get("page", 1))
    except ValueError:
        page = 1
    return values.get("q", "").strip(), values.get("cls", ""), page


def _ticked_rows() -> list[dict]:
    """The ticked rows of a review form: cell, reviewed value, original proposal, and the value to write."""
    rows = []
    for key in request.form.getlist("row"):
        tag, _, field = key.rpartition("|")
        rows.append({
            "tag": tag, "field": field,
            "old": json.loads(request.form.get(f"old::{key}", "null")),
            "proposed": request.form.get(f"proposed::{key}", ""),
            "new": request.form.get(f"new::{key}", "").strip(),
        })
    return rows


@bp.route('/audit')
@admin_required
def audit_page():
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)
    return render_template('audit.html')


@bp.route('/audit/subset')
@admin_required
def audit_subset():
    """The summary, actions and changesets for a query; no review table."""
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)
    raw_query, _cls, _page = _form_state()
    note_search(lib, "network", raw_query)
    return _render_body(lib, raw_query)


@bp.route('/audit/review')
@admin_required
def audit_review():
    """The same, with the review table of one class."""
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)
    return _render_body(lib, *_form_state())


@bp.route('/audit/commit', methods=['POST'])
@admin_required
def audit_commit():
    """Write the ticked rows to the library through ``Library.apply_field_changes``."""
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)
    raw_query, cls, page = _form_state()
    ticked = _ticked_rows()
    if not ticked:
        return _render_body(lib, raw_query, cls, page, "No rows ticked.", "warning")

    changes = [
        {"tag": r["tag"], "field": r["field"], "old": r["old"], "new": r["new"],
         "evidence": {"class": cls, "source": "local", "proposed": r["proposed"]}}
        for r in ticked
    ]
    try:
        path = lib.apply_field_changes(changes, f"audit-{cls or 'changes'}")
    except ValueError as e:
        return _render_body(lib, raw_query, cls, page, f"Nothing committed. {e}", "danger")
    except Exception as e:
        logger.error("Audit commit failed: %s", e, exc_info=True)
        return _render_body(lib, raw_query, cls, page, f"Commit failed, see the newest changeset. {e}", "danger")

    result = cs.read_changeset(path)
    outcomes = {(r["tag"], r["field"]): r["outcome"] for r in result["rows"]}
    journal.append_lines(lib.config_path, journal.DECISIONS, [
        {"decision": journal.ACCEPTED, "class": cls, "tag": r["tag"], "field": r["field"], "old": r["old"],
         "proposed": r["proposed"], "new": r["new"], "outcome": outcomes[(r["tag"], r["field"])],
         "changeset": path.name}
        for r in ticked
    ])
    applied = sum(o == cs.APPLIED for o in outcomes.values())
    skipped = len(outcomes) - applied
    message = f"Committed {applied} of {len(outcomes)} rows as {path.stem}."
    if skipped:
        message += f" {skipped} skipped: the value changed since review."
    return _render_body(lib, raw_query, cls, page, message, "warning" if skipped else "success")


@bp.route('/audit/reject', methods=['POST'])
@admin_required
def audit_reject():
    """Record the ticked rows as rejected, so they are not proposed again."""
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)
    raw_query, cls, page = _form_state()
    ticked = _ticked_rows()
    if not ticked:
        return _render_body(lib, raw_query, cls, page, "No rows ticked.", "warning")
    journal.append_lines(lib.config_path, journal.DECISIONS, [
        {"decision": journal.REJECTED, "class": cls, "tag": r["tag"], "field": r["field"], "old": r["old"],
         "proposed": r["proposed"]}
        for r in ticked
    ])
    return _render_body(lib, raw_query, cls, page, f"Rejected {len(ticked)} rows; they will not be proposed again.")


@bp.route('/audit/revert', methods=['POST'])
@admin_required
def audit_revert():
    """Revert one changeset, as ``archivum doi-audit-revert <changeset> -x`` does."""
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)
    raw_query, cls, page = _form_state()
    try:
        path = cs.find_changeset(lib.config_path, request.form.get("changeset", ""))
        inverse = cs.inverse_changes(cs.read_changeset(path))
        if not inverse:
            return _render_body(lib, raw_query, cls, page, f"{path.stem} applied no changes.", "warning")
        result = cs.read_changeset(lib.apply_field_changes(inverse, f"revert-{path.stem}", reverts=path.name))
    except (FileNotFoundError, ValueError) as e:
        return _render_body(lib, raw_query, cls, page, f"Nothing reverted. {e}", "danger")
    except Exception as e:
        logger.error("Audit revert failed: %s", e, exc_info=True)
        return _render_body(lib, raw_query, cls, page, f"Revert failed, see the newest changeset. {e}", "danger")

    applied = sum(r["outcome"] == cs.APPLIED for r in result["rows"])
    skipped = len(result["rows"]) - applied
    message = f"Reverted {applied} of {len(result['rows'])} cells of {path.stem}."
    if skipped:
        message += f" {skipped} left alone: edited since the commit."
    return _render_body(lib, raw_query, cls, page, message, "warning" if skipped else "success")
