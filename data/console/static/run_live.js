// Follows a running job: polls its status once a second, appends the new output
// to the two output blocks, and reloads the page when the job ends so the server
// renders the final state. All text goes in through textContent.
(() => {
  const live = document.getElementById("run-live");
  if (!live) return;

  const stdoutBlock = document.getElementById("run-live-stdout");
  const stderrBlock = document.getElementById("run-live-stderr");
  const stepLine = document.getElementById("run-live-step");
  const statusUrl = live.dataset.statusUrl;
  const state = {
    step: Number(live.dataset.step),
    stdout: Number(live.dataset.stdoutOffset),
    stderr: Number(live.dataset.stderrOffset),
  };
  const INTERVAL_MS = 1000;

  const query = () =>
    `?step=${state.step}&stdout=${state.stdout}&stderr=${state.stderr}`;

  const clearOutput = () => {
    stdoutBlock.textContent = "";
    stderrBlock.textContent = "";
    state.stdout = 0;
    state.stderr = 0;
  };

  // Returns false once the job is done, which stops the polling.
  const apply = (data) => {
    if (data.step !== state.step) clearOutput();
    state.step = data.step;
    stdoutBlock.textContent += data.stdout;
    stderrBlock.textContent += data.stderr;
    state.stdout = data.stdout_offset;
    state.stderr = data.stderr_offset;
    if (stepLine && data.steps > 0) {
      stepLine.textContent = "";
      const strong = document.createElement("strong");
      strong.textContent = `Step ${data.step + 1}:`;
      stepLine.appendChild(strong);
      stepLine.appendChild(document.createTextNode(` ${data.step_label}`));
    }
    if (data.done) {
      window.location.reload();
      return false;
    }
    // A finished earlier step with nothing left to read: move on to the next.
    if (data.caught_up && data.step < data.steps - 1) {
      state.step = data.step + 1;
      clearOutput();
    }
    return true;
  };

  const poll = () => {
    fetch(`${statusUrl}${query()}`, {
      cache: "no-store",
      headers: { Accept: "application/json" },
    })
      .then((response) => {
        if (!response.ok) throw new Error(`status ${response.status}`);
        return response.json();
      })
      .then((data) => {
        if (apply(data)) window.setTimeout(poll, INTERVAL_MS);
      })
      .catch(() => window.setTimeout(poll, INTERVAL_MS));
  };

  window.setTimeout(poll, INTERVAL_MS);
})();
