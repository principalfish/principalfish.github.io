# feat: redesign the data console into five uniform sections

The data console has grown page by page. It has no navigation, so the home page is the only way around. "Run model", "outputs" and "import" each have three different URL shapes, Westminster, Holyrood and US do the same jobs in different ways, and long runs block the page and lose their output once you leave.

This redesigns it around five uniform sections: Westminster, Holyrood, House, Senate and President. Each offers the same actions: automatic poll import, view polls, run models with parameters, view model runs, and delete. Single-poll import stays for Westminster, and section-specific tools such as by-election import stay where they belong. A global export page lets you tick sections and what to write, and rebuild, backup and restore move to an admin page. Templates are consolidated, URLs made consistent, and destructive actions get confirm steps.

Model runs move to the background with live progress, and every run's output is kept in a log. Each run form gets an "export when done" tick box, on by default.

Every existing diagnostic number stays visible, along with the safeguards around imports and destructive actions. Full stage-by-stage diagnostics for Holyrood and US are a separate follow-up issue.

Closes #83

Progress: the plan checklist on #83.
