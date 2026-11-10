# Security Policy

## Supported versions

当前只维护 `main` 分支的最新代码。历史提交不单独提供安全修复。

## Reporting a vulnerability

请**不要**在公开 issue 中粘贴：

- `.env`、API key、JWT 密钥、数据库连接串；
- 包含个人信息或用户数据的日志/截图；
- 可直接利用的完整攻击细节。

优先使用 GitHub 的 **Security → Advisories → Report a vulnerability** 私下报告，
或通过仓库维护者公开的联系方式联系。请在报告中说明影响范围、复现步骤和
建议修复；确认后会尽快处理并在修复发布后致谢（如你愿意）。

## Secret handling

- `backend/.env`、`deploy/pgvector/.env`、`deploy/milvus/.env` 必须留在本地，
  已被 `.gitignore` 忽略。
- 前端只允许放高德 JS API key（公开 key，靠域名白名单限制）；
  高德 Web 服务 key 只能放后端。
- 一旦发现密钥进入提交历史：立即轮换密钥，再清理历史；不要只删文件。
