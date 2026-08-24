# KC761x Home Assistant 集成

本仓库用于开发 KC761A、KC761B、KC761C、KC761CN 便携式核辐射分析仪的 Home Assistant 自定义集成，并以 HACS 兼容仓库形式发布。

当前开发版本为 `0.2.0-alpha.37`。在 `0.1.0-rc.1` 实机只读基线上，本项目提供 2048 道结构化谱图、ECharts SVG、ROI/Data/zoom 本地持久化、Ctrl+滚轮横向缩放、原生 Lovelace 配置/可见性/布局接口和固定 Sections 行高适配；自动上传谱模式由 A3 状态帧界定轮次，A1 分片在内存中有界重组后复用同一完整谱图快照接口；详细功能与验收边界见 [0.2 扩展设计](docs/V0_2_EXPANSION.md)。

alpha.31 延续 alpha.28 的暂态缺失配置保护，并将可选 `diagnostics: true` 测试诊断升级为 schema 4：普通生命周期事件仍保留有界环形记录，连续的 HA readiness 噪声会合并；错误事件按稳定脱敏签名去重，并另存于不会被普通事件挤出的有界 `failures` 环中。诊断会在当前浏览器 tab 的 `sessionStorage` 中脱敏保存，可记录窗口错误、未处理 rejection 以及递归发现的开放 Shadow DOM 中的 Lovelace `hui-error-card`。除卡片内 `Export diagnostics` 外，启用诊断后还会在页面 body 放置一个固定、可访问且不占布局空间的 `Export KC761 diagnostics` 按钮；点击时以 256 个 root、10000 个节点和约 100ms 为界限做一次深层快照，弥补持续 observer 的 root 上限，并只生成本地 JSON 下载。ResizeObserver 的布局/图表重绘统一延后到 requestAnimationFrame，避免同步 resize 触发循环；默认关闭时不写存储、不注册监听器、不显示按钮；不会上传实体、状态、点阵、设备 ID、主机或凭据。

alpha.35 将诊断升级为 schema 7，并在最多 9 秒的错误卡观察窗口中优先对 document 和每个已接入 open Shadow Root 定向查询 `hui-error-card`，再进行通用节点遍历；目标查询预算与通用节点预算分离，root 上限为 160，覆盖 alpha34 实测约 112 个 root 并保留有限余量。只对已连接且确认为 KC761 的外层错误卡派发一次标准 `ll-rebuild`，记录脱敏的目标查询命中、候选阻塞、预算类别、派发结果和清理原因；不执行页面刷新、不放宽校验、不处理其它卡片。诊断关闭时不写入诊断存储；导出文件仍只保存在本地，建议在错误卡可见时立即点击 `Export KC761 diagnostics`。

## 0.2 当前范围

谱图卡可从 Lovelace 的“添加卡片”选择器进入原生配置页；实体选择器只建议带 KC761 身份及结构化谱图属性的 `sensor` 实体。配置页支持实体、标题、X/Y 轴、自动刷新和五个内容开关（controls、summary、legend、actions、peak），默认均为可见，未知 YAML 字段（包括 HA 的整卡 `visibility`）由运行时配置保留。`getCardSize()` 使用确定性的 9 行估算，不读取已渲染 `ha-card` 高度，避免 Masonry 行数与卡片自身高度互相反馈；`getGridOptions()` 仅声明 Sections 使用满宽并至少 6 列，不声明固定行数或任意像素高度。整卡的“可见性”条件页和“布局”页由 Home Assistant 标准 `hui-card-element-editor` 提供；卡片内部的 `show_*` 只控制谱图内容，不替代整卡条件。

alpha.19 的“Configure”按钮在普通视图不调用 HA 私有 API：先写入按仪表盘 URL、完整配置指纹和短 TTL 约束的一次性 handoff，再安全进入当前仪表盘的 `?edit=1`；普通视图即使存在路径祖先也不会误派发 `ll-edit-card`，只有 `preview=true` 且找到唯一真实 Lovelace path 时才由 HA 编辑壳打开当前卡片的原生编辑对话框。重复配置、错配或过期 marker 不会猜测卡片索引，也不会自动打开；带真实路径的 dashboard edit wrapper 仍可直接冒泡事件。普通视图和带真实路径的 dashboard edit wrapper 都隐藏前四项选择器并保留 Configure；实际卡片编辑对话框 preview（`preview=true` 且没有路径）才显示 Channel、X/Y axis、Saved spectrum 和 ROI 辅助控件。ROI start/end 使用键盘和辅助技术可识别的双 range input，`markArea` 保留高亮而不再绘制两条 ROI 竖线；Data cursor 改为控制区内单 range input，图内只保留不可拖动的数据位置指示线，当前通道/能量/计数同时更新摘要和辅助读数。摘要使用固定紧凑值区，完整动态值保留在 `title`/`aria-label` 中。实体状态暂时 unavailable 时仍保留严格谱图实体校验，不清除最后有效数据，也不把缺少状态属性的 unavailable 状态误报为普通错误实体。

alpha.12 浏览器复核已确认谱图卡真实渲染并存在 `kc761-spectrum-card` DOM；六个 summary cell 均为 `80px`，六个 value 区均为 `39px`，动态值的 `title`/`aria-label` 与完整文本一致。Data cursor 从 `CH 149 · 96.71 keV · 2147` 更新到 `CH 154 · 103.68 keV · 2171` 后，卡片高度始终为 `1217.484375px`（`delta=0`），浏览器 error/warning 日志为空。该证据只覆盖 alpha.12 summary/注册修复，不替代其它谱图交互和真实设备门禁。

alpha.13 已部署到测试 HA；当前 `HUI-SECTIONS-VIEW` 页面中的卡片已真实渲染，DOM 存在 `kc761-spectrum-card`，显示 2048 点谱图控件，未出现 `Custom element not found`。浏览器自动化可以进入编辑模式并识别卡片菜单，但打开卡片编辑器面板多次超时，因此 Config、HA 原生整卡 Visibility 和 Sections Layout 三页仍是人工检查项，不将运行时渲染或静态 schema 检查写成三页已通过。

alpha.14 修复 Sections 的“Automatic height”关闭后固定行高场景：卡片 host 和内部 `ha-card` 沿 HA grid wrapper 使用 `height: 100%`，`min-height: 0` 允许固定行高正确收缩，内容超出时在卡片内部滚动，不再溢出覆盖后续卡片。Automatic height 开启时父级高度仍为 auto，卡片保持自然内容高度；`grid_options.rows` 等用户配置字段继续原样保留。

alpha.14 已在测试 HA 的同一 `HUI-SECTIONS-VIEW` 固定行高编辑场景完成浏览器复核：`--row-size:8` 时卡片 rect 为 `503.997375px`，`HUI-CARD-EDIT-MODE` wrapper rect 同为 `503.997375px`（delta `0`）；内部 `ha-card` 同高，卡片 `overflow:hidden`、内部 `overflow:auto`，`clientHeight=502`、`scrollHeight=940`。修复前卡片高度为 `941.302px`，相对 `503.997px` wrapper 溢出约 `437px`。该复核覆盖 Automatic height 关闭的固定行高路径；Automatic height 开启的自然高度路径尚未完成浏览器验收。KC761 过滤后的浏览器错误/警告为空，但部署/重载时间点有一条无法归因于集成的通用 `Object` 错误，因此不宣称全局浏览器日志绝对无错误。

- 集成域名规划为 `kc761`。
- 首版传输仅支持局域网 TCP；设备作为 TCP 服务端，Home Assistant 主动连接。
- 配置通过 Home Assistant Config Flow 完成，以设备返回的 `DEVICE_ID` 作为唯一标识。
- 提供实时辐射数据、环境数据、完整设备信息、A6 校准只读字段和一个结构化谱图实体；每个已安装通道另外提供可重置的累计时间、累计剂量和累计剂量当量普通实体。
- 默认每 5 秒读取一次实时状态，每 60 秒读取一次设备信息。
- Options 可调整轮询、是否创建 raw 实体、定时校时使能/间隔、定时保存谱图使能/周期。
- 读取命令扩展为 `0x52/0x53/0x54/0x55`；模拟器控制覆盖 `0x62/0x63/0x66`，校准写入 `0x64` 继续禁止。
- 谱图的 2048 点数组仅保留在当前 HA state/WebSocket 和独立有界存档中，通过 `_unrecorded_attributes` 排除 Recorder；Recorder 只保留总计数和有界元数据。
- 提供 select、switch、button 控制实体；破坏性清空在谱图卡中显示设备型号、完整 ID、通道和清空类别后要求确认。
- BLE、串口、科创云、校准写入和区间谱不进入本分支范围。

## 环境分工

| 环境 | 用途 | 本阶段动作 |
| --- | --- | --- |
| 开发机 | Git、协议实现、单元测试、文档 | 已建立文档基线 |
| 测试 Home Assistant | 集成安装、设备联调、故障与升级验证 | alpha.6 仅验证模拟器 reconfigure 连接所有权；真实条目只观察；AVG=0 作为已知问题保留 |
| 正式 Home Assistant | 通过测试门禁后的固定版本部署 | 仅接受固定构建；不在本仓库流程中直接操作 |

alpha.6 已在隔离测试栈完成模拟器条目的标准 Config Flow same-endpoint reconfigure 验收：旧连接所有权由 Coordinator 串行切换，候选失败路径有回滚，模拟器条目恢复为 loaded，30 个实体均可用。验收脚本不会从 HA 脱敏的 REST endpoint data 推断地址，而使用测试栈固定的 simulator endpoint；真实条目仍仅观察。

任何运行时验证都必须先在测试环境完成；正式环境只接收已经固定版本、可回滚且通过验收的构建。本分支先在一次性、无网络、只读文件系统的固定镜像 runner 中通过自动化，再以完整备份和不可变候选包替换持久隔离测试栈；测试 HA 和模拟器已完成迁移、控制、Recorder、卸载重载和重启验收。正式环境未连接、修改或重启。

两台主机的具体内网地址由开发机现有 SSH 配置维护，不写入可能公开发布的 HACS 仓库文档。

## 文档索引

- [功能与运行时架构](docs/ARCHITECTURE.md)
- [协议证据与待验证项](docs/PROTOCOL_EVIDENCE.md)
- [Home Assistant 实体模型](docs/ENTITY_MODEL.md)
- [测试、发布与部署计划](docs/TEST_RELEASE_PLAN.md)
- [架构决策记录](docs/DECISIONS.md)
- [0.2 控制与谱图扩展设计](docs/V0_2_EXPANSION.md)

## 原始资料

- 本地 `docs/KC761x编程手册.pdf`，适用软件版本 V1.8，2025 年 5 月第一版。
- 本地 `docs/KCSDI_1.png` 至 `docs/KCSDI_5.png`，官方上位机网络配置、主界面、设备设置、显示设置和设备信息截图。
- 本地 `private/kcsdi_data/`，本轮官方上位机导出的计数率、剂量率和能谱 CSV；作为私有取证输入保留，不纳入公开提交。

原始 PDF、截图、通信捕获和上位机 CSV 保留在开发机，不纳入 Git 历史；Git 记录经过审阅的脱敏结论、SHA-256，以及供代码审阅的 Markdown 转录/封面派生图。转录不替代原件，稳定发布前仍需复核其再分发授权。当前实现的 `manual_v1_8_candidate` 明确 `hardware_verified=false`。真实帧与 CSV 已确认本设备若干字段，但对手册没有明确说明、跨固件尚未复验或仍存在单位歧义的内容，均继续标为 `hardware-unverified`，不得直接固化为写操作。

## 规划中的仓库结构

实现目录如下，协议层仍保持与 Home Assistant 无关：

```text
custom_components/kc761/
  protocol/          # 与 Home Assistant 无关的帧、编解码和 TCP 客户端
  config_flow.py     # 首次配置与重新配置
  coordinator.py     # 连接生命周期、轮询、推送帧和可用性
  sensor.py          # 标量测量、设备信息和单一结构化谱图实体
  switch.py          # 声音、灯光、自动上传和谱图累积
  select.py          # 当前传感器、音量和背光亮度
  button.py          # 校时、谱图获取/保存和清空
  spectrum.py        # 谱图结构、CSV 和独立有界存档
  websocket_api.py   # 已认证的谱图卡本地 API
  frontend/          # KC761 ECharts SVG 谱图卡及本地第三方资源
  diagnostics.py     # 脱敏诊断
  manifest.json
  strings.json
  translations/
tests/               # 纯协议测试与 asyncio 模拟器
```

协议层保留为集成内部的纯 Python 子包，以便独立测试；首版不拆分额外 PyPI 包，避免 HACS 安装和双版本发布的耦合。

## 安全原则

- 真实设备证据只提升已逐帧对齐的字段；本设备的实时剂量当量率 binary16/缩放、完整谱图和能量刻度不能直接推广为跨型号、跨固件通用结论。
- 写操作使用字段白名单、范围校验、串行事务、ACK 回显校验和读回刷新；在模拟器及专门测试设备验收前不得部署正式环境。
- 不把主机地址、完整设备 ID、原始通信帧或凭据写入默认诊断输出。
- 不在正式环境进行探索性协议测试。

## 0.1 RC 历史验证边界与 0.2 继承规则

- 审查前提交的 GitHub Actions 已通过 Ruff、JSON、编译、32 项 pytest、98.81% branch coverage、Hassfest 和 HACS 9/9 checks；`0.2.0-alpha.3` 候选完成持久测试 HA 的迁移、Recorder、模拟器控制、卸载重载与重启生命周期门禁。随后发现 alpha.3 只提供卡片静态文件、没有注册前端模块；`0.2.0-alpha.4` 已修复自动加载，在固定镜像再次通过 54 项 pytest 和 96% branch coverage，并在实际测试页面确认 Custom Element 与唯一选择器条目均已注册。alpha.5 的固定 runner 和测试栈结果见下方 0.2 扩展门禁；当前扩展提交的官方 GitHub 检查仍待推送后重跑，软件检查与 2026-08-20 的实机逐帧证据分别记录，不能互相替代。
- 模拟器允许验证 TCP 分片、粘包、重连、自动上传交错和畸形帧恢复；模拟结果不能替代实机 L 级证据。
- 上述 0.1 RC 默认不创建完整谱图实体、不注册 service/action，也不会发送 `0x62`、`0x63`、`0x64`、`0x66`。0.2 分支新增接口的验证必须重新执行，不能引用这条历史只读断言作为通过证据。
- 本设备已确认 A2/A5/A6、主动 A0 谱图片段、CPS、实时剂量当量率的小端 binary16/单位换算、设备时间、2048 道谱图、工厂能量刻度和电池低 7 位百分比，本轮实机验收通过。完整 AVG 字段恒为 0 已登记为 `KI-AVG-001`，按已知问题接受，不作为本轮实机验收失败；跨固件表示、累计量单位、自动上传开启态和 BLE UUID 仍保持 `hardware-unverified`。字段级边界见 [协议证据账本](docs/PROTOCOL_EVIDENCE.md#103-2026-08-20-五分钟透明流与-kcsdi-导出对齐脱敏)。

## 外部设计依据

- [Home Assistant - Fetching data](https://developers.home-assistant.io/docs/integration_fetching_data/)
- [Home Assistant - Config flow](https://developers.home-assistant.io/docs/core/integration/config_flow/)
- [Home Assistant - Sensor entity](https://developers.home-assistant.io/docs/core/entity/sensor/)
- [HACS - Integration publishing requirements](https://hacs.xyz/docs/publish/integration/)
