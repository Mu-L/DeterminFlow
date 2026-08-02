# Security Policy

## 报告安全问题

请使用 GitHub 仓库的 **Security → Report a vulnerability** 私密报告功能，不要把漏洞、
凭据或可复现攻击细节发到公开 Issue。

报告中请包含受影响版本、复现条件、影响范围和你已经尝试过的缓解方法。维护者确认后会
在修复完成前与你保持沟通。

## 运行边界

DeterminFlow Plugin 当前是本机可信代码，会继承 Core 进程的系统权限。只安装你信任且
审查过来源的 Plugin，并使用独立运行账户、最小化环境变量和节点级工具权限。更强的
Plugin 与 LLM 工作区沙箱仍在 Roadmap。

请始终通过环境变量或 Secret File 提供凭据，不要把凭据写入仓库配置。
