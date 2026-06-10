// Manual tile entry: 5 slot columns, each holding one or more guess rows.
// A guess row is 5 tiles. Type A-Z to fill letters (auto-advances); click a tile
// (or press Space) to cycle its colour gray -> yellow -> green.
//
// createManualUI(container, onChange) renders the UI and calls onChange() whenever
// the state changes. getSlots() returns the solver slot model.

const COLOR_CLASS = ["gray", "yellow", "green"]; // index === colour digit

// Wordle clue colours for a guess against an answer, as digits [0|1|2] per
// position (0 gray, 1 yellow, 2 green). Two-pass so duplicate letters are
// counted correctly: greens claim their slot first, then yellows consume the
// remaining unmatched answer letters.
export function wordleColors(guess, answer) {
  const res = [0, 0, 0, 0, 0];
  const pool = answer.split("");
  for (let i = 0; i < 5; i++) {
    if (guess[i] === pool[i]) {
      res[i] = 2;
      pool[i] = null;
    }
  }
  for (let i = 0; i < 5; i++) {
    if (res[i] === 2) continue;
    const j = pool.indexOf(guess[i]);
    if (j !== -1) {
      res[i] = 1;
      pool[j] = null;
    }
  }
  return res;
}

// Master clue grid = both the input AND the overall game-state view. Five rows:
// each tile's COLOUR is the clue vs. the final word (rows 1-4 editable, default
// gray, cycle gray->yellow->green; row 5 is fixed all-green). After a solve,
// letters and per-word counts are overlaid via setResults().
// createClueGridUI(container) -> { getClueGrid, setClueGrid, setResults }.
export function createClueGridUI(container, onEdit) {
  const rows = [
    [0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0],
  ];
  let results = null; // per-word { letters[5], deduced[5], count } or null

  const colorOf = (r, c) => (r < 4 ? rows[r][c] : 2); // row 5 always green

  function render() {
    container.innerHTML = "";
    for (let r = 0; r < 5; r++) {
      const rowEl = document.createElement("div");
      rowEl.className = "clue-row";

      const label = document.createElement("span");
      label.className = "label";
      label.textContent = `Word ${r + 1}`;
      rowEl.appendChild(label);

      const tiles = document.createElement("span");
      tiles.className = "tiles";
      const res = results && results[r];
      for (let c = 0; c < 5; c++) {
        const tile = document.createElement("div");
        const ded = res && res.deduced[c];
        tile.className = `tile ${COLOR_CLASS[colorOf(r, c)]}${ded ? " deduced" : ""}`;
        tile.textContent = res ? res.letters[c] : "";
        if (r < 4) {
          tile.title = "click: gray → yellow → green";
          tile.style.cursor = "pointer";
          tile.addEventListener("click", () => {
            rows[r][c] = (rows[r][c] + 1) % 3;
            results = null; // results are now stale — clear until next Solve
            render();
            if (onEdit) onEdit(); // user changed the grid by hand → manual mode
          });
        }
        tiles.appendChild(tile);
      }
      rowEl.appendChild(tiles);

      const count = document.createElement("span");
      count.className = "count";
      count.innerHTML = res ? res.count : r === 4 ? '<span class="muted">final</span>' : "";
      rowEl.appendChild(count);

      container.appendChild(rowEl);
    }
  }

  function getClueGrid() {
    return [...rows.map((r) => r.join("")), "22222"];
  }

  // Overwrite rows 1-4 from a clue-grid array (strings of 0/1/2, or null = gray).
  function setClueGrid(grid) {
    for (let r = 0; r < 4; r++) {
      const s = grid && grid[r];
      for (let c = 0; c < 5; c++) {
        rows[r][c] = s && "012".includes(s[c]) ? +s[c] : 0;
      }
    }
    results = null;
    render();
  }

  function setResults(overlay) {
    results = overlay;
    render();
  }

  render();
  return { getClueGrid, setClueGrid, setResults };
}

export function createManualUI(container, onChange) {
  // state[slot] = { rows: [ { letters:[5], colors:[5] } ] }
  const state = Array.from({ length: 5 }, () => ({ rows: [emptyRow()] }));

  function emptyRow() {
    return { letters: ["", "", "", "", ""], colors: [0, 0, 0, 0, 0] };
  }

  function render() {
    container.innerHTML = "";
    state.forEach((slot, si) => {
      const col = document.createElement("div");
      col.className = "slot-col";

      const title = document.createElement("h3");
      title.textContent = `Word ${si + 1}`;
      col.appendChild(title);

      slot.rows.forEach((row, ri) => {
        col.appendChild(renderRow(si, ri, row));
      });

      const add = document.createElement("button");
      add.className = "add-guess";
      add.textContent = "+ guess";
      add.onclick = () => {
        slot.rows.push(emptyRow());
        render();
        emit();
      };
      col.appendChild(add);

      container.appendChild(col);
    });
  }

  function renderRow(si, ri, row) {
    const rowEl = document.createElement("div");
    rowEl.className = "tile-row";
    for (let ti = 0; ti < 5; ti++) {
      const tile = document.createElement("div");
      tile.className = `tile ${COLOR_CLASS[row.colors[ti]]}`;
      tile.tabIndex = 0;
      tile.textContent = row.letters[ti];

      tile.addEventListener("click", () => {
        if (document.activeElement === tile) {
          row.colors[ti] = (row.colors[ti] + 1) % 3;
          tile.className = `tile ${COLOR_CLASS[row.colors[ti]]}`;
          emit();
        } else {
          tile.focus();
        }
      });
      tile.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        row.colors[ti] = (row.colors[ti] + 1) % 3;
        tile.className = `tile ${COLOR_CLASS[row.colors[ti]]}`;
        emit();
      });
      tile.addEventListener("keydown", (e) => {
        if (/^[a-zA-Z]$/.test(e.key)) {
          row.letters[ti] = e.key.toUpperCase();
          tile.textContent = row.letters[ti];
          focusTile(rowEl, ti + 1);
          emit();
        } else if (e.key === "Backspace") {
          if (row.letters[ti] === "") focusTile(rowEl, ti - 1);
          row.letters[ti] = "";
          tile.textContent = "";
          emit();
        } else if (e.key === " ") {
          e.preventDefault();
          row.colors[ti] = (row.colors[ti] + 1) % 3;
          tile.className = `tile ${COLOR_CLASS[row.colors[ti]]}`;
          emit();
        } else if (e.key === "ArrowLeft") {
          focusTile(rowEl, ti - 1);
        } else if (e.key === "ArrowRight") {
          focusTile(rowEl, ti + 1);
        }
      });
      rowEl.appendChild(tile);
    }
    return rowEl;
  }

  function focusTile(rowEl, idx) {
    const tiles = rowEl.querySelectorAll(".tile");
    if (idx >= 0 && idx < tiles.length) tiles[idx].focus();
  }

  function emit() {
    if (onChange) onChange();
  }

  function getSlots() {
    return state.map((slot) => ({
      guesses: slot.rows
        .filter((r) => r.letters.every((l) => l !== ""))
        .map((r) => ({
          word: r.letters.join(""),
          colors: r.colors.join(""),
        })),
    }));
  }

  function reset() {
    for (let i = 0; i < state.length; i++) state[i] = { rows: [emptyRow()] };
    render();
  }

  // Programmatically write a completed guess row onto board `si`. Fills the
  // trailing empty row if there is one, else appends. Used by the auto-solver to
  // populate the boards as it plays.
  function addGuess(si, word, colors) {
    const row = {
      letters: word.split(""),
      colors: colors.split("").map(Number),
    };
    const rows = state[si].rows;
    const last = rows[rows.length - 1];
    if (last && last.letters.every((l) => l === "")) rows[rows.length - 1] = row;
    else rows.push(row);
    render();
  }

  // Recolour every completed guess row against the real answers (answers[si] is
  // board si's final word). Overwrites any hand-set colours that disagree with
  // the true Wordle clue. Returns the number of tiles that were corrected.
  function checkAgainst(answers) {
    let changed = 0;
    state.forEach((slot, si) => {
      const ans = answers && answers[si];
      if (!ans) return;
      slot.rows.forEach((row) => {
        if (!row.letters.every((l) => l !== "")) return;
        const want = wordleColors(row.letters.join(""), ans);
        for (let p = 0; p < 5; p++) {
          if (row.colors[p] !== want[p]) {
            row.colors[p] = want[p];
            changed++;
          }
        }
      });
    });
    render();
    emit();
    return changed;
  }

  // Rows that are part-typed: at least one letter but not a full five. A row of
  // all-empty tiles is fine (it's the trailing input row). Used to refuse a
  // solve on a half-entered word. Returns 1-based board numbers (deduped).
  function incompleteBoards() {
    const bad = new Set();
    state.forEach((slot, si) => {
      slot.rows.forEach((r) => {
        const filled = r.letters.filter((l) => l !== "").length;
        if (filled > 0 && filled < 5) bad.add(si + 1);
      });
    });
    return [...bad];
  }

  render();
  return { getSlots, reset, addGuess, checkAgainst, incompleteBoards };
}
