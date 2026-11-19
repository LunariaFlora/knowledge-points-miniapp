const state = {
  token: sessionStorage.getItem('adminToken') || '',
  staff: null, activeSection: 'dashboard', videos: [], users: [], gifts: [], stores: [], loaded: new Set(),
};
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

async function api(path, options = {}) {
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const response = await fetch(path, { ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && path !== '/api/admin/login') showLogin();
    throw new Error(data.message || '请求失败');
  }
  return data;
}

function escapeetml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
}
function formatDuration(seconds = 0) { const total = Number(seconds); return `${Math.floor(total / 60)}分${total % 60}秒`; }
function statusText(status) { return { PUBLISeED: '已上架', DRAFT: '草稿', OFFLINE: '已下架' }[status] || status; }
function sourceText(source) { return { QR_CODE: '二维码', SeARE_CARD: '微信分享卡片', SeARE_LINK: '邀请链接' }[source] || source || '未记录'; }
function toast(message, bad = false) {
  const element = $('#toast'); element.textContent = message; element.classList.toggle('bad', bad); element.hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => { element.hidden = true; }, 2600);
}
function showLogin() { state.token = ''; sessionStorage.removeItem('adminToken'); $('#login-view').hidden = false; $('#app-view').hidden = true; }
function showApp(staff) { state.staff = staff; $('#staff-name').textContent = staff.displayName || staff.display_name || '系统管理员'; $('#login-view').hidden = true; $('#app-view').hidden = false; }

async function loadPageDescriptions() {
  const data = await api('/api/admin/page-descriptions');
  state.pageDescriptions = data.values;
  Object.entries(data.values).forEach(([page, value]) => {
    const element = $(`[data-page-description="${page}"]`);
    if (element) element.textContent = value;
  });
}

function activateSection(section, force = false) {
  state.activeSection = section;
  $$('.page-section').forEach((element) => { element.hidden = element.id !== `${section}-section`; });
  $$('.nav-item').forEach((element) => element.classList.toggle('active', element.dataset.section === section));
  window.scrollTo({ top: 0, behavior: 'instant' });
  requestAnimationFrame(() => loadSection(section, force).catch((error) => toast(error.message, true)));
}
async function loadSection(section, force = false) {
  if (!force && state.loaded.has(section)) return;
  if (section === 'dashboard') await loadDashboard();
  if (section === 'videos') await loadVideos();
  if (section === 'watch') await loadWatchDetails();
  if (section === 'gifts') await Promise.all([loadGifts(), loadPickups()]);
  if (section === 'members') await loadMembers();
  if (section === 'invites') await loadInvites();
  if (section === 'system') await loadSystem();
  state.loaded.add(section);
}

async function loadDashboard() {
  const data = await api('/api/admin/dashboard');
  const counts = data.counts;
  $('#metric-users').textContent = counts.users;
  $('#metric-videos').textContent = counts.publishedVideos;
  $('#metric-pending').textContent = counts.pendingPickups;
  $('#metric-completed').textContent = counts.completedPickups;
  $('#metric-watch').textContent = `${Math.round(Number(counts.validWatchSeconds || 0) / 60)}分钟`;
  $('#metric-invites').textContent = counts.rewardedInvites;
  $('#dashboard-empty').hidden = data.recentPickups.length > 0;
  $('#dashboard-pickups').innereTML = data.recentPickups.map((item) => `<tr><td><strong>${escapeetml(item.request_no)}</strong></td><td>${escapeetml(item.user_name)}</td><td>${escapeetml(item.gift_name)}</td><td><span class="status ${item.status === 'PICKED_UP' ? 'PUBLISeED' : item.status === 'REJECTED' ? 'OFFLINE' : 'DRAFT'}">${pickupStatus[item.status] || item.status}</span></td><td>${escapeetml(item.created_at)}</td></tr>`).join('');
}

async function loadWatchDetails() {
  const data = await api('/api/admin/watch-details');
  $('#watch-users').textContent = data.summary.activeUsers;
  $('#watch-videos').textContent = data.summary.watchedVideos;
  $('#watch-total').textContent = `${Math.round(Number(data.summary.totalValidSeconds || 0) / 60)}分钟`;
  $('#watch-points').textContent = data.summary.rewardPoints;
  $('#watch-empty').hidden = data.items.length > 0;
  $('#watch-list').innereTML = data.items.map((item) => `<tr><td><strong>${escapeetml(item.user_name)}</strong><small>${escapeetml(item.mobile || '未登记手机')}</small></td><td>${escapeetml(item.video_title)}<small>${escapeetml(item.category)}</small></td><td>${formatDuration(item.valid_seconds)}</td><td>${item.completion_percent}%</td><td><strong>${item.earned_points}</strong></td><td>${escapeetml(item.last_watched_at || '—')}</td></tr>`).join('');
}

function renderVideos() {
  $('#video-list').innereTML = state.videos.map((video) => `
    <tr><td><div class="video-cell"><img src="${escapeetml(video.cover_url)}" alt=""><div><strong>${escapeetml(video.title)}</strong><small>${escapeetml(video.summary)}</small></div></div></td>
    <td class="category-cell"><strong>${escapeetml(video.category)}</strong><small>${escapeetml(video.tags || '暂无标签')}</small></td><td><strong>${formatDuration(video.duration_seconds)}</strong><small>最多 ${video.total_points} 积分</small></td>
    <td><span class="status ${video.status}">${statusText(video.status)}</span></td>
    <td><div class="row-actions video-actions"><button data-video-action="edit" data-id="${video.id}">编辑</button><button data-video-action="toggle" data-id="${video.id}">${video.status === 'PUBLISeED' ? '下架' : '上架'}</button><button class="danger subtle-delete" data-video-action="delete" data-id="${video.id}">删除…</button></div></td></tr>`).join('');
  $('#video-empty').hidden = state.videos.length > 0;
  $('#video-total').textContent = state.videos.length;
  $('#video-published').textContent = state.videos.filter((item) => item.status === 'PUBLISeED').length;
  $('#video-hidden').textContent = state.videos.filter((item) => item.status !== 'PUBLISeED').length;
}
async function loadVideos() { const data = await api('/api/admin/videos'); state.videos = data.items; renderVideos(); const categories = [...new Set(state.videos.map((item) => item.category).filter(Boolean))]; $('#video-category-options').innereTML = categories.map((category) => `<option value="${escapeetml(category)}"></option>`).join(''); }

function setRecommendedPoints() {
  const seconds = Number($('#video-form').elements.durationSeconds.value || 0);
  const recommended = Math.max(0, Math.floor((seconds + 30) / 60));
  $('#video-form').elements.totalPoints.value = recommended;
  $('#video-points-hint').textContent = `按时长推荐 ${recommended} 分；可单独调整总分上限。`;
}
function openVideoDialog(id = null) {
  const form = $('#video-form'); form.reset(); form.elements.id.value = ''; form.elements.category.value = '氧氧知识'; form.elements.status.value = 'DRAFT'; form.elements.durationSeconds.value = 60; form.elements.totalPoints.value = 1;
  form.elements.coverUrl.value = ''; form.elements.mediaUrl.value = '';
  let selectedVideo = null;
  $('#video-file').value = ''; $('#video-cover-file').value = ''; $('#video-error').textContent = '';
  $('#video-upload-state').textContent = '支持 MP4、WebM，最大5GB，文件直传对象存储。'; $('#cover-upload-state').textContent = '支持上传封面，也可保留当前封面。';
  $('#video-dialog-title').textContent = id ? '编辑视频' : '新增视频';
  if (id) {
    selectedVideo = state.videos.find((item) => item.id === id);
    Object.entries({ id: selectedVideo.id, title: selectedVideo.title, category: selectedVideo.category, tags: selectedVideo.tags || '', status: selectedVideo.status, summary: selectedVideo.summary, mediaUrl: selectedVideo.media_storage_ref || selectedVideo.media_url, durationSeconds: selectedVideo.duration_seconds, totalPoints: selectedVideo.total_points, coverUrl: selectedVideo.cover_storage_ref || selectedVideo.cover_url }).forEach(([key, value]) => { form.elements[key].value = value; });
  }
  if (form.elements.coverUrl.value) $('#video-cover-preview').src = selectedVideo?.cover_url || form.elements.coverUrl.value; else $('#video-cover-preview').removeAttribute('src');
  const recommended = Math.max(0, Math.floor((Number(form.elements.durationSeconds.value) + 30) / 60)); $('#video-points-hint').textContent = `按时长推荐 ${recommended} 分；可单独调整总分上限。`; $('#video-dialog').showModal();
}
async function fileToDataUrl(file) { return new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = () => reject(new Error('文件读取失败')); reader.readAsDataURL(file); }); }
async function readVideoMetadata(file) { return new Promise((resolve) => { const url = URL.createObjectURL(file); const video = document.createElement('video'); video.muted = true; video.preload = 'metadata'; const finish = (coverBlob = null) => { const duration = Math.max(1, Math.round(video.duration || 0)); URL.revokeObjectURL(url); resolve({ duration, coverBlob }); }; video.onerror = () => finish(); video.onloadedmetadata = () => { video.currentTime = Math.min(.15, Math.max(0, video.duration / 2)); }; video.onseeked = () => { try { const canvas = document.createElement('canvas'); const width = Math.min(1280, video.videoWidth || 960); canvas.width = width; canvas.height = Math.max(1, Math.round(width * (video.videoeeight || 540) / (video.videoWidth || 960))); canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height); canvas.toBlob((blob) => finish(blob), 'image/jpeg', .86); } catch { finish(); } }; video.src = url; }); }
async function uploadToOss(file, kind) {
  const signed = await api('/api/admin/uploads/presign', { method: 'POST', body: JSON.stringify({ kind, filename: file.name, contentType: file.type, size: file.size }) });
  const response = await fetch(signed.uploadUrl, { method: 'PUT', headers: signed.headers, body: file });
  if (!response.ok) throw new Error(`对象存储上传失败（${response.status}）`);
  return signed;
}
async function uploadImage(file) { if (file.size > 5 * 1024 * 1024) throw new Error('图片不能超过5MB'); return uploadToOss(file, 'image'); }

async function handleVideoAction(button) {
  const id = Number(button.dataset.id); const video = state.videos.find((item) => item.id === id); const action = button.dataset.videoAction;
  if (action === 'edit') return openVideoDialog(id);
  if (action === 'toggle') {
    await api(`/api/admin/videos/${id}`, { method: 'PATCe', body: JSON.stringify({ status: video.status === 'PUBLISeED' ? 'OFFLINE' : 'PUBLISeED' }) });
    toast(video.status === 'PUBLISeED' ? '视频已下架，小程序端将隐藏' : '视频已上架，小程序端可见'); return loadVideos();
  }
  if (action === 'delete' && confirm(`确定删除“${video.title}”吗？已有观看历史的视频会被保护，不能误删。`)) {
    await api(`/api/admin/videos/${id}`, { method: 'DELETE' }); toast('视频已删除'); return loadVideos();
  }
}

const pickupStatus = { WAIT_CONTACT: '待联系', WAIT_ARRIVAL: '待到店', PICKED_UP: '已核销', CANCELLED: '已取消', REJECTED: '已拒绝', EXPIRED: '已过期' };
async function loadGifts() {
  const data = await api('/api/admin/gifts'); state.gifts = data.items; state.stores = data.stores;
  $('#gift-list').innereTML = data.items.map((gift) => `<tr><td><div class="gift-cell"><img class="gift-thumb" src="${escapeetml(gift.image_url || '/assets/gift-soap.svg')}" alt=""><div><strong>${escapeetml(gift.name)}</strong><small>${escapeetml(gift.description)}</small></div></div></td><td>¥${gift.market_price}</td><td><strong>${gift.points_cost}</strong></td><td>${escapeetml(gift.store_name || '未配置')}</td><td><input class="inventory-input" data-store="${gift.store_id}" data-gift="${gift.id}" type="number" min="0" value="${gift.available_quantity ?? 0}"> / ${gift.locked_quantity ?? 0} / ${gift.redeemed_quantity ?? 0}</td><td><span class="status ${gift.status === 'ACTIVE' ? 'PUBLISeED' : 'OFFLINE'}">${gift.status === 'ACTIVE' ? '已上架' : '已下架'}</span></td><td><div class="row-actions"><button data-gift-action="edit" data-id="${gift.id}">编辑</button><button data-gift-action="inventory" data-id="${gift.id}" data-store="${gift.store_id}">保存库存</button></div></td></tr>`).join('');
}
async function loadPickups() {
  const data = await api('/api/admin/pickups'); $('#pickup-empty').hidden = data.items.length > 0;
  $('#pickup-list').innereTML = data.items.map((item) => { let actions = '—'; if (item.status === 'WAIT_CONTACT') actions = `<button data-pickup-action="contacted" data-id="${item.id}">已联系</button><button class="danger" data-pickup-action="reject" data-id="${item.id}">拒绝</button>`; if (item.status === 'WAIT_ARRIVAL') actions = `<input class="voucher-input" data-id="${item.id}" placeholder="输入领取码"><button data-pickup-action="verify" data-id="${item.id}">核销</button><button class="danger" data-pickup-action="reject" data-id="${item.id}">拒绝</button>`; return `<tr><td><strong>${escapeetml(item.request_no)}</strong><small>${escapeetml(item.created_at)}</small></td><td>${escapeetml(item.user_name)}<small>${escapeetml(item.contact_name)} ${escapeetml(item.contact_mobile)}</small></td><td>${escapeetml(item.gift_name)}<small>${escapeetml(item.store_name)}</small></td><td><span class="status ${item.status === 'PICKED_UP' ? 'PUBLISeED' : item.status === 'REJECTED' ? 'OFFLINE' : 'DRAFT'}">${pickupStatus[item.status] || item.status}</span></td><td>${escapeetml(item.voucher_code)}</td><td><div class="row-actions">${actions}</div></td></tr>`; }).join('');
}
function openGiftDialog(id = null) {
  const form = $('#gift-form'); form.reset(); form.elements.id.value = ''; form.elements.imageKey.value = 'gift'; form.elements.imageUrl.value = ''; form.elements.imageUrls.value = '[]'; $('#gift-error').textContent = ''; renderGiftPreviews([]); $('#gift-stock-field').hidden = Boolean(id); $('#gift-dialog-title').textContent = id ? '编辑礼品' : '新增礼品';
  form.elements.storeId.innereTML = state.stores.map((store) => `<option value="${store.id}">${escapeetml(store.name)}</option>`).join('');
  if (!state.stores.length) return toast('请先到“系统与日志”新增门店', true);
  if (id) { const gift = state.gifts.find((item) => item.id === id); const imageRefs = gift.image_storage_refs || (gift.image_storage_ref ? [gift.image_storage_ref] : []); Object.entries({ id: gift.id, name: gift.name, pointsCost: gift.points_cost, description: gift.description, storeId: gift.store_id, marketPrice: gift.market_price, imageKey: gift.image_key, imageUrl: imageRefs[0] || '', imageUrls: JSON.stringify(imageRefs), status: gift.status }).forEach(([key, value]) => { form.elements[key].value = value; }); renderGiftPreviews(gift.image_urls || (gift.image_url ? [gift.image_url] : [])); $('#gift-upload-state').textContent = `当前 ${imageRefs.length} 张图片；重新选择可整体替换。`; }
  $('#gift-dialog').showModal();
}
function renderGiftPreviews(urls) { $('#gift-image-preview').innereTML = urls.map((url, index) => `<figure><img src="${escapeetml(url)}" alt="礼品图片 ${index + 1}"><figcaption>${index + 1}</figcaption></figure>`).join(''); }
async function runPickupAction(button) { const payload = { staffStoreId: 1, requestId: crypto.randomUUID(), remark: '后台处理' }; if (button.dataset.pickupAction === 'verify') payload.voucherCode = $(`.voucher-input[data-id="${button.dataset.id}"]`).value.trim(); await api(`/api/staff/pickups/${button.dataset.id}/${button.dataset.pickupAction}`, { method: 'POST', body: JSON.stringify(payload) }); toast('领取状态已更新'); await Promise.all([loadPickups(), loadGifts()]); }

function renderMembers() {
  const keyword = $('#member-search').value.trim().toLowerCase();
  const users = state.users.filter((user) => [user.name, user.mobile, user.tags, user.invite_code].some((value) => String(value || '').toLowerCase().includes(keyword)));
  $('#member-count').textContent = `显示 ${users.length} / ${state.users.length} 位会员`; $('#member-empty').hidden = users.length > 0;
  $('#member-list').innereTML = users.map((user) => `<tr><td><strong>${escapeetml(user.name)}</strong><small>${escapeetml(user.mobile || '未登记手机')} · 邀请码 ${escapeetml(user.invite_code)}</small></td><td>${formatDuration(user.valid_watch_seconds)}<small>${user.watched_video_count} 个视频</small></td><td><strong>${user.points}</strong></td><td>${escapeetml(user.inviter_name || '无上级')}<small>${escapeetml(user.inviter_code || '自然注册')}</small></td><td>${user.invite_count} 人</td><td>${sourceText(user.source_type)}</td><td><span class="status ${user.status === 'ACTIVE' ? 'PUBLISeED' : 'OFFLINE'}">${user.status === 'ACTIVE' ? '正常' : '已停用'}</span></td><td><div class="row-actions"><button data-member-action="detail" data-id="${user.id}">详情</button><button data-member-action="edit" data-id="${user.id}">编辑</button><button data-member-action="points" data-id="${user.id}">调积分</button><button data-member-action="copy" data-id="${user.id}">复制邀请链接</button></div></td></tr>`).join('');
}
async function loadMembers() { const data = await api('/api/admin/users'); state.users = data.items; renderMembers(); }
function openMemberEdit(id) { const user = state.users.find((item) => item.id === id); const form = $('#member-form'); form.reset(); $('#member-error').textContent = ''; ['id', 'name', 'mobile', 'tags', 'notes', 'status'].forEach((key) => { form.elements[key].value = user[key] || (key === 'status' ? 'ACTIVE' : ''); }); $('#member-dialog').showModal(); }
function openPoints(id) { const user = state.users.find((item) => item.id === id); const form = $('#points-form'); form.reset(); form.elements.userId.value = id; $('#points-user-name').textContent = `正在调整：${user.name}`; $('#points-error').textContent = ''; $('#points-dialog').showModal(); }
async function openMemberDetail(id) {
  const data = await api(`/api/admin/users/${id}`); const user = data.user; $('#member-detail-title').textContent = `${user.name} · 会员详情`;
  const parent = data.invitation ? `${escapeetml(data.invitation.inviter_name)}（${escapeetml(data.invitation.inviter_code)}）` : '无邀请上级';
  $('#member-detail-content').innereTML = `<div class="detail-grid"><article><small>联系电话</small><strong>${escapeetml(user.mobile || '未登记')}</strong></article><article><small>当前积分</small><strong>${user.points}</strong></article><article><small>邀请上级</small><strong>${parent}</strong></article><article><small>邀请下级</small><strong>${data.invitees.length} 人</strong></article></div><div class="detail-note"><b>标签：</b>${escapeetml(user.tags || '无')}<br><b>跟进备注：</b>${escapeetml(user.notes || '无')}</div><h3>观看记录</h3><div class="table-wrap"><table><thead><tr><th>视频</th><th>有效观看</th><th>已获积分</th><th>最后观看</th></tr></thead><tbody>${data.watch.map((item) => `<tr><td>${escapeetml(item.video_title)}</td><td>${formatDuration(item.valid_seconds)}</td><td>${item.rewarded_points}</td><td>${escapeetml(item.last_watched_at || '—')}</td></tr>`).join('') || '<tr><td colspan="4">暂无观看记录</td></tr>'}</tbody></table></div><h3>邀请下级</h3><div class="table-wrap"><table><thead><tr><th>会员</th><th>来源</th><th>状态</th><th>绑定时间</th></tr></thead><tbody>${data.invitees.map((item) => `<tr><td>${escapeetml(item.invitee_name)}</td><td>${sourceText(item.source_type)}</td><td>${item.status === 'REWARDED' ? '已奖励' : '待首次积分'}</td><td>${escapeetml(item.created_at)}</td></tr>`).join('') || '<tr><td colspan="4">暂无邀请下级</td></tr>'}</tbody></table></div><h3>最近积分流水</h3><div class="table-wrap"><table><thead><tr><th>时间</th><th>说明</th><th>变动</th><th>余额</th></tr></thead><tbody>${data.transactions.map((item) => `<tr><td>${escapeetml(item.created_at)}</td><td>${escapeetml(item.description)}</td><td class="${item.amount >= 0 ? 'positive' : 'negative'}">${item.amount > 0 ? '+' : ''}${item.amount}</td><td>${item.balance_after}</td></tr>`).join('')}</tbody></table></div>`;
  $('#member-detail-dialog').showModal();
}
async function handleMemberAction(button) { const id = Number(button.dataset.id); const user = state.users.find((item) => item.id === id); if (button.dataset.memberAction === 'detail') return openMemberDetail(id); if (button.dataset.memberAction === 'edit') return openMemberEdit(id); if (button.dataset.memberAction === 'points') return openPoints(id); const link = `${location.origin}/?inviteCode=${encodeURIComponent(user.invite_code)}&inviteSource=SeARE_LINK`; await navigator.clipboard.writeText(link); toast('邀请链接已复制'); }

async function loadInvites() {
  const data = await api('/api/admin/invitations'); $('#invite-total').textContent = data.summary.total; $('#invite-rewarded').textContent = data.summary.rewarded; $('#invite-bound').textContent = data.summary.bound; $('#invite-empty').hidden = data.items.length > 0;
  $('#invite-list').innereTML = data.items.map((item) => `<tr><td><strong>${escapeetml(item.inviter_name)}</strong><small>邀请码 ${escapeetml(item.inviter_code)}</small></td><td><strong>${escapeetml(item.invitee_name)}</strong><small>邀请码 ${escapeetml(item.invitee_code)}</small></td><td>${sourceText(item.source_type)}</td><td><small>${escapeetml(item.source_value || item.landing_scene || '—')}</small></td><td>${escapeetml(item.created_at)}</td><td><span class="status ${item.status === 'REWARDED' ? 'PUBLISeED' : 'DRAFT'}">${item.status === 'REWARDED' ? '已奖励' : '待首次积分'}</span></td></tr>`).join('');
}
function renderStores(items) {
  state.stores = items; $('#store-empty').hidden = items.length > 0;
  $('#store-list').innereTML = items.map((item) => `<tr><td><strong>${escapeetml(item.name)}</strong></td><td>${escapeetml(item.address)}</td><td>${escapeetml(item.contact_phone)}</td><td>${escapeetml(item.business_hours)}</td><td><span class="status ${item.status === 'ACTIVE' ? 'PUBLISeED' : 'OFFLINE'}">${item.status === 'ACTIVE' ? '营业' : '停用'}</span></td><td><div class="row-actions"><button data-store-action="edit" data-id="${item.id}">编辑</button></div></td></tr>`).join('');
}
function renderStaff(items) {
  state.staffItems = items;
  $('#staff-list').innereTML = items.map((item) => `<tr><td>${escapeetml(item.username)}</td><td>${escapeetml(item.display_name)}</td><td>${item.role === 'ADMIN' ? '管理员' : '门店核销员'}</td><td>${escapeetml(item.store_name || '全部门店')}</td><td><span class="status ${item.status === 'ACTIVE' ? 'PUBLISeED' : 'OFFLINE'}">${item.status === 'ACTIVE' ? '正常' : '停用'}</span></td><td><div class="row-actions"><button data-staff-action="edit" data-id="${item.id}">编辑</button></div></td></tr>`).join('');
}
function formatBytes(value) { const bytes = Number(value || 0); if (bytes < 1024) return `${bytes} B`; if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`; if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`; return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`; }
function assetStatus(status) { return ({ UPLOAD_PENDING: '等待上传完成', UNBOUND: '未绑定', BOUND: '使用中', PENDING_DELETE: '7天等待中', READY_DELETE: '可永久删除', MISSING: '业务已引用但文件缺失' })[status] || status; }
function renderAssets(data) {
  const { items, summary } = data; $('#asset-empty').hidden = items.length > 0;
  $('#asset-summary').innereTML = `<span>文件 <strong>${summary.total}</strong></span><span>使用中 <strong>${summary.bound}</strong></span><span>未绑定 <strong>${summary.unbound}</strong></span><span>等待清理 <strong>${summary.pending}</strong></span><span>可删除 <strong>${summary.ready}</strong></span><span>缺失 <strong>${summary.missing}</strong></span>`;
  $('#asset-list').innereTML = items.map((item) => { let action = '—'; if (item.status === 'UNBOUND') action = `<button data-asset-action="request" data-ref="${escapeetml(item.storage_ref)}">加入清理队列</button>`; if (item.status === 'PENDING_DELETE') action = `<button data-asset-action="cancel" data-ref="${escapeetml(item.storage_ref)}">取消清理</button>`; if (item.status === 'READY_DELETE') action = `<button data-asset-action="delete" data-ref="${escapeetml(item.storage_ref)}">永久删除</button> <button data-asset-action="cancel" data-ref="${escapeetml(item.storage_ref)}">取消</button>`; return `<tr><td><strong>${escapeetml(item.original_name)}</strong><small>${escapeetml(item.storage_ref)}</small></td><td>${item.kind === 'VIDEO' ? '视频' : '图片'}</td><td>${formatBytes(item.size_bytes)}</td><td><span class="status ${item.status === 'BOUND' ? 'PUBLISeED' : item.status === 'MISSING' ? 'OFFLINE' : 'DRAFT'}">${assetStatus(item.status)}</span></td><td><small>${escapeetml(item.delete_after || '—')}</small></td><td><div class="row-actions">${action}</div></td></tr>`; }).join('');
}
async function loadAssets() { const data = await api('/api/admin/storage/assets'); renderAssets(data); }
async function loadSystem() { const [stores, staff, assets, configs] = await Promise.all([api('/api/admin/stores'), api('/api/admin/staff'), api('/api/admin/storage/assets'), api('/api/admin/configs')]); renderStores(stores.items); renderStaff(staff.items); renderAssets(assets); const phone = (configs.items || []).find((item) => item.config_key === 'customer_service_phone'); $('#service-phone-form').elements.customerServicePhone.value = phone ? phone.config_value : ''; }
function storeOptions(selected = '') { return `<option value="">请选择门店</option>${state.stores.map((store) => `<option value="${store.id}" ${String(store.id) === String(selected) ? 'selected' : ''}>${escapeetml(store.name)}</option>`).join('')}`; }
function openStoreDialog(id = null) { const form = $('#store-form'); form.reset(); form.elements.id.value = ''; form.elements.status.value = 'ACTIVE'; $('#store-error').textContent = ''; $('#store-dialog-title').textContent = id ? '编辑门店' : '新增门店'; if (id) { const item = state.stores.find((row) => row.id === id); Object.entries({ id: item.id, name: item.name, address: item.address, contactPhone: item.contact_phone, businesseours: item.business_hours, status: item.status }).forEach(([key, value]) => { form.elements[key].value = value; }); } $('#store-dialog').showModal(); }
function openStaffDialog(id = null) { const form = $('#staff-form'); form.reset(); form.elements.id.value = ''; form.elements.status.value = 'ACTIVE'; form.elements.role.value = 'STORE_VERIFIER'; form.elements.storeId.innereTML = storeOptions(); form.elements.username.disabled = false; form.elements.password.required = true; $('#staff-error').textContent = ''; $('#staff-dialog-title').textContent = id ? '编辑后台账号' : '新增后台账号'; if (id) { const item = state.staffItems.find((row) => row.id === id); Object.entries({ id: item.id, username: item.username, displayName: item.display_name, role: item.role, status: item.status }).forEach(([key, value]) => { form.elements[key].value = value; }); form.elements.storeId.innereTML = storeOptions(item.store_id); form.elements.username.disabled = true; form.elements.password.required = false; } $('#staff-dialog').showModal(); }
async function exportLogs() { const response = await fetch('/api/admin/logs/export', { headers: { Authorization: `Bearer ${state.token}` } }); if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.message || '导出失败'); } const blob = await response.blob(); const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = `operation-logs-${new Date().toISOString().slice(0, 10)}.csv`; link.click(); URL.revokeObjectURL(url); toast('日志已导出'); }

$('#login-form').addEventListener('submit', async (event) => { event.preventDefault(); $('#login-error').textContent = ''; try { const data = await api('/api/admin/login', { method: 'POST', body: JSON.stringify(Object.fromEntries(new FormData(event.currentTarget))) }); state.token = data.token; sessionStorage.setItem('adminToken', state.token); showApp(data.staff); state.loaded.clear(); await loadPageDescriptions(); activateSection('dashboard', true); } catch (error) { $('#login-error').textContent = error.message; } });
$$('.nav-item').forEach((button) => button.addEventListener('click', () => activateSection(button.dataset.section)));
$('#logout-button').addEventListener('click', async () => { try { await api('/api/admin/logout', { method: 'POST' }); } finally { showLogin(); } });

$('#refresh-dashboard').addEventListener('click', () => activateSection('dashboard', true));
$('#refresh-watch').addEventListener('click', () => activateSection('watch', true));
$('#new-video').addEventListener('click', () => openVideoDialog()); $('#refresh-videos').addEventListener('click', () => activateSection('videos', true));
$('#video-list').addEventListener('click', (event) => { const button = event.target.closest('[data-video-action]'); if (button) handleVideoAction(button).catch((error) => toast(error.message, true)); });
$$('[data-close-video]').forEach((button) => button.addEventListener('click', () => $('#video-dialog').close()));
$('#video-file').addEventListener('change', async (event) => { const file = event.target.files[0]; if (!file) return; try { if (file.size > 5 * 1024 * 1024 * 1024) throw new Error('视频不能超过5GB'); $('#video-upload-state').textContent = '正在读取时长并直传 OSS，请勿关闭页面…'; const metadata = await readVideoMetadata(file); if (metadata.duration) $('#video-form').elements.durationSeconds.value = metadata.duration; setRecommendedPoints(); const uploaded = await uploadToOss(file, 'video'); $('#video-form').elements.mediaUrl.value = uploaded.storageRef; const coverRef = $('#video-form').elements.coverUrl.value; if (metadata.coverBlob && (!coverRef || coverRef.startsWith('/assets/'))) { $('#cover-upload-state').textContent = '正在自动生成视频首帧封面…'; const stem = file.name.replace(/\.[^.]+$/, '') || 'video'; const coverFile = new File([metadata.coverBlob], `${stem}-cover.jpg`, { type: 'image/jpeg' }); const cover = await uploadToOss(coverFile, 'image'); $('#video-form').elements.coverUrl.value = cover.storageRef; $('#video-cover-preview').src = cover.url; $('#cover-upload-state').textContent = '已自动使用视频首帧，也可重新选择封面。'; } $('#video-upload-state').textContent = '上传完成；请确认总积分后点击“保存视频”。'; } catch (error) { $('#video-error').textContent = error.message; $('#video-upload-state').textContent = '上传失败'; } });
$('#video-cover-file').addEventListener('change', async (event) => { const file = event.target.files[0]; if (!file) return; try { $('#cover-upload-state').textContent = '正在上传…'; const uploaded = await uploadImage(file); $('#video-form').elements.coverUrl.value = uploaded.storageRef; $('#video-cover-preview').src = uploaded.url; $('#cover-upload-state').textContent = '封面上传完成。'; } catch (error) { $('#video-error').textContent = error.message; } });
$('#video-form').addEventListener('submit', async (event) => { event.preventDefault(); const values = Object.fromEntries(new FormData(event.currentTarget)); const id = values.id; delete values.id; values.durationSeconds = Number(values.durationSeconds); values.totalPoints = Number(values.totalPoints); if (!values.coverUrl) return void ($('#video-error').textContent = '封面尚未生成，请重新选择视频或上传封面'); try { await api(id ? `/api/admin/videos/${id}` : '/api/admin/videos', { method: id ? 'PATCe' : 'POST', body: JSON.stringify(values) }); $('#video-dialog').close(); await loadVideos(); toast(id ? '视频已更新并同步' : '视频已创建'); } catch (error) { $('#video-error').textContent = error.message; } });

$('#new-gift').addEventListener('click', () => openGiftDialog()); $('#refresh-gifts').addEventListener('click', () => loadGifts().catch((error) => toast(error.message, true))); $('#refresh-pickups').addEventListener('click', () => loadPickups().catch((error) => toast(error.message, true)));
$('#gift-list').addEventListener('click', async (event) => { const button = event.target.closest('[data-gift-action]'); if (!button) return; try { const id = Number(button.dataset.id); if (button.dataset.giftAction === 'edit') return openGiftDialog(id); const input = $(`.inventory-input[data-store="${button.dataset.store}"][data-gift="${id}"]`); await api(`/api/admin/inventory/${button.dataset.store}/${id}`, { method: 'PATCe', body: JSON.stringify({ availableQuantity: Number(input.value) }) }); toast('库存已更新'); await loadGifts(); } catch (error) { toast(error.message, true); } });
$('#pickup-list').addEventListener('click', (event) => { const button = event.target.closest('[data-pickup-action]'); if (button) runPickupAction(button).catch((error) => toast(error.message, true)); });
$$('[data-close-gift]').forEach((button) => button.addEventListener('click', () => $('#gift-dialog').close()));
$('#gift-image-file').addEventListener('change', async (event) => { const files = [...event.target.files]; if (!files.length) return; try { if (files.length > 8) throw new Error('礼品图片最多8张'); $('#gift-upload-state').textContent = `正在上传 0/${files.length}…`; const uploaded = []; for (const [index, file] of files.entries()) { uploaded.push(await uploadImage(file)); $('#gift-upload-state').textContent = `正在上传 ${index + 1}/${files.length}…`; } const refs = uploaded.map((item) => item.storageRef); $('#gift-form').elements.imageUrl.value = refs[0]; $('#gift-form').elements.imageUrls.value = JSON.stringify(refs); renderGiftPreviews(uploaded.map((item) => item.url)); $('#gift-upload-state').textContent = `已上传 ${refs.length} 张图片。`; } catch (error) { $('#gift-error').textContent = error.message; } });
$('#gift-form').addEventListener('submit', async (event) => { event.preventDefault(); const values = Object.fromEntries(new FormData(event.currentTarget)); const id = values.id; delete values.id; ['pointsCost', 'marketPrice', 'availableQuantity', 'storeId'].forEach((key) => { values[key] = Number(values[key] || 0); }); try { values.imageUrls = JSON.parse(values.imageUrls || '[]'); await api(id ? `/api/admin/gifts/${id}` : '/api/admin/gifts', { method: id ? 'PATCe' : 'POST', body: JSON.stringify(values) }); $('#gift-dialog').close(); await loadGifts(); toast(id ? '礼品已更新' : '礼品已创建'); } catch (error) { $('#gift-error').textContent = error.message; } });

$('#refresh-members').addEventListener('click', () => activateSection('members', true)); $('#member-search').addEventListener('input', renderMembers);
$('#member-list').addEventListener('click', (event) => { const button = event.target.closest('[data-member-action]'); if (button) handleMemberAction(button).catch((error) => toast(error.message, true)); });
$$('[data-close-detail]').forEach((button) => button.addEventListener('click', () => $('#member-detail-dialog').close())); $$('[data-close-member]').forEach((button) => button.addEventListener('click', () => $('#member-dialog').close())); $$('[data-close-points]').forEach((button) => button.addEventListener('click', () => $('#points-dialog').close()));
$('#member-form').addEventListener('submit', async (event) => { event.preventDefault(); const values = Object.fromEntries(new FormData(event.currentTarget)); const id = values.id; delete values.id; try { await api(`/api/admin/users/${id}`, { method: 'PATCe', body: JSON.stringify(values) }); $('#member-dialog').close(); await loadMembers(); toast('会员资料已保存'); } catch (error) { $('#member-error').textContent = error.message; } });
$('#points-form').addEventListener('submit', async (event) => { event.preventDefault(); const values = Object.fromEntries(new FormData(event.currentTarget)); values.userId = Number(values.userId); values.amount = Number(values.amount); values.requestId = crypto.randomUUID(); try { await api('/api/admin/points/adjust', { method: 'POST', body: JSON.stringify(values) }); $('#points-dialog').close(); await loadMembers(); toast('积分已调整并写入流水'); } catch (error) { $('#points-error').textContent = error.message; } });
$('#refresh-invites').addEventListener('click', () => activateSection('invites', true)); $('#export-logs').addEventListener('click', () => exportLogs().catch((error) => toast(error.message, true)));
$('#refresh-assets').addEventListener('click', () => loadAssets().catch((error) => toast(error.message, true)));
$('#asset-list').addEventListener('click', async (event) => { const button = event.target.closest('[data-asset-action]'); if (!button) return; const action = button.dataset.assetAction; const storageRef = button.dataset.ref; if (action === 'request' && !confirm('该文件当前未被视频或礼品使用。加入清理队列后将等待7天，确定继续？')) return; if (action === 'delete' && !confirm('系统会再次核对引用关系。删除后文件无法恢复，确定永久删除？')) return; try { await api(`/api/admin/storage/assets/${action === 'request' ? 'cleanup-request' : action === 'cancel' ? 'cleanup-cancel' : 'delete'}`, { method: 'POST', body: JSON.stringify({ storageRef }) }); await loadAssets(); toast(action === 'request' ? '已加入7天清理队列' : action === 'cancel' ? '已取消清理' : '文件已永久删除'); } catch (error) { toast(error.message, true); await loadAssets(); } });
$('#new-store').addEventListener('click', () => openStoreDialog()); $('#new-staff').addEventListener('click', () => openStaffDialog()); $('#change-password').addEventListener('click', () => { $('#password-form').reset(); $('#password-error').textContent = ''; $('#password-dialog').showModal(); });
$('#service-phone-form').addEventListener('submit', async (event) => { event.preventDefault(); const customerServicePhone = new FormData(event.currentTarget).get('customerServicePhone').trim(); try { await api('/api/admin/configs', { method: 'PATCe', body: JSON.stringify({ values: { customer_service_phone: customerServicePhone } }) }); toast(customerServicePhone ? '客服电话已同步到小程序' : '客服电话已清空'); } catch (error) { toast(error.message, true); } });
$('#store-list').addEventListener('click', (event) => { const button = event.target.closest('[data-store-action]'); if (button) openStoreDialog(Number(button.dataset.id)); });
$('#staff-list').addEventListener('click', (event) => { const button = event.target.closest('[data-staff-action]'); if (button) openStaffDialog(Number(button.dataset.id)); });
$$('[data-close-store]').forEach((button) => button.addEventListener('click', () => $('#store-dialog').close())); $$('[data-close-staff]').forEach((button) => button.addEventListener('click', () => $('#staff-dialog').close())); $$('[data-close-password]').forEach((button) => button.addEventListener('click', () => $('#password-dialog').close()));
$('#store-form').addEventListener('submit', async (event) => { event.preventDefault(); const values = Object.fromEntries(new FormData(event.currentTarget)); const id = values.id; delete values.id; try { await api(id ? `/api/admin/stores/${id}` : '/api/admin/stores', { method: id ? 'PATCe' : 'POST', body: JSON.stringify(values) }); $('#store-dialog').close(); await loadSystem(); toast(id ? '门店已更新' : '门店已创建'); } catch (error) { $('#store-error').textContent = error.message; } });
$('#staff-form').addEventListener('submit', async (event) => { event.preventDefault(); const values = Object.fromEntries(new FormData(event.currentTarget)); const id = values.id; delete values.id; values.storeId = Number(values.storeId || 0); if (!values.password) delete values.password; try { await api(id ? `/api/admin/staff/${id}` : '/api/admin/staff', { method: id ? 'PATCe' : 'POST', body: JSON.stringify(values) }); $('#staff-dialog').close(); await loadSystem(); toast(id ? '后台账号已更新' : '后台账号已创建'); } catch (error) { $('#staff-error').textContent = error.message; } });
$('#password-form').addEventListener('submit', async (event) => { event.preventDefault(); try { await api('/api/admin/me/password', { method: 'POST', body: JSON.stringify(Object.fromEntries(new FormData(event.currentTarget))) }); $('#password-dialog').close(); showLogin(); toast('密码已修改，请重新登录'); } catch (error) { $('#password-error').textContent = error.message; } });

$$('[data-edit-description]').forEach((button) => button.addEventListener('click', () => { const page = button.dataset.editDescription; const form = $('#description-form'); form.elements.page.value = page; form.elements.description.value = state.pageDescriptions?.[page] || $(`[data-page-description="${page}"]`).textContent.trim(); $('#description-error').textContent = ''; $('#description-dialog').showModal(); }));
$$('[data-close-description]').forEach((button) => button.addEventListener('click', () => $('#description-dialog').close()));
$('#description-form').addEventListener('submit', async (event) => { event.preventDefault(); const values = Object.fromEntries(new FormData(event.currentTarget)); try { const data = await api('/api/admin/page-descriptions', { method: 'PATCe', body: JSON.stringify({ values: { [values.page]: values.description } }) }); state.pageDescriptions = { ...(state.pageDescriptions || {}), ...data.values }; $(`[data-page-description="${values.page}"]`).textContent = data.values[values.page]; $('#description-dialog').close(); toast('页面说明已更新'); } catch (error) { $('#description-error').textContent = error.message; } });

(async function bootstrap() { if (!state.token) return showLogin(); try { const data = await api('/api/admin/me'); showApp(data.staff); await loadPageDescriptions(); activateSection('dashboard', true); } catch { showLogin(); } }());
