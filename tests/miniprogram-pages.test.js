const assert = require("node:assert/strict");
const path = require("node:path");

const root = path.resolve(__dirname, "..", "miniprogram");
const apiPath = path.join(root, "utils", "api.js");

function createPageState(definition) {
  return {
    ...definition,
    data: JSON.parse(JSON.stringify(definition.data || {})),
    setData(patch) { Object.assign(this.data, patch); }
  };
}

function loadPage(relativePath, apiOverrides = {}) {
  let definition;
  const calls = [];
  global.Page = (value) => { definition = value; };
  global.getApp = () => ({
    globalData: { apiBase: "http://127.0.0.1:8770", userToken: "test-token" },
    assetUrl: (value) => /^https?:\/\//.test(value) || value.startsWith("/assets/") ? value : `http://127.0.0.1:8770${value}`
  });
  global.wx = {
    navigateTo: (options) => calls.push(["navigateTo", options]),
    switchTab: (options) => calls.push(["switchTab", options]),
    showToast: (options) => calls.push(["showToast", options])
  };

  delete require.cache[require.resolve(apiPath)];
  const api = require(apiPath);
  Object.assign(api, apiOverrides);
  const pagePath = path.join(root, relativePath);
  delete require.cache[require.resolve(pagePath)];
  require(pagePath);
  return { page: createPageState(definition), calls, api };
}

async function testHome() {
  const snapshot = {
    gifts: [{ id: 1, name: "小氢皂", image_url: "/assets/gift-soap.svg", market_price: 68, points_cost: 3 }]
  };
  const { page, calls } = loadPage("pages/home/home.js", { snapshot: async () => snapshot });
  let selected = -1;
  page.getTabBar = () => ({ setData: (value) => { selected = value.selected; } });
  await page.onShow();
  assert.equal(selected, 0);
  assert.equal(page.data.gifts.length, 1);
  assert.equal(page.data.gifts[0].displayImage, "/assets/gift-soap.svg");
  page.goVideos();
  assert.deepEqual(calls.at(-1), ["navigateTo", { url: "/pages/videos/videos" }]);
}

async function testVideos() {
  const snapshot = {
    user: { points: 5 },
    transactions: [{ amount: 5 }, { amount: -3 }, { amount: 1 }],
    videos: [
      { id: 1, title: "专题会报告", category: "专题", duration_seconds: 45, total_points: 1, cover_url: "/assets/video-conference.svg", view_count: 12, user_valid_seconds: 15, user_rewarded_points: 0 },
      { id: 2, title: "健康访谈", category: "访谈", duration_seconds: 65, total_points: 1, cover_url: "/uploads/custom-cover.png", view_count: 3 }
    ]
  };
  const { page, calls } = loadPage("pages/videos/videos.js", { snapshot: async () => snapshot });
  await page.load();
  assert.equal(page.data.points, 5);
  assert.equal(page.data.totalEarned, 6);
  assert.deepEqual(page.data.categories, ["全部", "专题", "访谈"]);
  assert.equal(page.data.videos[1].displayDuration, "01:05");
  assert.equal(page.data.videos[0].displayViews, 12);
  assert.equal(page.data.videos[0].progressText, "已看 00:15 · 已得 0/1分");
  assert.equal(page.data.videos[1].displayCover, "http://127.0.0.1:8770/uploads/custom-cover.png");
  page.changeCategory({ currentTarget: { dataset: { category: "访谈" } } });
  assert.deepEqual(page.data.filteredVideos.map((item) => item.id), [2]);
  page.openPlayer({ currentTarget: { dataset: { id: 2 } } });
  assert.deepEqual(calls.at(-1), ["navigateTo", { url: "/pages/player/player?id=2" }]);
}

async function testPlayerHeartbeat() {
  const requests = [];
  let markedStale = false;
  const snapshot = {
    videos: [{ id: 1, title: "专题会报告", media_url: "/media/demo.mp4", duration_seconds: 45, total_points: 1, user_valid_seconds: 10, user_rewarded_points: 0, view_count: 1 }]
  };
  const { page, calls } = loadPage("pages/player/player.js", {
    snapshot: async () => snapshot,
    request: async (url, options) => {
      requests.push([url, options]);
      if (url === "/api/watch/sessions") return { sessionToken: "session-1", resumePositionSeconds: 10 };
      if (url === "/api/watch/heartbeat") return { awardedPoints: 0, validSeconds: 15 };
      return { awardedPoints: 1 };
    },
    markSnapshotStale: () => { markedStale = true; }
  });
  page.videoId = 1;
  await page.loadVideo();
  assert.equal(page.data.videoUrl, "http://127.0.0.1:8770/media/demo.mp4");
  assert.equal(page.data.resumePosition, 10);
  await page.handlePlay();
  await page.handleTimeUpdate({ detail: { currentTime: 15.2 } });
  assert.equal(requests[0][0], "/api/watch/sessions");
  assert.equal(requests[1][0], "/api/watch/heartbeat");
  assert.equal(requests[1][1].data.positionSeconds, 15);
  assert.equal(page.data.lastReportedPosition, 15);
  assert.equal(page.data.progressPercent, 33);
  assert.equal(markedStale, false);
  assert.equal(calls.some(([name]) => name === "showToast"), false);
  markedStale = false;
  await page.handleEnded({ detail: { currentTime: 45 } });
  assert.equal(requests[2][0], "/api/watch/complete");
  assert.equal(requests[2][1].data.positionSeconds, 45);
  assert.equal(markedStale, true);
}

(async () => {
  await testHome();
  await testVideos();
  await testPlayerHeartbeat();
  console.log("miniprogram page logic: ok");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
