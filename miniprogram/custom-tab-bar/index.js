Component({
  data: {
    hidden: false,
    selected: 0,
    tabs: [
      { path: "/pages/home/home", label: "首页", icon: "⌂", iconClass: "home-icon" },
      { path: "/pages/exchange/exchange", label: "兑换", icon: "🎁", iconClass: "gift-icon" },
      { path: "/pages/profile/profile", label: "我的", icon: "👤", iconClass: "user-icon" }
    ]
  },
  methods: {
    switchTab(event) {
      const { index, path } = event.currentTarget.dataset;
      const nextIndex = Number(index);
      if (nextIndex === this.data.selected) return;
      wx.switchTab({
        url: path,
        success: () => this.setData({ selected: nextIndex })
      });
    }
  }
});
