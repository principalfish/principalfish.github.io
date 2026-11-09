"""Election model runners, one subpackage per parliament, plus the code they share.

The parliaments are ``westminster`` (with its uncertainty calibration and validation),
``holyrood`` and ``us``; ``support`` holds the simulation, persistence, trends and
uncertainty code the runners use.

Kept empty on purpose: each runner is imported by its full name
(``models.us.run_us_house_model``), so there is one module object per runner.
"""
