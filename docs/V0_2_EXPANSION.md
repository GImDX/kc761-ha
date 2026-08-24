# KC761x 0.2 控制与谱图扩展设计

状态：`0.2.0-alpha.36`，在 alpha35 的谱图卡与生命周期基础上补齐 A5 三通道累计字段普通实体、A6 七个校准只读实体和对应协议/HA 测试；谱图卡 ECharts SVG 游标/网格对齐修复、缓存版本提升、左下角轴标签间距、紧凑 summary、模块幂等注册、原生 Lovelace 编辑器候选、Sections 固定行高修复、ROI 双滑块/编辑预览边界、unavailable 状态保留、Configure 一键编辑交接、Data cursor 单滑块、确定性 Masonry sizing、Data slider 垂直尺寸、自然图高收紧、Ctrl+滚轮 X 轴横向缩放、浏览器本地 ROI/Data/zoom 状态持久化、HA 刷新状态初始化防护、暂态缺失卡片配置防护、alpha32 schema 5 脱敏诊断、alpha33/34/35 延迟注册后的错误卡重建保护已完成本地静态核对；alpha.6 的 reconfigure 单连接所有权修复保留，真实条目仍只观察

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

设备级实体包括电池、气压、设备温度、设备时间、时钟偏差、协处理器固件版本、设备唯一 ID、A6 七个校准只读字段，以及 Gamma、Neutron、PIN 三个固定通道的传感器型号。即使某通道未安装，也保留其型号实体并显示“未安装”，使设备信息完整且稳定。

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

已安装通道继续创建 AVG 测量实体和四个 A5 累计普通实体；累计实体的时间单位为秒，剂量/剂量当量单位为 `mGy`/`mSv`，不设置 state class，因为 `0x66` 清空会重置它们。本设备 AVG 恒为 0 的行为按 `KI-AVG-001` 已知问题接受。raw 实体仍由 Options 显式选择是否创建。

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

若旧部署尚未更新并且必须临时恢复，可将 `/kc761/kc761-spectrum-card.js?v=0.2.0-alpha.22` 手动注册为 JavaScript Module。自动加载与手动资源同时存在时，卡片会通过 `registerKC761SpectrumCard()` 对 Custom Element 和选择器条目做幂等注册；确认新版本自动加载正常后可由用户自行移除旧的手动资源。

### 6.0 原生 Lovelace 编辑器与可见性边界

从 alpha.13 起，卡片提供 `getConfigForm()`：实体字段使用 Home Assistant 原生 entity selector，并按 `integration: kc761`、`domain: sensor` 过滤；标题、X/Y 轴、刷新间隔和内容开关使用内置 text/select/number/boolean 控件。刷新间隔只接受 `0` 或 `5`–`3600` 的整数；缺省字段按兼容默认值补齐，未知字段不会被 `setConfig()` 删除。`getEntitySuggestion()` 只对带 `config_entry_id`/`codec_profile` 身份且包含 0 或 2048 点结构化 `points` 的 KC761 谱图状态提出建议；卡片选择器首次打开时不再注入虚构的 `sensor.kc761_spectrum`。

五个内容开关的作用范围如下：`show_controls` 控制通道、坐标、历史和 ROI 输入；`show_summary` 控制摘要网格；`show_legend` 控制图例；`show_actions` 控制刷新、保存、CSV 和清空按钮；`show_peak` 同时控制峰值摘要、峰值图例和 ECharts `markPoint`。这些开关只改变 DOM/ECharts 呈现，不改变数据、历史或协议请求。

卡片不自行实现整卡条件编辑器。Home Assistant `hui-card-element-editor` 的标准编辑壳在支持的 Sections/仪表盘上下文提供 `config`、`visibility`、`layout` 三页；用户在“可见性”页设置的整卡条件与卡片内部 `show_*` 内容开关是两层不同语义。`getCardSize()` 固定返回确定性的 9 行估算，不读取渲染后高度，避免 HA Masonry 行数与卡片高度互相反馈；`getGridOptions()` 仅声明 Sections 使用满宽和至少 6 列，不固定行数，也不暴露任意像素高度。

Y 轴有两个持久化层级：YAML/编辑器显式改变 `y_axis` 时更新该实体的 configured baseline 并覆盖旧偏好；用户在卡片运行时切换线性/对数时只更新 selected preference。页面刷新且 configured baseline 未改变时恢复 selected preference；每个实体使用独立 localStorage 键，存储异常安全降级为配置值。切换 `entity` 会清理旧设备、entry、历史、数据、ECharts 实例和自动刷新计时器，迟到的旧 WebSocket 结果不会回写新实体。

alpha.25 另使用独立的 schema-versioned JSON localStorage 键持久化谱图视图状态。键至少包含实体、谱图 `channel`、`source` 和视图模式；实时模式固定为 `live`，不把每次自动/手动捕获生成的 snapshot id 放进键，历史模式使用稳定的 `snapshot_id`。每个键保存 ROI 起止通道、Data cursor 通道以及 zoom 的起止通道边界，因此同一实时谱图刷新、重绘、轴模式切换和页面重载不会重置视图；切换实体、通道或历史记录后，各自状态隔离，切回时恢复并按当前点数组钳制。交互写入采用合并节流，断开卡片前 flush；localStorage 不可用、JSON 损坏、schema 过期或数值越界时安全忽略/钳制，不触发网络请求。状态只属于当前浏览器站点和卡片配置档案，不会由 Home Assistant 同步；清除站点数据会回到默认全范围/游标起点。

卡片提供：

- Gamma、Neutron、PIN 已安装通道切换；
- X 轴道址/能量 keV 切换；
- Y 轴线性/对数切换；
- Y 轴选择按谱图实体写入浏览器 `localStorage`，刷新或重新打开页面后恢复；
- 使用本地 vendored ECharts 6.1.0 的 SVG renderer，Y 轴留出独立刻度/label 边距，不使用 Canvas 或外网 CDN；
- 两个可拖动的 ROI 游标、非黄色的半透明 ROI 区域，以及独立颜色的自动峰值标记；
- 一个控制区单 range 数据游标滑块，实时显示道址、能量和计数；
- 累积时间、获取时间、总计数和峰值显示；
- 用户选择的 ROI 范围及范围内计数；
- 手动刷新、保存和 CSV 下载；
- YAML 可配置 `refresh_interval`（默认 `0` 关闭；启用时为 `5`–`3600` 秒）。自动刷新只在 `Latest`、已连接且没有保存/清空等操作进行时读取最新谱图，卡片卸载时清理定时器；

ECharts 6.1.0 的 Apache-2.0 `LICENSE`、`NOTICE` 和 D3 派生许可证随卡片分发；npm registry integrity 和 vendored 文件 SHA-256 记录在 [echarts-THIRD-PARTY.md](../custom_components/kc761/frontend/echarts-THIRD-PARTY.md) 中。
- 已保存谱图列表和历史谱图回看；
- 三类清空操作及带设备身份的简单确认。

### 6.1 alpha.8 游标与原生 History 外观对齐

- 游标的屏幕位置不再根据固定 `GRID.left/right` 横向换算；卡片使用 ECharts `convertToPixel({xAxisIndex: 0}, value)` 取得实际 plot 坐标，并使用 `convertFromPixel` 将拖动位置反算回当前 X 轴值。该路径同时覆盖 `containLabel`、Energy/Channel 和 resize 后的实际坐标。
- ROI `markArea` 和唯一的数据游标 `markLine` 共用同一最近通道点的 X 值，因此高亮区域、数据线与谱图保持像素级对齐；ROI 起止点由双 range input 选择，Data cursor 由控制区单 range input 选择，不再在图内叠加 HTML 竖线或文字标签。
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
- `show_controls/show_summary/show_legend/show_actions/show_peak` 只改变内容 DOM 或 `markPoint` 呈现；峰值隐藏不会修改数据或发起协议请求。整卡 visibility/layout 继续交给 HA 标准 `hui-card-element-editor`，卡片新增 `getGridOptions()` 只声明 Sections 的满宽/最少 6 列约束，并由 `getCardSize()` 固定返回 9 行，避免已渲染高度反馈造成异常 24 行分配。
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

### 8.14 `0.2.0-alpha.19` Data cursor 单滑块

- 图内原先可拖动的 `#data-cursor` HTML 句柄已移除，避免在固定/紧凑布局中产生额外覆盖层；ECharts `markLine` 继续显示不可拖动的数据位置指示线，峰值与数据指示线的垂直错开逻辑保留。
- 控制区新增单个 `input[type=range]#data-cursor-slider`，范围固定为 `0..2047`，使用 Data cursor 独立主题色；`aria-label`、`aria-valuemin/max/now/text` 和旁侧完整读数随滑块输入即时更新，不触发网络请求。摘要中的 Data cursor 同步显示通道、能量和计数。
- 新谱图、历史谱图、实体切换和重置都会把当前游标吸附到最近有效通道；当数据点少于 2048 个时保留用户可识别的 2047 上限，但不会把游标停在不存在的点上。旧图内 pointer/keyboard 拖动监听和像素换算路径已删除。
- alpha.19 测试机部署和浏览器滑块可视/键盘门禁待本轮完成后补录；本地契约覆盖 slider listener、无 pointerdown、短谱图 clamp、ARIA 状态和无网络 getter。

### 8.15 `0.2.0-alpha.20` Masonry sizing feedback 修复

- alpha.19 新增 Data cursor 单滑块后，普通视图实测出现卡片约 `1171.68px` 高、文档 `scrollHeight=1309` 的异常增长；根因是 `getCardSize()` 读取已渲染 `ha-card` 高度，再把结果换算为 Masonry 行数，形成自引用反馈并放大到约 24 行。
- alpha.20 将 `getCardSize()` 改为无 DOM/无网络、确定性返回 `9`，不再从 `getBoundingClientRect()` 反推行数；`getGridOptions()` 继续只声明 `columns: "full"` 与 `min_columns: 6`。固定行高仍由 host/`ha-card` 高度链和 `ResizeObserver` 的 `compact-height` 处理，Data cursor 单滑块保留不变。
- 新增前端契约回归：即使伪造已渲染高度为 `2400px`，`getCardSize()` 仍返回 `9`，防止再次出现 24-row feedback；浏览器 alpha.20 的自然高度与固定行高视觉门禁待部署后复核。

### 8.16 `0.2.0-alpha.21` Data slider 垂直 flex 修复

- alpha.20 浏览器复核发现 Data slider 实际高度约 `311.99px`，CSS computed height 约 `300px`，使 `#controls` 增至约 `417.79px`，再次把卡片推高到约 `1171.68px`；根因是 `.roi-slider,.data-slider { flex:1 1 300px }` 共享规则被放在纵向 `.data-slider-label` 内，300px basis 被解释为垂直高度。
- alpha.21 将 ROI 的横向 `flex:1 1 300px` 与 Data slider 分离：`.data-slider` 使用 `width:100%; min-width:0; flex:none`，其高度由滑块、读数和自然内容决定；`.data-slider-row`/label 仍负责横向响应式宽度，slider 与 ROI 保持同宽。
- 静态契约新增共享 selector/flex-basis 回归，确保 Data slider 不再继承 ROI 的 300px 垂直 basis；确定性 `getCardSize()=9`、compact fixed-height 和 Data slider 行为保持不变。浏览器 alpha.21 高度复核待部署后完成。

### 8.17 `0.2.0-alpha.22` 自然谱图高度收紧

- alpha.21 浏览器复核确认 Data slider 已收缩为约 `51.19px`、slider row 约 `74.38px`、controls 约 `156.95px`，但普通卡片总高仍约 `911.92px`，视口底部外溢约 `42px`。alpha.22 只将自然高度路径 `.chart-wrap` 的 `height/flex-basis` 从 `430px` 调为 `370px`，固定行高的 `compact-height` 仍保持 `240px`/最小 `200px`。
- ECharts 仍通过既有 `ResizeObserver` 和 `chart.resize()` 跟随实际容器尺寸；Data slider、ROI、摘要、markLine 和数据更新逻辑不变。alpha.22 浏览器高度与 ECharts SVG 视觉门禁待部署后复核。

### 8.18 `0.2.0-alpha.23` ECharts X 轴横向缩放

- 谱图卡在同一 ECharts 实例中配置两个 `dataZoom`：一个可见 `slider` 位于图表内部底部保留区，另一个 `inside` 负责拖动/触控板交互；`inside.zoomOnMouseWheel` 固定为 `"ctrl"`，普通仪表盘滚轮不会被卡片抢占。两者均限定 `xAxisIndex: [0]`、`filterMode: "none"`，因此完整 2048 点数组仍用于 ROI 统计、自动峰值、数据游标、历史和 CSV。
- 缩放状态是卡片本地状态，不写入 HA 配置或网络：alpha.23/24 的内存态在同一实体/通道/历史数据集的自动刷新、重绘和 Y 轴切换时保留；alpha.25 进一步将 zoom 起止通道写入隔离的 localStorage 状态，页面重载和切换回同一数据集时也能恢复。实体、通道或历史快照没有对应记录时从 0–100% 开始；少于 2048 点时按实际点数反算读数，最小跨度约 5 个通道（短至两点时退化为全范围）。
- 标题栏提供 `View CH a–b` 的 `role=status`/`aria-live` 读数和可访问 `Reset zoom` 按钮；重置只派发既有 ECharts `dataZoom` action，不产生协议调用。图内数据游标仍是完整 0–2047 范围的控制区 slider，缩放只影响视窗，不改游标数据。
- `datazoom` listener 在每个 chart 实例创建时只绑定一次，在实体切换、断开和 dispose 前通过 `off` 移除；`ResizeObserver` 与 ECharts 实例沿用同一幂等销毁路径。图表底部从 28px 增加到 58px 的空间只属于 ECharts grid，卡片外层 `getCardSize()=9` 和固定行高 compact 链不变。
- 本地前端契约覆盖 inside/slider 选项、Ctrl 滚轮、x-only/filter none、状态保持/数据集重置、短谱图最小跨度、重置按钮、无网络 getter 和 listener 清理；测试机浏览器的 slider/Ctrl-wheel 手势仍需在部署后由主任务人工验收。

### 8.19 `0.2.0-alpha.24` 原生 History 修饰键一致性

- alpha.23 的 inside dataZoom 初版修饰键设置与原生 History 不一致；为与 Home Assistant 原生 History 图表的交互约定一致，alpha.24 将唯一缩放修饰键改为 Ctrl。普通滚轮仍不触发缩放，visible slider、拖动、触控板和完整数据集语义不变。
- 该修订同步 `CARD_VERSION`、集成 `VERSION`、manifest、卡片资源缓存 URL、运行时静态测试、前端契约和公开文档；无需变更协议层，也不会增加任何设备请求。

### 8.20 `0.2.0-alpha.25` 谱图游标与横向缩放本地持久化

- alpha.25 将 ROI 起止通道、Data cursor 通道和横向 zoom 起止通道纳入浏览器 `localStorage` 的 schema-versioned JSON；键隔离实体、谱图通道、`source` 和视图模式。实时谱图使用稳定的 `live` 模式键，不受设备每次捕获生成的新 snapshot id 影响；已保存谱图按稳定 `snapshot_id` 建立独立键。
- 新卡片实例首次渲染或页面重载时恢复对应键；同一实时数据自动/手动刷新、重绘和 X/Y 轴切换保留状态；实体、source、channel 或 history 切换先 flush 当前键，再读取目标键，切回时恢复各自 ROI、Data cursor 和 zoom。当前点数变化时通道坐标和 zoom 会钳制到可用范围，短谱图不会产生 NaN 或越界。
- ROI/Data slider 和 ECharts `datazoom` 交互写入使用一个合并节流 timer；`disconnectedCallback`、实体/数据集切换前会 flush 并清理 timer，chart listener 数量不变。`Reset zoom` 立即保存全范围。存储被浏览器策略阻止、JSON 损坏、schema 不匹配或数值非法时安全回退，不发起任何 HA WebSocket/设备请求。
- 该状态是浏览器站点本地偏好，不写 HA 配置、不进入 Recorder、不会在设备或多个浏览器之间同步；清除站点数据、隐私模式禁用存储或更换浏览器会回到默认 ROI `0–2047`、Data cursor `0` 和 zoom `0–100%`。alpha.25 的本地契约覆盖 round-trip、页面重载新实例、同 live 刷新保持、实体/source/history 隔离、切回恢复、reset 持久化、损坏/异常存储、写入合并和断开 flush；固定测试栈/浏览器复核作为后续门禁记录。

### 8.21 `0.2.0-alpha.26` 刷新初始化、ROI 文案与图内边框

- alpha.26 为 `set hass()` 增加初始化顺序防护：HA 尚未提供对象、`states` 缺失或状态对象在刷新中暂时不可读时，卡片停止轮询并显示稳定的 `Waiting for Home Assistant state`，不让 `hass.states[...]` 的 TypeError 穿透 Lovelace 卡片配置包装；状态对象恢复后仍走原有严格的 KC761 谱图实体校验，真正缺少实体继续显示 `Entity not found: ...`。
- 契约覆盖 hass-before-config、config-before-hass、null/incomplete hass、空 states、localStorage getter 异常和多次初始化；localStorage 失败仍安全降级，不改变协议请求或网络 getter 行为。该修复针对可复现的初始化异常，不把暂时 unavailable 或普通实体误报为配置有效。
- ROI 摘要从裸的 `245 – 1549` 改为紧凑、可读并可访问的 `Start CH 245 · End CH 1549`，两个端点保留独立 `title`/`aria-label`，不会依赖额外换行改变卡片高度。
- 移除 `.chart-wrap` 的外层视觉 border；HA `ha-card` 的外边框、ECharts axisLine、splitLine、ROI 高亮、数据游标、peak 和内部 zoom slider 保持不变。浏览器资源/高度/刷新次数及视觉边界仍需测试机部署后的人工门禁确认。

### 8.22 `0.2.0-alpha.28` 暂态缺失卡片配置保护

- 浏览器控制在本轮不可用（`agent.browsers.list()` 返回空），因此没有把用户当前错误卡片的 shadow DOM/console stack 写成已观测事实。测试机只读核对的 Lovelace 卡片配置是合法对象：`sensor.kc761_2158_spectrum`、`refresh_interval: 60`、`x_axis: energy`、`y_axis: log`、`grid_options.rows: auto`；alpha.26 资源为 HTTP 200 `text/javascript`，HA 与模拟器保持运行，最近日志无新的 KC761/frontend/exception 行。
- 本地最小 DOM harness 在 alpha.26 精确复现了一个同步错误入口：`new Card().setConfig(undefined)` 和 `setConfig(null)` 都抛出 `Error("KC761 spectrum card configuration must be an object")`。这证明 Lovelace 若在重建窗口传入暂态缺失配置，会把该异常包装为“配置错误”；但由于浏览器 stack 不可取得，本轮不宣称这已经单独证明了用户当前页面的唯一根因。
- alpha.28 只对 `null`/`undefined` 配置显示 `Waiting for card configuration` 并等待下一次有效 `setConfig`；字符串、数组、非法 entity/字段仍通过 `validateConfig` 抛出明确错误，不用宽泛 catch 隐藏真正的坏配置。新增测试覆盖真实保存配置的 12 次构造/连接/断开/重连、暂态缺失配置恢复和非法非对象配置仍报错。
- alpha.28 的暂态保护已进入新的测试候选；浏览器连续刷新、错误卡片 stack、真实页面注册/资源时序仍是人工门禁。不得把本地 harness 通过或远端 HTTP 200 当成浏览器根因已证实。

### 8.23 alpha.28 可选测试诊断（默认关闭）

- 在卡片编辑器的 `Diagnostics` / `Test diagnostics` 开关，或 YAML 中加入 `diagnostics: true` 后，卡片才会在浏览器当前 tab 的 `sessionStorage` 开启诊断。它保存 schema 版本化的最多 100 条、约 60 KB 上限的环形事件；页面刷新/模块重新评估可继续追加，关闭 tab 后由浏览器清除。默认关闭时不写诊断存储、不注册全局监听器，也不显示导出按钮。
- 记录仅限模块评估、Custom Element 幂等注册、构造/连接生命周期、配置入口/有效性、HA 状态就绪、绘制 generation、ECharts 动态导入和过滤到 KC761 源的窗口错误/未处理 rejection。事件只保留时间、序号、版本、安全布尔/计数及脱敏错误名/消息/栈；不写实体 ID、状态值、点阵、DEVICE_ID、host、token 或完整配置。存储不可用、JSON 损坏或配额不足时静默降级，不改变卡片配置校验与协议行为。
- 开启后标题栏出现 `Export diagnostics`。点击只在本地生成一个下载 JSON，包含 `schema`、卡片版本、`redacted: true` 和事件；不自动上传。导出前后仍应检查内容不含实体、host、点阵、设备 ID 或凭据。该诊断仅用于用户主动提供的测试证据，不是稳定版默认遥测。
- alpha.28 本地门禁为 `48 passed, 3 skipped`，Ruff、compileall、JSON、git diff check、Bundled Node syntax 和前端 contract 均通过；候选包 SHA-256 为 `9240406240d2bf78e26658a3943a9fc59d7b5a56cc50f113a89571af60352975`。测试栈部署后 manifest/card/ECharts release 与 live 哈希一致、HA 根页及两个静态资源均为 HTTP 200，两个容器保持 running/restart=0；本次部署后观察窗口模拟器命令计数为 `0x52/0x53/0x54/0x55/0x62/0x63/0x64/0x66 = 0`，未执行真实设备探针。浏览器开启诊断、连续刷新和下载 JSON 仍由用户手动完成。

### 8.24 `0.2.0-alpha.29` 诊断 schema 2：错误证据独立保留

- 用户提供的 alpha.28 导出文件有效（20,170 字节，SHA-256 `bfb345733429948feb6b8abc906e777fb4234164700d2efe53eadf30e64f654d`），但只包含最近 100 条正常事件：没有 failure、window error、unhandled rejection 或 Lovelace error-card 事件，不能确认“配置错误”的根因。该文件未发现 entity、host/IP、token/secret、device_id、entry_id 或 points 泄漏。
- alpha.29 将诊断存储升级为 schema 2，并使用新的 sessionStorage key。普通 `events` 仍最多 100 条；连续且 readiness signature 相同的 `hass-entry`/`hass-readiness` 合并为一条并记录 `repeat`、`last_timestamp`、`coalesced_events`。配置、窗口、rejection、初始化、绘制、动态导入、导出和 Lovelace `hui-error-card` 失败独立写入最多 20 条 `failures`，同时导出 `dropped_events`/`dropped_failures` 计数。
- 诊断启用后，全局错误/rejection 监听器与 MutationObserver 不因最后一个 KC761 卡片暂时断开而移除；它们会观察新增或已有的 `hui-error-card`/`ha-card.error`，只保存脱敏标题/文本和安全错误摘要。用户在错误卡出现后应只刷新到第一次成功的谱图卡，再点击 `Export diagnostics` 导出；不要在错误现场反复操作或执行写命令。明确关闭 `diagnostics` 后才停止监听、observer 与诊断写入。
- 诊断仍默认关闭、仅当前 tab sessionStorage、本地下载、无网络传输；存储异常/损坏/配额不足时安全降级，不改变卡片配置校验和协议行为。alpha.29 的本地回归覆盖 schema 2、normal/failure 分离、噪声合并、错误卡观察、断开后 sticky 监听、显式关闭清理、重复模块导入、脱敏和无存储环境。
- alpha.29 本地候选归档 SHA-256 为 `ed4c64e36ffebecda35d4d2dc60ea8ef1c7c3ce6e88ee3fd6ed2b4e318ce4dfb`；测试机 staging、release、live 的归档/组件哈希一致，manifest/card/ECharts 文件哈希分别为 `16be2eb7ec1e16a7a7c0952aec11a24a10cb25454fba83f252b37214a89f16c3`、`904aa52993b42984506c200d69779527750bf1819b2d6de001b5bc839b1cc72b`、`7208389ba18117a367f9393bd9cedece18a79ae052da30e050eaad9f4fad8c05`。部署 release 为 `/home/pi/Codes/kc761-ha-test/releases/v02-alpha29-diagnostics-20260823-1`，备份目录为 `/home/pi/Codes/kc761-ha-test/backups/pre-v02-alpha29-diagnostics-20260823-1-20260823T155029Z`；HA 与模拟器运行且 restart=0，两个静态资源 HTTP 200，部署窗口没有目标 `.127:7601` socket 或禁止命令。

### 8.25 `0.2.0-alpha.30` ResizeObserver 与 schema 3 诊断候选

- 用户提供的 alpha.29 schema 2 诊断文件有效（24,234 字节，SHA-256 `a7db5f795f38f5a8699f14940f9069ae3f73a98cef9b3781b30ba45948715a9a`）：保留 20 条 failure 中全部为 `window-error`，安全消息均为 `ResizeObserver loop completed with undelivered notifications.`；另有 16 条 failure 被容量淘汰，未出现 `setConfig`、draw、动态导入、rejection 或 Lovelace error-card 证据。因此它确认了浏览器存在 ResizeObserver 循环错误，但不能把该错误写成“配置错误”的已证根因。
- alpha.30 将布局 ResizeObserver 与 ECharts ResizeObserver 的同步回调改为每个实例最多一个 requestAnimationFrame 调度；回调检查 detached/disposed，断开和 dispose 会取消尚未执行的 frame，避免在 ResizeObserver 通知期间同步改变布局或调用 `chart.resize()`。现有 compact-height、Sections 固定行高和 ECharts SVG 行为保持不变。
- 诊断升级为 schema 3：failure 使用 event、错误名、脱敏消息和首个安全源码帧去重，重复记录增加 `repeat`、`first_timestamp`、`last_timestamp`，不会让同一 ResizeObserver 噪声挤出其它 failure；导出增加 `observer_stats`。diagnostics 开启时递归观察 document 及可访问的开放 Shadow DOM，限制最多 32 个 root、每次最多 2000 个扫描节点，并发现/去重嵌套 `hui-error-card`/`ha-card.error`；显式关闭仍会清理所有 observer/listener。默认关闭仍为零存储、零全局监听器、零额外扫描。
- alpha.30 的本地门禁为 `48 passed, 3 skipped`，Ruff、compileall、JSON、git diff check、Bundled Node syntax 和前端 contract 均通过。候选归档 SHA-256 为 `07563c98f184c1d530ec086cd021dbc698a8ac5c8cee84dc38ca417136da8d2e`；测试机 release 为 `/home/pi/Codes/kc761-ha-test/releases/v02-alpha30-resize-diagnostics-20260824-1`，部署前备份为 `/home/pi/Codes/kc761-ha-test/backups/pre-v02-alpha30-resize-diagnostics-20260824-1-20260823T162753Z`。release/live 的 manifest、卡片和 ECharts 哈希分别为 `ba32efbcde8d1141c2d44c79d31e56283b258de6244db993d2dfe452b21a2614`、`d85ae1d5b67292349eda4b533ff6f1243440e9760be4444c2fc7ce58c9375b9b`、`7208389ba18117a367f9393bd9cedece18a79ae052da30e050eaad9f4fad8c05`，两套路径一致；HA 根页及 alpha.30 卡片/ECharts 资源均 HTTP 200 `text/javascript`，HA 与模拟器 running/restart=0，未发现到真实 `.127:7601` 的 socket 或残留探针进程。
- 部署窗口模拟器命令计数 `0x52/0x53/0x54/0x55/0x62/0x63/0x64/0x66 = 0`，未执行真实设备探针。为下一次用户复现，已在测试机通过 HA WebSocket `logger/log_level` 以 `persistence=none` 临时启用 `custom_components.kc761`、`homeassistant.components.frontend` 和 `homeassistant.components.lovelace` 的 `DEBUG`，激活时间为 `2026-08-23T16:31:59.2368950Z`；启用前 `logger/log_info` 显示对应 integration effective level 均为 30（WARNING）。证据收集后回滚方式是对同三个 module 发送 `level=WARNING, persistence=none`，不得改动 YAML 或 `.storage`。
- alpha.30 的本地契约覆盖 failure 洪泛后唯一错误仍保留、稳定签名合并、正常事件 coalescing、嵌套开放 Shadow DOM error-card、observer 统计、模块重复导入、断开后 sticky 监听、显式关闭清理、RAF 调度/取消、脱敏与无存储环境。浏览器用户手动开启 diagnostics、复现刷新并导出 schema 3 仍是后续门禁；本候选不宣称已证明“配置错误”的唯一根因。

### 8.26 `0.2.0-alpha.31` 诊断 schema 4：全局深层快照按钮

- alpha.30 的 schema 3 诊断受到持续 Shadow DOM observer 最多 32 个 root 的有界限制。alpha.31 保留该低开销持续 observer，并将导出升级为 schema 4：页面启用 `diagnostics: true` 后创建一个 body 级固定 `Export KC761 diagnostics` 按钮。它不属于任何卡片 shadow root，卡片暂时断开或被 `hui-error-card` 替换时仍可用；明确关闭 diagnostics 才移除按钮、observer 和全局 listener。
- 点击全局按钮会在本地导出前执行一次有界深层扫描，优先遍历 `hui-*`、`ha-*` 和 `home-assistant*` 节点及可访问的 open Shadow DOM，最多 256 个 root、10000 个节点、约 100ms。扫描只保存脱敏的 `hui-error-card`/`ha-card.error` 标题、文本和安全错误摘要，并记录 `lovelace-error-card-snapshot` 与 `export_snapshot` 统计；相同快照去重，closed/cross-origin root 和 DOM 异常安全跳过。导出还包含有限的 `diagnostics_context`（Custom Element 是否定义、模块是否评估、卡片实例计数和 schema），不含实体、状态、配置、URL、主机、设备 ID、token 或点阵。
- 默认 diagnostics 关闭时无 body 按钮、无扫描、无 sessionStorage 写入和无全局监听器；sessionStorage 不可用时安全降级，不让导出诊断改变配置错误语义。该文件仍只在本地下载，用户需在错误卡可见时直接点击全局按钮并提供导出文件；alpha.31 不宣称已经证明“配置错误”的业务根因。
- alpha.31 固定 HA 2026.7.4 ARM64 runner 通过 `56 passed, 2 skipped`，branch coverage `96%`（总计 1967 statements、494 branches），达到 95% 门禁；两个 skip 是镜像内无 PATH Node，已由开发机绑定 Node 单独通过 `--check` 和 frontend contract。runner 报告的两个 warning 来自 HA 自身的 aiohttp 弃用提示及只读工作目录的 pytest cache，不是 KC761 异常。开发机当前旧 `.venv` 指向已不存在的 Python 3.13，未将本机 Ruff/pytest 缺失误报为通过；本轮 Ruff 未运行，须由后续可用工具补门禁。
- alpha.31 候选归档 SHA-256 为 `6e71ff507a6b3365c67895cd34126f824a6ba3f1b948fce286f90b55448fe12e`；测试机 release 为 `/home/pi/Codes/kc761-ha-test/releases/v02-alpha31-deep-diagnostics-20260824-2`，部署前备份为 `/home/pi/Codes/kc761-ha-test/backups/pre-v02-alpha31-deep-diagnostics-20260824-2-20260823T174307Z`。本机/远端包一致；release/live 的 manifest、卡片和 ECharts 哈希分别为 `c37cc5e9d78a35016eb5ba31962d3a3be79090f5e026ca1e2abfe96aa973e765`、`85bf3b02d2eafe25d976403c0294154c1bcfd20001835056d68d799abc58a244`、`7208389ba18117a367f9393bd9cedece18a79ae052da30e050eaad9f4fad8c05`，两套路径一致；HA 根页及 alpha.31 卡片/ECharts 资源均 HTTP 200 `text/javascript`，两个容器 running/restart=0。部署前后宿主与 HA namespace 均未发现真实 `.127:7601` socket；模拟器部署窗口命令计数 `0x52/0x53/0x54/0x55/0x62/0x63/0x64/0x66 = 0`，日志仅有预期集成未认证及 protocol profile WARNING，无 KC761 traceback/error/pending task。
- 用户下一步应在实际错误卡可见时直接点击 body 级 `Export KC761 diagnostics`；本轮不自动化刷新或浏览器点击，不把资源 HTTP 200、静态 contract 或 runner 通过写成真实 schema 4 导出已取得，也不宣称“配置错误”的业务根因已确认。

### 8.27 `0.2.0-alpha.32` 诊断 schema 5：错误卡归属关联

- alpha.31 的两份导出已经捕获到 `LovelaceErrorCard`，但普通 sessionStorage 事件仍混合了多次页面刷新，且全局深层扫描无法判断错误卡是否就是 KC761。测试机 `dashboard-kc761` 的目标 view 只包含一个 `grid`，其中只有一个 `custom:kc761-spectrum-card` 和一个原生 heading，因此错误卡与 KC761 的归属达到高置信，但具体底层异常仍未证明。
- alpha.32 为每条事件和 failure 增加短生命周期数值 `page_session`，同一页面内 cache-busted 模块重复加载共享该标识；页面刷新递增，不形成用户或设备指纹。readiness/failure 去重均限制在同一 page session，导出另含当前 page session，避免把历史注册事件误解为当前状态。
- 错误卡 failure 仅增加安全字段：`config_type_is_kc761` 为 `true`、`false` 或 `unknown`；不保存原始 config type。`node_path` 只含有限深度的元素标签和兄弟索引；同时记录当前 `custom_element_defined`、`module_evaluated`、`card_instances` 和 `resource_version_match`。原始 error name/message/stack 仍经过既有 host/entity/token/points 脱敏和长度限制。
- 诊断仍使用 WeakSet 与深度遍历的节点集合避免同一 DOM 节点重复记录；`hui-error-card` 与外层 `ha-card.error` 作为不同节点时仍可分别保留，便于识别嵌套结构。默认关闭时不创建 page marker、不写 sessionStorage、不增加监听器或扫描。
- alpha.32 已完成本地门禁和测试机部署；两份导出确认错误卡归属 KC761，但原始异常只有通用“配置错误”且 stack 不可用，因此在 alpha.33 前不把资源/模块加载时序升级为已确认业务根因，也不实施页面刷新或宽泛自动重试。

### 8.28 `0.2.0-alpha.33`：延迟注册后的错误卡重建保护

- alpha32 两份导出确认错误卡属于 `custom:kc761-spectrum-card`，失败页没有 `constructor`、`set-config` 或 `connected` 事件，且模块随后完成注册；这与 Home Assistant [`create-element-base.ts`](https://github.com/home-assistant/frontend/blob/dev/src/panels/lovelace/create-element/create-element-base.ts) 在自定义元素未定义时先创建隐藏 `hui-error-card`、再等待 `customElements.whenDefined(tag)` 发送 `ll-rebuild` 的路径一致。官方已有 issue [#19202](https://github.com/home-assistant/frontend/issues/19202) 记录错误卡元素尚未接入 DOM 时 `ll-rebuild` 无法向上冒泡的同类竞态；因此 alpha33 在自身注册完成后做一次有限、最多 4 帧的 Shadow Root 扫描，对已接入且归属 KC761 的错误卡发送一次 `ll-rebuild`。不刷新页面，不放宽 `validateConfig`，不启动重试循环。
- 扫描最多 64 个可访问 root、4000 个节点；同一 Window 内用 WeakSet 防止同一 DOM 节点重复派发；扫描只在模块注册时运行，诊断关闭时不写入 sessionStorage。若卡片仍未加载或底层错误仍无法恢复，仍保留 HA 原生错误卡，不掩盖实际配置问题。
- alpha33 新增前端契约：模块晚到且已有 KC761 错误卡时只派发一次 `ll-rebuild`，事件必须 `bubbles=true`、`composed=true`。Node syntax/contract、Ruff、`56 passed, 2 skipped`、分支覆盖率 `95.81%`、compileall、5 个 JSON 和 `git diff --check` 均通过。
- alpha33 已部署到持久 ARM64 测试栈：候选包 SHA-256 为 `b40773efb5048b2ec0289287d0e8a18bfa09462b9c0a6d6c04677d44c35eb0c8`，本地/远端一致；release/live 的 manifest、卡片和 ECharts SHA-256 分别为 `9b72a77f77187431d0e3864523c3a03032ebbebc621d6224f755d1e7bd914d55`、`02a5aca20e43124ca22256f44eedb65a0a7ca9e89a240702cd44c117926979f7`、`7208389ba18117a367f9393bd9cedece18a79ae052da30e050eaad9f4fad8c05`；两静态资源 HTTP 200，HA 与模拟器容器 running/restart=0，未观察到真实设备 socket、探针残留或新增模拟器协议命令（含禁止写命令均 0）。

### 8.29 `0.2.0-alpha.34`：延迟错误卡插入观察窗口

- 三份 alpha33 导出均确认新鲜错误卡在 alpha33 最后一次扫描后约 2.87–5.34 秒才出现；因此 alpha34 将注册后的 4 帧扫描改为最多 9 秒的有界观察窗口。窗口监听 document 和有限的 open Shadow Root，覆盖后续节点插入与新增 root，并设置 root、node、mutation 和派发预算；完成一次派发、超时、预算耗尽或页面卸载时统一断开 observer、清理 timer。
- 仅对已连接且 `config_type_is_kc761=true` 的外层 `hui-error-card` 派发一次 `ll-rebuild`，内部 `ha-card.error` 不重复处理。schema 6 仅记录脱敏的 `found_at`、`dispatch_outcome`、`dispatched`、`truncated`、`cleanup_reason` 和 `target_registry_defined` 等有限字段，不保存原始 type、entity、配置、状态、URL、主机或设备信息。
- alpha34 的自动化门禁覆盖迟到错误卡、非 KC761 卡、重复节点、断开节点、窗口超时与预算清理；浏览器仍需用户在错误卡出现时手动导出 schema 6 诊断并确认卡片是否恢复。
- alpha34 已部署到持久 ARM64 测试栈：候选包 SHA-256 为 `08bd8544228a2019554cb7b326aee3730abb9e67499cc99e3aa25f9d11abceb0`，本地/远端 staging 一致；release/live 的 manifest、卡片和 ECharts SHA-256 分别为 `4932fae6bb641cdaab0d9f2edd965d3423993e831087e1be08820298234a6242`、`782309cb5a5209b16df88485ea0542448462887bd5f530a6bb3e7e2bb4877659`、`7208389ba18117a367f9393bd9cedece18a79ae052da30e050eaad9f4fad8c05`。HA 根路径和两静态资源均 HTTP 200，HA 与模拟器容器 running/restart=0，未观察到真实设备 socket、探针残留或新增模拟器协议命令（含禁止写命令均 0）。

### 8.30 `0.2.0-alpha.35`：定向错误卡查询与有限 root 余量

- alpha34 诊断确认页面约有 112 个可访问 root；rebuild 在 96 个 root 处预算耗尽，未命中已存在的 KC761 `hui-error-card`。alpha35 将 schema 提升为 7，在每个已接入 document/open Shadow Root 上先执行 `hui-error-card` 定向查询，再进行通用节点遍历；定向查询与通用节点计数分离，root 上限提升为有证据的 160，目标查询上限为 256。
- 只对已连接且 `config_type_is_kc761=true` 的外层错误卡派发一次 `ll-rebuild`；非 KC761、断开或未知配置只记录脱敏的 `candidate_seen_but_blocked`，不派发。保留 Window `WeakSet`、9 秒硬超时、最大派发数、MutationObserver/pagehide 清理和目标 Window registry 检查。
- schema 7 新增的诊断字段仅为 `targeted_query_hit`、`candidate_seen_but_blocked`、`budget_reason`、`dispatch_outcome` 等布尔/枚举/计数，不保存 type、entity、host、URL、状态或设备信息。前端合同新增超过 96 roots 且目标在末端的回归；待测试机用户手动确认迟到错误卡是否恢复。
- alpha35 已部署到持久 ARM64 测试栈：候选包 SHA-256 为 `7e3db3ee6746a0424139b05f75da476db0933453b45758eba5d6c9fb11c8040f`，本地/远端 staging 一致；release/live 的 manifest、卡片和 ECharts SHA-256 分别为 `b70015d167b8f4ca6e959cb2dd7de7722144cad7b07a05baaf192ff9d4e1e2d7`、`bbb2dbf0daec1064e6ff4e20c3c225bbdd02d2e33f05ecaaaadcd9d710c2c8e2`、`7208389ba18117a367f9393bd9cedece18a79ae052da30e050eaad9f4fad8c05`；HA 根路径和两静态资源 HTTP 200，HA 与模拟器 running/restart=0，未观察到真实设备 socket、探针残留或新增模拟器协议命令（含禁止写命令均 0）。

本地合并后继续测试的剩余门禁：

1. 当前扩展提交推送后重新运行官方 Hassfest、HACS、GitHub Actions 和 Ruff；历史成功记录不能代替当前 HEAD。
2. 在用户浏览器完成谱图卡 SVG/无 canvas、编辑器/普通视图控件边界、双 ROI 滑块与数据游标拖动和读数、Y 轴视觉间距、配色、`y_axis` reload 持久性、自动刷新、确认对话框、实际 CSV 下载、手动/定时保存、历史回看和保留上限的可视验收；Recorder 排除已经实测通过。
3. 对持久测试栈补做独立的模拟器运行时命令计数审计，确认本轮自动刷新只增加 `0x52` 且 `0x62/0x63/0x64/0x66` 均为 0；当前固定 runner 的控制覆盖不能代替该审计。
4. 使用专门测试设备，逐项、人工确认后验证 `0x62` 和 `0x63` ACK/读回；`0x66` 必须在用户明确接受数据丢失后单独验收。
5. 自动上传开启态、其它传感器与型号、累计量跨固件单位和 BLE UUID 继续保持未确认；`hardware_verified=false` 不变。

用户已经授权在本轮软件验收后进行本地合并，以便继续测试；这不等于发布授权。在上述剩余门禁完成前，不得推送合并结果、发布 tag/Release、部署正式环境或宣称控制功能已通过实机验证。
