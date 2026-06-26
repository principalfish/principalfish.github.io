"""Registry of available Westminster poll importers.

Maps a stable pollster identifier (used in form values and URLs) to the
importer module that knows how to fetch and parse that pollster's release,
plus the keyword argument name that module expects for the source URL.
"""

from __future__ import annotations

from typing import Any

from typing_extensions import TypedDict

from polls.importers.westminster import (
    bmg_research_import,
    deltapoll_import,
    find_out_now_import,
    focaldata_import,
    ipsos_import,
    lord_ashcroft_import,
    more_in_common_import,
    opinium_import,
    survation_import,
    techne_import,
    yougov_import,
)


class ImporterMeta(TypedDict):
    """Metadata for a poll importer entry in IMPORTERS."""

    label: str
    module: Any
    url_arg: str


IMPORTERS: dict[str, ImporterMeta] = {
    "yougov": {
        "label": "YouGov",
        "module": yougov_import,
        "url_arg": "pdf_url",
    },
    "find_out_now": {
        "label": "Find Out Now",
        "module": find_out_now_import,
        "url_arg": "xlsx_url",
    },
    "more_in_common": {
        "label": "More in Common",
        "module": more_in_common_import,
        "url_arg": "xlsx_url",
    },
    "techne": {
        "label": "Techne",
        "module": techne_import,
        "url_arg": "pdf_url",
    },
    "opinium": {
        "label": "Opinium",
        "module": opinium_import,
        "url_arg": "xlsx_url",
    },
    "bmg_research": {
        "label": "BMG Research",
        "module": bmg_research_import,
        "url_arg": "xlsx_url",
    },
    "focaldata": {
        "label": "Focaldata",
        "module": focaldata_import,
        "url_arg": "xlsx_url",
    },
    "survation": {
        "label": "Survation",
        "module": survation_import,
        "url_arg": "xlsx_url",
    },
    "deltapoll": {
        "label": "Deltapoll",
        "module": deltapoll_import,
        "url_arg": "source_url",
    },
    "ipsos": {
        "label": "Ipsos",
        "module": ipsos_import,
        "url_arg": "pdf_url",
    },
    "lord_ashcroft": {
        "label": "Lord Ashcroft Polls",
        "module": lord_ashcroft_import,
        "url_arg": "source_url",
    },
}
