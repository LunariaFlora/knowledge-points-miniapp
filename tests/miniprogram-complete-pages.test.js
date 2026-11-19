const assert = require("node:assert/strict");
const path = require("node:path");

const root = path.resolve(__dirname, "..", "miniprogram");
const apiPath = path.join(root, "utils", "api.js");

function loadPage(relativePath, snapshot, requestHandler = async () => ({})) {
  let definition;
  const wxCalls = [];
  const app = { globalData: { apiBase: "http://127.0.0.1:8770", userToken: "token", snapshotCache: null, pendingGiftId: 0 }, assetUrl: (value) => value.startsWith("/assets/") ? value : `http://127.0.0.1:8770${value}` };
  global.Page = (value) => { definition = value; };
  global.getApp = () => app;
  global.wx = {
    navigateTo: (value) => wxCalls.push(["navigateTo", value]),
    switchTab: (value) => wxCalls.push(["switchTab", value]),
    makePhoneCall: (value) => wxCalls.push(["makePhoneCall", value]),
    showToast: (value) => wxCalls.push(["showToast", value]),
    showShareMenu: (value) => wxCalls.push(["showShareMenu", value]),
    showModal: (value) => { wxCalls.push(["showModal", value]); if (value.success) value.success({ confirm: true }); }
  };
  delete require.cache[require.resolve(apiPath)];
  const api = require(apiPath);
  api.snapshot = async () => snapshot;
  api.getCachedSnapshot = () => snapshot;
  api.invalidateSnapshot = () => wxCalls.push(["invalidateSnapshot"]);
  api.request = requestHandler;
  const pagePath = path.join(root, relativePath);
  delete require.cache[require.resolve(pagePath)];
  require(pagePath);
  const page = { ...definition, data: JSON.parse(JSON.stringify(definition.data || {})), setData(value) { Object.assign(this.data, value); } };
  return { page, wxCalls, app };
}

const snapshot = {
  user: { name: "王阿姨（主用户）", points: 5, invite_code: "WANG2026" },
  transactions: [{ id: 1, amount: 5, transaction_type: "MANUAL_ADJUST", description: "体验积分" }],
  store: { id: 1, name: "GIFT体验中心" },
  gifts: [
    { id: 1, name: "小氢皂", points_cost: 3, market_price: 68, available_quantity: 8, image_url: "/assets/gift-soap.svg" },
    { id: 2, name: "富氢水", points_cost: 8, market_price: 288, available_quantity: 8, image_url: "/assets/gift-water.svg" }
  ],
  pickups: [{ id: 1, gift_name: "小氢皂", store_name: "GIFT体验中心", request_no: "PU1", voucher_code: "CODE1", status: "WAIT_CONTACT" }],
  invitations: [{ id: 1, invitee_name: "李叔叔", status: "REWARDED" }]
};

(async () => {
  const exchange = loadPage("pages/exchange/exchange.js", snapshot, async (url) => url === "/api/pickups" ? { voucherCode: "PICKUP-CODE" } : {});
  exchange.page.getTabBar = () => ({ setData() {} });
  await exchange.page.onShow();
  assert.equal(exchange.page.data.gifts[0].canRedeem, true);
  assert.equal(exchange.page.data.gifts[1].redeemLabel, "积分不足");
  exchange.page.openGiftById(1);
  exchange.page.openPickup();
  exchange.page.setData({ contactName: "王阿姨", contactMobile: "13800000001" });
  await exchange.page.submitPickup();
  assert.equal(exchange.wxCalls.some(([name]) => name === "showModal"), true);

  const profile = loadPage("pages/profile/profile.js", snapshot);
  profile.page.getTabBar = () => ({ setData() {} });
  await profile.page.onShow();
  assert.equal(profile.page.data.userName, "王阿姨");
  assert.equal(profile.page.data.successfulInvites, 1);
  profile.page.openPage({ currentTarget: { dataset: { page: "records" } } });
  assert.equal(profile.wxCalls.at(-1)[1].url, "/pages/records/records");

  const points = loadPage("pages/points/points.js", snapshot);
  await points.page.load();
  points.page.changeFilter({ currentTarget: { dataset: { value: "income" } } });
  assert.equal(points.page.data.filteredItems.length, 1);

  const records = loadPage("pages/records/records.js", snapshot, async () => ({ status: "CANCELLED" }));
  await records.page.load();
  assert.equal(records.page.data.items[0].statusLabel, "待门店联系");
  records.page.changeFilter({ currentTarget: { dataset: { value: "pending" } } });
  assert.equal(records.page.data.filteredItems.length, 1);

  const invite = loadPage("pages/invite/invite.js", snapshot);
  await invite.page.load();
  const share = invite.page.onShareAppMessage();
  assert.equal(share.path, "/pages/home/home?inviteCode=WANG2026&inviteSource=SHARE_CARD");
  console.log("miniprogram complete pages: ok");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
