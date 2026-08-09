import assert from "node:assert/strict";
import test from "node:test";

import { parseHeaderStatusResponse } from "./header-status-model";

const validPayload = {
  header_status: {
    visible: true,
    label: "余",
    value: "$8.50",
    title: "公益模型账户余额",
    summary: "由笔枢免费提供",
    summary_href: "https://bishuxiezuo.cn/",
    tone: "normal",
    metrics: [{ label: "账户可用", value: "$8.50" }],
    metadata: [{ label: "身份", value: "已登录" }],
    actions: [
      {
        id: "account",
        label: "登录笔枢",
        kind: "request",
        endpoint: "/api/public-api/login",
        method: "POST",
      },
      { id: "models", label: "模型列表", kind: "page" },
      { id: "payment", label: "充值", kind: "link", href: "https://portal.example.test/top-up" },
    ],
    refresh_after_ms: 1000,
    updated_at: "2026-08-08T08:00:00+00:00",
  },
};

test("parses a bounded generic header status payload", () => {
  const parsed = parseHeaderStatusResponse(validPayload);

  assert.equal(parsed?.value, "$8.50");
  assert.equal(parsed?.summary_href, "https://bishuxiezuo.cn/");
  assert.deepEqual(parsed?.actions.map((action) => action.kind), ["request", "page", "link"]);
  assert.equal(parsed?.refresh_after_ms, 1000);

  const withoutPolling = parseHeaderStatusResponse({
    ...validPayload,
    header_status: { ...validPayload.header_status, refresh_after_ms: null },
  });
  assert.equal(withoutPolling?.refresh_after_ms, undefined);

  const withLocalPayment = parseHeaderStatusResponse({
    ...validPayload,
    header_status: {
      ...validPayload.header_status,
      actions: [{
        id: "payment",
        label: "充值",
        kind: "link",
        href: "http://127.0.0.1:5173/site/public-api-top-up.html",
      }],
    },
  });
  assert.equal(
    withLocalPayment?.actions[0]?.href,
    "http://127.0.0.1:5173/site/public-api-top-up.html",
  );
});

test("rejects unsafe action links and malformed payloads", () => {
  assert.equal(parseHeaderStatusResponse({}), null);
  assert.equal(parseHeaderStatusResponse({
    ...validPayload,
    header_status: {
      ...validPayload.header_status,
      actions: [{ id: "bad", label: "打开", kind: "link", href: "javascript:alert(1)" }],
    },
  }), null);
  assert.equal(parseHeaderStatusResponse({
    ...validPayload,
    header_status: {
      ...validPayload.header_status,
      actions: [{ id: "bad", label: "打开", kind: "link", href: "http://example.test/top-up" }],
    },
  }), null);
  assert.equal(parseHeaderStatusResponse({
    ...validPayload,
    header_status: {
      ...validPayload.header_status,
      summary_href: "javascript:alert(1)",
    },
  }), null);
  assert.equal(parseHeaderStatusResponse({
    ...validPayload,
    header_status: {
      ...validPayload.header_status,
      actions: [{
        id: "bad-request",
        label: "执行",
        kind: "request",
        endpoint: "https://example.test/action",
        method: "POST",
      }],
    },
  }), null);
  assert.equal(parseHeaderStatusResponse({
    ...validPayload,
    header_status: {
      ...validPayload.header_status,
      refresh_after_ms: 100,
    },
  }), null);
});
