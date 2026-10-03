// Run the actual vendored browser libraries without network access.
import { readFile } from "node:fs/promises";
import { strict as assert } from "node:assert";
import vm from "node:vm";

try {
  const bridge = await readFile(new URL("../fastmcp_server/resources/static/vendor/ext-apps/2.0.3/app-with-deps.js", import.meta.url));
  const { App, LATEST_PROTOCOL_VERSION } = await import(`data:text/javascript;base64,${bridge.toString("base64")}`);
  const transport = {
    async start() {},
    async close() {},
    async send(message) {
      if (message.method !== "ui/initialize") return;
      queueMicrotask(() => this.onmessage({
        jsonrpc: "2.0", id: message.id,
        result: {
          protocolVersion: "2026-01-26",
          hostInfo: { name: "Waystation", version: "1.0.0" },
          hostCapabilities: {
            serverTools: {}, serverResources: {}, openLinks: {},
            message: { text: {} },
            updateModelContext: { structuredContent: {}, text: {} },
            sandbox: { csp: { resourceDomains: ["http://localhost:3000"] } },
          },
          hostContext: {
            theme: "dark", locale: "en-US", platform: "web", displayMode: "inline",
            availableDisplayModes: ["inline", "fullscreen"],
            containerDimensions: { maxHeight: 600, maxWidth: 1280 },
          },
        },
      }));
    },
  };
  const app = new App({ name: "Waystation asset check", version: "1.0.0" }, {}, { autoResize: false });
  await app.connect(transport);
  assert.equal(LATEST_PROTOCOL_VERSION, "2026-01-26");
  assert.equal(app.getHostContext().theme, "dark");
  let notifiedTheme;
  app.onhostcontextchanged = (context) => { notifiedTheme = context.theme; };
  transport.onmessage({
    jsonrpc: "2.0", method: "ui/notifications/host-context-changed",
    params: { theme: "light", displayMode: "inline", containerDimensions: { maxHeight: 600, maxWidth: 1280 } },
  });
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(notifiedTheme, "light");
  await app.close();

  const context = { setTimeout, clearTimeout, setInterval, clearInterval, performance };
  vm.createContext(context);
  vm.runInContext(await readFile(new URL("../fastmcp_server/resources/static/vendor/d3/7.9.0/d3.min.js", import.meta.url), "utf8"), context);
  const d3 = context.d3;
  assert.equal(d3.version, "7.9.0");
  for (const name of ["select", "scaleOrdinal", "forceSimulation", "forceLink", "forceManyBody", "forceCenter", "forceCollide", "drag"]) {
    assert.equal(typeof d3[name], "function", name);
  }
  const nodes = [{ id: "a", radius: 5 }, { id: "b", radius: 5 }];
  d3.forceSimulation(nodes)
    .force("link", d3.forceLink([{ source: "a", target: "b" }]).id((node) => node.id))
    .force("charge", d3.forceManyBody())
    .force("center", d3.forceCenter(200, 200))
    .force("collision", d3.forceCollide().radius((node) => node.radius + 10))
    .stop().tick(20);
  assert(nodes.every((node) => Number.isFinite(node.x) && Number.isFinite(node.y)));
  console.log("PASS vendored Apps 2.0.3 initialize/theme bridge and D3 7.9.0 graph APIs");
} catch (error) {
  console.error(`Widget asset check failed: ${error.message}`);
  process.exitCode = 1;
}
