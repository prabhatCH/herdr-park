// Builds a clean, anonymous herdr session "demo" for recording herdr-park (see README.md).
import { execFileSync, spawn } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";

const DEMO = path.dirname(new URL(import.meta.url).pathname);
const REPO = path.dirname(DEMO);
// Outside the home folder: the restore picker shows full paths, and /Users/<name> would leak.
const PROJECT = process.env.DEMO_PROJECT ?? (process.platform === "darwin" ? "/Users/Shared/code/storefront-api" : "/tmp/code/storefront-api");
const STATE = process.env.HERDR_PARK_STATE ?? path.join(os.homedir(), ".local/state/herdr/plugins/herdr-park");
const ASIDE = path.join(DEMO, ".state-aside");
const SOCK = path.join(os.homedir(), ".config/herdr/sessions/demo/herdr.sock");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log("[setup]", ...a);

// herdr's socket API: newline-delimited JSON, one request per connection.
function call(method, params = {}, timeoutMs = 10000) {
  return new Promise((resolve, reject) => {
    const sock = net.createConnection(SOCK);
    let buf = "";
    const timer = setTimeout(() => (sock.destroy(), reject(new Error(`${method} timed out`))), timeoutMs);
    sock.setEncoding("utf8");
    sock.on("connect", () => sock.write(JSON.stringify({ id: "1", method, params }) + "\n"));
    sock.on("data", (d) => {
      buf += d;
      const nl = buf.indexOf("\n");
      if (nl < 0) return;
      clearTimeout(timer);
      sock.destroy();
      const msg = JSON.parse(buf.slice(0, nl));
      if (msg.error) reject(Object.assign(new Error(msg.error.message), { code: msg.error.code }));
      else resolve(msg.result);
    });
    sock.on("error", (e) => (clearTimeout(timer), reject(e)));
  });
}

// 0. The demo's Claude tabs need a working login. Check before building anything.
const env = Object.fromEntries(
  // Drop the variables that tie a process to the session it runs in, but keep the login token: with
  // CLAUDE_CODE_OAUTH_TOKEN set in your shell, the keychain login can be long expired.
  Object.entries(process.env).filter(([k]) => k === "CLAUDE_CODE_OAUTH_TOKEN" || !/^(HERDR_|CLAUDE)/.test(k)),
);
let auth = {};
try {
  auth = JSON.parse(execFileSync("claude", ["auth", "status"], { env, encoding: "utf8" }));
} catch {}
if (!auth.loggedIn) {
  console.error("[setup] Claude isn't logged in from this shell, so the demo tabs would be too. Run ./record.sh from a terminal where `claude auth status` says loggedIn: true (not from inside a Claude Code session).");
  process.exit(1);
}

// 1. A small, ordinary-looking demo project: the backend of an online shop.
const files = {
  "README.md": "# storefront-api\n\nBackend for the shop: products, carts, checkout and accounts.\n\n```sh\nnpm install\nnpm run dev\n```\n",
  "package.json": JSON.stringify({ name: "storefront-api", version: "0.4.2", private: true,
    scripts: { dev: "node --watch src/server.js", test: "jest" },
    dependencies: { express: "^4.21.0", bcrypt: "^5.1.1", pg: "^8.13.0" }, devDependencies: { jest: "^29.7.0", supertest: "^7.0.0" } }, null, 2) + "\n",
  "src/server.js": "const express = require('express');\nconst login = require('./routes/login');\nconst products = require('./routes/products');\n\nconst app = express();\napp.use(express.json());\napp.post('/login', login);\napp.use('/products', products);\napp.get('/health', (_req, res) => res.send('ok'));\n\nmodule.exports = app;\nif (require.main === module) app.listen(process.env.PORT || 3000);\n",
  "src/routes/login.js": "const bcrypt = require('bcrypt');\nconst db = require('../db');\n\nmodule.exports = async (req, res) => {\n  const { email, password } = req.body;\n  const user = await db.users.findByEmail(email);\n  if (!user || !(await bcrypt.compare(password, user.passwordHash))) return res.status(401).json({ error: 'invalid credentials' });\n  res.json({ token: await db.sessions.create(user.id) });\n};\n",
  "src/routes/products.js": "const router = require('express').Router();\nconst db = require('../db');\n\nrouter.get('/', async (_req, res) => res.json(await db.products.list()));\nrouter.get('/:id', async (req, res) => res.json(await db.products.get(req.params.id)));\n\nmodule.exports = router;\n",
  "src/db.js": "const { Pool } = require('pg');\nconst pool = new Pool();\n\nmodule.exports = {\n  users: { findByEmail: (email) => pool.query('select * from users where email = $1', [email]).then((r) => r.rows[0]) },\n  sessions: { create: (id) => pool.query('insert into sessions (user_id) values ($1) returning token', [id]).then((r) => r.rows[0].token) },\n  products: { list: () => pool.query('select * from products').then((r) => r.rows), get: (id) => pool.query('select * from products where id = $1', [id]).then((r) => r.rows[0]) },\n};\n",
  "test/login.test.js": "const request = require('supertest');\nconst app = require('../src/server');\nconst { seedUser } = require('./helpers');\n\nbeforeAll(() => seedUser('sam@example.com', 'hunter2'));\n\ntest('logs in with the right password', async () => {\n  const res = await request(app).post('/login').send({ email: 'sam@example.com', password: 'hunter2' });\n  expect(res.status).toBe(200);\n  expect(res.body.token).toBeDefined();\n});\n",
  ".gitignore": "node_modules/\n.env\n",
};
for (const [name, text] of Object.entries(files)) {
  fs.mkdirSync(path.dirname(path.join(PROJECT, name)), { recursive: true });
  fs.writeFileSync(path.join(PROJECT, name), text);
}
if (!fs.existsSync(path.join(PROJECT, ".git"))) execFileSync("git", ["init", "-q", "-b", "main"], { cwd: PROJECT });

// 2. Your real saved entries go aside, so the picker only shows demo tabs.
fs.mkdirSync(ASIDE, { recursive: true });
for (const f of ["parked.json", "index.json"]) {
  const src = path.join(STATE, f);
  if (fs.existsSync(src) && !fs.existsSync(path.join(ASIDE, f))) fs.renameSync(src, path.join(ASIDE, f));
}
log("moved aside:", fs.readdirSync(ASIDE).join(", ") || "nothing");

// 3. A minimal, anonymous prompt for every pane. herdr-park types its own absolute path into a
// hibernated pane (the wake command), so run a copy from a neutral folder rather than this
// checkout (a symlink wouldn't do: the script resolves its real path).
const TOOLS = path.join(path.dirname(PROJECT), "bin");
fs.mkdirSync(TOOLS, { recursive: true });
fs.copyFileSync(path.join(REPO, "herdr-park"), path.join(TOOLS, "herdr-park"));
fs.chmodSync(path.join(TOOLS, "herdr-park"), 0o755);
const zdot = path.join(DEMO, "zdotdir");
fs.mkdirSync(zdot, { recursive: true });
fs.writeFileSync(
  path.join(zdot, ".zshrc"),
  `PROMPT='%F{245}storefront-api%f %F{110}❯%f '\nRPROMPT=''\nexport PATH="${TOOLS}:$PATH"\nunsetopt BEEP\nsetopt interactivecomments\n`,
);

// 4. An isolated herdr server ("demo"), never your real session.
env.ZDOTDIR = zdot;
spawn("herdr", ["--session", "demo", "server"], { env, detached: true, stdio: "ignore" }).unref();
for (let i = 0; i < 40 && !fs.existsSync(SOCK); i++) await sleep(250);
await sleep(500);

const ws = await call("workspace.create", { cwd: PROJECT, label: "storefront", focus: true });
const shellPane = ws.root_pane.pane_id;
await call("tab.rename", { tab_id: ws.tab.tab_id, label: "main" });
const meterPane = (await call("pane.split", { pane_id: shellPane, direction: "right", ratio: 0.62, focus: false })).pane.pane_id;

async function startClaude(label, prompt) {
  const pane = (await call("tab.create", { workspace_id: ws.workspace.workspace_id, cwd: PROJECT, label, focus: false })).root_pane.pane_id;
  for (let i = 0; i < 30; i++) {
    try {
      await call("agent.start", { name: label, kind: "claude", pane_id: pane, timeout_ms: 60000 }, 65000);
      break;
    } catch (e) {
      if (e.code === "agent_pane_busy") { await sleep(500); continue; }
      if (e.code === "agent_not_ready") break;
      throw e;
    }
  }
  // A first run in this folder asks whether to trust it ("Yes" is the second choice).
  let answered = false;
  for (let i = 0; i < 90; i++) {
    const s = (await call("pane.get", { pane_id: pane })).pane.agent_status;
    if (s === "idle" || s === "done") break;
    if (s === "blocked" && !answered) {
      const screen = (await call("pane.read", { pane_id: pane, source: "visible", format: "text" })).read.text;
      if (screen.includes("trust this folder")) {
        await call("pane.send_keys", { pane_id: pane, keys: ["down", "enter"] });
        answered = true;
      }
    }
    await sleep(500);
  }
  try {
    await call("agent.prompt", { target: pane, text: prompt, wait: { until: ["idle", "done"], timeout_ms: 120000 } }, 125000);
  } catch (e) {
    // Show what the pane says (a login prompt, a dialog) instead of only herdr's error.
    const screen = (await call("pane.read", { pane_id: pane, source: "visible", format: "text" })).read.text;
    console.error(`[setup] ${label} didn't take the prompt. Its screen:\n${screen.trimEnd().split("\n").filter((l) => l.trim()).slice(-8).join("\n")}`);
    throw e;
  }
  log("ready:", label);
}

// Questions, not tasks, so no edit needs approving while the session is set up.
await startClaude("rate-limit", "the /login route has no rate limiting. what's the simplest way to add it here? just explain for now, no changes yet");
await startClaude("flaky-login", "test/login.test.js fails on CI about 1 in 5 runs but never locally. what should I look at first? no changes yet");
await startClaude("readme", "give me a one-line tagline for the README, don't edit it");

await call("pane.send_text", { pane_id: meterPane, text: `clear; python3 ${path.join(DEMO, "mem.py")}` });
await call("pane.send_keys", { pane_id: meterPane, keys: ["enter"] });
await call("pane.send_text", { pane_id: shellPane, text: "clear" });
await call("pane.send_keys", { pane_id: shellPane, keys: ["enter"] });
await call("tab.focus", { tab_id: ws.tab.tab_id });
log("demo session ready");
