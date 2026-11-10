# Contributing

欢迎提 issue 或 PR。这个项目是个人作品，但仍希望改动可复现、可审查。

## Before you start

- 先搜索已有 issue/PR，避免重复。
- 大的功能或架构改动先开 issue 说明目标、范围和取舍。
- 不要提交任何真实密钥、数据库连接串、用户数据或个人信息。

## Local checks

```bash
# backend
cd backend
.venv/Scripts/python.exe -m compileall app

# frontend
cd ../frontend
npm ci
npm run build
```

涉及数据库/模型/高德/LLM 的改动，请在 PR 描述里写明你实际跑过的环境和结果；
没有条件跑完整链路的，明确标注未验证部分。

## Pull requests

- 保持改动聚焦；格式化/重排和功能改动尽量分开。
- 说明“为什么改”和“怎么验证”，不要只写“优化一下”。
- UI 改动附截图时，先脱敏账号、邮箱、真实行程和个人信息。
- 提交信息用简洁英文；一个 commit 做一件事。
- 不要求新增运行时依赖；如确实需要，先说明理由和替代方案。

## Code style

- 后端注释用中文解释“为什么”，代码本身保持英文命名。
- 前端沿用现有 Vue SFC、组合式函数和 CSS token，不引入 UI 框架。
- 数据诚实优先：拿不到就说拿不到，估算就标估算，不编造价格/出处。

## License

提交即表示你同意以 MIT 许可贡献这些代码。第三方内容请确认授权并在
`THIRD_PARTY_NOTICES.md` 中补充。
