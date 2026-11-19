const app = getApp();
const api = require("../../utils/api");
function formatDuration(seconds) {
  const value = Math.max(0, Number(seconds) || 0);
  return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
}
Page({
  data: { loading: true, errorMessage: "", points: 0, totalEarned: 0, videos: [], filteredVideos: [], categories: ["全部"], activeCategory: "全部" },
  onShow() { return this.load(); },
  async load() {
    if (!api.getCachedSnapshot() && !this.data.videos.length) this.setData({ loading: true, errorMessage: "" });
    try {
      const snapshot = await api.snapshot();
      const videos = (snapshot.videos || []).map((video) => ({
        ...video,
        displayCover: app.assetUrl(video.cover_url || "/assets/video-conference.svg"),
        displayDuration: formatDuration(video.duration_seconds),
        displayTags: String(video.tags || "").split(/[,，]/).map((tag) => tag.trim()).filter(Boolean),
        displayViews: Number(video.view_count) || 0,
        progressPercent: Math.min(100, Math.round((Number(video.user_valid_seconds) || 0) / Math.max(1, Number(video.duration_seconds)) * 100)),
        progressText: Number(video.user_valid_seconds) ? `已看 ${formatDuration(video.user_valid_seconds)} · 已得 ${video.user_rewarded_points || 0}/${video.total_points}分` : "尚未观看"
      }));
      const categories = ["全部", ...new Set(videos.map((video) => video.category))];
      const totalEarned = (snapshot.transactions || []).filter((item) => Number(item.amount) > 0).reduce((sum, item) => sum + Number(item.amount), 0);
      this.setData({ videos, categories, points: Number(snapshot.user && snapshot.user.points) || 0, totalEarned, loading: false });
      this.applyFilter();
    } catch (error) {
      this.setData({ loading: false, errorMessage: error.message || "视频加载失败，请稍后重试" });
    }
  },
  applyFilter() {
    const { videos, activeCategory } = this.data;
    this.setData({ filteredVideos: activeCategory === "全部" ? videos : videos.filter((video) => video.category === activeCategory) });
  },
  changeCategory(event) { this.setData({ activeCategory: event.currentTarget.dataset.category }); this.applyFilter(); },
  openPlayer(event) { wx.navigateTo({ url: `/pages/player/player?id=${event.currentTarget.dataset.id}` }); },
  goExchange() { wx.switchTab({ url: "/pages/exchange/exchange" }); }
});
