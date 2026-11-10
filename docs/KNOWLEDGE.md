# 知识库语料

`knowledge/` 目录存放 RAG 使用的 Markdown 语料。应用启动后由
`backend/scripts/ingest_knowledge.py` 切块、生成向量并写入 PostgreSQL + pgvector。

## 为什么公开仓库只有示例

项目开发时使用过一批从公开网页整理的城市攻略，但这些内容可能包含第三方
文字或图片链接，再分发权并不明确。为了避免把来源不清的内容随代码一起公开，
公开仓库默认只保留：

- `knowledge/sample-guide.md`：一篇自写的格式示例，可自由修改或删除；
- 本说明文件（放在 `docs/`，不会被当作语料入库）。

原始城市语料仍保留在作者本地，不会进入公开仓库。这样既不影响本地完整演示，
也避免开源仓库承担不必要的版权风险。

## 使用自己的语料

1. 在 `knowledge/` 放入 `.md` 文件，使用 frontmatter。`title`、`city`、
   `doc_type`、`source` 四个字段都必须非空；`doc_type` 只能是
   `攻略`、`景点`、`美食`、`交通`、`住宿` 之一。

   ```markdown
   ---
   title: 文档标题
   city: 成都
   doc_type: 攻略
   source: https://example.com/source
   tags: [标签一, 标签二]
   ---
   ```

2. 在 `backend/` 目录下先 dry-run，确认切块结果：

   ```bash
   .venv/Scripts/python.exe scripts/ingest_knowledge.py --dry-run
   ```

3. 正式入库：

   ```bash
   .venv/Scripts/python.exe scripts/ingest_knowledge.py
   ```

4. 修改语料后重新入库；删除源文件后可加 `--prune` 清理旧文档：

   ```bash
   .venv/Scripts/python.exe scripts/ingest_knowledge.py --prune
   ```

## 许可

请只放入你有权使用和再分发的内容，并在 frontmatter 的 `source` 字段注明来源。
代码本身的 MIT 许可不自动覆盖本目录下的第三方文本。
