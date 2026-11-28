"""One release version, declared the same way everywhere the repository states it.

`version` in `pyproject.toml` and `lokahi.__version__` are the two declarations the
packaged artefact carries; the README status line and the newest dated CHANGELOG
entry are the two a reader trusts. They have disagreed before: the v1.3.1 entry
records `__version__` trailing at 1.1.0 and then 1.3.0 while the package released
as something else. This test makes the next drift fail on the tree instead of in a
downstream release.

A CHANGELOG entry that has not been released says so in its heading, so an
untagged draft cannot be read as a shipped date.
"""
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))

import lokahi  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as handle:
        return handle.read()


def declared_version():
    return re.search(r'^version = "([0-9]+\.[0-9]+\.[0-9]+)"', read("pyproject.toml"), re.M).group(1)


def readme_status_version():
    return re.search(r"\*\*Status:\*\* v([0-9]+\.[0-9]+\.[0-9]+)", read("README.md")).group(1)


def newest_dated_release():
    for line in read("CHANGELOG.md").splitlines():
        match = re.match(r"## v([0-9]+\.[0-9]+\.[0-9]+) \(([0-9]{4}-[0-9]{2}-[0-9]{2})\)", line)
        if match:
            return match.group(1)
    raise AssertionError("CHANGELOG.md has no dated release entry")


def test_package_and_pyproject_declare_the_same_version():
    assert lokahi.__version__ == declared_version()


def test_readme_status_names_the_declared_version():
    assert readme_status_version() == declared_version()


def test_newest_dated_changelog_entry_is_the_declared_version():
    assert newest_dated_release() == declared_version()
