const app = getApp();
const CACHE_TTL_MS = 10000;
let loginPromise = null;
let snapshotPromise = null;
let cacheGeneration = 0;

function createRequestError(response) {
  const error = new Error(response.data && response.data.message || "请求失败");
  error.code = response.data && response.data.code || "REQUEST_FAILED";
  error.statusCode = response.statusCode;
  return error;
}

function rawRequest(path, options = {}) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${app.globalData.apiBase}${path}`,
      method: options.method || "GET",
      data: options.data || {},
      header: { "content-type": "application/json", ...(options.header || {}) },
      success(response) {
        if (response.statusCode >= 200 && response.statusCode < 300) resolve(response.data);
        else reject(createRequestError(response));
      },
      fail(error) {
        reject(new Error(error.errMsg || "网络连接失败，请稍后重试"));
      }
    });
  });
}

async function login() {
  let result;
  if (app.globalData.apiBase.includes("127.0.0.1")) {
    result = await rawRequest("/api/auth/dev-login", { method: "POST", data: { userId: 1 } });
  } else {
    const loginResult = await new Promise((resolve, reject) => wx.login({ success: resolve, fail: reject }));
    result = await rawRequest("/api/auth/wechat-login", {
      method: "POST",
      data: {
        code: loginResult.code,
        inviteCode: app.globalData.pendingInviteCode || "",
        inviteSource: app.globalData.pendingInviteSource || "SHARE_LINK",
        sourceValue: app.globalData.pendingInviteSourceValue || app.globalData.pendingInviteCode || "",
        landingScene: app.globalData.pendingLandingScene || ""
      }
    });
    app.globalData.pendingInviteCode = "";
    app.globalData.pendingInviteSource = "";
    app.globalData.pendingInviteSourceValue = "";
    app.globalData.pendingLandingScene = "";
  }
  app.globalData.userToken = result.token;
  return result.token;
}

function ensureLogin() {
  if (app.globalData.userToken) return Promise.resolve(app.globalData.userToken);
  if (!loginPromise) loginPromise = login().finally(() => { loginPromise = null; });
  return loginPromise;
}

async function request(path, options = {}) {
  const token = await ensureLogin();
  try {
    return await rawRequest(path, { ...options, header: { Authorization: `Bearer ${token}`, ...(options.header || {}) } });
  } catch (error) {
    if (error.statusCode !== 401 || options.retryAfterLogin === false) throw error;
    app.globalData.userToken = "";
    const renewedToken = await ensureLogin();
    return rawRequest(path, { ...options, retryAfterLogin: false, header: { Authorization: `Bearer ${renewedToken}`, ...(options.header || {}) } });
  }
}

function invalidateSnapshot() {
  cacheGeneration += 1;
  app.globalData.snapshotCache = null;
}

function markSnapshotStale() {
  cacheGeneration += 1;
  if (app.globalData.snapshotCache) app.globalData.snapshotCache.expiresAt = 0;
}

function getCachedSnapshot() {
  return app.globalData.snapshotCache && app.globalData.snapshotCache.data || null;
}

function requestSnapshot() {
  const requestGeneration = cacheGeneration;
  let trackedPromise;
  trackedPromise = request("/api/snapshot")
    .then((data) => {
      if (requestGeneration === cacheGeneration) {
        app.globalData.snapshotCache = { data, expiresAt: Date.now() + CACHE_TTL_MS };
      }
      return data;
    })
    .finally(() => {
      if (snapshotPromise === trackedPromise) snapshotPromise = null;
    });
  snapshotPromise = trackedPromise;
  return trackedPromise;
}

function snapshot(options = {}) {
  const now = Date.now();
  const cache = app.globalData.snapshotCache;
  if (!options.force && cache) {
    if (cache.expiresAt <= now && !snapshotPromise) requestSnapshot().catch(() => {});
    return Promise.resolve(cache.data);
  }
  if (!options.force && snapshotPromise) return snapshotPromise;
  if (options.force) invalidateSnapshot();
  return requestSnapshot();
}

module.exports = { request, snapshot, getCachedSnapshot, invalidateSnapshot, markSnapshotStale };
