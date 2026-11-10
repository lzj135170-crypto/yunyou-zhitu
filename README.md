# 云游织图 · YUNYOU ZHITU

**An AI travel planner that shows its work.** Multi-agent itinerary generation, RAG citations, real map/POI data, and an honest distinction between facts and estimates.

[中文](#中文) · [English](#english) · [MIT License](LICENSE)

---

## English

### What it is

YUNYOU ZHITU (云游织图) turns a short trip brief into a day-by-day itinerary. Instead of asking one model to write everything, it runs a small LangGraph team:

- a **planner** that decides which experts to call;
- **weather / attraction / hotel / food / transport** experts that gather facts;
- a **RAG** path that retrieves from a local Markdown knowledge base with citations;
- an **itinerary** composer and a **response** writer that assemble the final answer.

The result page exposes the structured plan: map, weather, daily schedule, attractions, food, hotels, transport, budget, tips, citations, and the full answer. Missing data is shown as missing; prices are labelled as estimates/reference values, never as live quotes.

### Stack

| Layer | Choice |
| --- | --- |
| Frontend | Vue 3.5, Vite 8, hand-written CSS, no UI framework |
| Backend | FastAPI, SQLAlchemy 2, Alembic, Pydantic v2 |
| Orchestration | LangGraph + LangChain |
| Storage | PostgreSQL 16, pgvector, Redis (optional side cache) |
| RAG | BGE-M3 embeddings + BGE reranker, BM25 + vector hybrid retrieval |
| External facts | AMap (POI, routes, static maps), Open-Meteo |
| Models | DeepSeek and MiMo (both swappable/degradable) |

### Quick start

See the Chinese section below for the full walkthrough. In short:

```bash
# backend
cd backend
python -m venv .venv
.venv/Scripts/activate          # Windows; use source .venv/bin/activate on Unix
pip install -r requirements.txt
cp .env.example .env            # fill in DATABASE_URL / JWT_SECRET_KEY / keys
python -m alembic upgrade head
python -m uvicorn app.main:app --port 8000

# frontend (another terminal)
cd frontend
npm ci
npm run dev -- --port 5280
```

Open <http://127.0.0.1:5280/> in development, or <http://127.0.0.1:8000/> after `npm run build` (FastAPI serves `frontend/dist`).

### Honest limitations

- Hotel prices and trip budgets are **estimates/reference values**, not live quotes; room availability is not available.
- AMap data is fetched at runtime and is subject to AMap's terms. The repository does not redistribute map tiles or static map images.
- The public repository ships only a self-written sample knowledge document. The original third-party-derived corpus is intentionally excluded; add your own Markdown files under `knowledge/`.
- Without model/API keys the agent chain degrades instead of fabricating results.

### License

MIT © 2026 lzj135170-crypto. Third-party assets and services are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

---

## 中文

### 项目简介

云游织图把一段简短的旅行需求，整理成可执行的按天行程。它没有让一个模型从头写到尾，而是用 LangGraph 组织一个小团队：

- **planner** 决定这一轮要调用哪些专家；
- **天气 / 景点 / 住宿 / 美食 / 交通** 专家分别查事实；
- **RAG** 从本地 Markdown 知识库检索，并把引用带回结果；
- **itinerary** 汇总按天安排，**response** 生成最终回复。

结果页把结构化数据逐块展示：地图、天气、每日行程、景点推荐、美食、住宿、交通、预算、旅行提示、参考来源和完整回复。没拿到的数据就显示缺失，价格统一标注为估算/参考值，不写成实时报价。

### 界面预览

| 登录 | 规划 |
| --- | --- |
| ![登录](docs/screenshots/01-login.png) | ![规划](docs/screenshots/02-plan.png) |

| 结果概览 | 地图与天气 |
| --- | --- |
| ![结果概览](docs/screenshots/03-result-overview.png) | ![地图与天气](docs/screenshots/04-map-weather.png) |

| 美食推荐 | 住宿建议 |
| --- | --- |
| ![美食推荐](docs/screenshots/05-food.png) | ![住宿建议](docs/screenshots/06-hotels.png) |

> 截图使用测试账号和虚构的广州 3 天行程；账号名已替换为 `demo`。

### 功能亮点

- **多智能体编排**：planner 按需求动态选择专家，局部追问只重跑需要的节点。
- **RAG 出处回溯**：知识库引用带标题和章节；资料里没有的数字不会被写成确定事实。
- **MCP 事实校验**：天气等外部事实可走 MCP server，失败时回退直连。
- **真实地图/照片**：高德 POI、路线和静态地图；地图瓦片走后端代理，key 不下发。
- **美食参考图**：美食卡片按需加载高德餐厅图并标注“参考餐厅图 · 高德”，不把餐厅图冒充成菜品实拍。
- **结果页缓存**：plan 投影和酒店检索走 Redis 旁路缓存，新回复主动失效；Redis 不可用时退到进程内缓存。
- **降级而不是编造**：模型、Redis、向量库、高德任一不可用时，接口返回可解释的缺失/警告。
- **可打印/可导出**：结果页支持打印样式和 Markdown 导出。

### 架构

```mermaid
flowchart LR
  U[Vue 3 SPA] -->|/api| F[FastAPI]
  F --> A[Auth / Conversations / Trips]
  F --> G[LangGraph]
  G --> P[Planner]
  P --> W[Weather]
  P --> AT[Attraction]
  P --> H[Hotel]
  P --> FD[Food]
  P --> TR[Transport]
  P --> R[RAG]
  W --> IT[Itinerary]
  AT --> IT
  H --> IT
  FD --> IT
  TR --> IT
  R --> IT
  IT --> RS[Response]
  R --> PG[(PostgreSQL + pgvector)]
  F --> PG
  F --> RD[(Redis cache)]
  W --> OM[Open-Meteo / MCP]
  AT --> AM[AMap]
  H --> AM
  FD --> AM
  G --> LLM[DeepSeek / MiMo]
```

### 技术栈

| 层 | 选择 |
| --- | --- |
| 前端 | Vue 3.5、Vite 8、手写 CSS，不引入 UI 框架 |
| 后端 | FastAPI、SQLAlchemy 2、Alembic、Pydantic v2 |
| 编排 | LangGraph + LangChain |
| 存储 | PostgreSQL 16、pgvector、Redis（可选旁路缓存） |
| RAG | BGE-M3 嵌入 + BGE reranker，BM25 + 向量混合检索 |
| 外部事实 | 高德（POI、路线、静态地图）、Open-Meteo |
| 模型 | DeepSeek、MiMo，均可降级/替换 |

### 目录结构

```text
backend/
  app/
    agents/        LangGraph 图、节点、提示词、嵌入/重排
    api/           认证、会话、消息、结果页、RAG、追踪
    common/        缓存、熔断、统一响应/异常
    models/        SQLAlchemy ORM
    rag/           切块、检索、BM25、策略
    services/      业务服务与高德封装
  scripts/         入库、种子数据、评估、冒烟
  alembic/         数据库迁移
frontend/
  src/             Vue 组件、组合式函数、样式
  public/          favicon、map-config 示例
knowledge/         知识库 Markdown（公开仓库只带示例）
deploy/            pgvector / Milvus 本地容器配置
```

### 快速开始

#### 前置条件

- Python 3.11
- Node.js 20.19+
- PostgreSQL 16（业务库）
- pgvector（向量库；可用 `deploy/pgvector/docker-compose.yml`）
- Redis（可选；不启动时自动用进程内缓存）
- 模型权重（RAG 需要）：`models/bge-m3`、`models/bge-reranker-v2-m3`；没有时 RAG 降级，基础对话仍可用
- 高德 Web 服务 key（地图/POI）、DeepSeek/MiMo key（生成）

#### 1. 后端

```bash
cd backend
python -m venv .venv
# Windows
.venv/Scripts/activate
# macOS / Linux
# source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
```

编辑 `.env`，至少填写：

```dotenv
DATABASE_URL=postgresql+psycopg2://postgres:your-password@127.0.0.1:5432/travel
JWT_SECRET_KEY=<a-long-random-string>
```

按需填写 `DEEPSEEK_API_KEY`、`MIMO_API_KEY`、`AMAP_WEB_SERVICE_KEY`，以及模型路径。完整变量见 [`backend/.env.example`](backend/.env.example)。

初始化数据库：

```bash
python -m alembic upgrade head
```

导入知识库（没有语料时可跳过；公开仓库只带 `knowledge/sample-guide.md`，格式与导入说明见 [docs/KNOWLEDGE.md](docs/KNOWLEDGE.md)）：

```bash
python scripts/ingest_knowledge.py --dry-run
python scripts/ingest_knowledge.py
```

启动后端：

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

API 文档：<http://127.0.0.1:8000/docs>

#### 2. 前端

```bash
cd frontend
npm ci
# 可选：启用交互地图
cp public/map-config.example.js public/map-config.js
# 然后编辑 public/map-config.js，填入你自己的高德 JS API key

npm run dev -- --port 5280
```

打开 <http://127.0.0.1:5280/>。Vite 会把 `/api` 代理到 `127.0.0.1:8000`。

> Windows 上 5173 可能落在系统保留端口段（5080–5279）而报 `EACCES`，所以示例用 5280。可以用 `netsh interface ipv4 show excludedportrange protocol=tcp` 检查本机保留段。

#### 3. 生产/本地一体运行

```bash
cd frontend
npm run build

cd ../backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

FastAPI 会挂载 `frontend/dist`；打开 <http://127.0.0.1:8000/>。

### 环境变量

必需：

| 变量 | 说明 |
| --- | --- |
| `DATABASE_URL` | 业务库 PostgreSQL 连接串 |
| `JWT_SECRET_KEY` | JWT 签名密钥，生产环境用随机长串 |

常用可选（不配置时降级）：

| 变量 | 默认/说明 |
| --- | --- |
| `REDIS_URL` | `redis://127.0.0.1:6379/0`；不可用时进程内缓存 |
| `DEEPSEEK_API_KEY` | 为空时智能体链路降级 |
| `MIMO_API_KEY` | 为空时回退 DeepSeek |
| `AMAP_WEB_SERVICE_KEY` | 仅后端使用；为空时地图/POI 功能不可用 |
| `AMAP_MAP_ENABLED` | `true`；关闭后 `/map.png` 返回 404 |
| `EMBEDDING_MODEL` / `RERANKER_MODEL` | 本地模型目录 |
| `VECTOR_DATABASE_URL` | 留空时从 `DATABASE_URL` 推导 |
| `KNOWLEDGE_DIR` | 默认 `../knowledge` |
| `MCP_ENABLED` / `MCP_AMAP_ENABLED` | 默认 `false` |

其余缓存、MCP、Grounding、熔断参数见 `backend/.env.example`。

### 数据与诚实性

- 酒店价格、预算、交通耗时都是**估算/参考值**，不是实时报价或房态。
- POI 评分来自高德，不代表个人体验；营业时间以商家/官方渠道为准。
- 高德地图瓦片和静态图在运行时获取，仓库不存储、不再分发；静态地图接口设置 `no-store`。
- 知识库引用会展示来源标题；没有引用时不伪造出处。
- 公开仓库只带自写示例语料，第三方来源的城市攻略已排除，避免再分发风险。

### 测试与评估

当前仓库没有自动化测试套件。CI 只做两件事：

- 后端 `python -m compileall app`
- 前端 `npm ci && npm run build`

评估方法与结果（RAG、BM25、grounding 等）见 [`backend/eval/RESULTS.md`](backend/eval/RESULTS.md) 和 [`backend/eval/PHASE3_PREREG.md`](backend/eval/PHASE3_PREREG.md)。评估脚本在 `backend/scripts/eval_*.py`、`verify_*.py`，需要数据库/模型/API key 时请自行准备环境。

### 部署

- 本地开发：见上面的快速开始。
- 向量库容器：`deploy/pgvector/`（当前在用，端口只绑 `127.0.0.1:5433`）；`deploy/milvus/` 是实验性备选栈，默认不启动。
- 更多 Windows + Docker 细节见 [`deploy/README.md`](deploy/README.md)。
- 生产部署请自行补充反向代理、TLS、进程管理和密钥管理；不要把 `.env` 打进镜像。

### 安全

- 永远不要提交 `backend/.env`、`deploy/pgvector/.env`、`deploy/milvus/.env` 或任何真实 key。
- `frontend/public/map-config.js` 已被忽略；公开示例是 `map-config.example.js`。
- 漏洞报告方式见 [`SECURITY.md`](SECURITY.md)。

### 第三方与许可

- 代码：MIT，见 [`LICENSE`](LICENSE)。
- 字体：Albert Sans / Alumni Sans / JetBrains Mono，SIL OFL 1.1；许可证文件在 `frontend/src/assets/fonts/`。
- 模型、地图/天气服务、模型 API 的许可与条款见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。
- `knowledge/` 下的示例为自写内容；放入第三方语料前请确认你有权再分发。

### 贡献

见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。提交前请至少运行：

```bash
cd backend && python -m compileall app
cd frontend && npm ci && npm run build
```

不要在 issue、日志或截图中粘贴真实 key、数据库连接串或个人信息。
