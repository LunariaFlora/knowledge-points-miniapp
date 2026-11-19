const api = require("../../utils/api");
const labels = { VIDEO_REWARD: "观看视频奖励", INVITE_REWARD: "邀请好友奖励", PICKUP_DEBIT: "申请到店礼品", PICKUP_REFUND: "取消领取退回积分", MANUAL_ADJUST: "积分调整" };
Page({
  data: { loading: true, activeFilter: "all", filters: [{ value: "all", label: "全部" }, { value: "income", label: "获得" }, { value: "expense", label: "使用" }], items: [], filteredItems: [] },
  onShow() { return this.load(); },
  async load() {
    try {
      const snapshot = await api.snapshot();
      const items = (snapshot.transactions || []).map((item) => ({ ...item, displayTitle: labels[item.transaction_type] || item.description || "积分变动", displayAmount: `${Number(item.amount) > 0 ? "+" : ""}${item.amount}` }));
      this.setData({ items, loading: false }); this.applyFilter();
    } catch (error) { this.setData({ loading: false }); wx.showToast({ title: error.message || "积分明细加载失败", icon: "none" }); }
  },
  changeFilter(event) { this.setData({ activeFilter: event.currentTarget.dataset.value }); this.applyFilter(); },
  applyFilter() { const { items, activeFilter } = this.data; this.setData({ filteredItems: activeFilter === "all" ? items : items.filter((item) => activeFilter === "income" ? Number(item.amount) > 0 : Number(item.amount) < 0) }); }
});
