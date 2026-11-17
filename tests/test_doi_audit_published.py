"""Published versions of arXiv preprints: the run, the backfill in place, and the new reference. Fake arXiv and Crossref."""

from __future__ import annotations

import json
import threading

import pandas as pd
import pytest

from archivum import egress
from archivum.doi_audit import doi as rules
from archivum.doi_audit import changesets as cs
from archivum.doi_audit import journal, runner
from archivum.doi_audit.proposals import published_counts, published_versions

TZ = "Europe/London"
H1, H2 = "a1" + "0" * 62, "b2" + "0" * 62
COLUMNS = ["tag", "type", "author", "title", "year", "journal", "volume", "number", "pages", "doi", "eprint",
           "archivePrefix", "arxivId", "url"]


def _ref(tag, title, author, year, eprint="", doi="", journal="", kind="article", **extra):
    row = {c: "" for c in COLUMNS}
    row.update(tag=tag, type=kind, title=title, author=author, year=year, eprint=eprint, doi=doi, journal=journal,
               archivePrefix="arXiv" if eprint else "", **extra)
    return row


REFS = [
    _ref("Same2019", "A Preprint Published the Same Year", "Wang, Ruodu and Zitikis, R.", "2019", "1901.00001"),
    _ref("Later2019", "A Preprint Published {L}ater", "Embrechts, Paul", "2019", "arXiv:1902.00002v3"),
    _ref("Book2018", "Lecture Notes Published as a Chapter", "Roe, R.", "2018", "1803.00003", kind="book"),
    _ref("OwnDoi2017", "A Preprint With Its Journal DOI", "Poe, P.", "2017", "1704.00004", doi="10.1/own"),
    _ref("JRef2016", "A Preprint With a Journal Reference Only", "Doe, Jane", "2016", "1605.00005"),
    _ref("None2015", "A Preprint Never Published", "Doe, Jane", "2015", "", url="https://arxiv.org/abs/1506.00006v1"),
    _ref("Odd2014", "A Preprint Whose DOI Is Another Work's", "Doe, Jane", "2014", "1407.00007"),
    _ref("Done2013", "Already Published", "Doe, Jane", "2013", "1308.00008", journal="J. Done", doi="10.1/done"),
    _ref("Plain2012", "Not on arXiv", "Doe, Jane", "2012"),
    _ref("Bare2011", "An Old Identifier Without Its Archive", "Doe, Jane", "2011", "0501001"),
]
DOCS = [{"hash": H1, "version": 0, "path": "a1/later.pdf", "name": "later.pdf", "suffix": ".pdf", "size": 1000,
         "create": pd.Timestamp("2019-02-01", tz=TZ)}]
LINKS = [{"tag": "Later2019", "hash": H1, "version": 0, "priority": 0}]
REF_COLUMNS = COLUMNS

ARXIV = {
    "1901.00001": ("A preprint published the same year", "Ruodu Wang", 2019, "10.1/same", "J. Same 1 (2019)"),
    "1902.00002": ("A preprint published later", "Paul Embrechts", 2019, "10.1/Later", None),
    "1803.00003": ("Lecture notes published as a chapter", "R. Roe", 2018, "10.1/chapter", None),
    "1704.00004": ("A preprint with its journal DOI", "P. Poe", 2017, None, None),
    "1605.00005": ("A preprint with a journal reference only", "Jane Doe", 2016, None, "Ann. Things 5, 1-9 (2017)"),
    "1506.00006": ("A preprint never published", "Jane Doe", 2015, None, None),
    "1407.00007": ("A preprint whose DOI is another work's", "Jane Doe", 2014, "10.1/odd", None),
}
CROSSREF = {
    "10.1/same": ("A Preprint Published the Same Year", "Wang", 2019, "Journal of Same", "12", "3", "101-120", "journal-article"),
    "10.1/later": ("A Preprint Published Later", "Embrechts", 2021, "Journal of Later", "7", "1", "1-30", "journal-article"),
    "10.1/chapter": ("Lecture Notes Published as a Chapter", "Roe", 2018, "A Handbook", "2", None, "55-90", "book-chapter"),
    "10.1/own": ("A Preprint with its Journal DOI", "Poe", 2017, "Own Journal", "4", "2", "9-19", "journal-article"),
    "10.1/odd": ("Fish of the North Sea", "Cod", 2014, "Fish", "1", "1", "1-2", "journal-article"),
}


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code, self._payload, self.text, self.headers = status_code, payload or {}, text, {}

    def json(self):
        return self._payload


def _atom(identifiers):
    entries = []
    for i in identifiers:
        if i not in ARXIV:
            continue
        title, author, year, doi, journal_ref = ARXIV[i]
        entries.append(
            f"<entry><id>http://arxiv.org/abs/{i}v2</id><published>{year}-03-01T00:00:00Z</published>"
            f"<title>{title}</title><author><name>{author}</name></author>"
            + (f"<arxiv:doi>{doi}</arxiv:doi>" if doi else "")
            + (f"<arxiv:journal_ref>{journal_ref}</arxiv:journal_ref>" if journal_ref else "") + "</entry>")
    return ('<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">'
            + "".join(entries) + "</feed>")


class Sources:
    def __init__(self):
        self.calls = []

    def hosts(self):
        return [url.split("/")[2] for url, _ in self.calls]

    def __call__(self, method, url, *, params, headers, timeout):
        self.calls.append((url, dict(params or {})))
        if url == "https://export.arxiv.org/api/query":
            return FakeResponse(text=_atom(params["id_list"].split(",")))
        doi = url.removeprefix("https://api.crossref.org/works/")
        if doi not in CROSSREF:
            return FakeResponse(404)
        title, family, year, container, volume, issue, pages, kind = CROSSREF[doi]
        return FakeResponse(payload={"message": {
            "DOI": doi.replace("later", "Later"), "title": [title], "author": [{"given": "A.", "family": family}],
            "issued": {"date-parts": [[year]]}, "container-title": [container], "volume": volume, "issue": issue,
            "page": pages, "type": kind}})


def _client(transport, mode=egress.SPEEDY):
    return egress.Egress(mode, contact_email="", transport=transport, sleep=lambda seconds: None)


@pytest.fixture()
def lib(tmp_library):
    library = tmp_library(REFS, DOCS, LINKS, ref_columns=REF_COLUMNS)
    library.save()
    return library


def _plan(lib):
    return runner.plan_published(lib.config_path, lib.ref_df, 30)


def _run(lib, transport=None):
    transport = transport or Sources()
    state = runner.PublishedRun(lib.config_path, _plan(lib), egress.SPEEDY, client=_client(transport)).run()
    return state, transport


def _rows(lib):
    found = published_versions(lib.ref_df, journal.latest_observations(lib.config_path),
                               journal.settled_published(lib.config_path))
    return {p.tag: p for p in found}


def _disk(lib):
    return pd.read_feather(lib.config_path / "ref.feather").set_index("tag")


# ---- identifiers ----

@pytest.mark.parametrize("values, identifier", [
    (("1901.00001",), "1901.00001"),
    (("arXiv:1304.1234v2",), "1304.1234"),
    (("math/0501001v3",), "math/0501001"),
    (("arXiv:hep-th/9711200",), "hep-th/9711200"),
    (("math.PR/0501001",), "math.PR/0501001"),
    (("https://arxiv.org/abs/2101.12345v1",), "2101.12345"),
    (("0501001",), None),
    (("0501001", "math/0501001"), "math/0501001"),
    ((None, ""), None),
])
def test_arxiv_identifiers(values, identifier):
    assert rules.arxiv_id(*values) == identifier


def test_a_url_is_read_only_when_it_is_an_arxiv_address():
    assert runner.reference_arxiv_id({"url": "https://doi.org/10.1016/0304.4076"}) is None
    assert runner.reference_arxiv_id({"url": "https://arxiv.org/abs/1506.00006v1"}) == "1506.00006"
    assert runner.reference_arxiv_id({"arxivId": "arXiv:1304.1234v2", "eprint": "9999.99999"}) == "1304.1234"


# ---- the run ----

def test_the_plan_takes_arxiv_references_with_no_journal(lib):
    plan = _plan(lib)
    assert (plan.subset, plan.not_arxiv, plan.has_journal, plan.fresh, plan.tags) == (10, 2, 1, 0, 7)
    assert set(plan.stages.values()) == {runner.ARXIV_STAGE}
    assert (plan.arxiv_requests, plan.requests, plan.requests_most) == (1, 1, 8)


def test_arxiv_is_asked_once_and_crossref_for_each_published_doi(lib):
    state, transport = _run(lib)

    assert state["status"] == runner.FINISHED and state["action"] == "published"
    assert (state["total"], state["done"], state["errors"]) == (7, 7, 0)
    assert transport.hosts().count("export.arxiv.org") == 1
    asked = transport.calls[0][1]["id_list"].split(",")
    assert sorted(asked) == sorted(ARXIV) and all("v" not in i for i in asked)
    crossref = sorted(url.rsplit("/works/", 1)[1] for url, _ in transport.calls if "crossref" in url)
    assert crossref == ["10.1/chapter", "10.1/later", "10.1/odd", "10.1/own", "10.1/same"]


def test_only_identifiers_and_dois_are_sent(lib):
    _, transport = _run(lib)
    sent = json.dumps(transport.calls)
    assert "Wang" not in sent and "Preprint" not in sent and "Same2019" not in sent


def test_classes_after_a_run(lib):
    _run(lib)
    rows = _rows(lib)

    same = rows["Same2019"]
    assert same.cls == rules.PUBLISHED_SAME_YEAR
    assert dict((name, new) for name, _old, new in same.unit) == {
        "doi": "10.1/same", "journal": "Journal of Same", "volume": "12", "number": "3", "pages": "101--120"}
    assert all(old == "" for _name, old, _new in same.unit)   # type is article already, so it is not in the unit

    later = rows["Later2019"]
    assert later.cls == rules.PUBLISHED_OTHER_YEAR and later.unit == () and later.proposed == "10.1/Later"
    for line in ("author = {Embrechts, Paul}", "title  = {{A Preprint Published {L}ater}}", "year   = {2021}",
                 "journal = {{Journal of Later}}", "doi    = {10.1/Later}"):
        assert " ".join(line.split()) in " ".join(later.bibtex.split())
    assert later.bibtex.startswith("@article{PublishedVersion,")

    assert rows["OwnDoi2017"].cls == rules.PUBLISHED_SAME_YEAR
    assert "doi" not in [name for name, _, _ in rows["OwnDoi2017"].unit]   # it has the DOI already
    assert rows["JRef2016"].cls == rules.PUBLISHED_POSSIBLE and "Ann. Things 5, 1-9 (2017)" in rows["JRef2016"].note
    assert rows["None2015"].cls == rules.NOT_PUBLISHED
    assert rows["Odd2014"].cls == rules.PUBLISHED_POSSIBLE and rows["Odd2014"].unit == ()
    assert set(rows) == {"Same2019", "Later2019", "Book2018", "OwnDoi2017", "JRef2016", "None2015", "Odd2014"}
    assert published_counts(list(rows.values())) == {
        rules.PUBLISHED_SAME_YEAR: 3, rules.PUBLISHED_OTHER_YEAR: 1, rules.PUBLISHED_POSSIBLE: 2,
        rules.NOT_PUBLISHED: 1, rules.NOT_ASKED: 0}


def test_a_book_is_never_offered_a_type_change(lib):
    _run(lib)
    book = _rows(lib)["Book2018"]
    assert book.cls == rules.PUBLISHED_SAME_YEAR
    assert "type" not in [name for name, _, _ in book.unit] and ("journal", "", "A Handbook") in book.unit


def test_a_second_run_asks_nothing(lib):
    _run(lib)
    plan = _plan(lib)
    assert (plan.fresh, plan.tags, plan.requests) == (7, 0, 0)


def test_the_references_are_not_changed_by_a_run(lib):
    before = (lib.config_path / "ref.feather").read_bytes()
    _run(lib)
    assert (lib.config_path / "ref.feather").read_bytes() == before


# ---- the new reference ----

def test_a_published_version_in_another_year_becomes_a_new_reference(lib):
    _run(lib)
    before, links_before = _disk(lib), pd.read_feather(lib.config_path / "ref-doc.feather")
    new_tag = lib.add_published_version("Later2019", _rows(lib)["Later2019"].bibtex)
    after = _disk(lib)

    assert new_tag not in before.index and new_tag.startswith("Embrechts2021")
    # the preprint's reference is identical, cell for cell
    assert after.loc["Later2019"].equals(before.loc["Later2019"])
    assert after.drop(index=new_tag).drop(columns=["added"], errors="ignore").equals(
        before.drop(columns=["added"], errors="ignore"))
    new = after.loc[new_tag]
    assert (new["year"], new["doi"], new["volume"], new["pages"]) == ("2021", "10.1/Later", "7", "1--30")
    assert new["author"] == "Embrechts, Paul" and "Journal of Later" in new["journal"]
    # both tags open the same document, and the preprint's link is as it was
    assert lib.primary_doc(new_tag)["hash"] == H1 == lib.primary_doc("Later2019")["hash"]
    links = pd.read_feather(lib.config_path / "ref-doc.feather")
    assert links[links.tag == "Later2019"].reset_index(drop=True).equals(links_before[links_before.tag == "Later2019"].reset_index(drop=True))
    assert len(links) == len(links_before) + 1


def test_a_preprint_with_no_document_still_gets_its_published_reference(lib):
    _run(lib)
    row = _rows(lib)["Later2019"]
    bibtex = row.bibtex.replace("Embrechts, Paul", "Wang, Ruodu")
    new_tag = lib.add_published_version("Same2019", bibtex)
    assert new_tag.startswith("Wang2021") and lib.primary_doc(new_tag) is None


def test_an_unknown_preprint_is_refused(lib):
    with pytest.raises(ValueError, match="not found"):
        lib.add_published_version("Nobody1900", "@article{x, title={T}}")


# ---- the page ----

@pytest.fixture()
def client(lib, monkeypatch):
    from archivum.cli import LibraryContext
    from archivum.web.app import create_app

    monkeypatch.setattr(LibraryContext, "get", classmethod(lambda cls: lib))
    monkeypatch.setattr("archivum.analytics.semantic.warm_transformer_model", lambda background=True: None)
    monkeypatch.setattr("archivum.analytics.semantic.warm_semantic_projection", lambda background=True: None)
    transport = Sources()
    real = egress.Egress
    monkeypatch.setattr(egress, "Egress", lambda mode=egress.SPEEDY, **kw: real(
        mode, contact_email="", transport=transport, sleep=lambda s: None, stop=kw.get("stop")))
    application = create_app()
    application.config.update(TESTING=True)
    test_client = application.test_client()
    test_client.transport = transport
    return test_client


def _start(client):
    html = client.post("/audit/run", data={"q": "", "action": "published", "mode": "polite"}).get_data(as_text=True)
    for thread in threading.enumerate():
        if thread.name.startswith("doi-audit-run-"):
            thread.join(10)
    return html


def _unit_form(lib, tag):
    row = _rows(lib)[tag]
    key = f"{tag}|published"
    return {"q": "", "cls": rules.PUBLISHED_SAME_YEAR, "page": "1", "row": [key], f"old::{key}": "null",
            f"proposed::{key}": row.published_doi, f"unit::{key}": json.dumps(row.unit)}


def test_the_plan_and_the_results(client, lib):
    plan = " ".join(client.get("/audit/plan?action=published").get_data(as_text=True).split())
    assert "<strong>7</strong> preprints would be asked about" in plan and "<strong>1 to 8</strong> requests" in plan
    assert "Only arXiv identifiers and DOIs are sent" in plan and client.transport.calls == []

    assert "Search for published versions started: 7 references" in _start(client)
    response = client.get("/audit/run-status")
    assert json.loads(response.headers["HX-Trigger"]) == {"audit-run-ended": {"cls": "published"}}

    html = client.get("/audit/review?cls=published").get_data(as_text=True)
    assert "Published versions, Published, same year: 3 rows" in html and "Fill in ticked" in html
    for pill in ("Published, same year 3", "Published, different year 1", "Possibly published 2", "No published version 1"):
        assert pill in html
    other = client.get(f"/audit/review?cls={rules.PUBLISHED_OTHER_YEAR}").get_data(as_text=True)
    assert "Import as a new reference" in other and "@article{PublishedVersion," in other and 'name="row"' not in other


def test_a_same_year_backfill_leaves_tag_author_and_year(client, lib):
    _start(client)
    before = _disk(lib)
    html = client.post("/audit/published/commit", data=_unit_form(lib, "Same2019")).get_data(as_text=True)
    after = _disk(lib)

    assert "Filled in 1 reference, 5 cells" in html
    row = after.loc["Same2019"]
    assert (row["doi"], row["journal"], row["volume"], row["number"], row["pages"]) == (
        "10.1/same", "Journal of Same", "12", "3", "101--120")
    for column in ("author", "year", "title", "type", "eprint"):
        assert row[column] == before.loc["Same2019", column]
    assert list(after.index) == list(before.index)
    assert after.drop(index="Same2019").equals(before.drop(index="Same2019"))
    # it now has a journal, so it is no longer a preprint to ask about
    assert "Same2019" not in _rows(lib)
    [changeset] = cs.list_changesets(lib.config_path)
    assert changeset.stem.endswith("_audit-published") and len(cs.read_changeset(changeset)["rows"]) == 5


def test_a_backfill_is_whole_or_not_at_all(client, lib):
    _start(client)
    form = _unit_form(lib, "Same2019")
    lib.apply_field_changes([("Same2019", "volume", "", "99")], "hand")
    html = client.post("/audit/published/commit", data=form).get_data(as_text=True)

    assert "Nothing committed: changed since review: Same2019." in html
    assert _disk(lib).loc["Same2019", "journal"] == "" and _disk(lib).loc["Same2019", "volume"] == "99"


def test_a_backfill_cannot_carry_a_tag_author_or_year(client, lib):
    _start(client)
    form = _unit_form(lib, "Same2019")
    key = "Same2019|published"
    form[f"unit::{key}"] = json.dumps([["year", "2019", "2021"], ["author", "Wang, Ruodu and Zitikis, R.", "X"], ["journal", "", "J"]])
    client.post("/audit/published/commit", data=form)
    row = _disk(lib).loc["Same2019"]
    assert row["year"] == "2019" and row["author"].startswith("Wang, Ruodu") and row["journal"] == "J"


def test_preview_then_import_from_the_page(client, lib):
    _start(client)
    bibtex = _rows(lib)["Later2019"].bibtex
    preview = client.post("/audit/published/preview", data={"bibtex": bibtex}).get_data(as_text=True)
    assert "The importer would add <strong>Embrechts2021" in preview and "Journal of Later" in preview
    assert "Embrechts2021" not in _disk(lib).index.tolist()

    html = client.post("/audit/published/import", data={
        "tag": "Later2019", "bibtex": bibtex, "doi": "10.1/Later", "q": "", "cls": rules.PUBLISHED_OTHER_YEAR, "page": "1",
    }).get_data(as_text=True)
    new_tag = next(t for t in _disk(lib).index if t.startswith("Embrechts2021"))
    assert f"Added {new_tag} for the published version of Later2019, which is unchanged." in html
    assert "Published, different year 0" in html and "Later2019" not in _rows(lib)
    [decision] = [d for d in journal.read_lines(lib.config_path, journal.DECISIONS)]
    assert (decision["decision"], decision["tag"], decision["new_tag"]) == (journal.PUBLISHED_IMPORTED, "Later2019", new_tag)


def test_a_rejected_published_version_is_not_proposed_again(client, lib):
    _start(client)
    key = "Later2019|published"
    html = client.post("/audit/reject", data={
        "q": "", "cls": rules.PUBLISHED_OTHER_YEAR, "page": "1", "row": key, f"old::{key}": "null",
        f"proposed::{key}": "10.1/Later"}).get_data(as_text=True)
    assert "Rejected 1 rows" in html and "Later2019" not in _rows(lib)


@pytest.mark.parametrize("path", ["/audit/published/commit", "/audit/published/preview", "/audit/published/import"])
def test_a_visitor_from_the_bridge_is_refused(client, path):
    assert client.post(path, environ_overrides={"REMOTE_ADDR": "10.8.0.1"}).status_code == 403


def test_the_run_command_finds_published_versions(lib, client, capsys):
    from click.testing import CliRunner
    from archivum import cli as cli_module

    def run(command, *args):
        result = CliRunner().invoke(getattr(cli_module, command), list(args))
        return result.output + capsys.readouterr().out

    text = run("doi_audit_run", "--published")
    assert "7 preprints in 1 to 8 requests" in text and "DRY RUN" in text and client.transport.calls == []
    assert "Published versions: none." in run("doi_audit_status")
    assert "finished" in run("doi_audit_run", "--published", "-x")
    assert ("Published versions: published, same year 3, published, different year 1, possibly published 2, "
            "no published version 1.") in run("doi_audit_status")
