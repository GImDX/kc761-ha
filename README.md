# KC761x Home Assistant 集成

本仓库用于开发 KC761A、KC761B、KC761C、KC761CN 便携式核辐射分析仪的 Home Assistant 自定义集成，并以 HACS 兼容仓库形式发布。

当前开发版本为 `0.2.0-alpha.3` 控制与谱图扩展：在已完成实机只读取证的 `0.1.0-rc.1` 基线上，增加 2048 道结构化谱图、A6 能量刻度、独立历史存档、CSV、谱图卡、设备信息实体、状态控制、设备校时和人工触发清空。扩展功能已在固定镜像、持久隔离测试栈和模拟器完成软件验收；集成仍未向真实 KC761 发送 `0x62/0x63/0x66`，旧 RC 的实机与长期稳定性证据也不能自动继承为当前扩展的写入安全结论。详细范围和门禁见 [0.2 扩展设计](docs/V0_2_EXPANSION.md)，历史证据见 [RC 验证记录](docs/RC_TEST_RESULTS.md)。

## 0.2 当前范围

- 集成域名规划为 `kc761`。
- 首版传输仅支持局域网 TCP；设备作为 TCP 服务端，Home Assistant 主动连接。
- 配置通过 Home Assistant Config Flow 完成，以设备返回的 `DEVICE_ID` 作为唯一标识。
- 提供实时辐射数据、环境数据、完整设备信息和一个结构化谱图实体；累计剂量候选值仍不创建测量实体或长期统计。
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
| 测试 Home Assistant | 集成安装、设备联调、故障与升级验证 | 固定镜像验证和本设备实机逐帧验收已通过；AVG=0 作为已知问题保留 |
| 正式 Home Assistant | 通过测试门禁后的固定版本部署 | 仅接受固定构建；不在本仓库流程中直接操作 |

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
  frontend/          # KC761 谱图卡
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

- 审查前提交的 GitHub Actions 已通过 Ruff、JSON、编译、32 项 pytest、98.81% branch coverage、Hassfest 和 HACS 9/9 checks；`0.2.0-alpha.3` 候选在固定镜像通过 54 项 pytest 和 96% branch coverage，并通过持久测试 HA 的迁移、Recorder、模拟器控制、卸载重载与重启生命周期门禁。当前扩展提交的官方 GitHub 检查仍待推送后重跑；软件检查与 2026-08-20 的实机逐帧证据分别记录，不能互相替代。
- 模拟器允许验证 TCP 分片、粘包、重连、自动上传交错和畸形帧恢复；模拟结果不能替代实机 L 级证据。
- 上述 0.1 RC 默认不创建完整谱图实体、不注册 service/action，也不会发送 `0x62`、`0x63`、`0x64`、`0x66`。0.2 分支新增接口的验证必须重新执行，不能引用这条历史只读断言作为通过证据。
- 本设备已确认 A2/A5/A6、主动 A0 谱图片段、CPS、实时剂量当量率的小端 binary16/单位换算、设备时间、2048 道谱图、工厂能量刻度和电池低 7 位百分比，本轮实机验收通过。完整 AVG 字段恒为 0 已登记为 `KI-AVG-001`，按已知问题接受，不作为本轮实机验收失败；跨固件表示、累计量单位、自动上传开启态和 BLE UUID 仍保持 `hardware-unverified`。字段级边界见 [协议证据账本](docs/PROTOCOL_EVIDENCE.md#103-2026-08-20-五分钟透明流与-kcsdi-导出对齐脱敏)。

## 外部设计依据

- [Home Assistant - Fetching data](https://developers.home-assistant.io/docs/integration_fetching_data/)
- [Home Assistant - Config flow](https://developers.home-assistant.io/docs/core/integration/config_flow/)
- [Home Assistant - Sensor entity](https://developers.home-assistant.io/docs/core/entity/sensor/)
- [HACS - Integration publishing requirements](https://hacs.xyz/docs/publish/integration/)
