# Third-Party Notices

本项目代码以 MIT 许可发布（见 `LICENSE`）。以下第三方资产、模型和服务有各自的
许可或服务条款；使用、部署或再分发时请遵守对应条款。

## Fonts

`frontend/src/assets/fonts/` 下的字体均为 SIL Open Font License 1.1：

| 文件 | 版权 |
| --- | --- |
| `albertsans.ttf` | Copyright 2021 The Albert Sans Project Authors |
| `alumnisans.ttf` | Copyright 2015 The Alumni Sans Project Authors |
| `jetbrainsmono.ttf` | Copyright 2020 The JetBrains Mono Project Authors |

完整许可证文本在同目录的 `*-OFL.txt`。

## Models

这些模型权重不随仓库分发；如需 RAG，请从上游获取并遵守其许可：

| 模型 | 上游 | 许可 |
| --- | --- | --- |
| BAAI/bge-m3 | https://huggingface.co/BAAI/bge-m3 | MIT |
| BAAI/bge-reranker-v2-m3 | https://huggingface.co/BAAI/bge-reranker-v2-m3 | Apache-2.0 |

实际许可以模型仓库最新声明为准。

## Maps and POI data

- 高德地图（AMap）：地图瓦片、静态地图、POI、路线和评分来自高德服务，使用时受
  高德开放平台服务条款约束。仓库不存储、不再分发地图图片或瓦片；运行时通过后端
  代理/接口获取。
- 前端高德 JS API key 由使用者自行申请并配置在本地 `frontend/public/map-config.js`；
  后端 Web 服务 key 配置在 `backend/.env`。
- POI 照片 URL 来自高德，页面直接外链展示；仓库不保存这些图片。
- `docs/screenshots/` 中的界面截图包含运行时加载的高德地图/POI 内容，仅用于展示
  本项目界面；相关数据权利仍归高德及原权利人。

## Weather

- Open-Meteo：天气数据和服务条款见 https://open-meteo.com/en/license。
- 项目内的 MCP weather server 只是调用方封装，不改变上游数据许可。

## Model APIs

- DeepSeek：https://www.deepseek.com/
- MiMo：https://api.xiaomimimo.com/

API key 不随仓库分发；调用产生的费用和数据使用受各服务商条款约束。

## Knowledge corpus

公开仓库的 `knowledge/` 只包含作者自写的示例文档。开发期间使用的第三方来源
城市攻略已被排除，不随仓库再分发；其版权归原作者/原站点所有。使用者自行放入
`knowledge/` 的内容由使用者负责确认授权。

## Software dependencies

Python 依赖见 `backend/requirements.txt`，前端依赖见 `frontend/package.json`
和 `frontend/package-lock.json`。各依赖按其自身许可证分发；本项目不修改其许可。
