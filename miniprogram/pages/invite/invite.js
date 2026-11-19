const api = require("../../utils/api");
Page({
  data: { inviteCode: "", inviteTotal: 0, inviteSuccess: 0, items: [], posterVisible: false, miniCodeUrl: "" },
  onLoad() { wx.showShareMenu({ menus: ["shareAppMessage"] }); },
  onShow() { return this.load(); },
  async load() { try { const snapshot = await api.snapshot(); const items = snapshot.invitations || []; this.setData({ inviteCode: snapshot.user && snapshot.user.invite_code || "", inviteTotal: items.length, inviteSuccess: items.filter((item) => item.status === "REWARDED").length, items }); } catch (error) { wx.showToast({ title: error.message || "邀请信息加载失败", icon: "none" }); } },
  async showPoster() {
    const tabBar = this.getTabBar && this.getTabBar();
    if (tabBar) tabBar.setData({ hidden: true });
    this.setData({ posterVisible: true });
    if (this.data.miniCodeUrl || getApp().globalData.apiBase.includes("127.0.0.1")) return;
    try {
      const result = await api.request("/api/invitations/mini-code", { method: "POST" });
      this.setData({ miniCodeUrl: getApp().assetUrl(result.url) });
    } catch (error) { wx.showToast({ title: error.message || "小程序码生成失败", icon: "none" }); }
  },
  hidePoster() { this.setData({ posterVisible: false }); const tabBar = this.getTabBar && this.getTabBar(); if (tabBar) tabBar.setData({ hidden: false }); },
  onUnload() { const tabBar = this.getTabBar && this.getTabBar(); if (tabBar) tabBar.setData({ hidden: false }); },
  onShareAppMessage() { return { title: "一起看健康知识，攒积分兑到店好礼", path: `/pages/home/home?inviteCode=${encodeURIComponent(this.data.inviteCode)}&inviteSource=SHARE_CARD` }; }
});
