const app = getApp();
const api = require("../../utils/api");
Page({
  data: { loading: true, errorMessage: "", gifts: [] },
  onShow() {
    const tabBar = this.getTabBar && this.getTabBar();
    if (tabBar) tabBar.setData({ selected: 0 });
    return this.load();
  },
  async load() {
    if (!api.getCachedSnapshot() && !this.data.gifts.length) this.setData({ loading: true, errorMessage: "" });
    try {
      const snapshot = await api.snapshot();
      const gifts = (snapshot.gifts || []).map((gift) => ({ ...gift, displayImage: app.assetUrl(gift.image_url || "/assets/gift-soap.svg") }));
      this.setData({ gifts, loading: false });
    } catch (error) {
      this.setData({ loading: false, errorMessage: error.message || "礼品加载失败，请稍后重试" });
    }
  },
  goVideos() { wx.navigateTo({ url: "/pages/videos/videos" }); },
  goExchange(event) {
    app.globalData.pendingGiftId = Number(event.currentTarget.dataset.id) || 0;
    wx.switchTab({ url: "/pages/exchange/exchange" });
  }
});
