const app = getApp();
const api = require("../../utils/api");
Page({
  data: { loading: true, errorMessage: "", video: {}, videoUrl: "", resumePosition: 0, earnedPoints: 0, progressPercent: 0, progressLabel: "尚未开始", sessionToken: "", lastReportedPosition: 0, isPlaying: false, pageVisible: true, heartbeatPending: false },
  onLoad(options) { this.videoId = Number(options.id); this.loadVideo(); },
  onShow() { this.setData({ pageVisible: true }); },
  onHide() { this.setData({ pageVisible: false, isPlaying: false }); },
  onUnload() { this.setData({ pageVisible: false, isPlaying: false }); },
  async loadVideo() {
    try {
      const snapshot = await api.snapshot();
      const video = (snapshot.videos || []).find((item) => Number(item.id) === this.videoId);
      if (!video) throw new Error("视频不存在或已下架");
      video.displayTags = String(video.tags || "").split(/[,，]/).map((tag) => tag.trim()).filter(Boolean);
      const validSeconds = Math.min(Number(video.user_valid_seconds) || 0, Number(video.duration_seconds) || 0);
      const resumePosition = validSeconds > 0 && validSeconds < Number(video.duration_seconds) ? validSeconds : 0;
      const progressPercent = Math.min(100, Math.round(validSeconds / Math.max(1, Number(video.duration_seconds)) * 100));
      const progressLabel = validSeconds ? `已累计观看 ${Math.floor(validSeconds / 60)}分${validSeconds % 60}秒 · 已得 ${video.user_rewarded_points || 0}/${video.total_points} 分` : "尚未开始观看";
      this.setData({ video, videoUrl: app.assetUrl(video.media_url), resumePosition, earnedPoints: Number(video.user_rewarded_points) || 0, lastReportedPosition: resumePosition, progressPercent, progressLabel, loading: false });
    } catch (error) {
      this.setData({ loading: false, errorMessage: error.message || "视频加载失败，请稍后重试" });
    }
  },
  async ensureSession() {
    if (this.data.sessionToken) return this.data.sessionToken;
    const result = await api.request("/api/watch/sessions", { method: "POST", data: { videoId: this.videoId } });
    this.setData({ sessionToken: result.sessionToken, lastReportedPosition: Number(result.resumePositionSeconds) || 0 });
    return result.sessionToken;
  },
  async handlePlay() {
    this.setData({ isPlaying: true });
    try { await this.ensureSession(); }
    catch (error) { this.setData({ isPlaying: false }); wx.showToast({ title: error.message || "无法开始观看计时", icon: "none" }); }
  },
  handlePause() { this.setData({ isPlaying: false }); },
  async handleEnded(event) {
    this.setData({ isPlaying: false });
    try {
      if (this.heartbeatPromise) await this.heartbeatPromise.catch(() => {});
      if (!this.data.sessionToken) return;
      const result = await api.request("/api/watch/complete", {
        method: "POST",
        data: {
          sessionToken: this.data.sessionToken,
          requestId: `${this.data.sessionToken}-complete`,
          positionSeconds: Number(event.detail && event.detail.currentTime) || Number(this.data.video.duration_seconds) || 0
        }
      });
      const earnedPoints = this.data.earnedPoints + Number(result.awardedPoints || 0);
      this.setData({ earnedPoints, progressPercent: 100, progressLabel: `已完整观看 · 已得 ${earnedPoints}/${this.data.video.total_points} 分` });
      if (result.awardedPoints) api.markSnapshotStale();
    } catch (error) {
      if (error.code !== "WATCH_SESSION_INVALID") wx.showToast({ title: error.message || "观看进度结算失败", icon: "none" });
    }
  },
  async handleTimeUpdate(event) {
    const position = Math.floor(Number(event.detail.currentTime) || 0);
    const boundary = Math.floor(position / 5) * 5;
    if (!this.data.sessionToken || !this.data.isPlaying || !this.data.pageVisible || this.data.heartbeatPending || boundary < 5 || boundary <= this.data.lastReportedPosition) return;
    this.setData({ heartbeatPending: true });
    try {
      this.heartbeatPromise = api.request("/api/watch/heartbeat", { method: "POST", data: { sessionToken: this.data.sessionToken, requestId: `${this.data.sessionToken}-${boundary}`, positionSeconds: boundary, playing: true, pageVisible: true, playbackRate: 1 } });
      const result = await this.heartbeatPromise;
      const duration = Math.max(1, Number(this.data.video.duration_seconds) || 1);
      const validSeconds = Number(result.validSeconds) || 0;
      const earnedPoints = this.data.earnedPoints + Number(result.awardedPoints || 0);
      this.setData({ lastReportedPosition: boundary, earnedPoints, progressPercent: Math.min(100, Math.round(validSeconds / duration * 100)), progressLabel: `已累计观看 ${Math.floor(validSeconds / 60)}分${validSeconds % 60}秒 · 已得 ${earnedPoints}/${this.data.video.total_points} 分` });
      if (result.awardedPoints) {
        api.markSnapshotStale();
      }
    } catch (error) {
      if (!/暂停|离开|倍速|间隔|位置/.test(error.message || "")) wx.showToast({ title: error.message || "观看进度上报失败", icon: "none" });
    } finally { this.heartbeatPromise = null; this.setData({ heartbeatPending: false }); }
  },
  handleVideoError() { wx.showToast({ title: "视频加载失败，请检查服务连接", icon: "none" }); }
});
