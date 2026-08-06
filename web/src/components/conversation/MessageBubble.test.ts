import assert from "node:assert/strict";
import test from "node:test";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import MessageBubble from "./MessageBubble";

(globalThis as typeof globalThis & { React: typeof React }).React = React;
const { createElement } = React;

test("readonly user history hides injected prompt content", () => {
  const html = renderToStaticMarkup(createElement(MessageBubble, {
    message: {
      id: "user-1",
      type: "user",
      content: "<SYSTEM_INJECTION>internal rule</SYSTEM_INJECTION><USER_MESSAGE>用户问题",
      injection_meta: [],
    },
    readonly: true,
  }));

  assert.match(html, /用户问题/);
  assert.doesNotMatch(html, /internal rule|SYSTEM_INJECTION|USER_MESSAGE/);
});

test("agent-authored user history also hides injected prompt content", () => {
  const html = renderToStaticMarkup(createElement(MessageBubble, {
    message: {
      id: "agent-1",
      type: "user",
      source: "agent:researcher",
      content: "<SYSTEM_INJECTION>private context</SYSTEM_INJECTION><USER_MESSAGE>代理消息",
      injection_meta: [],
    },
    readonly: true,
  }));

  assert.match(html, /代理消息/);
  assert.doesNotMatch(html, /private context|SYSTEM_INJECTION|USER_MESSAGE/);
});

test("legacy content_filter_warning uses the content safety renderer", () => {
  const html = renderToStaticMarkup(createElement(MessageBubble, {
    message: {
      id: "warning-1",
      type: "content_filter_warning",
      content: "请求被安全审查拦截",
      session_id: "session-a",
    },
    readonly: true,
  }));

  assert.match(html, /内容安全警告/);
  assert.match(html, /请求被安全审查拦截/);
  assert.doesNotMatch(html, /未知消息类型/);
});

test("historical user attachments render as filename bubbles from metadata", () => {
  const html = renderToStaticMarkup(createElement(MessageBubble, {
    message: {
      id: "user-file-1",
      type: "user",
      content: "检查 /Users/me/Documents/report final.md 的内容",
      attachments: [
        {
          name: "report final.md",
          absolute_path: "/Users/me/Documents/report final.md",
        },
      ],
    },
    readonly: true,
  }));

  assert.match(html, /data-message-attachment/);
  assert.match(html, />report final\.md</);
  assert.match(html, /title="\/Users\/me\/Documents\/report final\.md"/);
  assert.doesNotMatch(html, />\/Users\/me\/Documents\/report final\.md</);
});

test("legacy workspace attachment lines render as bubbles without metadata", () => {
  const path = "/private/tmp/workspaces/session/attachments/README.en (2).md";
  const html = renderToStaticMarkup(createElement(MessageBubble, {
    message: {
      id: "legacy-file-1",
      type: "user",
      content: `${path}\n普通文本 /tmp/not-an-attachment.md`,
    },
    readonly: true,
  }));

  assert.match(html, /data-message-attachment/);
  assert.match(html, />README\.en \(2\)\.md</);
  assert.match(html, /普通文本 \/tmp\/not-an-attachment\.md/);
});

test("legacy inline workspace paths with simple filenames render as bubbles", () => {
  const path = "/private/tmp/workspaces/session/attachments/README.en.md";
  const html = renderToStaticMarkup(createElement(MessageBubble, {
    message: {
      id: "legacy-file-inline",
      type: "user",
      content: `${path} 测试`,
    },
    readonly: true,
  }));

  assert.match(html, /data-message-attachment/);
  assert.match(html, />README\.en\.md</);
  assert.match(html, /> 测试</);
});
