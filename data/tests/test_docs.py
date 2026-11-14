"""Tests that scan the markdown docs for console paths, links and stale wording.

Three checks run over the root README, ``data/README.md``, ``data/docs/*.md`` and
``AGENTS.md``:

* every console path a doc names (an inline code span or a local console URL)
  matches a rule in the app's URL map;
* every relative link resolves to a file and, if it has an anchor, to a heading;
* no doc names a page that has been removed or uses retired wording.

The extraction and matching helpers are exercised on synthetic markdown first, so
a green run on the real docs is evidence the checks can fail.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
from flask import Flask

from console import create_app
from console.sections import SECTIONS_BY_SLUG

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parents[1] / "console" / "static"


def docs_files(root: Path) -> list[Path]:
    """The markdown docs under ``root``, failing loudly if there are none.

    An empty result would make every check below pass without scanning anything.
    """
    found = [
        path
        for path in (
            root / "README.md",
            root / "AGENTS.md",
            root / "data" / "README.md",
            root / "uselectionmaps" / "README.md",
            *sorted((root / "data" / "docs").glob("*.md")),
        )
        if path.is_file()
    ]
    assert found, f"no docs found under {root}"
    return found


DOC_FILES = docs_files(REPO_ROOT)

SECTION_SLUGS = frozenset(SECTIONS_BY_SLUG)

# First path segments that are filesystem locations, not console routes. Every
# other leading-slash path in a doc is treated as a console path.
NON_CONSOLE_FIRST_SEGMENTS = frozenset(
    {"tmp", "home", "dev", "usr", "etc", "var", "opt", "proc", "mnt", "bin"}
)

# Paths (as whole leading segments) a doc must not name any more.
REMOVED_PATH_PREFIXES = (
    ("import",),
    ("us",),
    ("models",),
    ("site", "rebuild"),
    ("holyrood", "run-model"),
    ("holyrood", "import-polls"),
    ("polls", "coverage"),
)

# Wording that belongs to the old console layout.
STALE_PHRASES = (
    re.compile(r"run us models", re.IGNORECASE),
    re.compile(r"home page", re.IGNORECASE),
    re.compile(r"site card", re.IGNORECASE),
    re.compile(r"import us polls", re.IGNORECASE),
    re.compile(r"import us results \(nyt\)", re.IGNORECASE),
)

CODE_SPAN = re.compile(r"`([^`]+)`")
CONSOLE_URL = re.compile(r"https?://(?:127\.0\.0\.1|localhost):5055(/[^\s)`>\"']*)")
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
CONVERTER = re.compile(r"^<(?:(\w+)(?:\((.*)\))?:)?(\w+)>$")
PLACEHOLDER = re.compile(r"^<[^<>]+>$")


class Finding(NamedTuple):
    """One problem found in a doc.

    Attributes:
        line: 1-based line number in the doc.
        text: The offending text as written.
        reason: Why it is a problem.
    """

    line: int
    text: str
    reason: str


def describe(doc: Path, findings: list[Finding]) -> str:
    """Format findings as ``file:line  text  reason`` lines for an assertion."""
    shown = doc.relative_to(REPO_ROOT) if doc.is_relative_to(REPO_ROOT) else doc
    return "\n".join(f"{shown}:{f.line}: {f.text}  ({f.reason})" for f in findings)


def prose_lines(text: str) -> Iterator[tuple[int, str]]:
    """Yield ``(line number, line)`` for every line outside a fenced code block."""
    fence: str | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        marker = line.lstrip()[:3]
        if marker in ("```", "~~~"):
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            continue
        if fence is None:
            yield number, line


def console_paths(text: str) -> Iterator[tuple[int, str]]:
    """Yield ``(line, path)`` for each path-shaped code span or console URL.

    A code span counts only when the whole span is one path starting with ``/``,
    so commands, repo-relative paths and prose are not candidates. Console URLs
    (``http://127.0.0.1:5055/...``) are read anywhere, fenced blocks included.
    The returned path keeps any query and fragment.
    """
    for number, line in prose_lines(text):
        for span in CODE_SPAN.findall(line):
            if span.startswith("/") and not re.search(r"\s", span):
                yield number, span
    for number, line in enumerate(text.splitlines(), start=1):
        for match in CONSOLE_URL.finditer(line):
            yield number, match.group(1).rstrip(".,;:")


def path_segments(raw: str) -> list[str]:
    """Split a doc path into segments, dropping the query, fragment and slashes."""
    path = re.split(r"[?#]", raw, maxsplit=1)[0]
    return [segment for segment in path.split("/") if segment]


def rule_segments(rule: str) -> list[str]:
    """Split a URL-map rule template into segments."""
    return [segment for segment in rule.split("/") if segment]


def segment_matches(doc_segment: str, rule_segment: str, index: int = 0) -> bool:
    """Whether one doc segment fits one rule segment.

    A ``<placeholder>`` in the doc fits a rule segment that is a converter or, as
    the first segment, a section slug (``/<section>/outputs`` stands for each
    section's own rule); it fits no other literal, so ``/<section>/run-model``
    cannot hide behind ``/runs/<run_id>``. A converter in the rule fits any single
    literal, except ``any(a,b)``, which fits only its options, and ``int``, which
    fits only digits.

    Args:
        doc_segment: The segment as written in the doc.
        rule_segment: The rule template's segment.
        index: Position of the segment in the path.
    """
    converter = CONVERTER.match(rule_segment)
    if PLACEHOLDER.match(doc_segment):
        return converter is not None or (index == 0 and rule_segment in SECTION_SLUGS)
    if converter is None:
        return doc_segment == rule_segment
    if converter.group(1) == "any" and converter.group(2):
        return doc_segment in converter.group(2).split(",")
    if converter.group(1) == "int":
        return doc_segment.isdigit()
    return True


def matches_rule(raw: str, rule: str) -> bool:
    """Whether a doc path (query and fragment ignored) matches a rule template."""
    doc = path_segments(raw)
    template = rule_segments(rule)
    for index, rule_segment in enumerate(template):
        converter = CONVERTER.match(rule_segment)
        if converter and converter.group(1) == "path":
            return len(doc) > index
        if index >= len(doc) or not segment_matches(doc[index], rule_segment, index):
            return False
    return len(doc) == len(template)


def unmatched_console_paths(
    text: str, rules: list[str], static_dir: Path = STATIC_DIR
) -> list[Finding]:
    """Console paths in ``text`` that no rule answers.

    Every path is a console path except the root and those under a filesystem
    location (``/tmp/x``, ``/home/...``; see ``NON_CONSOLE_FIRST_SEGMENTS``), so a
    misspelt first segment is flagged. A literal ``/static/...`` path must also
    exist as a file under ``static_dir``.
    """
    findings: list[Finding] = []
    for number, raw in console_paths(text):
        segments = path_segments(raw)
        if not segments or segments[0] in NON_CONSOLE_FIRST_SEGMENTS:
            continue
        if not any(matches_rule(raw, rule) for rule in rules):
            findings.append(Finding(number, raw, "no URL rule matches"))
        elif segments[0] == "static" and not any(
            PLACEHOLDER.match(s) for s in segments
        ):
            if not static_dir.joinpath(*segments[1:]).is_file():
                findings.append(Finding(number, raw, "no such static file"))
    return findings


def removed_console_paths(text: str) -> list[Finding]:
    """Paths naming a removed console page, matched on whole leading segments."""
    findings: list[Finding] = []
    for number, raw in console_paths(text):
        segments = tuple(path_segments(raw))
        for prefix in REMOVED_PATH_PREFIXES:
            if segments[: len(prefix)] == prefix:
                findings.append(Finding(number, raw, "removed console page"))
                break
    return findings


def stale_phrases(text: str) -> list[Finding]:
    """Lines containing retired wording."""
    findings: list[Finding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for phrase in STALE_PHRASES:
            match = phrase.search(line)
            if match:
                findings.append(Finding(number, match.group(0), "stale wording"))
    return findings


def github_slug(heading: str) -> str:
    """The anchor GitHub gives a heading.

    Lowercases, drops everything but word characters, spaces and hyphens
    (backticks, brackets, slashes, ``&``), then turns spaces into hyphens. Link
    syntax in the heading keeps only its text.
    """
    heading = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    heading = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return heading.replace(" ", "-")


def heading_anchors(text: str) -> set[str]:
    """Anchors for every heading outside fenced blocks; repeats get ``-1``, ``-2``."""
    seen: dict[str, int] = {}
    anchors: set[str] = set()
    for _, line in prose_lines(text):
        match = HEADING.match(line)
        if not match:
            continue
        slug = github_slug(match.group(1))
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        anchors.add(slug if count == 0 else f"{slug}-{count}")
    return anchors


def broken_links(text: str, doc: Path) -> list[Finding]:
    """Relative links in ``text`` (a doc at ``doc``) whose file or anchor is missing.

    Absolute URLs (``http``, ``https``, ``mailto``), links inside fenced blocks and
    links inside inline code are ignored.
    """
    own_anchors = heading_anchors(text)
    findings: list[Finding] = []
    for number, line in prose_lines(text):
        for target in LINK.findall(CODE_SPAN.sub("", line)):
            if re.match(r"(?:https?|mailto):", target):
                continue
            file_part, _, anchor = target.partition("#")
            file_part = file_part.split("?", 1)[0]
            if not file_part:
                if anchor not in own_anchors:
                    findings.append(Finding(number, target, "no such heading here"))
                continue
            resolved = (doc.parent / file_part).resolve()
            if not resolved.exists():
                findings.append(Finding(number, target, "no such file"))
            elif anchor and resolved.suffix == ".md":
                anchors = heading_anchors(resolved.read_text(encoding="utf-8"))
                if anchor not in anchors:
                    findings.append(Finding(number, target, "no such heading"))
    return findings


@pytest.fixture(scope="module")
def rules() -> list[str]:
    """Every rule template in the console's URL map."""
    app: Flask = create_app()
    return [rule.rule for rule in app.url_map.iter_rules()]


# --- helpers prove themselves on synthetic markdown -------------------------

SYNTHETIC_RULES = [
    "/",
    "/runs",
    "/runs/<run_id>",
    "/polls",
    "/polls/<int:poll_id>",
    "/<any(westminster,holyrood):section>/polls",
    "/westminster/outputs",
    "/westminster/outputs/<int:election_id>",
    "/<any(house,president):chamber>/model",
    "/<any(house,senate):chamber>/results",
    "/holyrood/outputs/<int:election_id>",
    "/westminster/polls/import",
    "/static/<path:filename>",
]


def test_a_valid_console_path_is_accepted() -> None:
    text = (
        "See `/runs?section=holyrood`, `/runs/<run_id>`, `/house/results` and\n"
        "[x](http://127.0.0.1:5055/holyrood/outputs/12) or "
        "`/westminster/polls/import`.\n"
        "Not console: `/tmp/x`, `/home/me/y`, `/dev/null`, `importers/foo.py`.\n"
    )
    assert unmatched_console_paths(text, SYNTHETIC_RULES) == []


def test_an_unknown_console_path_is_flagged() -> None:
    text = "ok\nOpen `/runs/a/b` or `/senate/results` or `/import`.\n"
    flagged = unmatched_console_paths(text, SYNTHETIC_RULES)
    assert [(f.line, f.text) for f in flagged] == [
        (2, "/runs/a/b"),
        (2, "/import"),
    ]
    # Only the house and senate have results rules.
    assert [
        f.text for f in unmatched_console_paths("`/president/results`", SYNTHETIC_RULES)
    ] == ["/president/results"]


@pytest.mark.parametrize(
    "path",
    [
        "/polls/coverage",
        "/by-elections",
        "/westmnster/polls",
        "/presidnet/model",
        "/<section>/run-model",
        "/westminster/outputs/foo",
    ],
)
def test_misspelt_or_unrouted_paths_are_flagged(path: str) -> None:
    flagged = unmatched_console_paths(f"`{path}`", SYNTHETIC_RULES)
    assert [f.text for f in flagged] == [path]


@pytest.mark.parametrize(
    "path",
    [
        "/<section>/polls",
        "/<section>/outputs",
        "/westminster/outputs/12",
        "/polls/7",
        "/president/model",
    ],
)
def test_routed_paths_are_accepted(path: str) -> None:
    assert unmatched_console_paths(f"`{path}`", SYNTHETIC_RULES) == []


def test_filesystem_paths_are_not_console_paths() -> None:
    text = "`/tmp/x` `/home/x` `/dev/null` `/usr/bin/env` `/`"
    assert unmatched_console_paths(text, SYNTHETIC_RULES) == []


def test_the_retired_coverage_page_is_a_removed_path() -> None:
    assert [f.text for f in removed_console_paths("`/polls/coverage`")] == [
        "/polls/coverage"
    ]
    assert removed_console_paths("`/polls/7` `/westminster/polls/coverage`") == []


def test_stale_page_names_are_flagged() -> None:
    text = "the Site card, Import US Polls and Import US Results (NYT) buttons"
    assert [f.text for f in stale_phrases(text)] == [
        "Site card",
        "Import US Polls",
        "Import US Results (NYT)",
    ]


def test_a_converter_restricted_to_options_rejects_other_values() -> None:
    rule = "/<any(house,senate):chamber>/results"
    assert matches_rule("/senate/results", rule)
    assert not matches_rule("/president/results", rule)
    assert matches_rule("/<chamber>/results", rule)


def test_a_static_path_must_exist_on_disk(tmp_path: Path) -> None:
    (tmp_path / "app.css").write_text("")
    text = "`/static/app.css` and `/static/gone.css`"
    flagged = unmatched_console_paths(text, SYNTHETIC_RULES, static_dir=tmp_path)
    assert [f.text for f in flagged] == ["/static/gone.css"]


def test_removed_paths_match_whole_leading_segments_only() -> None:
    text = (
        "`/import` `/us/results` `/site/rebuild` `/models/x` `/holyrood/run-model`\n"
        "`/holyrood/import-polls` http://127.0.0.1:5055/import?x=1\n"
        "fine: `/westminster/polls/import` `importers/x.py` `/importers/x` "
        "`/site/rebuild-database` `/holyrood/model`\n"
    )
    flagged = [f.text for f in removed_console_paths(text)]
    assert flagged == [
        "/import",
        "/us/results",
        "/site/rebuild",
        "/models/x",
        "/holyrood/run-model",
        "/holyrood/import-polls",
        "/import?x=1",
    ]


def test_stale_wording_is_flagged_case_insensitively() -> None:
    text = "ok\nPress Run US Models on the home page.\n"
    assert [(f.line, f.text) for f in stale_phrases(text)] == [
        (2, "Run US Models"),
        (2, "home page"),
    ]


def test_slugs_follow_github_rules() -> None:
    assert github_slug("Review queue (`/us/import`)") == "review-queue-usimport"
    assert github_slug("Polls & forecasts") == "polls--forecasts"
    assert github_slug("Run local server") == "run-local-server"
    assert github_slug("The [link](x.md) here") == "the-link-here"


def test_repeated_headings_get_numbered_anchors() -> None:
    assert heading_anchors("# A\n## A\n## A\n") == {"a", "a-1", "a-2"}


def test_broken_links_are_flagged_and_good_ones_pass(tmp_path: Path) -> None:
    (tmp_path / "other.md").write_text("# Other\n\n## Part two\n", encoding="utf-8")
    doc = tmp_path / "doc.md"
    text = (
        "# Doc\n"
        "[ok](other.md) [ok2](other.md#part-two) [ok3](#doc) [web](https://x.y/z)\n"
        "[mail](mailto:a@b.c) `[code](nope.md)`\n"
        "[missing](gone.md) [badanchor](other.md#nope) [badself](#nope)\n"
        "```\n"
        "[fenced](also-gone.md)\n"
        "```\n"
    )
    flagged = broken_links(text, doc)
    assert [(f.line, f.text) for f in flagged] == [
        (4, "gone.md"),
        (4, "other.md#nope"),
        (4, "#nope"),
    ]


def test_fenced_blocks_are_ignored_for_paths_and_headings() -> None:
    text = "```\n# not a heading\n`/import`\n```\n# real\n"
    assert heading_anchors(text) == {"real"}
    assert list(console_paths(text)) == []


# --- the real docs ----------------------------------------------------------


def test_the_doc_list_is_not_empty_and_names_the_key_docs() -> None:
    names = {doc.relative_to(REPO_ROOT).as_posix() for doc in DOC_FILES}
    assert {"README.md", "data/README.md", "data/docs/console.md"} <= names
    assert "uselectionmaps/README.md" in names


def test_an_empty_docs_folder_makes_the_scan_fail(tmp_path: Path) -> None:
    with pytest.raises(AssertionError, match="no docs found"):
        docs_files(tmp_path)


def test_the_real_docs_yield_console_paths_and_links(rules: list[str]) -> None:
    texts = [doc.read_text(encoding="utf-8") for doc in DOC_FILES]
    assert sum(len(list(console_paths(text))) for text in texts) > 0
    assert sum(len(LINK.findall(text)) for text in texts) > 0


def doc_id(doc: Path) -> str:
    """Short test id for a doc file."""
    return str(doc.relative_to(REPO_ROOT))


@pytest.mark.parametrize("doc", DOC_FILES, ids=doc_id)
def test_console_paths_named_in_the_docs_exist(doc: Path, rules: list[str]) -> None:
    findings = unmatched_console_paths(doc.read_text(encoding="utf-8"), rules)
    assert not findings, "\n" + describe(doc, findings)


@pytest.mark.parametrize("doc", DOC_FILES, ids=doc_id)
def test_relative_links_in_the_docs_resolve(doc: Path) -> None:
    findings = broken_links(doc.read_text(encoding="utf-8"), doc)
    assert not findings, "\n" + describe(doc, findings)


@pytest.mark.parametrize("doc", DOC_FILES, ids=doc_id)
def test_the_docs_name_no_removed_page_or_stale_wording(doc: Path) -> None:
    text = doc.read_text(encoding="utf-8")
    findings = removed_console_paths(text) + stale_phrases(text)
    assert not findings, "\n" + describe(doc, findings)
