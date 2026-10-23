/* Smoke test for the playground bridge, without a browser.
 *
 *   cd playground && npm install pyodide && node test_bridge.mjs
 *
 * Loads the engine from ../squeal/ and bridge.py into real Pyodide
 * (CPython compiled to WebAssembly, the same runtime the playground
 * uses) and exercises the JSON API the page calls: mounting CSVs,
 * queries, joins, windows, views, EXPLAIN, and error rendering.
 */
import { loadPyodide } from "pyodide";
import { readFileSync, readdirSync } from "fs";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");

const pyodide = await loadPyodide();
pyodide.FS.mkdirTree("/squeal");
for (const name of readdirSync(join(root, "squeal"))) {
  if (name.endsWith(".py")) {
    pyodide.FS.writeFile("/squeal/" + name, readFileSync(join(root, "squeal", name)));
  }
}
pyodide.FS.writeFile("/bridge.py", readFileSync(join(here, "bridge.py")));
await pyodide.runPythonAsync("import sys; sys.path.insert(0, '/'); import bridge");

const call = async (code) => JSON.parse(await pyodide.runPythonAsync(code));
const str = JSON.stringify;

let failures = 0;
function check(name, ok, detail) {
  console.log((ok ? "ok   " : "FAIL ") + name + (ok ? "" : "  ->  " + detail));
  if (!ok) failures += 1;
}

let description = await call("bridge.describe()");
check("engine version", typeof description.version === "string" && description.version.length > 0,
      JSON.stringify(description));

const people = readFileSync(join(root, "tests", "fixtures", "people.csv"), "utf8");
const orders = readFileSync(join(root, "examples", "orders.csv"), "utf8");
const products = readFileSync(join(root, "examples", "products.csv"), "utf8");
description = await call("bridge.mount_csv('people', " + str(people) + ")");
await call("bridge.mount_csv('orders', " + str(orders) + ")");
description = await call("bridge.mount_csv('products', " + str(products) + ")");
check("three tables mounted",
      description.tables.map((t) => t.name).join(",") === "orders,people,products",
      JSON.stringify(description.tables));

let r = await call("bridge.run_sql('select city, count(*) from people group by city order by city')");
check("group by", r.ok && r.rows.length === 3 && r.rows[0][0] === "Amsterdam", JSON.stringify(r));

r = await call("bridge.run_sql('select count(*) from orders o join products p on p.product = o.product')");
check("join", r.ok && r.rows[0][0] === 20, JSON.stringify(r));

r = await call("bridge.run_sql('select name, row_number() over (order by age) from people where age is not null')");
check("window", r.ok && r.rows.length === 5, JSON.stringify(r));

r = await call("bridge.run_sql('create view londoners as select name from people where city = \\'London\\'')");
const viewMessage = r;
r = await call("bridge.run_sql('select count(*) from londoners')");
check("view round trip", viewMessage.ok && r.ok && r.rows[0][0] === 3, JSON.stringify(r));

r = await call("bridge.run_sql('explain select * from people')");
check("explain returns a message", r.ok && typeof r.message === "string" && r.message.length > 0,
      JSON.stringify(r));

r = await call("bridge.run_sql('select nope from people')");
check("unknown column is a clean error",
      !r.ok && r.rendered.includes("error: no such column"), JSON.stringify(r));

r = await call("bridge.run_sql('select from')");
check("parse error renders with caret",
      !r.ok && r.rendered.includes("error:") && r.rendered.includes("^"), JSON.stringify(r));

r = await call("bridge.run_sql('select 1 / 0')");
check("division by zero is a clean error", !r.ok && r.error.includes("division"), JSON.stringify(r));

description = await call("bridge.drop_table('products')");
check("drop table", description.tables.map((t) => t.name).join(",") === "orders,people",
      JSON.stringify(description.tables));

r = await call("bridge.run_sql('select name, age from people order by age')");
check("NULL sorts last", r.ok && r.rows[r.rows.length - 1][1] === null, JSON.stringify(r.rows));

if (failures) {
  console.log(failures + " check(s) failed");
  process.exit(1);
}
console.log("all bridge checks passed under Pyodide " + pyodide.version);
