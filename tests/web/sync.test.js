// 运行：node --test tests/web/sync.test.js
const test = require("node:test");
const assert = require("node:assert");
const { mergeMessages, applyUserEcho, applyBubble } = require("../../web/sync.js");

const m = (id, extra = {}) => ({ id, role: "assistant", content: `m${id}`, created_at: "t", ...extra });
const pending = (client_id) => ({ id: null, client_id, role: "user", content: "p", created_at: "t" });
const ids = (list) => list.map((x) => x.id ?? `pending:${x.client_id}`);

test("旧的历史快照不会覆盖已经收到的新气泡", () => {
  assert.deepStrictEqual(ids(mergeMessages([m(1), m(2), m(3)], [m(1), m(2)])), [1, 2, 3]);
});

test("快照和现有消息按编号去重", () => {
  assert.deepStrictEqual(ids(mergeMessages([m(1), m(2)], [m(1), m(2), m(3)])), [1, 2, 3]);
});

test("已经加载的更早分页不会丢", () => {
  const current = [1, 2, 3, 4, 5].map((i) => m(i));
  assert.deepStrictEqual(ids(mergeMessages(current, [m(4), m(5), m(6)])), [1, 2, 3, 4, 5, 6]);
});

test("还没确认的消息留在最后", () => {
  assert.deepStrictEqual(ids(mergeMessages([m(1), pending("c1")], [m(1)])), [1, "pending:c1"]);
});

test("确认事件认领自己发的消息", () => {
  const out = applyUserEcho([m(1), pending("c1")], { id: 2, client_id: "c1", text: "p", created_at: "t2" });
  assert.deepStrictEqual(ids(out), [1, 2]);
  assert.strictEqual(out[1].created_at, "t2");
});

test("其他标签页发的消息直接显示", () => {
  assert.deepStrictEqual(ids(applyUserEcho([m(1)], { id: 2, client_id: "c9", text: "x", created_at: "t" })), [1, 2]);
});

test("快照已经带回了这条消息时，确认事件不会造成重复", () => {
  const out = applyUserEcho([m(1), m(2), pending("c1")], { id: 2, client_id: "c1", text: "p", created_at: "t" });
  assert.deepStrictEqual(ids(out), [1, 2]);
});

test("同一个气泡收到两次只显示一次", () => {
  const ev = { id: 3, text: "嗯", created_at: "t" };
  assert.deepStrictEqual(ids(applyBubble(applyBubble([m(1)], ev), ev)), [1, 3]);
});

test("新气泡排在未确认消息前面之外的正确位置", () => {
  assert.deepStrictEqual(ids(applyBubble([m(1), pending("c1")], { id: 2, text: "x", created_at: "t" })), [1, 2, "pending:c1"]);
});
