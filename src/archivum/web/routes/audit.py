"""The Audit page: DOI proposals for a subset, reviewed and committed row by row. Admin only."""

from __future__ import annotations

import datetime as dt
import json
import logging
import os

import pandas as pd
from flask import abort, make_response, render_template, request

from ... import egress
from ...cli import LibraryContext
from ...doi_audit import changesets as cs
from ...doi_audit import journal, runner
from ...doi_audit.doi import MALFORMED
from ...utilities import clean_latex
from ...doi_audit.proposals import LOCAL_CLASSES, local_proposals, subset_rows, summarize
from ...search.universe import resolve_universe_tags
from ..decorators import admin_required
from ..services.search_history import note_search
from .shared import bp

logger = logging.getLogger(__name__)

PAGE_ROWS = 50
CHANGESETS_SHOWN = 20
COUNTS_CLASS = "counts"


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


def _run_view(lib) -> dict:
    """What the run status line shows: the newest run's progress, and whether it is still going."""
    state = runner.read_state(lib.config_path)
    active = bool(state) and state["status"] == runner.RUNNING
    view = {"state": state, "active": active}
    if state:
        view["elapsed"] = runner.format_duration(runner.elapsed_seconds(state))
        openalex = (state.get("hosts") or {}).get(egress.OPENALEX) or {}
        if openalex.get("budget_limit"):
            view["budget"] = (openalex["budget_remaining"], openalex["budget_limit"])
        view["stopped_hosts"] = {h: s["stopped"] for h, s in (state.get("hosts") or {}).items() if s.get("stopped")}
    return view


def _counts_summary(lib, rows) -> dict:
    """Count the subset's references with a citation count, and those whose count is older than the refresh period."""
    cites = journal.read_cites(lib.config_path)
    cites = cites[cites["tag"].isin(set(rows["tag"]))]
    cutoff = (dt.date.today() - dt.timedelta(days=lib.config.doi_audit_refresh_days)).isoformat()
    return {"with_counts": len(cites), "stale_counts": int((cites["cites_asof"] < cutoff).sum())}


def _counts_table(lib, rows, page: int) -> dict:
    """The subset's citation counts, most cited first, one page of them."""
    cites = journal.read_cites(lib.config_path).merge(rows[["tag", "title"]], on="tag")
    cites = cites.sort_values(["cites", "tag"], ascending=[False, True])
    pages = max(1, -(-len(cites) // PAGE_ROWS))
    page = min(max(page, 1), pages)
    shown = cites.iloc[(page - 1) * PAGE_ROWS: page * PAGE_ROWS]
    return {
        "cls": COUNTS_CLASS, "total": len(cites), "page": page, "pages": pages,
        "rows": [
            {"tag": r.tag, "title": clean_latex(str(r.title or "")), "cites": int(r.cites), "asof": r.cites_asof,
             "growth": None if pd.isna(r.cites_1y) else int(r.cites_1y)}
            for r in shown.itertuples()
        ],
    }


def _plan_view(lib, rows) -> dict:
    """The confirmation shown before a citation-count run: what it would ask, how long, and at what cost."""
    plan = runner.plan_counts(lib.config_path, rows, lib.config.doi_audit_refresh_days)
    default_mode = egress.choose_mode(plan.requests)
    return {
        "plan": plan,
        "default_mode": default_mode,
        "modes": [
            (mode, label, runner.format_duration(plan.seconds(mode)))
            for mode, label in ((egress.SPEEDY, "Speedy"), (egress.POLITE, "Polite"))
        ],
        "has_key": bool(os.environ.get("OPENALEX_API_KEY")),
        "active": runner.lock_holder(lib.config_path) is not None,
    }


def _render_body(lib, raw_query: str, cls: str = "", page: int = 1, message: str = "", level: str = "success",
                 show_plan: bool = False):
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
        q=raw_query, summary={**summarize(rows), **_counts_summary(lib, rows)}, review=review, classes=LOCAL_CLASSES,
        counts=_counts_table(lib, rows, page) if cls == COUNTS_CLASS else None,
        plan=_plan_view(lib, rows) if show_plan else None, run=_run_view(lib),
        refresh_days=lib.config.doi_audit_refresh_days,
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


@bp.route('/audit/counts-plan')
@admin_required
def audit_counts_plan():
    """Show what a citation-count run over the subset would ask, before anything is sent."""
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)
    raw_query, cls, page = _form_state()
    return _render_body(lib, raw_query, cls, page, show_plan=True)


@bp.route('/audit/run', methods=['POST'])
@admin_required
def audit_run():
    """Start the citation-count run for the subset, in a thread of this process."""
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)
    raw_query, cls, page = _form_state()
    mode = request.form.get("mode", "")
    if mode not in egress.MODES:
        return _render_body(lib, raw_query, cls, page, "Choose speedy or polite.", "warning", show_plan=True)
    try:
        plan = runner.plan_counts(lib.config_path, _subset(lib, raw_query), lib.config.doi_audit_refresh_days)
    except ValueError as e:
        return _render_body(lib, raw_query, cls, page, f"Query not understood: {e}", "danger")
    if not plan.requests:
        return _render_body(lib, raw_query, cls, page, "Nothing to fetch: every DOI in the subset has a fresh result.", "info")
    try:
        runner.CountsRun(lib.config_path, plan, mode).start()
    except runner.RunActive as e:
        return _render_body(lib, raw_query, cls, page, f"Not started. {e}", "warning")
    return _render_body(lib, raw_query, cls, page,
                        f"Run started: {plan.tags:,} references in {plan.requests:,} requests, {mode}.")


@bp.route('/audit/stop', methods=['POST'])
@admin_required
def audit_stop():
    """Ask the active run to stop after the request in flight."""
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)
    raw_query, cls, page = _form_state()
    if runner.request_stop(lib.config_path):
        return _render_body(lib, raw_query, cls, page, "Stop requested; the run ends after the request in flight.", "info")
    return _render_body(lib, raw_query, cls, page, "No run is active.", "warning")


@bp.route('/audit/run-status')
@admin_required
def audit_run_status():
    """
    The run status line, polled every 5 seconds while a run is active.

    The fragment asks to be polled only while the run is going, so the polling
    ends with the run. The response that reports the end also replaces the
    summary strip, whose count cards the run has changed, and fires
    ``audit-run-ended``, on which the page shows the counts table unless a
    review is in progress.
    """
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)
    view = _run_view(lib)
    html = render_template("components/audit_run.html", run=view)
    if not view["active"]:
        try:
            rows = _subset(lib, _form_state()[0])
        except ValueError:
            return html
        html += render_template(
            "components/audit_summary.html", oob=True, refresh_days=lib.config.doi_audit_refresh_days,
            summary={**summarize(rows), **_counts_summary(lib, rows)})
    response = make_response(html)
    if not view["active"]:
        response.headers["HX-Trigger"] = "audit-run-ended"
    return response
