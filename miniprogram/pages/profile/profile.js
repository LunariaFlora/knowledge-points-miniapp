const api = require("../../utils/api");
Page({
  data: { userName: "微信用户", points: 0, totalEarned: 0, successfulInvites: 0, customerServicePhone: "", errorMessage: "" },
  onShow() {
    const tabBar = this.getTabBar && this.getTabBar();
    if (tabBar) tabBar.setData({ selected: 2 });
    return this.load();
  },
  async load() {
    try {
      const snapshot = await api.snapshot();
      const totalEarned = (snapshot.transactions || []).filter((item) => Number(item.amount) > 0).reduce((sum, item) => sum + Number(item.amount), 0);
      this.setData({ userName: String(snapshot.user && snapshot.user.name || "微信用户").replace("（主用户）", ""), points: Number(snapshot.user && snapshot.user.points) || 0, totalEarned, successfulInvites: (snapshot.invitations || []).filter((item) => item.status === "REWARDED").length, customerServicePhone: snapshot.settings && snapshot.settings.customerServicePhone || "", errorMessage: "" });
    } catch (error) { this.setData({ errorMessage: error.message || "个人信息加载失败" }); }
  },
  openPage(event) { wx.navigateTo({ url: `/pages/${event.currentTarget.dataset.page}/${event.currentTarget.dataset.page}` }); },
  callService() { if (!this.data.customerServicePhone) return wx.showToast({ title: "客服电话尚未设置", icon: "none" }); wx.makePhoneCall({ phoneNumber: this.data.customerServicePhone }); }
});
