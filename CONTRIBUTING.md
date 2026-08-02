# Contributing to DeterminFlow

感谢你愿意参与 DeterminFlow。首版仍在快速迭代，越小、越清楚的改动越容易合并。

## 开始之前

- Bug 和功能建议请先开 Issue，说明场景、预期结果和实际结果。
- 大型功能或 Core Node 变更请先讨论设计，避免双方做无用功。
- 不要在 Issue、日志、测试数据或提交中放入 API Key、密码和真实业务数据。

## 本地验证

要求 Python 3.11+ 和 Node.js 22+。

```bash
python -m pip install -r requirements-dev.lock
python -m pytest -q

cd web
npm ci
npm run lint
npm run test:extensions
npm run test:workflow
npm run build
```

提交信息使用 [Conventional Commits](https://www.conventionalcommits.org/) 格式，例如：

```text
feat(workflow): add retry backoff policy
fix(plugin): reject paths outside package root
```

提交 Pull Request 即表示你的贡献按本仓库的 AGPL-3.0-only 许可证发布。
