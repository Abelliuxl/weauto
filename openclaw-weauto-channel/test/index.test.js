import assert from "node:assert/strict";
import test from "node:test";

import { shouldDeliverReply } from "../index.js";

test("only final non-reasoning replies are delivered", () => {
  assert.equal(shouldDeliverReply({ text: "tool progress" }, { kind: "tool" }), false);
  assert.equal(shouldDeliverReply({ text: "block" }, { kind: "block" }), false);
  assert.equal(
    shouldDeliverReply({ text: "thinking", isReasoning: true }, { kind: "final" }),
    false,
  );
  assert.equal(shouldDeliverReply({ text: "answer" }, { kind: "final" }), true);
});
