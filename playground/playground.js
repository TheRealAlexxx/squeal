/* squeal playground: the real Python engine, in the browser, via Pyodide.
 *
 * The engine files load straight from ../squeal/ (the same source the
 * CLI runs) and playground/bridge.py is the JSON API between JS and
 * Python. No server, no build step.
 */

const PYODIDE_MODULE = "https://cdn.jsdelivr.net/npm/pyodide@314.0.7/pyodide.mjs";
const PYODIDE_INDEX = "https://cdn.jsdelivr.net/npm/pyodide@314.0.7/";

// Every engine module, loaded verbatim. tests/test_playground.py fails
// if this list drifts from squeal/*.py on disk.
const ENGINE_FILES = [
  "__init__.py", "astnodes.py", "cli.py", "csvload.py", "errors.py",
  "executor.py", "explain.py", "lexer.py", "parser.py", "render.py",
  "schema.py", "unparse.py", "values.py",
];

const DATASETS = [
  {
    id: "people", label: "people",
    blurb: "six programmers, ages, cities, scores (with NULLs)",
    files: { people: "data/people.csv" },
    queries: [
      ["filter", "select name, age from people where age > 30 order by age"],
      ["group", "select city, count(*), avg(age) from people group by city order by city"],
      ["window", "select name, row_number() over (order by age) from people where age is not null"],
    ],
  },
  {
    id: "shop", label: "orders + products",
    blurb: "twenty orders to join against a product list",
    files: { orders: "data/orders.csv", products: "data/products.csv" },
    queries: [
      ["join", "select o.order_id, p.category, o.quantity * o.unit_price as total " +
               "from orders o join products p on p.product = o.product order by total desc limit 10"],
      ["group", "select region, count(*), sum(quantity) from orders group by region order by 3 desc"],
      ["subquery", "select product from orders group by product " +
                   "having sum(quantity) > (select avg(quantity) from orders) order by product"],
    ],
  },
  {
    id: "sensors", label: "sensors",
    blurb: "temperature readings over three days, NULLs and negatives",
    files: { sensors: "data/sensors.csv" },
    queries: [
      ["rank", "select sensor, temp, rank() over (partition by sensor order by temp) " +
               "from sensors where temp is not null"],
      ["running", "select day, temp, avg(temp) over (partition by day) from sensors order by id"],
      ["having", "select sensor, min(temp), max(temp) from sensors group by sensor " +
                 "having count(temp) > 3 order by sensor"],
    ],
  },
];

const MAX_RENDERED_ROWS = 1000;

const $ = (sel) => document.querySelector(sel);
let pyodide = null;
let bootedAt = 0;

function bootStep(text, fraction) {
  $("#boot-status").textContent = text;
  $("#boot-fill").style.width = Math.round(fraction * 100) + "%";
}

async function boot() {
  const t0 = performance.now();
  bootStep("downloading the Python runtime (about 12 MB, cached after the first visit)…", 0.1);
  const { loadPyodide } = await import(PYODIDE_MODULE);
  pyodide = await loadPyodide({ indexURL: PYODIDE_INDEX });

  bootStep("loading the squeal engine from this repository…", 0.55);
  pyodide.FS.mkdirTree("/squeal");
  await Promise.all(ENGINE_FILES.map(async (name) => {
    const response = await fetch("../squeal/" + name);
    if (!response.ok) throw new Error("cannot load engine file squeal/" + name);
    pyodide.FS.writeFile("/squeal/" + name, await response.text());
  }));
  const bridge = await fetch("bridge.py");
  if (!bridge.ok) throw new Error("cannot load playground/bridge.py");
  pyodide.FS.writeFile("/bridge.py", await bridge.text());

  bootStep("starting the engine…", 0.75);
  const description = JSON.parse(await pyodide.runPythonAsync(
    "import sys\nsys.path.insert(0, '/')\nimport bridge\nbridge.describe()"));

  bootStep("mounting sample data…", 0.9);
  await mountDataset(DATASETS[0], true);

  bootedAt = performance.now() - t0;
  $("#boot").classList.add("done");
  $("#run").disabled = false;
  setStatus("squeal " + description.version + " ready in " +
            (bootedAt / 1000).toFixed(1) + "s - the engine is running in this tab");
  $("#sql").value = DATASETS[0].queries[0][1];
  renderChips(DATASETS[0]);
}

async function bridgeCall(call) {
  return JSON.parse(await pyodide.runPythonAsync(call));
}

function pyString(value) {
  return JSON.stringify(String(value));
}

async function mountText(name, text) {
  return bridgeCall("bridge.mount_csv(" + pyString(name) + ", " + pyString(text) + ")");
}

async function mountDataset(dataset, silent) {
  for (const [name, path] of Object.entries(dataset.files)) {
    const response = await fetch(path);
    if (!response.ok) throw new Error("cannot load " + path);
    await mountText(name, await response.text());
  }
  const description = await bridgeCall("bridge.describe()");
  renderTables(description);
  if (!silent) {
    renderChips(dataset);
    $("#sql").value = dataset.queries[0][1];
  }
}

function renderTables(description) {
  const host = $("#tables");
  if (!description.tables.length) {
    host.innerHTML = '<p class="muted">nothing loaded yet</p>';
    return;
  }
  const rows = description.tables.map((t) =>
    "<tr><td class='tname'>" + escapeHtml(t.name) + "</td>" +
    "<td class='muted'>" + t.rows + " &times; " + t.columns.length + "</td>" +
    "<td class='tdrop'><button data-drop='" + escapeHtml(t.name) + "' title='drop table'>&times;</button></td></tr>");
  host.innerHTML = "<table>" + rows.join("") + "</table>";
  host.querySelectorAll("[data-drop]").forEach((button) => {
    button.addEventListener("click", async () => {
      renderTables(await bridgeCall("bridge.drop_table(" + pyString(button.dataset.drop) + ")"));
    });
  });
}

function renderDatasets() {
  const host = $("#datasets");
  host.innerHTML = "";
  for (const dataset of DATASETS) {
    const button = document.createElement("button");
    button.className = "dataset";
    button.disabled = true;
    button.innerHTML = "<b>" + escapeHtml(dataset.label) + "</b><small>" +
                       escapeHtml(dataset.blurb) + "</small>";
    button.addEventListener("click", async () => {
      button.disabled = true;
      try { await mountDataset(dataset, false); }
      finally { button.disabled = false; }
    });
    host.appendChild(button);
  }
}

function renderChips(dataset) {
  const host = $("#chips");
  host.innerHTML = "";
  for (const [label, sql] of dataset.queries) {
    const chip = document.createElement("button");
    chip.textContent = label;
    chip.title = sql;
    chip.addEventListener("click", () => { $("#sql").value = sql; $("#sql").focus(); });
    host.appendChild(chip);
  }
}

function setStatus(text) { $("#status").textContent = text; }

function escapeHtml(text) {
  return String(text).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

async function runQuery() {
  const sql = $("#sql").value.trim().replace(/;+\s*$/, "");
  if (!sql) return;
  $("#run").disabled = true;
  $("#error").hidden = true;
  $("#message").hidden = true;
  setStatus("running…");
  let outcome;
  try {
    outcome = await bridgeCall("bridge.run_sql(" + pyString(sql) + ")");
  } catch (exc) {
    outcome = { ok: false, rendered: "error: the engine itself crashed: " + exc };
  }
  $("#run").disabled = false;
  if (!outcome.ok) {
    $("#error").textContent = outcome.rendered || ("error: " + outcome.error);
    $("#error").hidden = false;
    $("#results").innerHTML = "";
    setStatus("query failed");
    return;
  }
  if (outcome.message) {
    $("#message").textContent = outcome.message;
    $("#message").hidden = false;
    $("#results").innerHTML = "";
    setStatus("done in " + outcome.ms + " ms");
    return;
  }
  renderResults(outcome);
}

function renderResults(outcome) {
  const total = outcome.rows.length;
  const shown = Math.min(total, MAX_RENDERED_ROWS);
  const head = "<tr>" + outcome.columns.map((c) => "<th>" + escapeHtml(c) + "</th>").join("") + "</tr>";
  const body = outcome.rows.slice(0, shown).map((row) =>
    "<tr>" + row.map((value) => {
      if (value === null) return "<td class='null'>NULL</td>";
      if (typeof value === "number") return "<td class='num'>" + escapeHtml(value) + "</td>";
      return "<td>" + escapeHtml(value) + "</td>";
    }).join("") + "</tr>").join("");
  $("#results").innerHTML = "<table>" + head + body + "</table>" +
    (shown < total ? "<p class='rowcap'>showing the first " + shown.toLocaleString() +
                     " of " + total.toLocaleString() + " rows</p>" : "");
  setStatus(total.toLocaleString() + " row" + (total === 1 ? "" : "s") +
            " in " + outcome.ms + " ms");
}

function tableNameFor(fileName) {
  const base = fileName.replace(/\.[^.]+$/, "").toLowerCase();
  const cleaned = base.replace(/[^a-z0-9_]/g, "_").replace(/^(\d)/, "_$1");
  return cleaned || "uploaded";
}

async function mountFiles(fileList) {
  for (const file of fileList) {
    if (!/\.csv$/i.test(file.name)) { setStatus("skipped " + file.name + ": not a .csv"); continue; }
    setStatus("loading " + file.name + "…");
    const text = await file.text();
    const description = await mountText(tableNameFor(file.name), text);
    renderTables(description);
    setStatus(file.name + " mounted as table " + tableNameFor(file.name));
  }
}

function wireUpload() {
  $("#file-input").addEventListener("change", (event) => mountFiles(event.target.files));
  ["dragenter", "dragover"].forEach((kind) =>
    document.addEventListener(kind, (event) => {
      event.preventDefault();
      document.body.classList.add("dragging");
    }));
  ["dragleave", "drop"].forEach((kind) =>
    document.addEventListener(kind, (event) => {
      event.preventDefault();
      document.body.classList.remove("dragging");
    }));
  document.addEventListener("drop", (event) => mountFiles(event.dataTransfer.files));
}

async function autotest() {
  const report = { checks: [] };
  const check = (name, ok, detail) => {
    report.checks.push({ name, ok, detail });
    return ok;
  };
  try {
    await mountDataset(DATASETS[2], true);  // sensors
    await mountDataset(DATASETS[1], true);  // orders + products

    let r = await bridgeCall("bridge.run_sql('select city, count(*) from people group by city order by city')");
    check("group by", r.ok && r.rows.length === 3, JSON.stringify(r.rows));

    r = await bridgeCall("bridge.run_sql('select count(*) from orders o join products p on p.product = o.product')");
    check("join", r.ok && r.rows[0][0] === 20, JSON.stringify(r.rows));

    r = await bridgeCall("bridge.run_sql('select sensor, rank() over (order by temp) from sensors where temp is not null')");
    check("window", r.ok && r.rows.length === 13, JSON.stringify(r.rows && r.rows.length));

    r = await bridgeCall("bridge.run_sql('select nope from people')");
    check("unknown column is a clean error", !r.ok && r.rendered && r.rendered.indexOf("error: no such column") === 0, r.rendered);
    r = await bridgeCall("bridge.run_sql('select from')");
    check("parse error has caret", !r.ok && r.rendered && r.rendered.indexOf("^") >= 0, r.rendered);

    r = await bridgeCall("bridge.run_sql('explain select * from people')");
    check("explain message", r.ok && typeof r.message === "string" && r.message.length > 0, r.message);

    const before = (await bridgeCall("bridge.describe()")).tables.length;
    await bridgeCall("bridge.drop_table('sensors')");
    const after = (await bridgeCall("bridge.describe()")).tables.length;
    check("drop table", before === after + 1, before + " -> " + after);
  } catch (exc) {
    report.checks.push({ name: "harness crash", ok: false, detail: String(exc) });
  }
  report.pass = report.checks.every((c) => c.ok);
  $("#autotest").textContent = JSON.stringify(report, null, 2);
  document.title = report.pass ? "AUTOTEST PASS" : "AUTOTEST FAIL";
}

renderDatasets();
wireUpload();
$("#run").addEventListener("click", runQuery);
$("#sql").addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") runQuery();
});

const wantsAutotest = new URLSearchParams(location.search).has("autotest");
boot()
  .then(() => { if (wantsAutotest) return autotest(); })
  .catch((exc) => {
    bootStep("failed to start: " + exc, 1);
    if (wantsAutotest) {
      $("#autotest").textContent = JSON.stringify({ pass: false, crash: String(exc) });
      document.title = "AUTOTEST FAIL";
    }
  });
