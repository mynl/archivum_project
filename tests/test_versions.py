"""The About page's version collector: no Flask, no library, never raises."""

from __future__ import annotations

from archivum import __version__
from archivum import versions


def test_collect_versions_reports_archivum_and_frontend():
    out = versions.collect_versions(frontend={"htmx": "9.9.9"})

    assert out["archivum"] == __version__
    assert out["frontend"] == {"htmx": "9.9.9"}
    assert set(out["packages"]) == set(versions.PYTHON_PACKAGES)
    assert set(out["tools"]) == set(versions.EXTERNAL_TOOLS) | {"ddjvu"}


def test_missing_executable_is_not_found_rather_than_raising():
    assert versions.tool_version("archivum-no-such-tool-xyz") == versions.NOT_FOUND


def test_missing_package_is_not_installed():
    assert versions.package_versions(["archivum-no-such-package-xyz"]) == {
        "archivum-no-such-package-xyz": versions.NOT_INSTALLED
    }
