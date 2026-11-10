# 云游织图前端设计系统

视觉世界：**薄荷日光 / Fresh Atlas**。清晨的旅行手帐：米白纸面、薄荷空气、深海绿行动色，真实地图和照片提供颜色。产品名统一为「云游织图」。

## 方向

- **THESIS**：旅行规划是一张可扫描、可核对的日光工作台，不是长文说明，也不是营销大屏。
- **OWN-WORLD**：米白纸面 + 白色抬起面板；深海绿承载主行动、激活态、路线、节点和关键数字；海玻璃蓝只做链接、焦点和图表第二色；暖杏只做价格与少量点缀；珊瑚红只做错误与估算警告。
- **SIGNATURE**：每日行程是一条绿色织线；滚动时线随内容绘制，节点进入视口点亮。线索同时承载顺序信息。
- **MODE**：Operate。导航、表单、图表保持用户熟悉的形态；高级感来自真实照片、地图、天气、路线和精密排版，不靠玻璃、紫色渐变或霓虹。

## Tokens

源文件：`src/styles/tokens.css`。浅色主题使用 hex/alpha，便于和打印主题对齐。

| 角色 | Token | 值 |
|---|---|---|
| 页面地面 | `--paper` | `#f8fbf6` |
| 抬起面板 | `--paper-raised` | `#ffffff` |
| 下沉井/输入 | `--paper-deep` | `#eff6f0` |
| 标题/强强调 | `--ink` | `#1f332d` |
| 正文 | `--text` | `#2c4039` |
| 说明/弱化 | `--text-muted` / `--text-faint` | `#4d635b` / `#5c7068` |
| 中性 hairline | `--rule` | `rgb(31 51 45 / 0.12)` |
| 强边界 | `--rule-strong` | `rgb(31 111 92 / 0.28)` |
| 控件边界 | `--edge` | `#7c9187` |
| 主色 | `--accent` / `--accent-strong` / `--accent-deep` | `#1f6f5c` / `#175648` / `#15463c` |
| 主色柔 | `--accent-soft` | `#5e9e8b`（只做装饰/背景，不做小字） |
| 主色底文字 | `--on-accent` | `#ffffff` |
| 暖杏 | `--sun` / `--sun-deep` / `--sun-soft` | `#c4823f` / `#8a5a22` / `#f4e3c9` |
| 海玻璃蓝 | `--patina` / `--patina-deep` / `--patina-pale` | `#3e8fa8` / `#2a7388` / `#dceff3` |
| 错误/危险 | `--vermilion` / `--vermilion-strong` / `--vermilion-soft` | `#c0493d` / `#a33a30` / `#fbe9e6` |
| 浅色仪表 | `--instrument` / `--instrument-raised` | `#eaf2ec` / `#ffffff` |
| 状态圆点/滚动条 | `--gray-2` | `#a9bdb3` |
| 地图 marker | `--map-marker-bg` / `--map-marker-text` / `--map-marker-border` | `#1f6f5c` / `#ffffff` / `#ffffff` |
| 地图弹窗 | `--map-info-bg` / `--map-info-text` | `#ffffff` / `#1f332d` |
| 遮罩 | `--scrim` / `--loading-scrim` | `rgb(31 51 45 / 0.32)` / `rgb(248 251 246 / 0.92)` |
| 焦点环 | `--focus-ring` | `2px solid #2a7388` |

规则：

- 深海绿只做主 CTA、激活态、路线、节点、关键数字和细线；**小号正文不用 `--accent-soft`**。
- 可读的链接/状态文字用 `--patina-deep`；`--patina` 只做图形、进度条和第二色图表，避免 hover 变浅后对比不足。
- 暖杏只做填充或价格标签；小字用 `--sun-deep`（#8a5a22），不用 `--sun`。
- 阴影只用于 Hero、地图、吸顶栏、对话框和大面板，使用青灰环境阴影；不用黑色重投影、白色 inset 高光或玻璃拟态。
- 圆角：控件 3px，面板 8px，大模块 12px；不用 24px 大圆角卡片。
- 浅色长文留空气：正文 15px / 1.7–1.8，中文行宽约 40–48 字（英文 65–75ch）。

对比度基线：ink/paper 12.82、text/paper 10.59、muted/paper 6.19、faint/paper 5.06、白字/accent 6.02、patina-deep/white 6.73、danger/white 4.93。

## 排版

- 标题/数字展示：`"Alumni Sans", "Noto Sans SC", ...`，字重 300–500，几何无衬线。
- 正文/UI：`"Albert Sans", "Noto Sans SC", ...`。
- 数据/标签：`"JetBrains Mono", ui-monospace, ...`，11–12px，等宽数字，大写小标签。
- 拉丁字体自托管；中文走系统字体栈，不打包大体积中文字体。
- 结果页 Hero 标题 `clamp(2.6rem, 5vw, 4.2rem)`；页面标题 `clamp(2.4rem, 3.6vw, 3.4rem)`。

## 页面与布局

- Shell：72px 吸顶半透明米白 Header + `backdrop-filter: blur(10px)`，底部 1px hairline；页面顶部 1px 绿色滚动进度线。
- 登录：左品牌叙事 + 右白卡表单；能力说明沿一条绿线排列；移动端堆叠。
- 规划：8/4 栅格；左侧 hairline 表单，右侧 sticky 行程摘要 + 真实表单状态的「简报完整度」。
- 结果：**单列服务流**。Overview、地图、天气、每日行程、景点、美食、住宿、按天路线、交通、预算、提示、来源、完整回复、调整各占一整行；不把两个服务左右并排。
- 推荐横排：每个服务内部用 `GalleryRail` 横向轨道排卡片。美食卡与酒店卡为纵向卡片（图片/信息上下排列），宽度 `clamp(250px, 34%, 286–292px)`，移动端 `min(82vw, 300px)`，`scroll-snap-align: start`。美食卡顶部的参考餐厅图按可视区域懒加载高德 POI 图片，明确标注“参考餐厅图 · 高德”；附近餐厅详情保持纵向列表。
- 住宿每个区域一条独立轨道；切换「舒适型 / 经济型」后调用 `scrollToStart()` 回到轨道开头。
- 历史：旅行日志自适应多列卡片（宽屏三列、中屏两列、移动端单列）；monogram plate + 真实本地元数据；缺字段显示「未记录」。
- 断点：1180 / 980 / 760 / 520。390 宽度无横向溢出。
- 地图保持真实亮色瓦片，白底卡片 + 薄荷边界 + 柔和青灰阴影；浮在地图上的 tab 使用不透明白底，避免和亮地图融合。

## 组件

`src/components/ui/`：

- `AppButton`：深海绿底白字 primary；secondary 白底绿边；ghost 深绿字；危险珊瑚红；焦点海玻璃蓝 2px + 4px offset；按钮内 Spinner 用 `currentColor`。
- `AppInput` / `AppSelect` / `AppTextarea`：薄荷底、hairline 边界、海玻璃蓝 focus 环；标签和错误通过 `aria-describedby` 关联。
- `AppChip`：白底；选中薄荷底 + 深绿边 + 绿点，不用大面积深色填充。
- `AppTabs`：instrument / paper 两种；白色 thumb + 青灰阴影；支持左右方向键、Home/End、roving tabindex。
- `AppDialog`：原生 `<dialog>`；焦点管理和 Esc 取消；backdrop 用 `--scrim`；删除确认用 danger。
- `AppToast`：白卡；错误 `role="alert"`，其它 `role="status"`。
- `PhotoPlate`：真实图片 + `useImageFailures` 回退；无图用首字 monogram，不画假图。
- `CountUp`：进入视口后滚动数字；读屏只读终值，视觉层 `aria-hidden`。
- `Sparkline`：真实温度序列折线；`aria-hidden`，具体温度由天气卡文字提供。
- `GalleryRail`：横向 scroll-snap、左右按钮、键盘方向键；暴露 `refresh()` 与 `scrollToStart()`；打印时展开为网格。
- `Skeleton` / `EmptyState` / `SectionHeader` / `Tag` / `Spinner`。

## 动效语法

动效只表达顺序、状态和空间关系，不携带信息，也不改变字号/行高/布局。

| 时刻 | 效果 | 时长 |
|---|---|---|
| 登录品牌线 | BrandMark `stroke-dashoffset` 画开 | 900ms |
| 页面切换 | 旧视图淡出、新视图 8px 上移；顶部绿线扫过 | 320 / 360ms |
| Tab thumb | 白色 active thumb 滑动 | 220ms |
| 简报完整度 | 绿线宽度 | 420ms |
| 关键数字 | 天数/人数/预算/来源数 count-up | 720ms |
| Hero 照片 | 1.03 → 1 settle；静态地图只淡入 | 900 / 600ms |
| 地图 | fly-to、marker 40ms stagger、路线 fade | 320ms |
| 织线 | `--thread-progress` 驱动 scaleY；节点进入视口点亮 | 跟滚动 |
| 天气 | 温度/降水条展开；最高温 Sparkline 绘制 | 600ms |
| 卡片 | hover 抬升 2px + 图片 scale 1.03 | 220ms |
| 加载 | LoadingLoom 绿/海玻璃蓝线交织；浅色半透明幕；只显示真实 elapsed | 2.4s loop |

- 统一使用 `prefers-reduced-motion`：直接到终态，不播放 count-up、编织、织线绘制、marker 入场和页面扫线。
- 不使用 View Transitions API，避免地图 canvas 被快照成空白。
- 不做逐段 fade、长中文段落透明度/逐字动画、温度从 0 计数、假百分比/假阶段。

## 打印

`src/styles/print.css` 是独立纯白主题：屏幕是薄荷日光，打印回到纸面文档。

- 覆盖浅色 token 为纸面白/近黑；隐藏 Header、目录、操作按钮、tab、地图模式切换、调整输入和附近餐厅抽屉。
- 地图打印使用同一次请求返回的静态图；没有静态图时明确说明。
- 行程、天气说明、路线备选、完整回复强制展开；CountUp 打印终值，Sparkline/温度条/织线到终态。
- 景点、美食、酒店横向轨道打印为两列网格；住宿区域打印当前档位说明；估算、来源、参考价和警告文字保留。
- `beforeprint` 展开所有 `<details>`，`afterprint` 恢复原来的折叠状态。

## 数据诚实

- 预算只是目标，不是花费；不做花费进度条、剩余预算或费用分类占比。
- 路线费用、打车/骑行费用是估算；保留「估算」「直线估算」和虚线含义。
- 酒店 `avg_cost` 是高德 POI 参考，不是房价；界面写「参考」。
- 餐厅评分是高德 POI 评分，不是点评；不写「高分推荐/评价数」。
- 酒店/餐厅图片和评分标注来自高德；照片署名不限于景点。
- 天气是真实预报但可能缺天/缺字段；不补数据，显示 `weather.error` / `weather.source`。
- 不编造 agent 阶段、百分比、实时徽章或当前定位脉冲；加载只显示真实 elapsed。
- 温度不从 0 计数；CountUp 只用于预算/人数/天数/来源数等计数型数字。
- 没有真实图片时用 monogram plate，不用图库、插画或假地图。

## 文件

```text
src/views/        Auth / Plan / Result / History
src/components/   ui / layout / result / map / loading
src/composables/  auth / route / plan form / trip result / media / hotels / restaurants / routes
src/utils/        date / format / inline / labels / routeModes / storage / scroll / markdown
src/styles/       tokens / base / print
```
