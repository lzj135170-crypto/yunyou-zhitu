# 两个向量库容器的部署说明

项目的向量库在 Docker 里，**不是**在 `app/config.py` 里那个 `DATABASE_URL` 指向的
native PostgreSQL（那个是业务库，装着 users / conversations / messages /
agent_runs / documents / user_preferences / trips / trip_days / trip_items / 检查点）。

分成两个库的原因：pgvector 是 PostgreSQL 的原生扩展，必须落在安装目录的
`lib/` 和 `share/extension/` 下（PG16 还没有 PG18 才有的 `extension_control_path`，
没有"装到别处"的绕法）。而 `C:\Program Files\PostgreSQL\16` 需要管理员权限才能写，
从源码编译还要额外几个 GB 的 VS Build Tools。容器化之后这两个问题一起消失。

| 容器 | 端口 | 用途 | 状态 |
| --- | --- | --- | --- |
| `travel-pgvector` | 5433 | **当前在用的向量库**（pgvector 0.8.7） | 常驻 |
| `milvus-etcd` / `milvus-minio` / `milvus-standalone` | 19530 / 9091 / 9000 / 9001 | 备用，数据多了再切 | **已停** |

---

## 业务库 schema 迁移（Alembic）

业务库（`DATABASE_URL` 指向的 native PostgreSQL）不再由应用启动时的
`Base.metadata.create_all` 建表。**启动服务前必须先升级 schema**：

```bash
cd backend
.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe -m alembic check
```

首次接入 Alembic 的老库（表结构与 `0001_baseline` 一致、但还没有
`alembic_version` 表）只需要执行一次：

```bash
cd backend
.venv/Scripts/python.exe -m alembic stamp 0001
.venv/Scripts/python.exe -m alembic upgrade head
```

注意事项：

- `alembic.ini` 里没有数据库密码；`alembic/env.py` 从
  `app.config.settings.DATABASE_URL` 读取连接串。
- 迁移会忽略 LangGraph 的 `checkpoints/checkpoint_*` 表和 `alembic_version`，
  不会生成 `drop_table` 去清空多轮记忆。
- 回退生产数据优先用迁移前的 `pg_dump` 备份恢复；
  `alembic downgrade` 只用于临时库的 schema 演练，会丢新列/新表的数据。

---

## 前端构建与开发

前端是 Vite + Vue 3 单文件组件，源码在 `frontend/src`，构建产物在
`frontend/dist`；FastAPI 只挂载 `dist`。

生产 / 本地运行：

```bash
cd frontend
npm ci
npm run build

cd ../backend
.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000
```

打开 <http://127.0.0.1:8000/>。如果 `frontend/dist/index.html` 不存在，
`/` 会返回 503 并提示先构建；`/api/*` 不受影响。

开发时前后端分开跑：

```bash
# 终端 1
cd backend
.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000

# 终端 2
cd frontend
npm install
npm run dev
```

打开 <http://127.0.0.1:5173/>，Vite 会把 `/api` 代理到 8000。

说明：

- Node 需要 20.19+；本机 Node 24.17 可用。
- `frontend/node_modules` 和 `frontend/dist` 不入库；修改前端后要重新
  `npm run build`，FastAPI 才能看到新产物。
- 高德 JS key 仍在 `frontend/public/map-config.js`，是随页面下发的公开 key；
  不要换成 Web 服务 key，也不要把 `backend/.env` 提交上去。
- 演示账号由 `scripts/seed_users.py` 创建（`user01 / 123456`）；登录页不再
  明写测试账号。

---

## pgvector（当前在用）

### 先建一份本地密码文件

```bash
cp deploy/pgvector/.env.example deploy/pgvector/.env
# 然后编辑 deploy/pgvector/.env，把 PGVECTOR_PASSWORD 填上
```

Windows 上不想复制文件的话，一行也行：

```bash
echo "PGVECTOR_PASSWORD=你的密码" > deploy/pgvector/.env
```

### ⚠️ 更好的做法：从 `backend/.env` 派生，不要手打

**手打密码有抄错的风险，而抄错的后果恰恰是上面那句"看不出是密码错了"。**
而且这个值和 `backend/.env` 里已经有一个了，抄第二遍没有任何意义。

在 `backend` 目录下跑这段，它会把 `DATABASE_URL` 的密码原样取出来写进
`deploy/pgvector/.env`（用 SQLAlchemy 的解析器，所以密码里带 `@`、`:` 这类
字符也不会解析错）：

```bash
cd backend
.venv/Scripts/python.exe -c "
from pathlib import Path
from sqlalchemy.engine import make_url
from app.config import settings
Path('../deploy/pgvector/.env').write_text(
    '# 向量库容器的本地配置（不进版本库）。密码派生自 backend/.env 的 DATABASE_URL。\n'
    'PGVECTOR_PASSWORD=' + make_url(settings.DATABASE_URL).password + '\n',
    encoding='utf-8')
print('已写入 deploy/pgvector/.env')
"
```

写完这样确认它**真的能用**（不是只看文件在不在）：

```bash
docker exec travel-pgvector psql -U postgres -d travel_vector -c "SELECT count(*) FROM document_chunks;"
# 能返回行数就说明凭据是对的
```

⚠️ **这个值必须和 `backend/.env` 里 `DATABASE_URL` 的密码一致。**
`app/rag/vector_store.py` 是从 `DATABASE_URL` 推导向量库连接串的
（同一台机器、同一套凭据，只换端口和库名 `travel_vector`）。
对不上时表现为"向量库连不上"，而日志里只有一条连接被拒，
看不出是密码错了还是容器没起。

`.env` 已经在 `.gitignore` 里（那条规则不带斜杠，匹配任何层级），不会进版本库。
密码**不写进 `docker-compose.yml`** 是刻意的——那个文件要提交，
密码一旦进 git 历史，后续删掉也没用。

没建这个文件时 `compose up` 会**直接报错退出**并打印这句话，而不是回退到空密码：

```
required variable PGVECTOR_PASSWORD is missing a value: 向量库密码未设置……
```

这是刻意的：静默用弱密码把库建出来，等发现的时候数据已经在里面了。

### 起停

```bash
# 起
docker compose -f deploy/pgvector/docker-compose.yml up -d

# 看状态
docker compose -f deploy/pgvector/docker-compose.yml ps

# 停（保留数据）
docker compose -f deploy/pgvector/docker-compose.yml stop

# 彻底删掉，包括数据卷
# ⚠️ 数据没了，但向量库是【派生物】——重跑 scripts/ingest_knowledge.py 就能重建
docker compose -f deploy/pgvector/docker-compose.yml down -v
```

**验证它真的可用**（不是只看容器在跑）：

```bash
docker exec travel-pgvector psql -U postgres -d travel_vector -c "\dx"
# 输出里应该有 vector | 0.8.7
```

配的是 `restart: unless-stopped`，Docker Desktop 重启后容器会自动回来。
不配这条的话，某次重启后检索会**静默降级**成关键词匹配——
`app/rag/vector_store.py` 的 60 秒重试会让它看起来只是"慢了一会儿"，
而不是"向量库根本没起来"。

### ⚠️ POSTGRES_PASSWORD 只在第一次初始化时生效

容器第一次 `up` 时用它建库（initdb）。**之后改这个值不会改掉库里的密码。**
要换密码得 `ALTER USER postgres PASSWORD ...`，
或者 `down -v` 删卷重建再重跑一次入库（向量库是派生物，重建很便宜）。

---

## Milvus（备用，现在停着）

这个栈是实验性的，默认不启动。MinIO 不再使用默认口令，起之前先建本地凭据文件：

```bash
cp deploy/milvus/.env.example deploy/milvus/.env
# 编辑 deploy/milvus/.env，填写 MINIO_ROOT_USER / MINIO_ROOT_PASSWORD
```

```bash
# 起
docker compose -f deploy/milvus/docker-compose.yml up -d

# 健康检查（启动要 60—90 秒，头几十秒不通是正常的）
curl http://localhost:9091/healthz
# 期望输出：OK

# 停（保留数据）
docker compose -f deploy/milvus/docker-compose.yml stop
```

**镜像来源**：三个镜像来自国内加速源，名字本来带前缀
（`docker.1panel.live/milvusdb/milvus:v2.5.7`），已经 `docker tag` 成了规范名。
换版本时三个 tag 要一起换——Milvus 对 etcd 和 MinIO 的版本有兼容要求，
只换中间那个会起不来。

**为什么现在就下好**：镜像拉取是整件事里最慢、最容易失败的一步
（Docker Hub 直连不通、加速源上不一定有缓存、tag 不一定对）。
把它和写代码耦合在一起，出问题时分不清是"代码写错了"还是"镜像根本没下来"。

**切过去时要改什么**：`app/config.py` 的 `VECTOR_DATABASE_URL`
换成 Milvus 的连接参数，`app/rag/vector_store.py` 换成 pymilvus 的实现，
`app/rag/bm25.py` 的 BM25 要么用 Milvus 2.5 的全文检索、要么继续留在 PG 里。
`app/rag/pipeline.py` 和上层的 `retriever.search()` 不用动——
那正是当初把检索层和存储层分开的原因。

---

## 知识与实时的边界

这条边界是**用代码守着的**，不是靠约定。见
`backend/scripts/ingest_knowledge.py` 的 `VOLATILE_DOC_TYPES`。

| | 知识库（进 pgvector） | 实时数据（**不进** RAG） |
| --- | --- | --- |
| 内容 | 攻略、景点、交通、住宿、美食——**变化很慢** | 天气、票价、班次、余票——**随时在变** |
| 过时的后果 | 还能用，用户能接受 | 会**误导用户的行动**（照着昨天的预报订票） |
| 现在在哪 | `document_chunks` | weather 节点直连 Open-Meteo |

**为什么实时数据绝不能进知识库**：向量库里那份天气一旦灌进去，
它和攻略、景点介绍在检索时**没有任何区别**——一样被当成稳定的事实取出来
交给模型。而用户看到"明天多云转晴"时**不知道那是哪天取的**。
三周前的预报和今天的预报，在库里长得一模一样。

这不是质量问题，是欺骗。而且**它在检索层拦不住**：数据已经在库里了，
除非每个块都标明"这条几小时前过期"，否则检索层没有任何依据去区别对待。

所以闸门放在唯一的口子上——**入库**。`doc_type` 命中
`{天气, 票价, 班次, 余票, 汇率, 实时}` 的文件会被直接拒绝并报错退出：

```
doc_type 是「天气」，属于实时数据，不能进知识库。
    原因：实时数据变化快，而灌进向量库之后它和攻略、景点介绍
    在检索时完全一样——都会被当成稳定的事实取出来。……
```

### ⚠️ 闸门是两道，顺序有讲究

**黑名单挡不住的东西，由受控词表兜底。** 实测过：`doc_type: 天气预报` 在只有黑名单时
是**放行**的——它既不是"天气"，也不是任何一个受控值。"实时天气"、"天气2024" 同理。
黑名单永远比现实慢一步。

所以 `_reject_volatile()` 先查黑名单，不在黑名单里再查 `DOC_TYPES`
（`攻略 / 景点 / 美食 / 交通 / 住宿`），两关都过才放行。

**顺序不能反**：反过来的话"天气"会先撞上白名单，报错变成干巴巴的
「不在受控词表里」——技术上没错，但完全没解释为什么。下一个人很可能
把"天气"加进词表再跑一遍。**先说清理由，再收口子。**

真实的历史气候统计（不随时间变的那种）是可以入库的，
把 `doc_type` 改成「攻略」并在正文里写明它是统计而不是预报即可。

### 旅行事实 MCP（天气 + POI，默认关闭）

`backend/app/mcp_servers/weather_server.py` 是独立的 stdio MCP server
（server 名 `travel-facts`；文件名是历史遗留，暂时不改）：

- 天气：`geocode_city` / `get_forecast` / `get_weather`；
- POI：`search_poi`，按城市 + 关键词查高德真实地点，返回名称/地址/类型/
  评分/参考人均。`types` 可传高德大类码：景点 `110000`、餐饮 `050000`、
  住宿 `100000`。

天气取数在 `app/services/weather_client.py`；POI 在 `amap_map.search_pois`，
复用同一个 0.5 秒进程级节流和 `amap:json` 熔断。
`app/services/mcp_weather.py` 是同步桥接层：后台 asyncio loop +
持久 ClientSession，把同步专家节点接到 async MCP SDK 上。
天气和 POI 共用一个子进程，但熔断和 span 分开：
`mcp:weather` / `mcp.weather.*` 与 `mcp:amap` / `mcp.amap.*`。

**默认关闭**：`MCP_ENABLED=false` 时天气走 Open-Meteo 直连；POI 还需要
`MCP_AMAP_ENABLED=true`，且必须在 `MCP_ENABLED=true` 下才生效。开启方式：

```powershell
# 临时：只对这次 uvicorn 生效，不改 .env
$env:MCP_ENABLED="true"
$env:MCP_AMAP_ENABLED="true"
.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000

# 脚本演示（天气 + POI）
.venv/Scripts/python.exe scripts/run_travel_graph.py --mcp --amap-mcp "帮我规划成都三日游"
```

长期启用需要往 `.env` 加 `MCP_ENABLED=true` / `MCP_AMAP_ENABLED=true`；
`.env` 属于敏感文件，修改前单独确认。

失败语义：

- 天气：MCP server 起不来 / 超时 / 传输错误且 `MCP_FALLBACK_DIRECT=true`
  时回退直连，finding 标 `mcp_fallback=true`；
  `CITY_NOT_FOUND` / `OUT_OF_RANGE` 是业务错误，**不回退**。
- POI：`AMAP_NOT_CONFIGURED` / `NO_RESULT` / `AMAP_UPSTREAM_UNAVAILABLE`
  都是 `ok:false` 业务结果，不回退；专家没有 POI 事实也照常回答。
  只有 transport / 超时才抛 `MCPUnavailableError`。
- `mcp:weather` / `mcp:amap` 熔断器连续失败 3 次后打开，60 秒冷却；
  状态在 `GET /api/system/dependencies`。
- MCP 调用在 `mcp.weather.*` / `mcp.amap.*` span 里，可在追踪接口/CLI 中看到。

验证脚本：

- `scripts/smoke_mcp_weather.py`（真实 stdio + Open-Meteo）；
- `scripts/verify_mcp_degradation.py`（天气节点降级 + 熔断）；
- `scripts/smoke_mcp_amap.py`（真实 stdio + 高德 POI，没配 key 时 SKIP）；
- `scripts/verify_mcp_amap_degradation.py`（POI 失败不阻塞专家 + 熔断）。

⚠️ QPS：高德 0.5 秒节流是**进程级**的。MCP 子进程和 FastAPI 进程各有一份，
同时跑结果页和 POI 专家时可能叠加触发 10021。10021 只计限流、重试一次；
POI 业务错误不影响天气。共享子进程的 transport 故障会同时影响两者——
天气按 `MCP_FALLBACK_DIRECT` 回退直连，POI 只是没有事实，不影响主流程。

范围边界：已接天气 + 高德 POI。POI 不返回门票/营业时间，票价仍按估算；
没有汇率、余票等第二个实时源。

### 生成后依据校验（默认开启）

`backend/app/agents/grounding.py` 在 response 写库前做最后一道代码校验：

- 出处白名单：回复里的《标题》必须来自 `rag.citations`；
- 数字回查：价格、时长、距离、班次、百分比、温度、地址门牌号必须出现在
  `rag.context`、`weather.days`、`poi.items` 或用户原话里；
- 估算标记：资料里没有的价格，只有区间或 10 的倍数，且带“约/参考/区间”
  等标记时才放行；非价格单位（时长/距离/温度）带估算标记即可；
- 处理顺序：先让模型按违规清单改一次；改不好删句；删完仍不合格或所剩
  内容不足原文 40% 时返回固定拒答。

报告写在 `agent_runs.output["grounding"]`，包含 `action`
（none/rewritten/stripped/refused/skipped/failed）、违规计数和样本；
追踪里对应 `grounding.verify` span。

开关：`GROUNDING_GUARD_ENABLED`（默认 true）、`GROUNDING_GUARD_RETRY`
（默认 true；设为 false 时跳过 LLM 改写，直接删句/拒答）。

验证：`scripts/verify_grounding.py`（合成 state，不调用真实 LLM）。

## Docker 的网络配置（踩过的坑）

**这台机器上 Docker Hub 直连不通**，所有镜像靠加速源。配在
`%USERPROFILE%\.docker\daemon.json`：

```json
{
  "registry-mirrors": [
    "https://docker.1ms.run",
    "https://dockerproxy.net",
    "https://docker.m.daocloud.io"
  ]
}
```

改完要**重启 Docker Desktop** 才生效（`docker desktop restart`）。

### ⚠️ 一个真实踩过的坑

Docker Desktop **默认继承 Windows 的系统代理**。本机的系统代理指向
FlClash 的 `127.0.0.1:7890`，而那个代理的节点不通——表现是
**所有 `docker pull` 都在下载数据时 EOF**，连 manifest 都拿不到，
但同一个地址用 `curl` 直连却是通的。

排查时容易被误导：当时以为是"加速源的 CDN 被墙了"
（它们确实都挂在 Cloudflare 上，也确实连不上），
但真正的证据是 `curl https://quay.io/...` 返回 200 而 `docker pull` 不行——
同一个目标，宿主机通、Docker 不通，说明问题在 Docker 的出口上。

**正确修法**：Docker Desktop → Settings → Resources → Proxies，
把模式从 System 改成 Manual 或 None。

**⚠️ 千万不要去改 `%APPDATA%\Docker\settings-store.json`**。
试过往里加 `ProxyHttpMode: "none"`，结果 Docker Desktop 的后端直接崩了：

```
backend crashed, dumping error to file and reporting to user:
initializing backend: initializing settings loader and loading startup providers
```

那个文件（SettingsVersion 44）有严格的 schema，塞一个它不认识的键进去
会让设置加载器整个挂掉；更糟的是它会把这个文件重写成最小版本，
**丢掉 `UseContainerdSnapshotter` 之类的既有设置**。

---

## Docker 数据盘（已经搬到 D 盘）

Docker Desktop 的数据默认在 `C:\Users\<用户>\AppData\Local\Docker`，
其中 `wsl\disk\docker_data.vhdx` 是镜像和卷的实际载体（约 6.5 GB）。

**本机已经搬到 D 盘**，用的是**目录联接（junction）**：

```
C:\Users\<you>\AppData\Local\Docker\wsl   ──junction──▶   D:\<docker-data>\wsl
                                                        ├─ disk\docker_data.vhdx  (6.5 GB)
                                                        └─ main\ext4.vhdx
```

Docker Desktop 看到的路径没变，所以**不需要改它的任何配置**——
它照常往 `C:\...\wsl` 读写，文件实际落在 D 盘。
验证方式：`C:\Users\<you>\AppData\Local\Docker` 的占用应该从 6.6 GB 降到几十 MB。

### 为什么不用官方的做法

Docker Desktop 官方只支持在**界面里**改（Settings → Resources → Advanced →
Disk image location），它会自己迁移数据。CLI 没有对应命令。

直接改 `%APPDATA%\Docker\settings-store.json` 是【不受支持】的，而且在这台机器上
**实测把 Docker Desktop 搞崩过**：写了一个它不认识的键，
后端启动时直接 `backend crashed ... initializing settings loader`，
它还会把这个文件重写成最小版本、丢掉其它设置。

那为什么不用界面？因为需要的键名藏在打包后的前端代码里，
调用形式是 `changeSettings({"vm.resources.wslDataFolder": 路径})`——
**是点号路径不是扁平键**，而 settings-store.json 里现存的键却是 PascalCase 平铺的
（`AutoStart` / `UseContainerdSnapshotter`）。两套命名对不上，
说明中间有一层映射，而那层代码在压缩过的 asar 里翻不出来。

继续猜键名的代价是**再崩一次 Docker**，而收益只是"用官方方式"这件事本身。
目录联接是确定性的、零风险的、而且完全可逆，所以选它。

### 怎么撤回去

```powershell
docker desktop stop
Remove-Item "C:\Users\<you>\AppData\Local\Docker\wsl"    # 只删联接，不删目标里的内容
Move-Item "D:\<docker-data>\wsl" "C:\Users\<you>\AppData\Local\Docker\wsl"
docker desktop start
```

### ⚠️ 一个已知的脆弱点

**Docker Desktop 升级或重装时，可能会重新创建 `C:\...\Docker\wsl` 目录。**
如果它把联接覆盖成了一个真目录，数据就会重新落到 C 盘——
表现出来是"升级完 C 盘又少了几 GB"，不会有任何报错。

升级后顺手确认一下联接还在：

```powershell
Get-Item "C:\Users\<you>\AppData\Local\Docker\wsl" | Select-Object LinkType, Target
# 期望：LinkType=Junction  Target=D:\<docker-data>\wsl
```

`settings-store.json` 的备份留在同目录的 `settings-store.json.bak`，
是当初排障时留的，确认稳定之后可以删。
