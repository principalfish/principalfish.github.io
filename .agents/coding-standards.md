# Coding Standards

Applies to all code in this repository (JS, Python, HTML/CSS).

---

## Documentation

### JavaScript

- **Always add or update JSDoc when creating or modifying a function or handler.**
- Single-line `/** ... */` for simple, self-evident functions.
- Multi-line `/** ... */` blocks for functions with non-obvious behaviour, parameters, or side effects.
- Document all parameters (`@param`) and return values (`@returns`) for public/exported functions.
- This applies to both exported functions (`core.js` style) and module-internal functions (`electionmaps.js` style).

```js
/**
 * Computes the swing required to flip a seat.
 * @param {number} majority - Current majority as a percentage of votes cast.
 * @param {string} fromParty - Normalised party key of the current holder.
 * @param {string} toParty - Normalised party key of the challenger.
 * @returns {number} Swing percentage required.
 */
function swingToFlip(majority, fromParty, toParty) { ... }
```

### Python

- **Always add or update docstrings when creating or modifying a function, class, or handler.**
- Use Google-style docstrings for functions with parameters/return values.
- One-line docstrings are fine for simple functions.
- Type-annotate all parameters and return values (this codebase uses mypy strict mode).

```python
def get_election_by_id(election_id: int) -> Election | None:
    """Return the Election row for the given ID, or None if not found."""
    ...

def apply_swing(base_votes: dict[str, float], swing: float, party: str) -> dict[str, float]:
    """
    Apply a uniform swing to a votes dict.

    Args:
        base_votes: Party-keyed vote shares (0–100).
        swing: Percentage points to add to the target party.
        party: Normalised party key.

    Returns:
        Updated vote share dict with swing applied.
    """
    ...
```

---

## Testing

- Add tests for new logic in `core.js` functions — test file is `tests/core.test.js`, runner is vitest (`npm test`).
- Add or update Python tests in `data/tests/` for new/changed data logic.
- After any JS change: run `npm test` to confirm no regressions, then rebuild minified output (`npm run minify:electionmaps`).
- Tests should cover the meaningful edge cases, not just the happy path.

---

## JS module architecture (electionmaps)

- `electionmaps/core.js` is a pure ES module (no DOM). All shared logic lives here.
- `electionmaps.js` imports from `core.js`; `electionmaps.min.js` is built by esbuild (`--bundle`, d3/topojson external).
- Keep map interaction state centralized; re-render from canonical seat datasets to avoid stale filter/search/highlight state.
- Party key aliases are resolved in `PARTY_KEY_ALIASES` in `core.js` — add aliases there, not ad-hoc in callers.
- `PARTY_LABELS` / `PARTY_COLOURS` constants are removed; use `labelParty()` / `colourParty()` which resolve from `manifestPartiesByKey`.

---

## Python typing (mypy strict)

- All SQLAlchemy model columns must use `Mapped[X] = mapped_column(...)` (SQLAlchemy 2.0 style); legacy `Column(X)` cascades type errors everywhere.
- Use `Mapped[Optional[X]]` for nullable columns, `Mapped[X]` for non-nullable.
- Annotate all function parameters and return types; bare `list`, `dict`, `tuple` are rejected — use parameterized forms (`list[str]`, `dict[str, Any]`).
- Flask route functions: annotate return as `str` or `str | WerkzeugResponse` (not `ResponseReturnValue`).
- For scripts that import each other under `explicit_package_bases = True`, add `mypy_path = scripts` to `[mypy]` rather than per-module `ignore_missing_imports`.

---

## General

- Prefer the smallest safe change that resolves the issue.
- No dead code, no unused imports.
- Shared styles stay in `site/styles.css`; rebuild minified output after CSS changes.
- After removing a runtime dependency (script/style include), delete the now-unused vendor file too.
