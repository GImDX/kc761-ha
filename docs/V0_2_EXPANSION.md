# KC761x 0.2 控制与谱图扩展设计

状态：`0.2.0-alpha.18`，谱图卡 ECharts SVG 游标/网格对齐修复、缓存版本提升、左下角轴标签间距、紧凑 summary、模块幂等注册、原生 Lovelace 编辑器候选、Sections 固定行高修复、ROI 双滑块/编辑预览边界、unavailable 状态保留和 Configure 一键编辑交接已完成本地静态核对；alpha.6 的 reconfigure 单连接所有权修复保留，真实条目仍只观察

日期：2026-08-21

本文是 0.2 扩展分支的当前设计依据，并覆盖其它文档中仅针对 `0.1.0-rc.1` 的“严格只读”“不创建谱图实体”和“不注册控制实体”等旧范围描述。0.1 RC 的历史验证结果仍然有效，但不能自动证明本扩展分支的软件行为或实机写入安全。

## 0.2.0-alpha.6 reconfigure 连接所有权修复

已加载条目的 reconfigure 现在只调用现有 `KC761Coordinator.async_reconfigure`。Coordinator 在同一锁内先关闭旧客户端，随后才创建候选客户端并读取 `DEVICE_INFO`；同一 `DEVICE_ID` 才切换所有权。候选连接、响应或设备 ID 校验失败时，候选客户端先关闭，再创建旧端点的新客户端恢复轮询；恢复失败会保留原配置数据并报告连接错误，不留下候选后台任务。未加载或 `setup_retry` 条目不在 `hass.data` 中时，Config Flow 才使用一次性临时客户端预检。

本修复的自动门禁必须同时覆盖：旧连接关闭顺序、同 ID 成功切换、候选连接/读取失败回滚、不同 ID 回滚、旧端点恢复失败、setup_retry 临时预检，以及 reload/shutdown 后无残留连接。真实 KC761 条目不执行 reconfigure，不以测试机模拟器结果替代实机服务可用性证据。

## 1. 边界和安全状态

- 本候选已经以完整备份和不可变源码包部署到持久隔离测试栈，并获授权在软件验收后本地合并；合并结果尚未推送，未部署正式环境，也未由集成对真实 KC761 发送写命令。
- 0.1 RC 的 24 小时稳定性测试已经完成；其结果只证明旧 RC 的基础轮询和生命周期，不能替代 0.2 控制与谱图扩展的测试。本轮另对 0.2 候选执行独立短时在线观察。
- 正式 Home Assistant 环境不在本分支开发和验收范围内。
- 允许生成和测试的读取命令为 `0x52/0x53/0x54/0x55`；允许在模拟器中测试的写命令为 `0x62/0x63/0x66`。
- 校准写入 `0x64` 继续禁止，客户端不提供对应编码器或公开方法。
- 所有写事务串行执行，必须收到同一 SYNC、成功状态且回显相同命令的 `0xAA` ACK；随后请求状态或设备信息完成读回。
- `hardware_verified=false` 保持不变。已有实机透明流证明本设备的 A0/A6 读取形状及官方上位机 `0x63` ACK，但不等于本集成已完成 `0x62/0x63/0x66` 实机验收。

## 2. 设备和实体呈现

所有信息统一作为设备下的普通实体呈现，不再使用“传感器实体”和“诊断实体”两套可见性策略。实体默认可见；只有用户未启用“创建原始读数实体”时，raw 测量实体不创建。

Device Registry 使用：

| 字段 | 来源 |
| --- | --- |
| `identifiers` | `(kc761, DEVICE_ID)` |
| `name` | 型号名称加 ID 尾段 |
| `model` | 映射后的型号名称 |
| `model_id` | 原始 `DEVICE_MODEL` 十进制文本 |
| `hw_version` | `HW_VER` |
| `sw_version` | 主处理器 `FW_VER` |
| `serial_number` | 完整原始 `DEVICE_ID` |

当前资料不能可靠确认制造商标准名称，因此不填写 `manufacturer`。

设备级实体包括电池、气压、设备温度、设备时间、时钟偏差、协处理器固件版本、设备唯一 ID，以及 Gamma、Neutron、PIN 三个固定通道的传感器型号。即使某通道未安装，也保留其型号实体并显示“未安装”，使设备信息完整且稳定。

传感器型号映射：

| 原始值 | 显示名称 |
| --- | --- |
| `0x00` | 未安装 |
| `0x01` | KC7601.21 CsI |
| `0x02` | KC7601.24 CsI |
| `0x03` | KC7601.25 CsI |
| `0x04` | KC7601.26 CsI |
| `0x05`–`0x07` | PIN |
| `0x08` | KC7601.31 6Li |
| 其它 | `Unknown (0xNN)` |

已安装通道继续创建 AVG 测量实体；本设备 AVG 恒为 0 的行为按 `KI-AVG-001` 已知问题接受。raw 实体仍由 Options 显式选择是否创建。

## 3. 非破坏性控制实体

| 平台 | 实体 | 协议字段 |
| --- | --- | --- |
| select | 当前传感器 | `RAD_SENSOR_STATUS` bit 0–1 |
| select | 全局音量 | `VOLUME_STATUS` bit 0–1 |
| select | 背光亮度 | `LED_SCREEN_STATUS` bit 2 |
| switch | 自动上传 | `AUTO_UPLOAD_STATUS` |
| switch | 按键音、计数音、密集计数效果 | `VOLUME_STATUS` bit 3/2/4 |
| switch | 计数闪光、背光 | `LED_SCREEN_STATUS` bit 0/1 |
| switch | 各已安装通道谱图累积 | `RAD_SENSOR_STATUS` bit 2/3/4 |
| button | 立即同步设备时间 | `0x63` |
| button | 刷新当前谱图 | `0x52`，只读 |
| button | 手动保存当前谱图 | `0x52`，只读后保存 |

`0x62` 使用 `0xFF` 跳过未修改字段，并从最近一次状态快照计算位掩码，避免覆盖其它设置。设备允许多个上位机同时操作，因此任何外部客户端都可能在读回后再次改变状态；HA 不宣称独占控制权。

设备时间选项只保留两个字段：

- 是否启用定时同步，默认关闭；
- 同步间隔，1–168 小时，默认 24 小时。

启用后在配置条目启动时同步一次，此后按间隔同步。另有“立即同步设备时间”按钮，不需要启用定时同步。

## 4. 清空操作和确认

每个已安装通道提供三个按钮：清空多道谱图及其累积时间、清空剂量—时间曲线、清空累计剂量及其累计时间。协议只把目标通道对应 bit 写 1，其余清空类别和通道写 0。

自定义谱图卡在发送 `0x66` 前显示设备型号、完整设备唯一 ID、目标通道、将被清除的数据类别及不可撤销提示，并要求用户点击浏览器确认按钮。

设备页面中的原生 Button Entity 也带有设备型号、完整 ID 和目标通道属性，用户必须主动按下按钮才会执行。该设计不增加口令、倒计时或二次状态机；验收时必须分别检查误触风险和读回行为。

## 5. 单一结构化谱图实体

集成只创建一个“谱图”传感器，当前值为最新完整谱图的总计数，属性包含：

- `source` 和通道名称；
- `acquired_at` 获取时间；
- `accumulation_seconds` 设备谱图累积时间；
- `point_count`、总计数、分片数和校准档案；
- `points`：恰好 2048 个 `[道址, 能量_keV, 计数]` 三元组。

`points` 在 HA 当前状态机和 WebSocket 中可用，供前端卡绘图，但通过实体 `_unrecorded_attributes` 明确排除在 Recorder 外。Recorder 只保留谱图总计数和有界元数据，不把 2048 点数组复制进每次状态历史。

完整谱图由同一 SYNC 下的 A0 分片按 source/offset 重组。当前本设备已确认 4×512 道；实现仍按覆盖恰好 2048 道判断完成，支持乱序、分片 TCP、重叠一致片段，并拒绝 source 不符、越界或重叠冲突。

能量值由同连接读取的 A6 校准数据计算。Gamma 支持工厂单段、工厂三段和自定义标定选择；Neutron、PIN 使用各自工厂多项式。读取和绘图不开放 `0x64` 校准写入。

## 6. 谱图卡、CSV 和历史谱图

集成内置 `/kc761/kc761-spectrum-card.js`，并在 Home Assistant 启动集成时自动注册为前端 JavaScript Module。正常安装或升级后不需要手动添加 Lovelace 资源；`KC761 Spectrum` 应自动出现在“添加卡片”选择器中，也可使用 YAML：

```yaml
type: custom:kc761-spectrum-card
entity: sensor.<kc761_spectrum_entity>
title: KC761 Spectrum
x_axis: energy
refresh_interval: 0
y_axis: linear
show_controls: true
show_summary: true
show_legend: true
show_actions: true
show_peak: true
```

若旧部署尚未更新并且必须临时恢复，可将 `/kc761/kc761-spectrum-card.js?v=0.2.0-alpha.18` 手动注册为 JavaScript Module。自动加载与手动资源同时存在时，卡片会通过 `registerKC761SpectrumCard()` 对 Custom Element 和选择器条目做幂等注册；确认新版本自动加载正常后可由用户自行移除旧的手动资源。

### 6.0 原生 Lovelace 编辑器与可见性边界

从 alpha.13 起，卡片提供 `getConfigForm()`：实体字段使用 Home Assistant 原生 entity selector，并按 `integration: kc761`、`domain: sensor` 过滤；标题、X/Y 轴、刷新间隔和内容开关使用内置 text/select/number/boolean 控件。刷新间隔只接受 `0` 或 `5`–`3600` 的整数；缺省字段按兼容默认值补齐，未知字段不会被 `setConfig()` 删除。`getEntitySuggestion()` 只对带 `config_entry_id`/`codec_profile` 身份且包含 0 或 2048 点结构化 `points` 的 KC761 谱图状态提出建议；卡片选择器首次打开时不再注入虚构的 `sensor.kc761_spectrum`。

五个内容开关的作用范围如下：`show_controls` 控制通道、坐标、历史和 ROI 输入；`show_summary` 控制摘要网格；`show_legend` 控制图例；`show_actions` 控制刷新、保存、CSV 和清空按钮；`show_peak` 同时控制峰值摘要、峰值图例和 ECharts `markPoint`。这些开关只改变 DOM/ECharts 呈现，不改变数据、历史或协议请求。

卡片不自行实现整卡条件编辑器。Home Assistant `hui-card-element-editor` 的标准编辑壳在支持的 Sections/仪表盘上下文提供 `config`、`visibility`、`layout` 三页；用户在“可见性”页设置的整卡条件与卡片内部 `show_*` 内容开关是两层不同语义。`getCardSize()` 在已渲染时按实际 `ha-card` 高度换算安全 Masonry 行数，未渲染时使用保守估算；`getGridOptions()` 仅声明 Sections 使用满宽和至少 6 列，不固定行数，也不暴露任意像素高度。

Y 轴有两个持久化层级：YAML/编辑器显式改变 `y_axis` 时更新该实体的 configured baseline 并覆盖旧偏好；用户在卡片运行时切换线性/对数时只更新 selected preference。页面刷新且 configured baseline 未改变时恢复 selected preference；每个实体使用独立 localStorage 键，存储异常安全降级为配置值。切换 `entity` 会清理旧设备、entry、历史、数据、ECharts 实例和自动刷新计时器，迟到的旧 WebSocket 结果不会回写新实体。

卡片提供：

- Gamma、Neutron、PIN 已安装通道切换；
- X 轴道址/能量 keV 切换；
- Y 轴线性/对数切换；
- Y 轴选择按谱图实体写入浏览器 `localStorage`，刷新或重新打开页面后恢复；
- 使用本地 vendored ECharts 6.1.0 的 SVG renderer，Y 轴留出独立刻度/label 边距，不使用 Canvas 或外网 CDN；
- 两个可拖动的 ROI 游标、非黄色的半透明 ROI 区域，以及独立颜色的自动峰值标记；
- 一个可拖动的数据游标，实时显示道址、能量和计数；
- 累积时间、获取时间、总计数和峰值显示；
- 用户选择的 ROI 范围及范围内计数；
- 手动刷新、保存和 CSV 下载；
- YAML 可配置 `refresh_interval`（默认 `0` 关闭；启用时为 `5`–`3600` 秒）。自动刷新只在 `Latest`、已连接且没有保存/清空等操作进行时读取最新谱图，卡片卸载时清理定时器；

ECharts 6.1.0 的 Apache-2.0 `LICENSE`、`NOTICE` 和 D3 派生许可证随卡片分发；npm registry integrity 和 vendored 文件 SHA-256 记录在 [echarts-THIRD-PARTY.md](../custom_components/kc761/frontend/echarts-THIRD-PARTY.md) 中。
- 已保存谱图列表和历史谱图回看；
- 三类清空操作及带设备身份的简单确认。

### 6.1 alpha.8 游标与原生 History 外观对齐

- 游标的屏幕位置不再根据固定 `GRID.left/right` 横向换算；卡片使用 ECharts `convertToPixel({xAxisIndex: 0}, value)` 取得实际 plot 坐标，并使用 `convertFromPixel` 将拖动位置反算回当前 X 轴值。该路径同时覆盖 `containLabel`、Energy/Channel 和 resize 后的实际坐标。
- ROI `markArea` 和唯一的数据游标 `markLine` 共用同一最近通道点的 X 值，因此高亮区域、数据线与谱图保持像素级对齐；ROI 起止点由双 range input 选择，不再在图内叠加两条竖线或文字标签。
- 网格采用 HA 原生统计图同类的 `containLabel` inset，轴标签启用 `hideOverlap`、明确的首尾标签和主题色；Y 轴标题改为 plot 顶部的水平 `nameLocation:"end"`/`nameRotate:0` 布局，并为标题保留独立 top reserve，避免与最大刻度重叠。Energy 轴标签按量级保留 1–2 位小数，Channel 轴使用整数，避免长浮点端点挤压 Y 轴边缘。谱图背景、文字、分隔线从 HA 主题变量读取，暗/亮主题均有安全 fallback。
- HTML 游标的 X 坐标使用 ECharts pixel 加上 `chartElement.offsetLeft`；若浏览器不提供该 offset，则使用实际 `getBoundingClientRect` 并扣除 `wrap.clientLeft`。不再把包含边框的 chart/wrap 左边距重复计入，目标是与 `markArea`/`markLine` 保持小于四分之一像素的边缘误差。
- 谱线与图例不再把 `#4fc3f7` 当作运行时主色，而是复用 HA History 的 `--graph-color-N → --color-N` 变量链；ROI、峰值和数据游标使用后续序号并做重复色保护，Tooltip 背景、边框和文字跟随 HA card/divider/text 主题变量。
- 测试机 HA 2026.7.4 构建取证：`frontend_latest/22752.a1299e9730c4c314.js` 的 `hui-history-graph-card` 将历史数据交给 `state-history-charts`，并设置 `--timeline-top-margin:16px`；`frontend_latest/23495.c7798ed370241bd4.js` 的 `statistics-chart` 使用 `ha-chart-base`、`grid:{top:15,left:1,right:1,bottom:0,containLabel:true}`、`yAxis.nameGap:2` 与 `tooltip.renderMode:"html"`。这些是测试机实际构建/运行规则，不将压缩产物误写成上游源码全文。
- 同一 HA 前端的 `frontend_latest/24652.561e19dfa7c4a178.js` 模块 `63746`（在其它懒加载副本中也保持一致）定义原生 History 调色板链：序列 `t` 先读取 `--graph-color-${t+1}`，缺失时回退到 `--color-${t%54+1}`，再交给颜色解析器。KC761 卡片的曲线、图例、ROI 区域、峰值和数据游标现在按同一 `--graph-color-N → --color-N → 固定 fallback` 链解析；ROI、peak、data 使用不同序号并检测重复值，保证仍与谱线可区分。Tooltip 使用 HA card/divider/text 主题变量、HTML render mode 和数据游标色轴指示线。

CSV 每行包含 source、通道名称、获取时间、累积时间、道址、能量 keV 和计数，采用 UTF-8/RFC 4180 兼容文本。

历史谱图不进入 Recorder，而是按配置条目保存到 HA 私有 `.storage/kc761/<entry_id>/` 下的独立 gzip JSON 文件：

- Options 选择是否定时保存及保存周期，默认关闭，周期 5–1440 分钟；
- 启用时依次保存所有已安装通道；
- 手动保存按钮不受定时选项影响；
- 每个条目最多保留 256 张，按获取时间淘汰最旧文件；
- 文件名使用受限快照 ID，原子替换写入，拒绝路径穿越并跳过损坏文件。

当前不实现区间谱、差分谱或“两个历史谱相减”。

## 7. 本地 API 和隐私

谱图卡通过 HA 已认证 WebSocket 命令读取当前/历史谱图、保存、导出和清空。捕获、保存、清空要求管理员权限；信息、列表、查看和导出要求已登录用户。完整设备 ID 只通过本地已认证信息接口返回，用于清空确认，不进入默认诊断或日志。

默认 diagnostics 继续脱敏主机、完整 ID、原始帧和谱图点，只记录 ID 尾段、版本、协议档案、连接/协议计数、控制命令计数、最近同步时间、最近谱图的有界元数据和最近保存时间。

## 8. 验收门禁

### 8.1 2026-08-21 `0.2.0-alpha.3` 软件候选结果

- 本地 Ruff、Python 编译、JSON/翻译和 JavaScript 语法检查通过。
- 当前候选在固定 Home Assistant 2026.7.4 ARM64 镜像的一次性隔离 runner 中通过 `54` 项 pytest，branch coverage 为 `96%`，达到不低于 95% 的门禁；仅出现一条 HA 自身 aiohttp 弃用告警。
- 最终候选源码包本地和测试机 SHA-256 均为 `305ac28734748e2534e773a5b8a65dc16a8b0539da3b818fe87abf87d304fbb7`。第一次精简包漏装 README、HACS 配置和公开文档，得到 52 项通过、2 项仅因文件缺失失败；失败包和日志保留，补全文件清单后复验通过。
- runner 使用 `--network none`、只读根文件系统、非 root 用户、移除 capabilities 和 `no-new-privileges`。通过后候选才进入持久隔离测试栈，替换前保存了完整配置、源码、集成和模拟器入口备份；测试 HA 与模拟器约 4 秒恢复就绪。
- 配置条目版本迁移到 5：实机条目注册 43 个实体、活动 42、不可用 0；唯一禁用实体是用户此前手动禁用的协处理器版本。模拟器条目注册/活动均为 30、不可用 0、无禁用实体。迁移恢复集成默认禁用的普通信息和 AVG 实体，同时保留用户禁用选择并按 Options 清理旧 raw 注册项。
- 模拟器专用验收保存 1 张 2048 点谱图，历史列表由 0 增至 1，历史回读为 2048 点，CSV 为 1 行表头加 2048 行数据。17 次实体控制调用结束后，开关和选择项恢复原状态；实机条目目标数为 0。
- 模拟器命令审计与动作完全一致：`0x62` 12 次、`0x63` 1 次、`0x66` 3 次，保存和刷新各产生一次额外 `0x52`；`0x64`、未知命令和 `FORBIDDEN` 均为 0。该结果只证明模拟器软件路径，不证明真实设备写入安全。
- Recorder 只读审计发现 2 个谱图实体和 19 条已记录谱图状态，其中包含 `points` 的记录为 0。2048 点数组只存在于当前状态/WebSocket 和独立 gzip 历史文件。
- 模拟器条目禁用时进入 `not_loaded`，30 个保留状态全部为 `unavailable`；重新启用后 30/30 恢复且不可用为 0，实机条目始终保持加载。随后单独重启 HA，约 4 秒恢复；两个条目、实体、当前谱图、历史谱图和卡片资源均恢复，日志没有集成异常或残留任务，模拟器只观察到初始化读命令。
- 重启和生命周期验收后继续只读观察 600 秒，共 21 个样本；HTTP 失败、条目加载失败、活动实体 `unavailable` 和两份实时谱图点数失败均为 0。该短时结果只覆盖 0.2 当前候选，不能替代 0.1 RC 已完成的 24 小时运行证据，也不能证明真实设备写入安全。
- 卡片 JavaScript 语法、静态资源 HTTP、当前/历史 WebSocket、保存和 CSV 已通过。自动化浏览器受到 LAN HTTP 客户端策略拦截，且没有已连接的外部浏览器，因此三种坐标、ROI、确认对话框和实际下载仍保留为人工可视门禁；没有绕过浏览器安全策略或向浏览器注入长期令牌。

### 8.2 2026-08-22 `0.2.0-alpha.4` 卡片自动加载修复

- alpha.3 只通过 `async_register_static_paths` 提供了卡片文件，但没有把模块加入 HA 前端启动资源。因此手写 `type: custom:kc761-spectrum-card` 会报 `Custom element not found`，卡片也不会自动进入选择器。
- alpha.4 在同一幂等 setup 路径调用 HA 前端模块注册接口，并用集成版本作为查询参数刷新浏览器缓存；manifest 显式依赖 `frontend` 和 `http`。
- 卡片脚本对 Custom Element 和 `window.customCards` 都增加重复加载保护，兼容升级后仍保留旧手工 Lovelace 资源的情况。
- 本地 5 项静态契约测试、24 个 Python 文件和 5 个 JSON 文件通过；固定 HA 2026.7.4 ARM64 镜像再次通过 54 项 pytest，branch coverage 为 96%。候选包在本机和测试机的 SHA-256 均为 `78a9d5e2f9927b49abdca4c9cbce48da1cbcac81488d17b0cde0bd2ac58801da`。
- 候选部署到持久测试栈并重启后，原来报错的手工卡已渲染为完整 `KC761 Spectrum` 界面。浏览器只读检查确认 `kc761-spectrum-card` 已定义，`window.customCards` 中恰有 1 个同类型条目，名称为 `KC761 Spectrum`；这同时验证了 YAML 加载与选择器登记。
- 示例中的 `sensor.<kc761_spectrum_entity>` 是占位符，必须替换成当前设备真实的谱图实体 ID；占位符本身会显示 `Entity not found`，这与卡片模块加载无关。

### 8.3 2026-08-22 `0.2.0-alpha.5` ECharts SVG 卡片重构（固定 runner 与测试栈结果）

- 卡片已移除手绘 Canvas 路径，改为集成内置的 ECharts 6.1.0 ESM，并固定 `renderer: "svg"`；第三方许可证、npm integrity 和文件哈希见 `frontend/echarts-THIRD-PARTY.md`。
- ECharts `grid` 的左右边距和 Y 轴 `containLabel`/`nameGap` 为刻度与 label 留出独立空间；ROI 改为青色半透明 `markArea`，峰值、谱线和数据游标使用互不相同的颜色。
- 两个 ROI 游标和一个数据游标使用可访问的 pointer/keyboard 控件，拖动时同步输入框、ROI 汇总、数据点读数和 ECharts 标记；游标位置在 ResizeObserver 调整后重新定位。
- `refresh_interval` 只接受 `0` 或 `5`–`3600` 秒，并且仅在 Latest、已连接且非 busy 时捕获；`disconnectedCallback` 清理定时器、ResizeObserver 和 ECharts 实例。
- `y_axis` 可在 YAML 中初始化，用户改变后的值以实体 ID 隔离写入浏览器 `localStorage`；存储异常按安全降级处理。
- 固定 Home Assistant 2026.7.4 ARM64 runner 使用不可变镜像完成 `54` 项 pytest，branch coverage 为 `96%`；本地/远端候选源码包 SHA-256 均为 `6930091552e7df3ca3c531913842af9d57c08c34a647c64e2133ceae67124841`，随集成提供的 ECharts 6.1.0 文件 SHA-256 为 `28515f26aa57f87eb1e4ed4eb446927eaf329b5f5e02b4bfd4b0264fea40d1da`。固定镜像内完成 Python 编译与 JSON 检查；该镜像未安装 Ruff，不能将 Ruff 记为通过。
- alpha.5 已在持久隔离测试栈启动并就绪；HA、卡片和 ECharts 静态资源均返回 HTTP 200，manifest 为 alpha.5。实机与模拟器条目均通过只读 API 检查为 loaded，实机 42/42 活动实体、模拟器 30/30 活动实体均无 unavailable，两个谱图均为 2048 点；模拟器条目通过正常 Config Flow 创建，未直接编辑 `.storage`。预部署只读核对未发现旧手工 KC761 JavaScript resource，未修改 Lovelace 配置。
- 替换前完整备份已保留在测试栈的 `pre-v02-alpha5-echarts-20260821T181750Z` 备份目录。部署启动窗口曾记录 3 条真实设备 coordinator `TimeoutError`；随后只读检查已恢复为 loaded 且 unavailable 为 0，但本轮 120 秒持续观察被工具中断，未据此宣称无错误长期运行。
- 本轮页面/Config Flow 只进行了读取路径核对，未调用控制实体；未对真实 KC761 发送写命令。固定 runner 中的模拟器控制测试属于历史软件路径覆盖，不能替代本轮持久运行命令计数审计。
- 浏览器发现阶段返回 `No browser is available`，因此尚未自动验证实际 SVG DOM、无 canvas、Y 轴视觉间距、三游标拖动/读数、配色独立性、`y_axis` reload 持久性和 `refresh_interval` 两周期捕获；这些项目保留为用户浏览器中的人工门禁。

### 8.4 2026-08-22 `0.2.0-alpha.6` reconfigure 单连接所有权验收

- alpha.6 固定 runner 在 HA 2026.7.4 ARM64 不可变镜像中通过 `55` 项 pytest，branch coverage 为 `96%`；候选包 SHA-256 为 `65f907c3a65627f5a75f5eab3b6a80e5a4c463f1ba64d3a97cf08ae8d6528ca5`，ECharts 文件 SHA-256 仍为 `28515f26aa57f87eb1e4ed4eb446927eaf329b5f5e02b4bfd4b0264fea40d1da`。
- 持久测试栈已保留完整替换前备份，并运行 alpha.6 release；manifest 为 alpha.6，HA、卡片和 ECharts 资源均 HTTP 200，最近集成日志错误计数为 0。
- 首次验收脚本从 REST 条目列表读取被脱敏的 endpoint `data`，将缺失端口默认为 `0`，因此得到 HTTP 400 `{"errors":{"port":"value must be at least 1"}}`。检查 HA 2026.7.4 实际 handler 后确认标准接口为 `POST /api/config/config_entries/flow` 携带 `handler` 和 `entry_id`，再向 flow resource 提交表单。修正脚本使用持久栈固定的 simulator endpoint 后，正常 Config Flow same-endpoint reconfigure 已完成；随后只读检查为 simulator `loaded`、30 个注册实体、0 个 `unavailable`。
- reconfigure 前后 simulator 容器日志的累计差分窗口只增加读取命令：初始观察值 `0x52/0x53/0x54/0x55 = 13/158127/12945/11`，提交后立即观察值为 `14/158148/12947/12`；`0x62/0x63/0x64/0x66` 计数保持 `12/1/0/3`，本轮没有新增写命令，`FORBIDDEN` 日志为 0。累计差分包含 reload 后的正常轮询，不能把每个读命令都归因于单次 flow。
- 严格单客户端测试证明旧 client 关闭事件先于候选连接事件；候选失败和 wrong `DEVICE_ID` 均先关闭候选再恢复旧端点。远端 simulator 当前 namespace 的活动 TCP socket 观测为 0，表示没有残留连接；远端峰值顺序仍以严格 fake 的事件断言为软件证据，不伪称为 wire-level 峰值抓包。
- 同期只读条目检查显示 simulator 为 `loaded`，真实条目仍为 `setup_retry`；真实条目未执行 reconfigure，未因本轮验收新增协议请求或写命令。

### 8.5 `0.2.0-alpha.8` 谱图游标/网格修订（历史测试栈候选）

- 卡片版本与 manifest/集成常量统一为 alpha.8；alpha.7 的 r2 功能代码保持不变，本次提升版本号是为了让 HA 额外 JS 模块 URL 由 `?v=0.2.0-alpha.7` 变为 `?v=0.2.0-alpha.8`，强制浏览器重新加载修订卡片；ECharts 6.1.0 vendored 文件未改动。
- 静态契约新增 `convertToPixel`、`convertFromPixel`、实际 grid rect、`containLabel`、首尾轴标签、水平 Y 轴标题、短 X 标签、实际 containing-block offset 和主题 fallback 检查，并明确禁止重新使用 `GRID.left/right` 固定横向换算。
- 本地 `pytest` 为 `47 passed, 1 skipped`，静态集成测试为 `5 passed`，Ruff、Python compile、JSON 和 diff whitespace 检查通过；本机未提供独立 Node/JavaScript parser，故未把 JS 语法门禁误记为独立通过。
- alpha.8 缓存失效候选包 SHA-256 为 `f908a203cb111fbe39d241b740e748fa0a990f678df283d9fb4f1ca4979f5789`，卡片文件 SHA-256 为 `34263dc82d813fd08255ce84c84e1dfd7a75295d3d2dc43564eff5c36fdb64f8`，ECharts 6.1.0 文件 SHA-256 仍为 `28515f26aa57f87eb1e4ed4eb446927eaf329b5f5e02b4bfd4b0264fea40d1da`；候选已在持久隔离测试栈运行，HA 根页面、alpha.8 卡片 URL 和 ECharts URL 均 HTTP 200，入口 HTML 已包含 `/kc761/kc761-spectrum-card.js?v=0.2.0-alpha.8` 且不再包含 alpha.7 URL。两个 KC761 条目均 loaded，谱图均为 2048 点且无 unavailable。
- 本轮部署前备份已保留；最近 15 分钟 HA 与模拟器日志未发现 KC761/frontend/traceback/pending-task 异常。模拟器累计命令审计的写命令计数仍为历史值 `0x62/0x63/0x64/0x66 = 12/1/0/3`，本轮部署及只读检查未增加写命令，禁止命令日志为 0；这不是对真实设备线速流量的替代证据。
- alpha.8 浏览器复测已通过 SVG/no-Canvas、Y 标题、ROI 游标、主题色、log 持久化和键盘实时更新门禁；仅剩左下角最小轴标签 bbox 边缘交叠，已由 alpha.9 的 X 轴 label margin 修复。

### 8.6 `0.2.0-alpha.9` 左下角轴标签间距修订（最终候选）

- alpha.8 浏览器验收已确认 Y 标题与全部 Y 刻度无重叠、ROI start/end 与图上游标误差分别为 `-0.0109px`/`-0.0026px`、SVG renderer 生效且无 Canvas、主题色链正确、log 模式刷新后持久、键盘 `ArrowRight` 可将 ROI start 从 0 实时更新为 1。
- alpha.8 仍有一个左下角 bbox 边缘交叠：Y 最小刻度 `1` 与 Energy 最小标签 `-92.47` 垂直方向相差约 `1.833px`。alpha.9 只将 X 轴 `axisLabel.margin` 从 8 调整为 12，并提升资源版本/查询参数以强制 HA 重新加载；不改变协议、游标映射、颜色或其它图表行为。
- alpha.9 候选包 SHA-256 为 `981b3976b26015e1e68324d20ff7cec3071c83f208d66aae77d98fbf30969e2d`，卡片文件 SHA-256 为 `7daf12e26c7838cbb4d0ba2cf6ff741ab79834c1da96d3dbc0805ebb8c90a9a2`，manifest 文件 SHA-256 为 `7ae34e5387a74587717fccaa5b3966f05d87ade8eb2d54dcac364cc30033d9c8`，ECharts 文件 SHA-256 仍为 `28515f26aa57f87eb1e4ed4eb446927eaf329b5f5e02b4bfd4b0264fea40d1da`；测试栈入口仅包含 alpha.9 URL，卡片/ECharts/HA 根页面均 HTTP 200，两个条目 loaded、谱图均 2048 点且无 unavailable。
- alpha.9 浏览器验收在 alpha.9 URL reload 后确认 `cardCount=1`、`SVG=true`、`Canvas=false`、所有 SVG text bbox `overlaps=[]`；ROI start/end delta 分别为 `-0.010894775390625px` 和 `-0.0026397705078125px`，vertical top/bottom 分别为 `0px` 和 `0.007843017578125px`。主题色为 curve `rgb(66,105,208)`、ROI `rgb(244,189,74)`、peak `rgb(108,197,176)`、data `rgb(164,99,242)`；log 模式跨 HA restart/reload 持久。`ArrowRight` 从 0 到 1 的实时更新已在 alpha.8 验证，alpha.9 映射代码未变。
- alpha.9 重启后最近 15 分钟 HA/模拟器日志未发现 KC761/frontend/traceback/pending-task 异常；模拟器写命令累计值仍为 `0x62/0x63/0x64/0x66 = 12/1/0/3`，本轮部署与只读检查未增加写命令，禁止命令日志为 0。CUA 自动化无法稳定定位指针拖动目标，实际 pointer-drag UX 仍保留为人工检查项；不将其误记为自动化通过。

### 8.7 `0.2.0-alpha.11` Summary 稳定高度修订

- 六个 summary 单元统一使用固定 `80px` grid row/cell 和完整两行 value 区；按当前 label、margin、padding 和两行 line-height 需求预留足够空间，短值、长值、峰值和 Data cursor 文本不会改变卡片整体高度，也不依赖固定整个 `ha-card` 高度。
- 可见值最多显示两行并安全换行/省略；每个动态值均通过统一 `_setSummaryValue` 设置 `title` 与 `aria-label`，完整文本仍可通过悬停和辅助技术访问，适配窄卡及暗/亮主题。
- alpha.11 仅改变 summary 布局/可访问性和缓存版本，不改变协议、谱图坐标、游标、ROI、颜色或自动刷新行为；浏览器高度视觉验收由主任务执行，自动化尚未宣称通过。
- alpha.10 的初始 `72px` 版本在代码审查中因两行值区理论需求约 `78.8px` 被淘汰，未作为最终候选；alpha.11 将 grid row/cell 同步提升为 `80px` 后重新打包部署。
- alpha.11 候选包 SHA-256 为 `3d16b4b20ba192c807059c95023461dfb64b3018e67e9f9758c4c74c9545db9a`，卡片文件 SHA-256 为 `69fbfcc025f9b2b4c68a45220834b41757549f2a07784d332ecdd4a4d654cdb5`，manifest 文件 SHA-256 为 `2edca2af540c2e4a767aed5bcc1083acb27201de2d6ee56715fcc319d172d8ad`，ECharts 文件 SHA-256 仍为 `28515f26aa57f87eb1e4ed4eb446927eaf329b5f5e02b4bfd4b0264fea40d1da`；测试栈入口仅包含 alpha.11 URL，卡片/ECharts/HA 根页面均 HTTP 200，两个条目 loaded、谱图均为 2048 点且无 unavailable，最近 15 分钟无相关日志异常。
- alpha.11 的模拟器命令审计中 `0x62/0x63/0x64/0x66` 累计值仍为 `12/1/0/3`，本轮部署与只读检查未增加写命令，禁止命令日志为 0；这不是对真实设备线速流量的替代证据。

### 8.8 `0.2.0-alpha.12` 卡片模块注册与缓存修订

- alpha.11 用户页面显示“配置错误”且卡片未渲染，入口 HTML 已包含其额外 JavaScript URL；本地 Node 最小 DOM 桩未复现模块语法或顶层注册异常。结合 alpha.12 使用新 URL 后真实卡片已渲染的结果，“启动早期加载失败后被相同 URL 缓存”是目前最符合现象的候选根因，但尚未被单独证明，也不宣称为协议或 ECharts 逻辑错误。
- alpha.12 提升集成/卡片版本查询参数以强制重新请求模块，并将 Custom Element 与选择器登记集中为 `registerKC761SpectrumCard()`：使用 `globalThis.customElements`、可重复导入保护和数组化 `window.customCards`，用于降低 HA 前端启动期间全局绑定/重复导入的风险；不能据此单独证明已解决某个特定启动时序问题。
- 本地门禁使用无绝对路径的 `node --check custom_components/kc761/frontend/kc761-spectrum-card.js`；本机绑定 Node 24.19.0 执行通过。静态契约以标准库 `unittest` 实跑为 `6` 项、`5` 通过、Node PATH 项 `1` 跳过（PATH 未暴露绑定 Node；绝对路径不写入测试），并另以绑定 Node 完成语法门禁和最小注册/重复导入桩测试。
- alpha.12 已部署到持久隔离测试栈；候选归档 SHA-256 为 `419feb1d4142bc20f7cd0b90baf5d8634f0efe6151d92e25c8ccd2ab40f57635`，远端 release/live 的 manifest、卡片和 ECharts SHA-256 分别一致为 `45a41468409a72206f2190c53b89535e769386a4f6b5ccdd54ca5656c7016048`、`d9b240204582c2f7b8daae53a8315c937334e57bcf615160421409286619099c`、`28515f26aa57f87eb1e4ed4eb446927eaf329b5f5e02b4bfd4b0264fea40d1da`。HA/模拟器保持 running、restart count 为 0，两个静态资源 GET 为 `200 text/javascript`，入口仅含 alpha.12 URL；只读 API 显示 real `loaded`、42/42 active、0 unavailable，simulator `loaded`、30/30 active、0 unavailable，两个谱图均 2048 点。
- alpha.12 部署后模拟器累计命令为 `0x52/0x53/0x54/0x55 = 22/161243/13199/20`，禁止写命令 `0x62/0x63/0x64/0x66 = 12/1/0/3`，较部署前只增加只读请求，`FORBIDDEN_LOGS=0` 且活动连接为 0；这不是对真实设备线速流量的替代证据。
- 主任务在 HA fully ready 后完成浏览器复核：卡片真实渲染且 DOM 存在 `kc761-spectrum-card`；六个 summary cell 均为 `80px`，六个 value 区均为 `39px`，每个动态值的 `title`/`aria-label` 与完整文本一致。Data cursor 从 `CH 149 · 96.71 keV · 2147` 更新为 `CH 154 · 103.68 keV · 2171` 后，卡片高度始终为 `1217.484375px`（`delta=0`），浏览器 error/warning 日志为空；该项确认 alpha.12 的注册与 summary 高度修复已生效。

### 8.9 `0.2.0-alpha.13` 原生编辑器与内容可见性候选

- 卡片版本、集成 `VERSION`、manifest 和 HA extra-JS 查询参数统一提升为 alpha.13。`getConfigForm()` 使用 HA 内置表单 schema 提供 entity/title/x_axis/y_axis/refresh_interval 及五个 `show_*` 内容开关；`assertConfig` 与运行时 `validateConfig` 共用刷新、轴模式和布尔值约束。
- `getStubConfig()` 不再产生虚构的 `sensor.kc761_spectrum`；`getEntitySuggestion()` 只对带 KC761 身份属性且为 0/2048 点结构的谱图状态推荐配置。缺实体卡实例显示可选实体提示，不在编辑器打开阶段抛出异常。
- `show_controls/show_summary/show_legend/show_actions/show_peak` 只改变内容 DOM 或 `markPoint` 呈现；峰值隐藏不会修改数据或发起协议请求。整卡 visibility/layout 继续交给 HA 标准 `hui-card-element-editor`，卡片新增 `getGridOptions()` 只声明 Sections 的满宽/最少 6 列约束，并由 `getCardSize()` 根据实际渲染高度估算 Masonry 行数。
- Y 轴 localStorage 改为 configured baseline + selected preference：显式编辑器配置覆盖旧偏好，运行时选择在 baseline 不变时跨刷新保留；entity 切换清理旧连接关联、历史、数据、计时器和 ECharts 实例，并用 generation 丢弃迟到结果。未知 YAML 字段在配置归一化时保留。
- 当前 alpha.13 已完成本地门禁：项目虚拟环境 `pytest` 为 `47 passed, 3 skipped`（HA 运行时依赖、PATH Node 两项按环境跳过）；绑定 Node 24.19.0 的 `--check` 与 `tests/frontend_card_contract.mjs` 均通过，Python `compileall`、JSON 解析和 `git diff --check` 通过。候选已部署到测试 HA；浏览器复核确认当前 `HUI-SECTIONS-VIEW` 页面中的卡片真实渲染，DOM 存在 `kc761-spectrum-card`，显示 2048 点谱图控件，未出现 `Custom element not found`。这只证明卡片运行时加载，不替代标准编辑器三页或其它浏览器视觉门禁。
- 浏览器自动化能够进入 `?edit=1` 并识别卡片菜单，但在继续打开卡片编辑器时多次超时；因此 Config 页字段、HA 原生整卡 Visibility 条件页、Sections 上下文 Layout 页目前均保留为人工门禁，不能把页面已渲染或静态 `getConfigForm()` 契约误记为三页已通过。预期的编辑器边界仍是：Config 页包含 entity/title/x_axis/y_axis/refresh_interval 与五个 `show_*` 内容开关；Visibility/Layout 由 HA 标准 `hui-card-element-editor` 提供，卡片不复制这两页。

### 8.10 `0.2.0-alpha.14` Sections 固定行高修复

- 测试机实时复现：HA `HUI-SECTIONS-VIEW` 的固定高度卡片 wrapper 为 `card fit-rows full-width`、`--row-size:8`，外层高度约 `503.997px`；alpha.13 的 `KC761-SPECTRUM-CARD` 仍按自然内容高度约 `941.302px` 渲染，overflow/overflowY 为 visible，超出约 `437px` 并覆盖后续布局。故障发生在 Sections 固定 grid cell 与卡片 host 高度链之间，不是谱图数据或协议问题。
- Home Assistant Sections 的固定 rows 由外层 `.card.fit-rows` 计算；卡片配置中的 `grid_options.rows` 仍由 HA 保存。alpha.14 不在 `getGridOptions()` 中伪造固定 rows，而是让卡片 host 和内部 `ha-card` 沿已有 wrapper 使用 `height:100%`、`min-height:0`；内部内容改为 `overflow:auto`，固定高度不足时在卡片内部滚动，避免溢出覆盖后续 grid item。Automatic height 为 auto 时百分比高度回落为自然内容高度，保留原有谱图、游标、控件和按钮。
- 静态回归契约覆盖 host/`ha-card` 高度链、内部滚动、`grid_options.rows` 保留、无 `rows:8` 硬编码和无网络 getter；官方背景见 [Home Assistant custom card sizing](https://developers.home-assistant.io/docs/frontend/custom-ui/custom-card/)。
- 主任务在测试 HA 的同一 `HUI-SECTIONS-VIEW` 编辑页面完成固定行高浏览器复核：保持 `--row-size:8` 时，卡片 rect 为 `503.997375px`，`HUI-CARD-EDIT-MODE` wrapper rect 为 `503.997375px`，delta 为 `0`；内部 `ha-card` 同高，卡片 `overflow:hidden`、内部 `overflow:auto`，`clientHeight=502`、`scrollHeight=940`。alpha.13 修复前卡片 rect 为 `941.302px`，相对约 `503.997px` wrapper 溢出约 `437px`，本次复核确认固定行高路径不再覆盖后续布局。
- 浏览器日志中 KC761 过滤后的 error/warning 为空；部署/重载时间点另有一条无法归因于 KC761 的通用 `Object` 错误，因此不把日志记为全局零错误。上述浏览器证据只覆盖 Automatic height 关闭的固定行高路径；Automatic height 开启时的自然高度视觉行为仍是人工门禁，尚未宣称通过。

### 8.11 `0.2.0-alpha.16` 谱图卡交互收敛与缓存修复候选

- 普通仪表盘视图和带真实 Lovelace `path` 的 dashboard edit wrapper 隐藏 Channel、X axis、Y axis 和 Saved spectrum 选择器，只保留谱图内容与必要操作；实际卡片编辑对话框 preview（标准 `preview=true` 且没有路径）显示这些配置辅助控件。卡片标题旁的 Configure 按钮不调用 HA 私有对话框 API：在真实编辑祖先提供 `path` 时冒泡官方 `ll-edit-card` 事件，否则仅把当前仪表盘 URL 切换到 `edit=1`，由 HA 编辑壳继续打开配置。
- ROI start/end 改为两个可键盘操作的 `input[type=range]`，带 `aria-label`、`aria-valuenow` 和可见范围摘要；ECharts 只保留 ROI `markArea`，移除 ROI 起止两条 `markLine` 与其图内文字。数据游标继续使用一条 `markLine` 和一个可拖动 HTML 游标。
- 摘要网格改为紧凑的 3 列/58px 值区（窄屏为 2 列），动态值单行省略但完整文本保留在 `title`/`aria-label`；卡片使用 `ResizeObserver` 按实际 `ha-card` 约束高度切换 `compact-height` class，在短固定高度下将谱图区收缩到约 240px，自动高度不依赖 CSS size containment，减少不必要的内部滚动，同时保留 alpha.14 的 host/`ha-card` 高度链和溢出隔离。峰值标记与数据游标接近时采用不同垂直层级，避免文字重叠。
- `hass` 收到 `state === "unavailable"` 且 HA 已省略谱图实体属性时，仍保留可用状态的严格 KC761 身份/2048 点校验；卡片显示 unavailable 状态并保留最后有效数据、entry 和设备关联，后续合法状态恢复时重新填充，避免编辑预览把暂时不可用误报为错误实体。
- alpha.16 候选包 SHA-256 为 `b33484921209d76f0f5d15a85b862172eee7f87791f9a46cad22c469ad726017`；本地门禁为 pytest `48 passed, 3 skipped`、Ruff、绑定 Node `--check`/frontend contract、Python `compileall`、JSON 和 `git diff --check` 通过。测试 HA 的 release/live 哈希一致，入口仅含 alpha.16，卡片与 ECharts 静态资源均返回 HTTP 200。
- alpha.16 浏览器复核确认普通视图和 dashboard edit view 都加载新 URL：普通 DOM 中 Configure 可见、编辑工具栏隐藏、两个 ROI 控件均为 `range`，图内没有 ROI 起止游标；不可用实体文案为 `Selected spectrum entity is unavailable`，无卡片配置错误。普通卡片 rect top `113.58`、bottom `946.07`、height `832.49`，viewport `983px` 下 document `scrollHeight=clientHeight=983`，chart `430px`、summary `121.98px`。Configure 的语义/坐标点击自动化均超时/重置且未打开对话框，卡片编辑对话框视觉验收仍保留人工门禁。
- 测试 HA 只读 API 中 simulator 为 loaded、30/30 active、0 unavailable、2048 点；real 条目仍为 setup_retry、42 个实体 unavailable、主谱图 0 点。当前 dashboard 目标为 real，因此峰值/数据游标实时叠加和 ROI live data 只完成自动化契约验证，未宣称浏览器实时数据视觉通过。官方编辑事件来源为 [Home Assistant `hui-view`](https://github.com/home-assistant/frontend/blob/dev/src/panels/lovelace/views/hui-view.ts) 与 [card edit mode](https://github.com/home-assistant/frontend/blob/dev/src/panels/lovelace/components/hui-card-edit-mode.ts)，卡片不导入私有 `showEditCardDialog`。

### 8.12 `0.2.0-alpha.17` Configure 一键原生编辑交接

- 普通视图点击 Configure 时，卡片把当前 dashboard URL（去除 `edit=1`）和完整规范化配置生成稳定指纹，并将带短 TTL 的一次性 marker 写入 session storage（不可用时安全降级为现有 storage/fallback）；随后只进入 `?edit=1`，不猜测卡片数组索引，也不导入 HA 私有 dialog API。
- 进入 dashboard edit view 后，重建卡片只在 `preview=true` 且祖先最终暴露真实 Lovelace `path` 时登记为候选；短暂收集窗口内只有一个相同 URL/指纹候选时才消费 marker 并冒泡官方 `ll-edit-card(detail.path)`。重复配置、URL/指纹不匹配、过期 marker、缺失 path 或事件构造器时均不自动打开，避免错误卡片或重复 dialog；候选定时器、marker 重试和卸载路径均有清理。
- 带真实 path 的 dashboard edit 卡片仍可直接点击 Configure；没有 path 的实际编辑对话框 preview 继续只显示配置辅助控件而不消费 handoff。alpha.17 仅改变前端缓存版本与编辑交接，不改变 TCP 协议或真实设备请求。
- 本地静态契约覆盖普通点击写入 marker、普通视图存在路径祖先仍导航、edit view 唯一路径事件、直接 path 事件、错配/过期与重复候选安全放弃、marker 消费后不重复打开、ShadowRoot path 祖先和卸载清理；测试机部署及浏览器一键对话框视觉门禁仍需记录实际结果。
- alpha.17 候选包 SHA-256 为 `4176e8a23baf8afc7c282f3eb11648642a7bd36c2035ba21032db438e5038d97`；测试栈 release/live 的 manifest、卡片和 ECharts SHA-256 分别一致为 `1ef3b74e264f742454d42e14e534041bf5bdbf06c32a39368a8c9cf6141634fd`、`754830d3e8ed7a392c46ab6d4de9682501cf351e5de3f8c844a5fbca58e5f823`、`28515f26aa57f87eb1e4ed4eb446927eaf329b5f5e02b4bfd4b0264fea40d1da`。固定 HA 2026.7.4 镜像启动后 HA_READY=1，HA/模拟器 running、restart count 均为 0；替换前备份已保留。
- alpha.17 入口只含新查询参数，卡片和 ECharts 返回 HTTP 200 `text/javascript`；测试 HA 只读 API 快照显示 real `loaded`、43 注册/42 active/0 unavailable，simulator `loaded`、30 注册/30 active/0 unavailable。该快照没有调用任何 `kc761/*` WebSocket 命令，当前两条谱图状态的普通属性点数为 0，不能据此宣称 2048 点实时谱图已捕获。
- 部署后模拟器日志中的命令计数为 `0x62/0x63/0x64/0x66 = 0/0/0/0`、`FORBIDDEN_LOGS=0`；宿主和 HA namespace 没有真实设备目标 socket，也没有探针残留。最近日志只有 HA 自定义集成未测试提示及 `hardware_verified=false` 既有警告，没有新的 KC761 traceback/TransportError/pending-task/frontend 异常。Configure 一键进入原生编辑对话框仍由主任务浏览器验收，不把 HTTP/契约结果写成视觉通过。

### 8.13 `0.2.0-alpha.18` 普通视图 Configure 路由修复

- alpha.17 浏览器复核显示普通仪表盘中的 Configure 点击后仍停留在原 URL，且没有原生编辑对话框；结合 HA 官方 `hui-view` 的事件契约，根因候选是卡片在普通视图发现路径祖先后错误地直接派发 `ll-edit-card`，绕过了普通视图必须执行的 `?edit=1` handoff。该判断由源码/契约回归测试支持，仍需浏览器重测确认。
- alpha.18 将直接派发 `ll-edit-card` 限定为 `preview=true` 且存在真实 Lovelace path 的 dashboard edit wrapper；普通视图无条件写入一次性 marker 并调用 `location.assign(?edit=1)`，即使宿主误暴露路径也不会提前返回。新增回归断言覆盖“普通视图有路径仍导航”和“编辑预览有路径才派发”两态，同时保留 marker 指纹、TTL、重复候选和 ShadowRoot 路径保护。
- 本地 Node syntax/contract 及全量 Python 门禁通过：Node `--check`、frontend contract、pytest `48 passed, 3 skipped`、Ruff、compileall、JSON 和 `git diff --check` 均通过；3 个 skip 仅为本机未安装 Home Assistant 或 PATH Node，绑定 Node contract 已单独执行。coverage 在缺少 Home Assistant 的开发机环境为 `51%`，不替代固定 HA runner 的历史 `96%` 结果。
- alpha.18 候选归档 SHA-256 为 `2b2e99497075e6acb7ddd054b84205bf765503c539371c15a90373061b9a14bf`；测试机 release `/home/pi/Codes/kc761-ha-test/releases/v02-alpha18-configure-fix-2b2e9949` 与 live 的 manifest、卡片和 ECharts SHA-256 分别一致为 `1de4c0c2d1cee29e40a1b33a37c9fbaed4794216b48494e5905c73a8da3aa748`、`8d90362dce9cde2d5e782a92272a8d47fda86fe2a72f6363e980ca9c6caabf5a`、`28515f26aa57f87eb1e4ed4eb446927eaf329b5f5e02b4bfd4b0264fea40d1da`。备份为 `/home/pi/Codes/kc761-ha-test/backups/pre-v02-alpha18-configure-fix-20260822T202453Z`。
- 测试机 HA_READY=1，HA 与 simulator 均 running、restart count 为 0；alpha.18 卡片/ECharts 资源均 HTTP 200 `text/javascript`，入口仅含 alpha.18。只读 API 快照显示 real `loaded`、43 注册/42 active/0 unavailable，simulator `loaded`、30 注册/30 active/0 unavailable；该快照不调用 `kc761/*` 命令，谱图普通状态点数为 0，不能据此宣称实时 2048 点已捕获。
- 部署后 simulator 命令日志 `0x52/0x53/0x54/0x55/0x62/0x63/0x64/0x66 = 0/0/0/0/0/0/0/0`、`FORBIDDEN_LOGS=0`；宿主/HA namespace 无真实设备目标 socket、无探针残留，HA 最近日志无新的 KC761/frontend/pending-task 异常。alpha.18 普通视图 Configure 的真实浏览器一键对话框仍由主任务复测，不把 HTTP/契约结果写成视觉通过。

本地合并后继续测试的剩余门禁：

1. 当前扩展提交推送后重新运行官方 Hassfest、HACS、GitHub Actions 和 Ruff；历史成功记录不能代替当前 HEAD。
2. 在用户浏览器完成谱图卡 SVG/无 canvas、编辑器/普通视图控件边界、双 ROI 滑块与数据游标拖动和读数、Y 轴视觉间距、配色、`y_axis` reload 持久性、自动刷新、确认对话框、实际 CSV 下载、手动/定时保存、历史回看和保留上限的可视验收；Recorder 排除已经实测通过。
3. 对持久测试栈补做独立的模拟器运行时命令计数审计，确认本轮自动刷新只增加 `0x52` 且 `0x62/0x63/0x64/0x66` 均为 0；当前固定 runner 的控制覆盖不能代替该审计。
4. 使用专门测试设备，逐项、人工确认后验证 `0x62` 和 `0x63` ACK/读回；`0x66` 必须在用户明确接受数据丢失后单独验收。
5. 自动上传开启态、其它传感器与型号、累计量跨固件单位和 BLE UUID 继续保持未确认；`hardware_verified=false` 不变。

用户已经授权在本轮软件验收后进行本地合并，以便继续测试；这不等于发布授权。在上述剩余门禁完成前，不得推送合并结果、发布 tag/Release、部署正式环境或宣称控制功能已通过实机验证。
