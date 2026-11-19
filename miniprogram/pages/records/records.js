const api = require("../../utils/api");
const statusLabels = { WAIT_CONTACT: "待门店联系", WAIT_ARRIVAL: "待到店", PICKED_UP: "已领取", CANCELLED: "已取消", REJECTED: "已拒绝", EXPIRED: "已过期" };
Page({
  data: { loading: true, activeFilter: "all", filters: [{ value: "all", label: "全部" }, { value: "pending", label: "待到店" }, { value: "complete", label: "已完成" }], items: [], filteredItems: [], voucherVisible: false, voucherLoading: false, activeVoucher: null },
  onShow() { return this.load(); },
  async load(options = {}) {
    try {
      const snapshot = await api.snapshot(options);
      const items = (snapshot.pickups || []).map((item) => ({ ...item, statusLabel: statusLabels[item.status] || item.status, canCancel: ["WAIT_CONTACT", "WAIT_ARRIVAL"].includes(item.status), statusClass: item.status === "PICKED_UP" ? "complete" : ["CANCELLED", "REJECTED", "EXPIRED"].includes(item.status) ? "closed" : "pending" }));
      this.setData({ items, loading: false }); this.applyFilter();
    } catch (error) { this.setData({ loading: false }); wx.showToast({ title: error.message || "领取记录加载失败", icon: "none" }); }
  },
  changeFilter(event) { this.setData({ activeFilter: event.currentTarget.dataset.value }); this.applyFilter(); },
  applyFilter() { const { items, activeFilter } = this.data; const pending = ["WAIT_CONTACT", "WAIT_ARRIVAL"]; this.setData({ filteredItems: activeFilter === "all" ? items : items.filter((item) => activeFilter === "pending" ? pending.includes(item.status) : !pending.includes(item.status)) }); },
  copyVoucher(event) {
    const code = String(event.currentTarget.dataset.code || "");
    if (!code) return;
    wx.setClipboardData({ data: code, success: () => wx.showToast({ title: "领取码已复制", icon: "success" }) });
  },
  async showVoucher(event) {
    const id = Number(event.currentTarget.dataset.id);
    const item = this.data.items.find((record) => Number(record.id) === id);
    if (!item) return;
    this.setData({ voucherVisible: true, voucherLoading: true, activeVoucher: { ...item, dataUrl: "" } });
    try {
      const result = await api.request(`/api/pickups/${id}/voucher-qr`, { method: "POST" });
      this.setData({ voucherLoading: false, activeVoucher: { ...item, ...result } });
    } catch (error) {
      this.setData({ voucherLoading: false });
      wx.showToast({ title: error.message || "二维码生成失败", icon: "none" });
    }
  },
  hideVoucher() { this.setData({ voucherVisible: false, activeVoucher: null }); },
  cancelPickup(event) {
    const id = Number(event.currentTarget.dataset.id);
    wx.showModal({ title: "取消领取申请", content: "取消后库存会释放，已扣积分将原路退回积分账户。", confirmText: "确认取消", success: async (result) => {
      if (!result.confirm) return;
      try { await api.request(`/api/pickups/${id}/cancel`, { method: "POST", data: { requestId: `wx-cancel-${id}-${Date.now()}`, reason: "用户在小程序取消" } }); api.invalidateSnapshot(); await this.load({ force: true }); wx.showToast({ title: "已取消并退回积分", icon: "none" }); }
      catch (error) { wx.showToast({ title: error.message || "取消失败", icon: "none" }); }
    }});
  },
  onUnload() { this.setData({ voucherVisible: false, activeVoucher: null }); }
});
