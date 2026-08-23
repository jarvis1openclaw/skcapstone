#!/usr/bin/env node
/** Qualify session reload and CSP delivery through real Chrome DevTools. */

import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawn } from "node:child_process";

class QualificationError extends Error {}

class Cdp {
  constructor(url) {
    this.socket = new WebSocket(url);
    this.nextId = 1;
    this.pending = new Map();
    this.events = [];
  }

  async open() {
    await new Promise((resolve, reject) => {
      this.socket.addEventListener("open", resolve, { once: true });
      this.socket.addEventListener("error", reject, { once: true });
    });
    this.socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      const pending = this.pending.get(message.id);
      if (pending) {
        this.pending.delete(message.id);
        if (message.error)
          pending.reject(new QualificationError(message.error.message));
        else pending.resolve(message.result ?? {});
      } else {
        this.events.push(message);
      }
    });
    return this;
  }

  call(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }

  async evaluate(expression) {
    const result = await this.call("Runtime.evaluate", {
      expression,
      returnByValue: true,
    });
    return result.result?.value;
  }

  close() {
    this.socket.close();
  }
}

function argumentsByName(values) {
  const parsed = {};
  for (let index = 0; index < values.length; index += 2) {
    const key = values[index];
    const value = values[index + 1];
    if (!key?.startsWith("--") || value === undefined) {
      throw new QualificationError("arguments must be --name value pairs");
    }
    parsed[key.slice(2)] = value;
  }
  return parsed;
}

async function poll(cdp, expression, expected) {
  const deadline = Date.now() + 10_000;
  let last;
  while (Date.now() < deadline) {
    last = await cdp.evaluate(expression);
    if (last === expected) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new QualificationError(`expected ${expected}, received ${last}`);
}

async function waitForPage(debugPort) {
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${debugPort}/json/list`);
      const pages = await response.json();
      const page = pages.find((entry) => entry.type === "page");
      if (page) return page;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new QualificationError(
    "Chrome debugging endpoint did not become ready",
  );
}

async function main() {
  const args = argumentsByName(process.argv.slice(2));
  for (const required of ["web-url", "matter-id", "debug-port", "output"]) {
    if (!args[required]) throw new QualificationError(`missing --${required}`);
  }
  const matterPath = `/matters/${args["matter-id"]}`;
  const expectedCsp =
    "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'; object-src 'none'";
  const response = await fetch(`${args["web-url"]}${matterPath}`, {
    method: "HEAD",
    cache: "no-store",
  });
  if (response.headers.get("content-security-policy") !== expectedCsp) {
    throw new QualificationError("direct-route response CSP differs");
  }

  const profile = await mkdtemp(join(tmpdir(), "sklegal-cc214fff-chrome-"));
  const chrome = spawn(
    args.chrome ?? "google-chrome",
    [
      "--headless=new",
      "--disable-gpu",
      "--disable-background-networking",
      "--no-first-run",
      "--no-default-browser-check",
      "--remote-debugging-address=127.0.0.1",
      `--remote-debugging-port=${args["debug-port"]}`,
      `--user-data-dir=${profile}`,
      "about:blank",
    ],
    { stdio: "ignore", detached: true },
  );
  try {
    const page = await waitForPage(args["debug-port"]);
    const cdp = await new Cdp(page.webSocketDebuggerUrl).open();
    try {
      for (const method of [
        "Page.enable",
        "Runtime.enable",
        "Log.enable",
        "Network.enable",
        "Accessibility.enable",
      ]) {
        await cdp.call(method);
      }
      await cdp.call("Page.navigate", {
        url: `${args["web-url"]}${matterPath}`,
      });
      await poll(cdp, "document.readyState", "complete");
      await poll(cdp, "location.pathname", "/sign-in");
      await cdp.evaluate(
        'document.querySelector("button[type=submit]").click()',
      );
      await poll(cdp, "location.pathname", matterPath);
      await poll(
        cdp,
        'document.querySelector("#ai-cockpit h2")?.textContent?.trim()',
        "AI Matter cockpit",
      );
      const requiredMatterText = [
        "Public Synthetic Supply Agreement Review",
        "Public Synthetic Client",
        "The invented delivery date is 2099-02-01.",
        "Public Synthetic Invented Agreement",
        "Public Synthetic Review Memorandum",
        "work_product.approved",
        "public-synthetic-snapshot-v1",
        "receipt_verified",
        "No typed recommendation or scoring-policy response is mounted",
        "No model request is sent",
      ];
      const missingMatterText = await cdp.evaluate(
        `${JSON.stringify(requiredMatterText)}.filter((value) => !document.body.innerText.includes(value))`,
      );
      if (missingMatterText.length) {
        throw new QualificationError(
          `enriched or truthful Matter text missing: ${missingMatterText.join(", ")}`,
        );
      }
      cdp.events = [];
      await cdp.call("Page.reload", { ignoreCache: true });
      await poll(cdp, "document.readyState", "complete");
      await poll(cdp, "location.pathname", matterPath);
      await poll(
        cdp,
        'document.querySelector("#ai-cockpit h2")?.textContent?.trim()',
        "AI Matter cockpit",
      );
      const storage = await cdp.evaluate(
        "({local: Object.keys(localStorage), session: Object.keys(sessionStorage), cookie: document.cookie})",
      );
      if (
        storage.local.length !== 0 ||
        storage.session.length !== 0 ||
        storage.cookie !== ""
      ) {
        throw new QualificationError(
          "script-readable browser state is not empty",
        );
      }
      const accessibility = await cdp.call("Accessibility.getFullAXTree");
      const namedGroups = accessibility.nodes.filter(
        (node) =>
          node.role?.value === "group" &&
          typeof node.name?.value === "string" &&
          node.name.value.length > 0,
      ).length;
      if (namedGroups < 2) {
        throw new QualificationError(
          "truthful cockpit groups are absent from the accessibility tree",
        );
      }
      await cdp.call("Emulation.setDeviceMetricsOverride", {
        width: 390,
        height: 844,
        deviceScaleFactor: 1,
        mobile: true,
      });
      const compactLayout = await cdp.evaluate(
        "({width: document.documentElement.clientWidth, pageOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth, labelledScrollRegions: document.querySelectorAll('[role=region][aria-label]').length})",
      );
      await cdp.call("Emulation.clearDeviceMetricsOverride");
      if (
        compactLayout.width !== 390 ||
        compactLayout.pageOverflow ||
        compactLayout.labelledScrollRegions < 1
      ) {
        throw new QualificationError(
          "compact layout or labelled scroll-region contract failed",
        );
      }
      await cdp.evaluate(
        `history.pushState({}, "", "/corpus?matterId=${args["matter-id"]}"); dispatchEvent(new PopStateEvent("popstate"))`,
      );
      await poll(
        cdp,
        'document.querySelector("#corpus-search-input") !== null',
        true,
      );
      await cdp.evaluate(
        `(() => {
          const input = document.querySelector("#corpus-search-input");
          const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
          setter.call(input, "invented delivery date");
          input.dispatchEvent(new Event("input", { bubbles: true }));
          input.form.requestSubmit();
        })()`,
      );
      await poll(
        cdp,
        'document.body.innerText.includes("Public Synthetic Widget Rule")',
        true,
      );
      await poll(
        cdp,
        'document.body.innerText.includes("Invented fixture authority for the invented delivery date only.")',
        true,
      );
      const requiredCorpusText = [
        "The invented delivery date is 2099-02-01.",
        "Public Synthetic Widget Rule",
        "PUB-SYN 1:1",
        "Invented fixture authority for the invented delivery date only.",
        "official_sources",
        "Unavailable",
      ];
      const missingCorpusText = await cdp.evaluate(
        `${JSON.stringify(requiredCorpusText)}.filter((value) => !document.body.innerText.includes(value))`,
      );
      if (missingCorpusText.length) {
        throw new QualificationError(
          `governed corpus text missing: ${missingCorpusText.join(", ")}`,
        );
      }
      const networkFailures = cdp.events.filter(
        (event) =>
          event.method === "Network.responseReceived" &&
          event.params.response.status >= 400,
      );
      if (networkFailures.length) {
        throw new QualificationError(
          "reload or governed corpus journey produced a failed network response",
        );
      }
      const cspEvents = cdp.events.filter(
        (event) =>
          event.method === "Log.entryAdded" &&
          JSON.stringify(event)
            .toLowerCase()
            .includes("content security policy"),
      );
      if (cspEvents.length) {
        throw new QualificationError("Chrome reported a CSP warning or error");
      }
      await writeFile(
        args.output,
        `${JSON.stringify(
          {
            status: "PASS",
            matterPath,
            reloadPath: matterPath,
            corpusPath: await cdp.evaluate(
              "location.pathname + location.search",
            ),
            csp: expectedCsp,
            storage,
            networkFailures: 0,
            namedAccessibilityGroups: namedGroups,
            compactLayout,
            cspConsoleEvents: 0,
          },
          null,
          2,
        )}\n`,
      );
    } finally {
      cdp.close();
    }
  } finally {
    chrome.kill("SIGTERM");
    await new Promise((resolve) => chrome.once("exit", resolve));
    await rm(profile, {
      recursive: true,
      force: true,
      maxRetries: 10,
      retryDelay: 100,
    });
  }
}

await main();
