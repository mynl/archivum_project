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
from ...doi_audit.doi import (
    MALFORMED, NOT_CHECKED, NOT_SEARCHED, PUBLISHED_OTHER_YEAR, PUBLISHED_SAME_YEAR, WORK_NO_DOI, normalize_doi,
)
from ...doi_audit.proposals import (
    BACKFILL_FIELDS, CHECK_CLASSES, CHECK_COMMIT_LISTS, LOCAL_CLASSES, PUBLISHED_CLASSES, SEARCH_CLASSES,
    SEARCH_COMMIT_CLASSES, check_counts, check_rows, checked_dois, local_proposals, published_counts,
    published_versions, search_counts, searched_references, subset_rows, summarize,
)
from ...utilities import clean_latex
from ...search.universe import resolve_universe_tags
from ..decorators import admin_required
from ..services.search_history import note_search
from .shared import bp

logger = logging.getLogger(__name__)

PAGE_ROWS = 50
CHANGESETS_SHOWN = 20
COUNTS_CLASS = "counts"
CHECK_CLASS = "check"
SEARCH_CLASS = "search"
PUBLISHED_CLASS = "published"
COUNTS_ACTION, CHECK_ACTION, SEARCH_ACTION, PUBLISHED_ACTION = "counts", "check", "search", "published"
# what the page shows when a run of each kind ends
RESULT_CLASS = {COUNTS_ACTION: COUNTS_CLASS, CHECK_ACTION: CHECK_CLASS, SEARCH_ACTION: SEARCH_CLASS,
                PUBLISHED_ACTION: PUBLISHED_CLASS}


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
    cites = journal.current_cites(lib.config_path, rows)
    cites = cites[cites["tag"].isin(set(rows["tag"]))]
    cutoff = (dt.date.today() - dt.timedelta(days=lib.config.doi_audit_refresh_days)).isoformat()
    return {"with_counts": len(cites), "stale_counts": int((cites["cites_asof"] < cutoff).sum())}


def _counts_table(lib, rows, page: int) -> dict:
    """The subset's citation counts, most cited first, one page of them."""
    cites = journal.current_cites(lib.config_path, rows).merge(rows[["tag", "title"]], on="tag")
    cites = cites.sort_values(["cites", "tag"], ascending=[False, True])
    pages = max(1, -(-len(cites) // PAGE_ROWS))
    page = min(max(page, 1), pages)
    shown = cites.iloc[(page - 1) * PAGE_ROWS: page * PAGE_ROWS]
    return {
        "cls": COUNTS_CLASS, "total": len(cites), "page": page, "pages": pages,
        "has_second": bool(cites["cites_s2"].notna().any()),
        "rows": [
            {"tag": r.tag, "title": clean_latex(str(r.title or "")), "cites": int(r.cites), "asof": r.cites_asof,
             "second": None if pd.isna(r.cites_s2) else int(r.cites_s2)}
            for r in shown.itertuples()
        ],
    }


def _plan_view(lib, rows, action: str) -> dict:
    """
    The confirmation shown before a run: what it would ask, how long, and at what cost.

    Parameters
    ----------
    lib : Library
        The open library.
    rows : pandas.DataFrame
        The references of the subset.
    action : str
        ``counts``, ``check``, ``search`` or ``published``.
    """
    days = lib.config.doi_audit_refresh_days
    if action == PUBLISHED_ACTION:
        plan = runner.plan_published(lib.config_path, rows, days)
        most = plan.requests_most
        durations = {mode: (plan.seconds(mode), plan.seconds(mode, most)) for mode in egress.MODES}
    elif action == SEARCH_ACTION:
        plan = runner.plan_search(lib.config_path, rows, lib.config.doi_audit_search_days)
        most = plan.requests_most
        durations = {mode: (plan.seconds(mode), plan.seconds(mode, most)) for mode in egress.MODES}
    elif action == CHECK_ACTION:
        plan = runner.plan_check(lib.config_path, rows, days)
        most = plan.requests_most
        durations = {mode: (plan.seconds(mode), plan.seconds(mode, most)) for mode in egress.MODES}
    else:
        plan = runner.plan_counts(lib.config_path, rows, days)
        most = plan.requests
        durations = {mode: (plan.seconds(mode), plan.seconds(mode)) for mode in egress.MODES}
        # what ticking the Semantic Scholar box would add
        second = runner.plan_counts(lib.config_path, rows, days, second=True)
        second_view = {"dois": len(second.second), "requests": len(second.second_batches),
                       "has_key": bool(os.environ.get("SEMANTIC_SCHOLAR_API_KEY"))}

    def span(low, high):
        low, high = runner.format_duration(low), runner.format_duration(high)
        return low if low == high else f"{low} to {high}"

    return {
        "action": action,
        "plan": plan,
        "second": second_view if action == COUNTS_ACTION else None,
        "requests_most": most,
        # the default follows the most the run could need, so a long check is polite
        "default_mode": egress.choose_mode(most),
        "modes": [(mode, label, span(*durations[mode])) for mode, label in ((egress.SPEEDY, "Speedy"), (egress.POLITE, "Polite"))],
        "has_key": bool(os.environ.get("OPENALEX_API_KEY")),
        "active": runner.lock_holder(lib.config_path) is not None,
    }


def _checked(lib, rows) -> list:
    """Every usable DOI of the subset with its class from the journal, rejected rows left out."""
    return checked_dois(rows, lib.ref_df, journal.latest_observations(lib.config_path),
                        journal.rejected_proposals(lib.config_path))


def _searched(lib, rows) -> list:
    """Every reference of the subset with no DOI, with its class from the searches in the journal."""
    return searched_references(rows, journal.latest_observations(lib.config_path),
                               journal.rejected_proposals(lib.config_path), journal.accepted_works(lib.config_path))


def _published(lib, rows) -> list:
    """Every preprint of the subset, with what is known of its published version."""
    return published_versions(rows, journal.latest_observations(lib.config_path),
                              journal.settled_published(lib.config_path))


def _summary(lib, rows, checked=None, searched=None) -> dict:
    """The counts of the summary strip: the local ones, the citation counts, the DOIs checked and searched for."""
    checked = _checked(lib, rows) if checked is None else checked
    searched = _searched(lib, rows) if searched is None else searched
    return {**summarize(rows), **_counts_summary(lib, rows),
            "checked": sum(1 for p in checked if p.cls != NOT_CHECKED),
            "searched": sum(1 for p in searched if p.cls != NOT_SEARCHED)}


def _page_of(found: list, page: int) -> tuple[list, int, int]:
    """Return one page of ``found``, the page number within range, and the number of pages."""
    pages = max(1, -(-len(found) // PAGE_ROWS))
    page = min(max(page, 1), pages)
    return found[(page - 1) * PAGE_ROWS: page * PAGE_ROWS], page, pages


def _changeset_view(lib, name: str) -> dict | None:
    """The content of one changeset for display; ``None`` when there is no such changeset."""
    try:
        path = cs.find_changeset(lib.config_path, name)
    except FileNotFoundError:
        return None
    content = cs.read_changeset(path)
    return {
        "stem": path.stem,
        "created": content["created"].replace("T", " ")[:19],
        "outcome": content["outcome"],
        "error": content.get("error", ""),
        "reverts": content["reverts"].removesuffix(".json"),
        "rows": content["rows"],
    }


def _render_body(lib, raw_query: str, cls: str = "", page: int = 1, message: str = "", level: str = "success",
                 show_plan: str = "", view_changeset: str = ""):
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
    show_plan : str, optional
        ``counts`` or ``check`` to show the confirmation for that run.
    view_changeset : str, optional
        The changeset whose rows are listed.
    """
    try:
        rows = _subset(lib, raw_query)
    except ValueError as e:
        return render_template("components/alert.html", level="danger", message=f"Query not understood: {e}")

    checked, searched = _checked(lib, rows), _searched(lib, rows)
    review = None
    if cls in LOCAL_CLASSES:
        found = local_proposals(rows, cls, journal.rejected_proposals(lib.config_path))
        shown, page, pages = _page_of(found, page)
        review = {
            "kind": "local", "cls": cls, "label": LOCAL_CLASSES[cls], "total": len(found), "page": page, "pages": pages,
            "rows": [{"p": p, "old_json": json.dumps(p.current)} for p in shown],
            "can_commit": cls == MALFORMED,
        }
    elif cls == CHECK_CLASS or cls in CHECK_CLASSES:
        counts = check_counts(checked)
        if cls == CHECK_CLASS:
            # open on the first class with something in it
            cls = next((c for c in CHECK_CLASSES if counts[c]), next(iter(CHECK_CLASSES)))
        shown, page, pages = _page_of(check_rows(checked, cls), page)
        review = {
            "kind": "check", "cls": cls, "label": CHECK_CLASSES[cls], "total": counts[cls], "page": page, "pages": pages,
            "rows": [{"p": p, "old_json": json.dumps(p.current)} for p in shown],
            "can_commit": cls in CHECK_COMMIT_LISTS,
            "pills": [(c, label, counts[c]) for c, label in CHECK_CLASSES.items()],
        }
    elif cls == SEARCH_CLASS or cls in SEARCH_CLASSES:
        counts = search_counts(searched)
        if cls == SEARCH_CLASS:
            cls = next((c for c in SEARCH_CLASSES if counts[c]), next(iter(SEARCH_CLASSES)))
        shown, page, pages = _page_of([p for p in searched if p.cls == cls], page)
        review = {
            "kind": "search", "cls": cls, "label": SEARCH_CLASSES[cls], "total": counts[cls], "page": page, "pages": pages,
            "rows": [{"p": p, "old_json": json.dumps(p.current)} for p in shown],
            "can_commit": cls in SEARCH_COMMIT_CLASSES,
            "can_accept_work": cls == WORK_NO_DOI,
            "pills": [(c, label, counts[c]) for c, label in SEARCH_CLASSES.items()],
        }
    elif cls == PUBLISHED_CLASS or cls in PUBLISHED_CLASSES:
        published = _published(lib, rows)
        counts = published_counts(published)
        if cls == PUBLISHED_CLASS:
            cls = next((c for c in PUBLISHED_CLASSES if counts[c]), next(iter(PUBLISHED_CLASSES)))
        shown, page, pages = _page_of([p for p in published if p.cls == cls], page)
        review = {
            "kind": "published", "cls": cls, "label": PUBLISHED_CLASSES[cls], "total": counts[cls], "page": page,
            "pages": pages,
            "rows": [{"p": p, "old_json": "null", "unit_json": json.dumps(p.unit)} for p in shown],
            "can_commit": False, "can_backfill": cls == PUBLISHED_SAME_YEAR, "can_import": cls == PUBLISHED_OTHER_YEAR,
            "pills": [(c, label, counts[c]) for c, label in PUBLISHED_CLASSES.items()],
        }
    return render_template(
        "components/audit_body.html",
        q=raw_query, summary=_summary(lib, rows, checked, searched), review=review,
        counts=_counts_table(lib, rows, page) if cls == COUNTS_CLASS else None,
        plan=_plan_view(lib, rows, show_plan) if show_plan else None, run=_run_view(lib),
        refresh_days=lib.config.doi_audit_refresh_days, search_days=lib.config.doi_audit_search_days,
        changesets=_changeset_rows(lib), changeset_view=_changeset_view(lib, view_changeset) if view_changeset else None,
        message=message, level=level,
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
    for r in ticked:
        # a DOI pasted as an address or with a prefix is stored bare
        if r["field"] == "doi" and r["new"]:
            r["new"] = normalize_doi(r["new"])
    if cls in SEARCH_CLASSES:
        # here an empty box is a DOI not yet chosen, never a request to clear one
        empty = [r["tag"] for r in ticked if not r["new"]]
        if empty:
            return _render_body(lib, raw_query, cls, page,
                                f"Nothing committed. No DOI given for {', '.join(empty)}.", "warning")

    changes = [
        {"tag": r["tag"], "field": r["field"], "old": r["old"], "new": r["new"],
         "evidence": {"class": cls, "proposed": r["proposed"]}}
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
         "proposed": r["proposed"],
         # a rejected work with no DOI is also withdrawn if it had been accepted
         **({"openalex_id": r["proposed"]} if r["field"] == "work" else {})}
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


def _action() -> str:
    """The run a request is about: ``check``, ``search``, ``published``, or ``counts`` by default."""
    action = request.values.get("action")
    return action if action in (CHECK_ACTION, SEARCH_ACTION, PUBLISHED_ACTION) else COUNTS_ACTION


@bp.route('/audit/plan')
@admin_required
def audit_plan():
    """Show what a run over the subset would ask, before anything is sent."""
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)
    raw_query, cls, page = _form_state()
    return _render_body(lib, raw_query, cls, page, show_plan=_action())


@bp.route('/audit/run', methods=['POST'])
@admin_required
def audit_run():
    """Start a citation-count run or a DOI check for the subset, in a thread of this process."""
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)
    raw_query, cls, page = _form_state()
    action, mode = _action(), request.form.get("mode", "")
    if mode not in egress.MODES:
        return _render_body(lib, raw_query, cls, page, "Choose speedy or polite.", "warning", show_plan=action)
    days = lib.config.doi_audit_refresh_days
    try:
        rows = _subset(lib, raw_query)
    except ValueError as e:
        return _render_body(lib, raw_query, cls, page, f"Query not understood: {e}", "danger")
    if action == PUBLISHED_ACTION:
        plan = runner.plan_published(lib.config_path, rows, days)
        make, what = runner.PublishedRun, "Search for published versions"
    elif action == SEARCH_ACTION:
        plan = runner.plan_search(lib.config_path, rows, lib.config.doi_audit_search_days)
        make, what = runner.SearchRun, "Search"
    elif action == CHECK_ACTION:
        plan = runner.plan_check(lib.config_path, rows, days)
        make, what = runner.CheckRun, "DOI check"
    else:
        plan = runner.plan_counts(lib.config_path, rows, days, second=request.form.get("second") == "1")
        make, what = runner.CountsRun, "Run"
    if not plan.requests:
        return _render_body(lib, raw_query, cls, page, "Nothing to ask: everything in the subset has a fresh result.", "info")
    try:
        make(lib.config_path, plan, mode).start()
    except runner.RunActive as e:
        return _render_body(lib, raw_query, cls, page, f"Not started. {e}", "warning")
    return _render_body(lib, raw_query, cls, page,
                        f"{what} started: {plan.tags:,} references, {plan.requests:,} requests or more, {mode}."
                        if action in (CHECK_ACTION, SEARCH_ACTION, PUBLISHED_ACTION) else
                        f"{what} started: {plan.tags:,} references in {plan.requests:,} requests, {mode}.")


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
            search_days=lib.config.doi_audit_search_days, summary=_summary(lib, rows))
    response = make_response(html)
    if not view["active"]:
        # the page then shows what the run produced: the counts table, or the check results
        action = (view["state"] or {}).get("action", COUNTS_ACTION)
        response.headers["HX-Trigger"] = json.dumps({"audit-run-ended": {"cls": RESULT_CLASS.get(action, COUNTS_CLASS)}})
    return response


@bp.route('/audit/changeset')
@admin_required
def audit_changeset():
    """Show the rows of one changeset: each cell's old value, new value and outcome."""
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)
    raw_query, cls, page = _form_state()
    name = request.args.get("changeset", "")
    if _changeset_view(lib, name) is None:
        return _render_body(lib, raw_query, cls, page, f"No changeset {name!r}.", "warning")
    return _render_body(lib, raw_query, cls, page, view_changeset=name)


@bp.route('/audit/accept-work', methods=['POST'])
@admin_required
def audit_accept_work():
    """
    Accept the ticked OpenAlex works, which have no DOI, as the works of their references.

    Nothing is written to the references. The acceptance goes to
    ``decisions.jsonl``, and from then on a citation-count run asks OpenAlex
    for the work's count by its id.
    """
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)
    raw_query, cls, page = _form_state()
    ticked = [r for r in _ticked_rows() if r["field"] == "work" and r["proposed"]]
    if not ticked:
        return _render_body(lib, raw_query, cls, page, "No rows ticked.", "warning")
    journal.append_lines(lib.config_path, journal.DECISIONS, [
        {"decision": journal.WORK_ACCEPTED, "class": cls, "tag": r["tag"], "openalex_id": r["proposed"]} for r in ticked
    ])
    return _render_body(lib, raw_query, cls, page,
                        f"Accepted {len(ticked)} works. Fetch citation counts collects their counts.")


@bp.route('/audit/published/commit', methods=['POST'])
@admin_required
def audit_published_commit():
    """
    Fill in the ticked preprints from their published versions of the same year.

    Each ticked row is a unit of up to six cells (DOI, type, journal, volume,
    number, pages), written whole or not at all: a unit with any cell that no
    longer holds its reviewed value is left out and reported. The tag, author
    and year are not among the cells.
    """
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)
    raw_query, cls, page = _form_state()
    ticked = request.form.getlist("row")
    if not ticked:
        return _render_body(lib, raw_query, cls, page, "No rows ticked.", "warning")

    current = lib.ref_df.set_index("tag")
    changes, stale = [], []
    for key in ticked:
        tag = key.rpartition("|")[0]
        unit = [cell for cell in json.loads(request.form.get(f"unit::{key}", "[]")) if cell[0] in BACKFILL_FIELDS]
        if tag not in current.index or any(cs.cell_value(current.at[tag, name]) != old for name, old, _new in unit):
            stale.append(tag)
            continue
        changes += [{"tag": tag, "field": name, "old": old, "new": new,
                     "evidence": {"class": cls, "published_doi": request.form.get(f"proposed::{key}", "")}}
                    for name, old, new in unit]
    if not changes:
        return _render_body(lib, raw_query, cls, page,
                            f"Nothing committed: changed since review: {', '.join(stale)}.", "warning")
    try:
        path = lib.apply_field_changes(changes, "audit-published")
    except ValueError as e:
        return _render_body(lib, raw_query, cls, page, f"Nothing committed. {e}", "danger")
    except Exception as e:
        logger.error("Audit published commit failed: %s", e, exc_info=True)
        return _render_body(lib, raw_query, cls, page, f"Commit failed, see the newest changeset. {e}", "danger")

    result = cs.read_changeset(path)
    applied = sum(r["outcome"] == cs.APPLIED for r in result["rows"])
    done = len(ticked) - len(stale)
    message = f"Filled in {done} {'reference' if done == 1 else 'references'}, {applied} cells, as {path.stem}."
    if stale:
        message += f" Left out, changed since review: {', '.join(stale)}."
    return _render_body(lib, raw_query, cls, page, message, "warning" if stale else "success")


@bp.route('/audit/published/preview', methods=['POST'])
@admin_required
def audit_published_preview():
    """Show what the importer would add for a published version, without adding it."""
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)
    bibtex = request.form.get("bibtex", "").strip()
    if not bibtex:
        return render_template("components/alert.html", level="warning", classes="py-2 small mb-0", message="The entry is empty.")
    try:
        preview = lib.preview_staged_document_import(bibtex)
    except Exception as e:
        logger.error("Published version preview failed: %s", e, exc_info=True)
        return render_template("components/alert.html", level="danger", classes="py-2 small mb-0", message=f"Preview failed: {e}")
    return render_template("components/audit_published_preview.html", preview=preview)


@bp.route('/audit/published/import', methods=['POST'])
@admin_required
def audit_published_import():
    """
    Add a published version as a new reference beside its preprint.

    The preprint's reference is left as it is. The new reference is made by
    the importer, which allocates its tag, and is linked to the preprint's
    primary document. The pair is recorded in ``decisions.jsonl``, after
    which the published version is not proposed for the preprint again.
    """
    lib = LibraryContext.get()
    if lib.is_empty: abort(404)
    raw_query, cls, page = _form_state()
    tag, bibtex = request.form.get("tag", ""), request.form.get("bibtex", "").strip()
    doi = request.form.get("doi", "")
    if not bibtex:
        return _render_body(lib, raw_query, cls, page, "Nothing imported: the entry is empty.", "warning")
    try:
        new_tag = lib.add_published_version(tag, bibtex)
    except ValueError as e:
        return _render_body(lib, raw_query, cls, page, f"Nothing imported. {e}", "danger")
    except Exception as e:
        logger.error("Published version import failed: %s", e, exc_info=True)
        return _render_body(lib, raw_query, cls, page, f"Import failed: {e}", "danger")
    journal.append_lines(lib.config_path, journal.DECISIONS, [
        {"decision": journal.PUBLISHED_IMPORTED, "class": cls, "tag": tag, "new_tag": new_tag, "doi": doi}])
    LibraryContext.refresh()
    return _render_body(lib, raw_query, cls, page,
                        f"Added {new_tag} for the published version of {tag}, which is unchanged. Both open the same document.")
