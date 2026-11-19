const assert = require("node:assert/strict");
const path = require("node:path");

const app = { globalData: { apiBase: "http://127.0.0.1:8770", userToken: "", snapshotCache: null } };
const requests = [];

global.getApp = () => app;
global.wx = {
  request(options) {
    requests.push(options.url);
    setImmediate(() => {
      if (options.url.endsWith("/api/auth/dev-login")) {
        options.success({ statusCode: 200, data: { token: "token-1" } });
      } else if (options.url.endsWith("/api/snapshot")) {
        options.success({ statusCode: 200, data: { user: { points: requests.length } } });
      } else {
        options.success({ statusCode: 404, data: { message: "not found" } });
      }
    });
  }
};

const api = require(path.resolve(__dirname, "..", "miniprogram", "utils", "api.js"));

(async () => {
  const [first, second] = await Promise.all([api.snapshot(), api.snapshot()]);
  assert.deepEqual(first, second);
  assert.equal(requests.filter((url) => url.endsWith("/api/auth/dev-login")).length, 1);
  assert.equal(requests.filter((url) => url.endsWith("/api/snapshot")).length, 1);

  const cached = await api.snapshot();
  assert.deepEqual(cached, first);
  assert.equal(requests.filter((url) => url.endsWith("/api/snapshot")).length, 1);

  app.globalData.snapshotCache.expiresAt = 0;
  const stale = await api.snapshot();
  assert.deepEqual(stale, first);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(requests.filter((url) => url.endsWith("/api/snapshot")).length, 2);

  await api.snapshot({ force: true });
  assert.equal(requests.filter((url) => url.endsWith("/api/snapshot")).length, 3);
  console.log("miniprogram api cache: ok");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
