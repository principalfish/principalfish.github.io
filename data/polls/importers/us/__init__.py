"""US national opinion-poll importers (Wikipedia-driven).

One thin importer per election type (House generic ballot, Presidential, Senate),
all sharing :mod:`polls.importers.us.us_polls_common`. Each scrapes a national
two-party polling table and inserts national ``PollRow`` rows (``region_id=NULL``)
against the type's US map, feeding the national-uniform-swing forecast runners in
``models/us``.
"""
