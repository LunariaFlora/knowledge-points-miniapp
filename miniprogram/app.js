App({
  globalData: {
    apiBase: "https://api.example.com",
    userToken: "",
    snapshotCache: null,
    pendingInviteCode: "",
    pendingInviteSource: "",
    pendingInviteSourceValue: "",
    pendingLandingScene: "",
    pendingGiftId: 0
  },

  onLaunch(options) {
    this.captureInviteCode(options);
    setTimeout(() => {
      require("./utils/api").snapshot().catch(() => {});
    }, 0);
  },

  onShow(options) {
    this.captureInviteCode(options);
  },

  captureInviteCode(options = {}) {
    const query = options.query || {};
    let miniCodeScene = "";
    try { miniCodeScene = decodeURIComponent(query.scene || ""); } catch (_) { miniCodeScene = String(query.scene || ""); }
    const sceneValues = miniCodeScene.split("&").reduce((result, part) => {
      const [key, value = ""] = part.split("=");
      if (key) result[key] = value;
      return result;
    }, {});
    const inviteCode = query.inviteCode || sceneValues.i || "";
    if (!inviteCode) return;
    this.globalData.pendingInviteCode = String(inviteCode).trim();
    this.globalData.pendingInviteSource = String(query.inviteSource || (miniCodeScene ? "QR_CODE" : "SHARE_LINK"));
    this.globalData.pendingInviteSourceValue = miniCodeScene || this.globalData.pendingInviteCode;
    this.globalData.pendingLandingScene = String(options.scene || "");
  },

  assetUrl(path) {
    if (!path) return "";
    if (/^https?:\/\//.test(path)) return path;
    if (path.startsWith("/assets/")) return path;
    return `${this.globalData.apiBase}${path}`;
  }
});
