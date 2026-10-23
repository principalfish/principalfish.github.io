# Pinned Senate cycles

`model_support/senate_cycles.py` resolves versioned configuration in
`senate_cycles/cycles-v1.json` using the run's as-of date. No live frontend file
or wall-clock date determines a model field.

- 2026-01-01 through 2026-12-31: 33 Class-2 races use 2020 results; Florida
  and Ohio Class-3 specials use 2022. The pinned 100-member roster supplies
  65 fixed holdovers. Its source bytes have SHA-256
  `13a0234b35cfb66b71aa245556322bca5d37488e3cd2341728fe43f302e6341d`.
- 2027-01-01 through 2028-12-31: 34 Class-3 races use 2022 results. Only the
  contested field is configured. No post-2026 roster or future control policy
  is assumed, and `require_chamber()` fails clearly.
- Other dates fail before output replacement. Ordinary latest-poll capping
  cannot silently cross a cycle boundary. Daily history and gap fills resolve
  and preflight each cycle separately.

The 2026 control policy explicitly aligns King and Sanders with Democrats,
leaves Osborn unaligned, and assumes a Republican vice president. These are
recorded assumptions for later counting; this module does not enable odds.
Configuration revisions must use new identifiers and retain their provenance.

Deterministic Senate outputs save the complete field, baseline references,
roster text and policy in `model_context`, with content checksums. The optional
SQLite table is created inside the output replacement transaction on the
first context-bearing save. Context insertion, votes and election replacement
commit together or roll back together. Context rows are removed with scoped
output deletion. Dry runs neither create this table nor write any outputs.
No live database migration or history regeneration is performed by adding this
code. House and presidential outputs do not start writing contexts here.

Export uses that frozen context, not a later configuration revision. A legacy
2026 output without context can use the pinned interpretation only when its
recorded state field matches all 35 configured races. Unknown or partial legacy
fields stay central-only. A rerun of an older date cannot displace the latest
dated Senate forecast.

The forecast gets a separate hidden roster result identified by its content
hash. The actual Current Senate entry and `senate-current.json` remain separate.
Forecast metadata, comparison baseline and special contests follow the saved
cycle, including metadata-only exports. Without a verified roster, the forecast
has only its Seats-up view. Future activation of a verified 2028 full chamber
must also make the frontend chamber merger accept the configured regular class;
its existing regular-Class-2 default is not used for the 2028 field-only view.

Tests cover configuration boundaries and corruption, fixed-roster counts,
control assumptions, central-row equality, direct runs, history, gap filling,
rebuilds, atomic persistence, legacy export checks and full/metadata-only exports.
