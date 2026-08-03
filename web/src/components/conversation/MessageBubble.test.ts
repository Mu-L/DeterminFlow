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
