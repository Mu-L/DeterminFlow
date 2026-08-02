# LLM 用量与计价配置

DeterminFlow 在 Session 中持久化每次 LLM 响应的调用级 Token 账本，并在 Workflow
Task 统计接口中按节点、模型和 Agent 类型聚合。价格只从
`config/llm_pricing.json` 读取；供应商、模型、渠道或调价必须用独立规则和生效时间
显式配置。

## 当前 DeepSeek 直连规则

当前 `deepseek` Provider 的 `base_url` 是 `https://api.deepseek.com`，因此只应用
DeepSeek 官方直连美元价格，不应用腾讯云渠道的峰时倍数：

| 模型 | 缓存输入 | 非缓存输入 | 输出 | 币种与单位 |
|---|---:|---:|---:|---|
| `deepseek-v4-flash` | 0.0028 | 0.14 | 0.28 | USD / 百万 Token |
| `deepseek-v4-pro` | 0.003625 | 0.435 | 0.87 | USD / 百万 Token |

价格核对 [DeepSeek 官方价格页](https://api-docs.deepseek.com/zh-cn/quick_start/pricing)。
仓库规则从 2026-07-18 本项目启用调用级账本起生效，不反推更早任务的历史费用。

腾讯云 DeepSeek V4 的高峰双倍价格属于另一条销售渠道，公告见
[腾讯云官方说明](https://docs.cloudbase.net/ai/announcement/deepseek-v4-price-update)。如果未来
接入该渠道，模型配置必须使用不同的 Provider ID（例如 `tencent-cloudbase`），并配置
独立的人民币价格表与 `time_bands`。不得把腾讯云时段规则写到 `deepseek` 直连规则上。
当前单份价格表只使用一种币种；同一部署需要同时结算不同币种时，应分别统计，不能
用未经记录的汇率直接相加。

## 配置结构

```json
{
  "version": "price-table-version",
  "currency": "USD",
  "timezone": "UTC",
  "unit_tokens": 1000000,
  "rules": [
    {
      "id": "stable-rule-id",
      "provider": "provider-id",
      "model": "model-name",
      "effective_from": "2026-07-18T00:00:00Z",
      "effective_to": null,
      "rates": {
        "prompt": "CONFIGURE_ME",
        "cached_prompt": "CONFIGURE_ME",
        "completion": "CONFIGURE_ME"
      },
      "time_bands": []
    }
  ]
}
```

`rates` 表示每 `unit_tokens` 个 Token 的价格。可用字段为 `prompt`、
`cached_prompt`、`completion`，以及可选的 `reasoning`。如果没有单独配置
`reasoning`，推理 Token 按其已包含的 `completion` Token 计价，不重复收费。

渠道存在分时价格时，`time_bands` 使用该规则时区中的本地时间，开始时间包含、结束
时间不包含；开始时间晚于结束时间表示跨午夜。可选 `days` 使用 `0` 至 `6`（周一至
周日）或 `mon` 至 `sun`。开始与结束相同不是合法的全天窗口；全天规则应省略时间带。

## 失败关闭规则

- `response_metadata.token_usage` 与 `usage_metadata` 按字段合并，不相加；前者缺少的
  字段才由后者补齐。
- 缺少 `prompt_tokens` 或 `completion_tokens` 时，该次调用标记
  `usage_status=incomplete`、`cost_status=unpriced`，不会按零成本入账。
- 非有限数字、坏时区、坏日期或时间、倒置生效窗口、非法 `rates`、`time_bands` 或
  `multiplier` 会使整份配置标记 `config_status=config_invalid`；所有调用保持
  `unpriced`，接口不会返回 500，也不会回退到猜测价格。
- 价格、时间戳或规则无法匹配时同样返回 `unpriced`；只有部分调用可计价时，Task
  汇总返回 `partially_priced`。
- 升级前没有调用账本、调用时间或完整 Usage 的 Task 保留 Token 汇总，但不反推费用。

Task 端点把持久化的调用级 Usage 作为事实来源，每次查询都按当前版本化配置重新估值；
旧响应中附带的 `cost` 或 `pricing_snapshot` 不参与计算，并由新结果覆盖。返回快照中的
`valuation_mode=current_config` 明确表达这一口径。升级前没有完整性标记的非零调用，
只有在输入、输出与总量关系一致时才可保守重估；全零或矛盾账本继续保持 `unpriced`。
批次等上层组件若缓存了旧 Task 统计，必须从 Core Task 端点重新读取后再聚合，不能把
不同版本或币种的缓存金额相加。

公共 `pricing_snapshot.source` 只返回配置文件名，不暴露主机绝对路径。金额使用十进制
字符串返回，避免 JSON 浮点舍入。

## 查询接口

```text
GET /api/workflows/{workflow_id}/tasks/{task_id}/token-usage
```

响应包括 `calls`、`nodes`、`by_model`、`by_agent_type`、`total`、`cost`、
`currency` 和 `pricing_snapshot`。同一份统计也通过 Extension
`WorkflowRuntime.get_task_token_usage` 提供。
