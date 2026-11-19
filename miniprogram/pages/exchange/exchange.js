const app = getApp();
const api = require("../../utils/api");
Page({
  data: { loading: true, errorMessage: "", points: 0, totalEarned: 0, gifts: [], allStores: [], stores: [], storeIndex: 0, selectedGift: null, currentImageIndex: 0, showGiftDetail: false, showPickup: false, contactName: "", contactMobile: "", consent: true, submitting: false },
  onShow() {
    if (wx.setNavigationBarTitle) wx.setNavigationBarTitle({ title: "积分兑换" });
    const tabBar = this.getTabBar && this.getTabBar();
    if (tabBar) tabBar.setData({ selected: 1, hidden: false });
    return this.load().then(() => {
      if (app.globalData.pendingGiftId) {
        this.openGiftById(app.globalData.pendingGiftId);
        app.globalData.pendingGiftId = 0;
      }
    });
  },
  async load(options = {}) {
    if (!api.getCachedSnapshot()) this.setData({ loading: true, errorMessage: "" });
    try {
      const snapshot = await api.snapshot(options);
      const points = Number(snapshot.user && snapshot.user.points) || 0;
      const gifts = (snapshot.gifts || []).map((gift) => {
        const canRedeem = points >= Number(gift.points_cost) && Number(gift.available_quantity) > 0;
        const imageRefs = Array.isArray(gift.image_urls) && gift.image_urls.length ? gift.image_urls : [gift.image_url || "/assets/gift-soap.svg"];
        const displayImages = imageRefs.map((item) => app.assetUrl(item));
        return { ...gift, canRedeem, redeemLabel: canRedeem ? "去领取" : Number(gift.available_quantity) > 0 ? "积分不足" : "库存不足", displayImages, displayImage: displayImages[0] };
      });
      const totalEarned = (snapshot.transactions || []).filter((item) => Number(item.amount) > 0).reduce((sum, item) => sum + Number(item.amount), 0);
      const stores = snapshot.stores || (snapshot.store ? [snapshot.store] : []);
      this.setData({ points, totalEarned, gifts, allStores: stores, stores, loading: false, errorMessage: "" });
    } catch (error) { this.setData({ loading: false, errorMessage: error.message || "礼品加载失败，请稍后重试" }); }
  },
  openGift(event) { this.openGiftById(event.currentTarget.dataset.id); },
  setTabBarHidden(hidden) { const tabBar = this.getTabBar && this.getTabBar(); if (tabBar) tabBar.setData({ hidden }); },
  openGiftById(id) { const selectedGift = this.data.gifts.find((gift) => Number(gift.id) === Number(id)); if (selectedGift) { this.setTabBarHidden(true); this.setData({ selectedGift, currentImageIndex: 0, showGiftDetail: true }); } },
  closeGift() { this.setData({ showGiftDetail: false }); this.setTabBarHidden(false); },
  imageChange(event) { this.setData({ currentImageIndex: Number(event.detail.current) || 0 }); },
  openPickup() {
    if (!this.data.selectedGift || !this.data.selectedGift.canRedeem) return;
    const linkedStores = this.data.selectedGift.store_id
      ? this.data.allStores.filter((store) => Number(store.id) === Number(this.data.selectedGift.store_id))
      : this.data.allStores;
    this.setData({ showGiftDetail: false, showPickup: true, stores: linkedStores, storeIndex: 0 });
  },
  closePickup() { if (!this.data.submitting) { this.setData({ showPickup: false }); this.setTabBarHidden(false); } },
  storeChange(event) { this.setData({ storeIndex: Number(event.detail.value) }); },
  nameInput(event) { this.setData({ contactName: event.detail.value }); },
  mobileInput(event) { this.setData({ contactMobile: event.detail.value }); },
  consentChange(event) { this.setData({ consent: event.detail.value.includes("agree") }); },
  async submitPickup() {
    if (!this.data.consent) return wx.showToast({ title: "请先同意门店联系说明", icon: "none" });
    if (!this.data.contactName.trim() || this.data.contactMobile.trim().length < 6) return wx.showToast({ title: "请填写联系人和手机号", icon: "none" });
    const store = this.data.stores[this.data.storeIndex];
    if (!store) return wx.showToast({ title: "暂无可领取门店", icon: "none" });
    this.setData({ submitting: true });
    try {
      const result = await api.request("/api/pickups", { method: "POST", data: { giftId: this.data.selectedGift.id, storeId: store.id, contactName: this.data.contactName.trim(), contactMobile: this.data.contactMobile.trim(), requestId: `wx-pickup-${Date.now()}-${Math.random().toString(16).slice(2)}` } });
      api.invalidateSnapshot();
      this.setData({ showPickup: false, selectedGift: null, submitting: false });
      this.setTabBarHidden(false);
      this.load({ force: true });
      wx.showModal({ title: "申请成功", content: `到店领取码：${result.voucherCode}`, showCancel: false, success: () => wx.navigateTo({ url: "/pages/records/records" }) });
    } catch (error) { this.setData({ submitting: false }); wx.showToast({ title: error.message || "领取申请失败", icon: "none" }); }
  },
  onUnload() { this.setTabBarHidden(false); }
});
