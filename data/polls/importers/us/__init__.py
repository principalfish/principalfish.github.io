"""US opinion-poll importers (Wikipedia-driven).

:mod:`~polls.importers.us.us_polls_common` parses a polling table,
:mod:`~polls.importers.us.us_geography` names US seats, and
:mod:`~polls.importers.us.us_wikipedia_polls` holds the contest rules (which
pages and tables count, which seat and matchup a table belongs to) together
with the import plan, the commit and the shared command line.

One thin wrapper script per chamber runs those contests: the House generic
ballot plus the district polls, the Senate race pages, and the presidential
nationwide and statewide pages. Each lists what it found and writes nothing
unless given ``--commit``.
"""
