// 聊天消息列表的合并规则：按消息编号去重，已确认的按编号排序，还没确认的（刚发出去）排在最后。
// 浏览器里挂在 window.MiraSync 上；node 测试里通过 require 使用。
(function (root) {
  function normalize(list) {
    const confirmed = new Map();
    const pending = [];
    for (const msg of list) {
      if (msg.id == null) pending.push(msg);
      else confirmed.set(msg.id, { ...confirmed.get(msg.id), ...msg });
    }
    return [...confirmed.values()].sort((a, b) => a.id - b.id).concat(pending);
  }

  // 用服务器返回的历史快照更新列表：快照可能比实时事件旧，所以只能合并，不能替换
  function mergeMessages(current, fetched) {
    return normalize([...current, ...fetched]);
  }

  // 服务器确认了一条用户消息：认领自己发的，或者显示其他标签页发的
  function applyUserEcho(list, ev) {
    const confirmed = { id: ev.id, role: "user", content: ev.text, created_at: ev.created_at };
    const rest = list.filter((msg) => !(msg.id == null && ev.client_id && msg.client_id === ev.client_id));
    return normalize([...rest, confirmed]);
  }

  function applyBubble(list, ev) {
    return normalize([...list, { id: ev.id, role: "assistant", content: ev.text, created_at: ev.created_at }]);
  }

  const api = { mergeMessages, applyUserEcho, applyBubble };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.MiraSync = api;
})(this);
