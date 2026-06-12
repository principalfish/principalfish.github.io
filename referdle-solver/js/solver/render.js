// Pure + DOM render helpers, ported from static/js/app.js and extended for the
// static solver's step-through viewer.

export const COLOR_SQUARES = { "0": "⬛", "1": "🟨", "2": "🟩" };
const WORD_LABEL = (b) => `Word ${b + 1}`;
const fmtExp = (x) => (x == null ? "opener" : `~${x.toFixed(1)}`);

export function colorSquares(colors) {
  return [...colors].map((c) => COLOR_SQUARES[c]).join("");
}

export function wordCloud(words, cap = 300) {
  if (words.length === 0) return `<div class="muted small">none</div>`;
  const shown = words.slice(0, cap);
  const extra = words.length - shown.length;
  return (
    `<div class="words">` +
    shown.map((w) => `<span class="chip">${w}</span>`).join("") +
    (extra > 0 ? `<span class="chip more">+${extra} more</span>` : "") +
    `</div>`
  );
}

// Green letters confirmed in position from a board's guesses.
function knownGreens(slot) {
  const letters = ["", "", "", "", ""];
  for (const g of slot.guesses || []) {
    for (let p = 0; p < 5; p++) if (g.colors[p] === "2") letters[p] = g.word[p];
  }
  return letters;
}

export function consensus(words) {
  const out = [null, null, null, null, null];
  if (!words.length) return out;
  for (let p = 0; p < 5; p++) {
    const ch = words[0][p];
    if (words.every((w) => w[p] === ch)) out[p] = ch;
  }
  return out;
}

function mustContain(words) {
  if (!words.length) return [];
  let common = [...new Set(words[0].split(""))];
  for (const w of words) common = common.filter((c) => w.includes(c));
  return common.sort();
}

export function mustWithPositions(words, clueRow) {
  return mustContain(words).map((ch) => {
    const all = [];
    const meaningful = [];
    for (let p = 0; p < 5; p++) {
      if (words.some((w) => w[p] === ch)) {
        all.push(p + 1);
        if (!clueRow || clueRow[p] !== "0") meaningful.push(p + 1);
      }
    }
    const pos = meaningful.length ? meaningful : all;
    return `${ch}(${pos.join(",")})`;
  });
}

export function cantContain(slots, clueGrid, idx) {
  const out = new Set();
  for (let j = 0; j < 5; j++) {
    if (j === idx) continue;
    const clue = clueGrid && clueGrid[j];
    if (typeof clue !== "string") continue;
    const greens = knownGreens(slots[j]);
    for (let p = 0; p < 5; p++) {
      if (greens[p] && clue[p] === "0") out.add(greens[p]);
    }
  }
  return [...out].sort();
}

// Per-word overlay (letters + count) for the merged clue grid.
export function buildOverlay(slots, res) {
  return slots.map((slot, i) => {
    const feas = res.solvable ? res.perSlotFeasible[i] : [];
    const solved = res.solvable && feas.length === 1;
    const greens = knownGreens(slot);
    const forced = res.solvable ? consensus(feas) : ["", "", "", "", ""];

    const letters = [];
    const deduced = [];
    for (let p = 0; p < 5; p++) {
      let ch = "";
      let d = false;
      if (solved) ch = feas[0][p];
      else if (greens[p]) ch = greens[p];
      else if (forced[p]) { ch = forced[p]; d = true; }
      letters.push(ch);
      deduced.push(d);
    }

    let count;
    if (!res.solvable) count = `<span class="bad">${res.candCounts[i]}</span> consistent`;
    else if (solved) count = `<span class="good">solved</span>`;
    else count = `<b>${feas.length}</b> placeable`;

    return { letters, deduced, count };
  });
}

// --- DOM render helpers --------------------------------------------------------

// Renders the per-slot feasible word panels (one per word) into a container.
export function renderWordLists(slotsEl, slots, clueGrid, res) {
  slotsEl.innerHTML = res.perSlotFeasible
    .map((feasible, i) => {
      const label = WORD_LABEL(i);
      const must = mustWithPositions(feasible, clueGrid && clueGrid[i]);
      const cant = cantContain(slots, clueGrid, i);
      return (
        `<div class="slot-result">` +
        `<h4>${label}</h4>` +
        `<div class="muted small">${feasible.length} placeable</div>` +
        `<div class="small constraints">must: <b>${must.join(" ") || "—"}</b>` +
        ` &nbsp; can't: <span class="bad">${cant.join(" ") || "—"}</span></div>` +
        wordCloud(feasible) +
        `</div>`
      );
    })
    .join("");
}

// Per-word "top 5" block (expanded). `perBoard` = [{board, top:[{word,
// expRemaining, probe}]}]; `isChosen(board, word)` optionally highlights a pick.
export function perWordTopHTML(perBoard, isChosen) {
  if (!perBoard || !perBoard.length) return "";
  const secs = perBoard
    .map((pb) => {
      const chips = pb.top
        .map((t, i) => {
          const chosen = isChosen && isChosen(pb.board, t.word);
          const tag = t.probe ? ` <span class="probe-tag">probe</span>` : "";
          return (
            `<span class="chip${i === 0 ? " top" : ""}${chosen ? " chosen" : ""}">` +
            `${t.word} <span class="muted small">~${t.expRemaining}</span>${tag}</span>`
          );
        })
        .join("");
      return `<div class="perword-row"><span class="pw-label">${WORD_LABEL(pb.board)}</span>${chips}</div>`;
    })
    .join("");
  return (
    `<details class="step-cands" open><summary>Top 5 per word</summary>` +
    `<div class="perword-list">${secs}</div></details>`
  );
}

// Renders the suggestion panel for a stepped-to move. `moveVerb` labels the move
// ("Suggested" for solver-chosen daily moves, "Played" for user-entered ones).
// When `asCurrent` is set, the panel is framed as the live state to guess FROM
// (e.g. right after "Suggest next guess") rather than a played-move recap.
export function renderStepSuggest(container, K, move, sug, totalMoves, moveVerb = "Suggested", asCurrent = false) {
  const ranked = sug && sug.solvable && sug.ranked ? sug.ranked : [];
  const perBoard = sug && sug.perBoard ? sug.perBoard : [];

  if (asCurrent) {
    const n = totalMoves;
    const head =
      `<h3>Current state <span class="muted small">— ${n} guess${n === 1 ? "" : "es"} entered</span></h3>`;
    const lead = ranked.length
      ? `<p>Next: best guess for each word below.</p>`
      : `<p><span class="good">All words pinned — nothing left to guess.</span></p>`;
    let tbl = "";
    if (ranked.length) {
      const rows = ranked
        .slice(0, 5)
        .map((r, i) =>
          `<tr><td>${i + 1}</td><td><b>${r.word}</b></td><td>${WORD_LABEL(r.board)}</td>` +
          `<td>${r.setSize}</td><td>${fmtExp(r.expRemaining)}</td></tr>`)
        .join("");
      tbl =
        `<table class="suggest-table"><thead><tr><th>#</th><th>word</th><th>board</th>` +
        `<th>now</th><th>leaves</th></tr></thead><tbody>${rows}</tbody></table>`;
    }
    const top = ranked[0];
    const perWord = perWordTopHTML(perBoard, (b, w) => top && b === top.board && w === top.word);
    container.innerHTML = `<div class="panel">${head}${lead}${tbl}${perWord}</div>`;
    return;
  }

  const desc = move.isClosing
    ? `<b>${move.word}</b> on <b>${WORD_LABEL(move.board)}</b> ` +
      `<span class="muted small">— closing (board pinned by deduction; submit the answer)</span>`
    : `<b>${move.word}</b> on <b>${WORD_LABEL(move.board)}</b> → result ${colorSquares(move.colors)}` +
      (move.expanded
        ? ` <span class="probe-tag expanded">probe·exp</span>`
        : move.probe ? ` <span class="probe-tag">probe</span>` : "");

  let globalTbl = "";
  if (ranked.length) {
    const rows = ranked
      .slice(0, 5)
      .map((r, i) => {
        const chosen = r.word === move.word && r.board === move.board ? ' class="chosen"' : "";
        return (
          `<tr${chosen}><td>${i + 1}</td><td><b>${r.word}</b></td><td>${WORD_LABEL(r.board)}</td>` +
          `<td>${r.setSize}</td><td>${fmtExp(r.expRemaining)}</td></tr>`
        );
      })
      .join("");
    globalTbl =
      `<div class="muted small" style="margin:8px 0 3px">Top candidates · best guess for each word:</div>` +
      `<table class="suggest-table"><thead><tr><th>#</th><th>word</th><th>board</th>` +
      `<th>now</th><th>leaves</th></tr></thead><tbody>${rows}</tbody></table>`;
  }

  const perWord = perWordTopHTML(
    perBoard,
    (b, w) => !move.isClosing && b === move.board && w === move.word
  );

  container.innerHTML =
    `<div class="panel"><h3>Move ${K + 1} of ${totalMoves}</h3>` +
    `<p>${moveVerb}: ${desc}</p>${globalTbl}${perWord}</div>`;
}

// Renders the clickable move table; calls onJump(K) on row click.
export function renderAutoSolveTable(container, lastAutoSolve, activeIdx, onJump) {
  const { game, moves, source, ms } = lastAutoSolve;
  const rows = moves
    .map((m, i) => {
      const probeTag = m.expanded
        ? ` <span class="probe-tag expanded">probe·exp</span>`
        : m.probe ? ` <span class="probe-tag">probe</span>` : "";
      const cut = m.isClosing
        ? `<span class="muted small">answer</span>`
        : m.setSize === undefined
          ? `<span class="muted small">…</span>`
          : `<span class="muted small">${m.setSize}→${fmtExp(m.expRemaining)}</span>`;
      const cls =
        `move-row${m.isClosing ? " closing" : ""}${m.probe ? " probe" : ""}${i === activeIdx ? " active" : ""}`;
      return (
        `<tr class="${cls}" data-move="${i}"><td>${i + 1}</td><td>${WORD_LABEL(m.board)}</td>` +
        `<td><b>${m.word}</b>${probeTag}</td><td>${colorSquares(m.colors)}</td><td>${cut}</td></tr>`
      );
    })
    .join("");
  let note;
  if (game.manual) {
    note =
      `<span class="muted"><b>${moves.length}</b> guess${moves.length === 1 ? "" : "es"} entered ` +
      `— click a move to step through</span>`;
  } else if (game.inProgress) {
    note =
      `<span class="muted">In progress — <b>${moves.length}</b> move${moves.length === 1 ? "" : "s"} played</span>` +
      `<span class="muted small"> (${game.guessCount} guesses + ${moves.length - game.guessCount} closing)</span>`;
  } else if (game.solved) {
    note =
      `<span class="good">Solved</span> all 5 in <b>${game.completedCount}</b> guesses ` +
      `<span class="muted small">(${game.guessCount} guesses + ${game.completedCount - game.guessCount} closing)</span>` +
      (ms != null ? ` · solved in ${ms} ms` : "");
  } else {
    note =
      `<span class="bad">Couldn't solve — stopped after ${game.guessCount} guesses</span>` +
      (ms != null ? ` · ${ms} ms` : "");
  }
  const title = game.manual ? "Move list" : "Auto-solve";
  const playedLine = game.words
    ? `<p class="muted small">Played against ${source}: ${game.words.join(", ")}</p>`
    : `<p class="muted small">${source}</p>`;
  container.innerHTML =
    `<div class="panel"><h3>${title} <span class="muted small">— click a move to step through</span></h3>` +
    `<p>${note}</p>${playedLine}` +
    `<table class="suggest-table move-table"><thead><tr><th>#</th><th>board</th>` +
    `<th>guess</th><th>result</th><th>cut</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  container
    .querySelectorAll(".move-row")
    .forEach((tr) => tr.addEventListener("click", () => onJump(+tr.dataset.move)));
}
