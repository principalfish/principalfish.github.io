# Migrating from a static site to a full-stack app on Cloudflare

**Question:** What would it take to move this site from GitHub Pages to a full-stack app? Is Cloudflare suitable, and what would it cost?

**Short answer:** Cloudflare is a good fit. Hosting the current site there costs $0, and almost any full-stack version fits inside the $5/month Workers Paid plan at this site's traffic. The hard part isn't the hosting: the Python/SQLite pipeline doesn't run on Cloudflare's serverless runtime without changes. Which route to take depends on what "full stack" is meant to achieve, and that is still open.

Pricing below was checked against Cloudflare's docs on 2026-11-07.

## What exists today

- **Front end:** 549 tracked files, all static.
  - The maps fetch small exported JSON files (`electionmapslogic/files.js:9`): `electionmaps/data` is about 1.7 MB and `uselectionmaps/data` about 1.5 MB.
  - The only outside API is postcodes.io.
  - The largest tracked file is `guesstheyear/wikipedia_history.db` at 17 MB, so every file fits the 25 MiB per-file limit for static assets.
- **Back end:** local only.
  - The Flask console (`data/console/`) runs importers and models through `subprocess` (`data/console/services/runner.py:37`).
  - The database is a 532 MB SQLite file. Almost all of it is `votes`: 7.7M rows, 264 MB, plus `idx_votes_election_id` (116 MB) and `idx_votes_seat_id` (105 MB). Next is `model_uncertainty` at 45 MB. Everything else is about 3 MB.
  - Exports are committed to git: 76 commits touched the data directories in the 6 months to 2026-11-07.

## Three levels of "full stack"

| Level | What changes | Cloudflare pieces | Cost per month |
|-|-|-|-|
| 1. Move hosting only | Same static site, deployed from git | Workers static assets (unlimited free requests) | $0 |
| 2. Public API | Maps query a database instead of reading committed JSON; exports go to R2 or D1 rather than git | Worker + D1 (+ R2) | $0 if the published data stays under 500 MB; otherwise $5 |
| 3. Console online | Flask console and model runs hosted, behind a login | Containers + Cloudflare Access + persistent storage for the database | $5, with occasional small overage |

### Level 2 details

- The free D1 plan allows 500 MB per database, and the full database is 532 MB. The public site only needs the published slice, which is about 3 MB as JSON today, so free is realistic.
- The free Workers plan allows 100k requests a day with 10 ms of CPU per request, which is enough for simple lookups.
- **Writes are the cost to watch.** D1 charges $1 per million rows written over the 50M a month included on Paid, and each index adds a write. Loading all 7.7M `votes` rows with their two indexes is about 23M row-writes. That's fine once a month, but frequent syncs need to send only what changed.

### Level 3 details

- Containers are billed only while running, and they can sleep when idle.
- Workers Paid includes 25 GiB-hours of memory, 375 vCPU-minutes and 200 GB-hours of disk a month. A small instance used about 20 hours a month fits inside that.
- CPU over the allowance costs $0.000020 per vCPU-second, about $0.07 per vCPU-hour.
- Cloudflare Access is free for up to 50 users, which is enough to lock the console down.

## Constraints

1. **Python on Workers isn't CPython.**
   - Python Workers run on Pyodide. They support Flask and synchronous SQLAlchemy; async SQLAlchemy needs greenlet, which isn't available.
   - There is no filesystem, so the SQLite file the whole pipeline uses can't be opened.
   - D1 is reached through a Worker binding, not a DB-API driver. Running the pipeline on Workers means rewriting the data layer.
   - Not checked: whether a usable SQLAlchemy dialect for D1 exists.
2. **Container disk is believed to be ephemeral (not checked).** If so, a SQLite file on the container doesn't survive a sleep. Options:
   - copy the database from R2 when the container starts and back when it stops (single writer only; fragile);
   - move to Postgres through Hyperdrive (a migration and a new provider);
   - rewrite the data layer against D1.
3. **Workers run in front of the cache.** Cached API responses still count as requests and CPU time, unlike responses from GitHub Pages.
4. **DNS for principalfish.co.uk has to move to Cloudflare** to serve a Worker on the custom domain. The domain itself can stay with its current registrar.

## Alternatives

- **Cloudflare Tunnel + Access (free):** if the goal is remote use of the console, this exposes the local Flask console securely with no migration. The machine has to be on.
- **A small VPS for level 3:** Hetzner-class servers cost roughly €4–6 a month (price not checked). They run Flask and SQLite unchanged, which is far less work than reworking the pipeline for Containers. A VPS can be combined with level 1 or 2 on Cloudflare for the public side.

## Recommendation

- Do level 1 now: it's free and low-risk.
- Add level 2 only when a specific feature needs live queries.
- Keep the pipeline local unless remote access is the real need. If it is, try Tunnel first, then a VPS.

## Open questions

- **What is "full stack" meant to achieve?** The level depends on which of these matters:
  - getting exports out of git;
  - live queries, such as per-seat history or postcode lookups;
  - using the console away from the dev machine;
  - scheduled poll imports. These would use Cron Triggers, and the CPU limits on Workers would then matter.
- Does container disk persist across sleeps? This needs checking against the Containers docs.
- Is there a SQLAlchemy dialect for D1 good enough to run the pipeline against?
- What is the site's actual traffic? GA has it. The cost estimates assume it stays well under 3M requests a month.

## Sources

- [Workers pricing](https://developers.cloudflare.com/workers/platform/pricing/)
- [Workers limits](https://developers.cloudflare.com/workers/platform/limits/)
- [D1 pricing](https://developers.cloudflare.com/d1/platform/pricing/)
- [D1 limits](https://developers.cloudflare.com/d1/platform/limits/)
- [Containers pricing](https://developers.cloudflare.com/containers/platform/pricing/)
- [Containers CPU pricing change, 2025-11-21](https://developers.cloudflare.com/changelog/post/2025-11-21-new-cpu-pricing/)
- [Python Workers packages](https://developers.cloudflare.com/workers/languages/python/packages/)
- [Flask on Python Workers](https://developers.cloudflare.com/workers/languages/python/packages/flask/)
- [Hyperdrive with Python Workers](https://developers.cloudflare.com/hyperdrive/examples/python-workers/)
- [Python Workers redux](https://blog.cloudflare.com/python-workers-advancements)
