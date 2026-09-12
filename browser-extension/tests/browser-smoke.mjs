// Real Chromium test. The test copy pre-grants the optional loopback permission;
// Chrome's permission prompt still needs a human check with the shipped manifest.
import assert from "node:assert/strict";
import {mkdtemp, cp, readFile, writeFile, mkdir, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import path from "node:path";
import {fileURLToPath} from "node:url";
import {spawn} from "node:child_process";
import {chromium} from "playwright";

const extensionDir = fileURLToPath(new URL("..", import.meta.url));
const repoDir = path.dirname(extensionDir);
const scratch = await mkdtemp(path.join(tmpdir(), "reflex-browser-test-"));
const testExtension = path.join(scratch, "extension");
let browser, bridge, fixtureRoot;
try {
  await mkdir(testExtension);
  const manifest = JSON.parse(await readFile(path.join(extensionDir, "manifest.json"), "utf8"));
  for (const file of ["background.js", "context.mjs", "preview.mjs", "ember.js", "slack-adapter.js", "content.js", "panel.html", "panel.css", "panel.js"]) {
    await cp(path.join(extensionDir, file), path.join(testExtension, file));
  }
  manifest.host_permissions = ["http://127.0.0.1/*"];
  await writeFile(path.join(testExtension, "manifest.json"), JSON.stringify(manifest));
  browser = await chromium.launchPersistentContext(path.join(scratch, "profile"), {
    channel: "chromium", headless: true,
    args: [`--disable-extensions-except=${testExtension}`, `--load-extension=${testExtension}`],
  });
  const worker = browser.serviceWorkers()[0] || await browser.waitForEvent("serviceworker");
  const extensionId = new URL(worker.url()).hostname;
  const errors = [];
  const panel = await browser.newPage();
  panel.on("pageerror", error => errors.push(error.message));
  await panel.setViewportSize({width: 380, height: 1000});
  await panel.goto(`chrome-extension://${extensionId}/panel.html`);
  await panel.waitForFunction(() => document.querySelector("#start-report").disabled);

  await browser.route("https://app.slack.com/**", route => route.fulfill({
    contentType: "text/html",
    body: '<!doctype html><title>Slack test fixture</title><main><h1>Sample project channel</h1><p id="request">Please prepare the client update before cleanup.</p></main>',
  }));
  const slack = await browser.newPage();
  await slack.goto("https://app.slack.com/client/T123/C123");
  await slack.bringToFront();
  const click = async selector => {
    await panel.waitForFunction(selector => {const el = document.querySelector(selector); return el && !el.disabled && !el.hidden;}, selector);
    // Interact without making the standalone panel test tab the active Slack tab.
    await panel.locator(selector).evaluate(element => element.click());
  };
  await click("#refresh-context");
  await panel.waitForFunction(() => document.querySelector("#channel-name").textContent.includes("C123"));
  await slack.evaluate(() => {const range = document.createRange(); range.selectNodeContents(document.querySelector("#request")); const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);});
  await click("#use-selection");
  await panel.waitForFunction(() => document.querySelector("#request-text").value.includes("client update"));
  await click("#start-report");
  await panel.waitForFunction(() => !document.querySelector("#report-section").hidden);
  await click("#start-cleanup");
  await panel.waitForFunction(() => document.querySelectorAll(".cleanup-card").length === 1);
  assert.deepEqual(await panel.locator(".cleanup-card .status-badge").allTextContents(), ["DEFER"]);
  assert.equal(await panel.locator("[data-cleanup-approval]").count(), 0);
  await click("#approve-report");
  await panel.waitForFunction(() => document.querySelector("#report-status").textContent === "Published");
  assert.equal(await panel.locator("[data-cleanup-approval]").count(), 1);
  await mkdir(path.join(repoDir, "artifacts"), {recursive: true});
  assert.equal(await panel.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, "Panel must fit a narrow viewport");
  await panel.screenshot({path: path.join(repoDir, "artifacts", "browser-preview.png"), fullPage: true});

  // A navigation must remove the old channel's reviews from the panel.
  await slack.goto("https://app.slack.com/client/T123/C456");
  await click("#refresh-context");
  await panel.waitForFunction(() => document.querySelector("#channel-name").textContent.includes("C456") && document.querySelector("#report-section").hidden);

  // Connect the actual extension service worker to the actual Python executor.
  bridge = spawn("python3", ["browser_bridge.py", "--extension-id", extensionId], {cwd: repoDir, stdio: ["ignore", "pipe", "pipe"]});
  const startup = await new Promise((resolve, reject) => {
    let output = "";
    const timer = setTimeout(() => reject(new Error("Local bridge did not start")), 10000);
    bridge.on("exit", code => {clearTimeout(timer); reject(new Error(`Local bridge exited: ${code}`));});
    bridge.stdout.on("data", chunk => {output += chunk; if (output.includes("Sample reports do not use an AI model.")) {clearTimeout(timer); resolve(output);}});
  });
  const tokenPath = startup.match(/Pairing token file \(private; copy its contents into extension settings\): (.+)/)?.[1];
  fixtureRoot = startup.match(/Disposable fixtures and audit files: (.+)/)?.[1];
  assert.ok(tokenPath, "Bridge provides a private token file path");
  const token = (await readFile(tokenPath, "utf8")).trim();
  const connected = await panel.evaluate(token => chrome.runtime.sendMessage({type: "CONNECT", token}), token);
  assert.equal(connected.ok, true, connected.error?.message);
  await click("#refresh-context");
  await panel.waitForFunction(() => document.querySelector("#mode-badge").textContent === "Local demo");
  await click("#start-report");
  await panel.waitForFunction(() => !document.querySelector("#report-section").hidden);
  await click("#start-cleanup");
  await panel.waitForFunction(() => document.querySelectorAll(".cleanup-card").length === 1);
  assert.deepEqual(await panel.locator(".cleanup-card .status-badge").allTextContents(), ["DEFER"]);
  assert.equal(await panel.locator("[data-cleanup-approval]").count(), 0);
  await click("#approve-report");
  await panel.waitForFunction(() => document.querySelector("#report-status").textContent === "Published");
  await click("[data-cleanup-approval]");
  await panel.waitForFunction(() => Array.from(document.querySelectorAll(".cleanup-card .status-badge")).filter(el => el.textContent === "Quarantined").length === 1);
  await panel.screenshot({path: path.join(repoDir, "artifacts", "browser-local-demo.png"), fullPage: true});
  assert.deepEqual(errors, []);
  console.log("Chromium smoke passed: actual extension, Slack page fixture, channel isolation, and local executor approval flow.");
  console.log("Permission was pre-granted in the test copy; native toolbar/side-panel opening and the permission prompt remain manual checks.");
} finally {
  if (bridge && bridge.exitCode === null) {
    bridge.kill("SIGINT");
    await new Promise(resolve => {bridge.once("exit", resolve); setTimeout(resolve, 2000);});
  }
  await browser?.close();
  await rm(scratch, {recursive: true, force: true});
  if (fixtureRoot?.startsWith(path.join(tmpdir(), "reflex_browser_demo_"))) await rm(fixtureRoot, {recursive: true, force: true});
}
