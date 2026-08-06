import assert from "node:assert/strict";
import test from "node:test";

import {
  formatComposerMessage,
  formatComposerParts,
  getDroppedFileName,
  shouldOfferComposerExpansion,
} from "./conversationComposerModel";

test("file bubbles expand to absolute paths at their inline positions", () => {
  assert.equal(
    formatComposerParts([
      { type: "text", value: "比较" },
      { type: "file", name: "first.txt", path: "/workspace/attachments/first.txt" },
      { type: "text", value: "和" },
      { type: "file", name: "second.txt", path: "/workspace/attachments/second.txt" },
      { type: "text", value: "的差异" },
    ]),
    "比较 /workspace/attachments/first.txt 和 /workspace/attachments/second.txt 的差异",
  );
});

test("format preserves user whitespace and omits unresolved uploads", () => {
  assert.equal(
    formatComposerParts([
      { type: "text", value: "第一行\n" },
      { type: "file", name: "pending.md", path: null },
      { type: "file", name: "report.md", path: "/tmp/report.md" },
      { type: "text", value: "\n第二行\u200b" },
    ]),
    "第一行\n/tmp/report.md\n第二行",
  );
});

test("composer message keeps UI attachment metadata beside absolute-path content", () => {
  assert.deepEqual(
    formatComposerMessage([
      { type: "text", value: "检查" },
      { type: "file", name: "report final.md", path: "/tmp/report final.md" },
    ]),
    {
      content: "检查 /tmp/report final.md",
      attachments: [
        { name: "report final.md", absolute_path: "/tmp/report final.md" },
      ],
    },
  );
});

test("dropped file names support Unix and Windows paths", () => {
  assert.equal(getDroppedFileName("/Users/me/report.md"), "report.md");
  assert.equal(getDroppedFileName("C:\\Users\\me\\report.md"), "report.md");
});

test("composer expansion is offered only when collapsed content overflows", () => {
  assert.equal(shouldOfferComposerExpansion(129, 128), false);
  assert.equal(shouldOfferComposerExpansion(130, 128), true);
  assert.equal(shouldOfferComposerExpansion(80, 128), false);
});
