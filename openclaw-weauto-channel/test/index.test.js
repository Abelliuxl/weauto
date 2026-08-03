import assert from "node:assert/strict";
import test from "node:test";

import {
  listConversationDirectory,
  outboundFrame,
  sendWithDeliveryReceipt,
  settleDeliveryReceipt,
  shouldDeliverReply,
  syncConversationDirectory,
} from "../index.js";

test("only final non-reasoning replies are delivered", () => {
  assert.equal(shouldDeliverReply({ text: "tool progress" }, { kind: "tool" }), false);
  assert.equal(shouldDeliverReply({ text: "block" }, { kind: "block" }), false);
  assert.equal(
    shouldDeliverReply({ text: "thinking", isReasoning: true }, { kind: "final" }),
    false,
  );
  assert.equal(shouldDeliverReply({ text: "answer" }, { kind: "final" }), true);
});

test("conversation directory preserves exact titles and separates chat kinds", () => {
  syncConversationDirectory("test-directory", [
    { id: "群魔兽", title: "群-魔兽", kind: "group" },
    { id: "real刘晓亮", title: "real刘晓亮", kind: "direct" },
  ]);

  assert.deepEqual(
    listConversationDirectory({ accountId: "test-directory", kind: "group" }),
    [{ id: "群魔兽", name: "群-魔兽", kind: "group" }],
  );
  assert.deepEqual(
    listConversationDirectory({
      accountId: "test-directory",
      kind: "direct",
      query: "real",
      limit: 1,
    }),
    [{ id: "real刘晓亮", name: "real刘晓亮", kind: "user" }],
  );
});

test("outbound targets must exist in the synchronized directory", () => {
  syncConversationDirectory("test-outbound", [
    { id: "群魔兽", title: "群-魔兽", kind: "group" },
  ]);

  const frame = outboundFrame({
    accountId: "test-outbound",
    to: "group:群魔兽",
    text: "hello",
  });
  assert.deepEqual(frame.conversation, {
    id: "群魔兽",
    title: "群-魔兽",
    kind: "group",
  });
  assert.throws(
    () =>
      outboundFrame({
        accountId: "test-outbound",
        to: "group:不存在",
        text: "must fail",
      }),
    /Unknown or unsynchronized target/,
  );
  assert.throws(
    () =>
      outboundFrame({
        accountId: "test-outbound",
        to: "user:群魔兽",
        text: "must fail",
      }),
    /target kind mismatch/,
  );
});

test("proactive send waits for a successful delivery receipt", async () => {
  const socket = {
    readyState: 1,
    sent: [],
    send(value) {
      this.sent.push(JSON.parse(value));
    },
  };
  const frame = { type: "message.send", message_id: "receipt-ok" };
  const pending = sendWithDeliveryReceipt(socket, frame, 100);

  assert.equal(socket.sent.length, 1);
  assert.equal(
    settleDeliveryReceipt(socket, {
      type: "message.delivered",
      message_id: "receipt-ok",
      ok: true,
    }),
    true,
  );
  await pending;
});

test("proactive send surfaces delivery failure and timeout", async () => {
  const socket = {
    readyState: 1,
    send() {},
  };
  const failed = sendWithDeliveryReceipt(
    socket,
    { type: "message.send", message_id: "receipt-failed" },
    100,
  );
  settleDeliveryReceipt(socket, {
    type: "message.delivered",
    message_id: "receipt-failed",
    ok: false,
    error: "target_window_not_confirmed",
  });
  await assert.rejects(failed, /target_window_not_confirmed/);

  await assert.rejects(
    sendWithDeliveryReceipt(
      socket,
      { type: "message.send", message_id: "receipt-timeout" },
      5,
    ),
    /delivery timeout/,
  );
});
