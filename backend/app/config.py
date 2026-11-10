from pydantic_settings import BaseSettings,SettingsConfigDict
from pathlib import Path

BASE_DIR=Path(__file__).resolve().parent.parent   #后端项目的根路径

# 读取.env文件中的配置
class Settings(BaseSettings):
    DATABASE_URL: str
    JWT_SECRET_KEY: str
    JWT_EXPIRE_HOURS: int = 24     # 过期时间
    JWT_ALGORITHM: str = "HS256"

    # Redis 只是旁路缓存，未配置时自动回退数据库。
    REDIS_URL: str = "redis://127.0.0.1:6379/0"
    CACHE_TTL_SECONDS: int = 1800   # 缓存有效期(秒)
    # 结果页缓存：行程投影和酒店检索都只缓存短时间，新回复会主动失效。
    # 不缓存高德静态地图（条款要求），地图仍每次实时取。
    RESULT_CACHE_TTL_SECONDS: int = 600

    # DeepSeek：允许为空，未配置时整条智能体链路降级。
    # langchain-deepseek 读取进程环境变量，llm.py 启动时负责回填。
    DEEPSEEK_API_KEY: str = ""

    # MiMo 用于长文生成节点；未配置时回退 DeepSeek。
    MIMO_API_KEY: str = ""
    MIMO_BASE_URL: str = "https://api.xiaomimimo.com/v1"
    MIMO_MODEL: str = "mimo-v2.6-flash"

    # LangGraph 检查点连接串；留空时由 checkpointer 从 DATABASE_URL 推导。
    # checkpointer 使用 psycopg3，连接串格式与业务库不同。
    CHECKPOINT_DB_URL: str = ""

    # 高德 Web 服务 key 仅后端使用，不下发前端；未配置时地图显示未配置。
    AMAP_WEB_SERVICE_KEY: str = ""
    # 单个高德请求超时。
    AMAP_HTTP_TIMEOUT_SECONDS: float = 5.0
    # 结果页地图总开关；关闭后 /map.png 返回 404。
    AMAP_MAP_ENABLED: bool = True

    # HF_* 是 huggingface_hub 在 import 时读取的环境变量，
    # embedding.py 必须在导入 sentence_transformers 前回填。
    HF_ENDPOINT: str = "https://hf-mirror.com"

    # HF 缓存目录；仅在 EMBEDDING_MODEL 使用仓库 id 时生效。
    HF_HOME: str = str(BASE_DIR.parent / "models" / "huggingface")

    # 嵌入模型路径，默认使用本地目录，避免联网检查版本和下载。
    EMBEDDING_MODEL: str = str(BASE_DIR.parent / "models" / "bge-m3")

    # 向量库连接串；空串表示从 DATABASE_URL 推导端口和库名。
    # 必须保留默认值：缺字段会让应用无法启动，而向量库不可用只应触发检索降级。
    VECTOR_DATABASE_URL: str = ""

    # 重排模型本地目录。
    RERANKER_MODEL: str = str(BASE_DIR.parent / "models" / "bge-reranker-v2-m3")

    # 知识库 Markdown 语料目录。
    KNOWLEDGE_DIR: str = str(BASE_DIR.parent / "knowledge")

    # MCP 默认关闭。开启后天气节点走 MCP，server 不可用时回退直连。
    MCP_ENABLED: bool = False
    # POI 走同一个 MCP server，默认关闭以避免和高德结果页叠加 QPS。
    MCP_AMAP_ENABLED: bool = False
    MCP_WEATHER_TRANSPORT: str = "stdio"        # stdio | streamable-http
    MCP_WEATHER_MODULE: str = "app.mcp_servers.weather_server"
    MCP_WEATHER_URL: str = "http://127.0.0.1:8765/mcp"
    # 组合 tool 内含 geocode + forecast 两次 HTTP。
    MCP_WEATHER_TOOL_TIMEOUT_SECONDS: float = 12.0
    MCP_WEATHER_DOWN_RETRY_SECONDS: float = 60.0
    MCP_FALLBACK_DIRECT: bool = True

    # 生成后依据校验；RETRY=false 时跳过改写，直接删句/拒答。
    GROUNDING_GUARD_ENABLED: bool = True
    GROUNDING_GUARD_RETRY: bool = True

    # 熔断器默认值；逐依赖阈值在 common/resilience.py 的 POLICIES。
    CIRCUIT_BREAKER_ENABLED: bool = True
    CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = 5
    CIRCUIT_BREAKER_COOLDOWN_SECONDS: float = 30.0
    CIRCUIT_BREAKER_HALF_OPEN_MAX_CALLS: int = 1

    # pydantic-settings 为 extra="forbid"：.env 中未声明的键会导致启动失败，
    # 新增环境变量前必须先在这里加字段。

    model_config =SettingsConfigDict(
        env_file=BASE_DIR / ".env", env_file_encoding="utf-8"
 )

    
settings = Settings()