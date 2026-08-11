# KC761x Home Assistant 集成

本仓库用于开发 KC761A、KC761B、KC761C、KC761CN 便携式核辐射分析仪的 Home Assistant 自定义集成，并以 HACS 兼容仓库形式发布。

当前分支实现 `0.1.0-rc.1` 只读候选：包含协议候选实现、异步 TCP 模拟器、Home Assistant 生命周期测试和 HACS 校验配置。固定 Home Assistant 2026.7.4 镜像的全包 branch coverage 与部署回滚证据、以及 GitHub runner 上通过的 Hassfest/HACS 官方验证，均已记录在 [RC 验证记录](docs/RC_TEST_RESULTS.md)；真实 KC761 协议字段仍未完成 L 级实机确认，不能据此宣称硬件兼容。

## 已确定的首版范围

- 集成域名规划为 `kc761`。
- 首版传输仅支持局域网 TCP；设备作为 TCP 服务端，Home Assistant 主动连接。
- 配置通过 Home Assistant Config Flow 完成，以设备返回的 `DEVICE_ID` 作为唯一标识。
- 首版只读：提供实时辐射数据、环境数据和设备信息；累计量保持诊断候选并默认关闭。
- 默认每 5 秒读取一次实时状态，每 60 秒读取一次设备信息。
- 能够解析设备已经启用的自动上传帧，但不会主动开启、关闭或依赖自动上传。
- 完整谱图不写入 Home Assistant 实体状态或 Recorder；后续采用按需获取方案。
- BLE、串口、科创云、校准写入、清除统计和完整谱图实体暂不进入首版。

## 环境分工

| 环境 | 用途 | 本阶段动作 |
| --- | --- | --- |
| 开发机 | Git、协议实现、单元测试、文档 | 已建立文档基线 |
| 测试 Home Assistant | 集成安装、设备联调、故障与升级验证 | 固定镜像验证已完成，后续实机证据另行记录 |
| 正式 Home Assistant | 通过测试门禁后的固定版本部署 | 仅接受固定构建；不在本仓库流程中直接操作 |

任何运行时验证都必须先在测试环境完成；正式环境只接收已经固定版本、可回滚且通过验收的构建。本分支未连接、修改或重启远端环境，模拟器只监听测试进程内的本地临时端口。

两台主机的具体内网地址由开发机现有 SSH 配置维护，不写入可能公开发布的 HACS 仓库文档。

## 文档索引

- [功能与运行时架构](docs/ARCHITECTURE.md)
- [协议证据与待验证项](docs/PROTOCOL_EVIDENCE.md)
- [Home Assistant 实体模型](docs/ENTITY_MODEL.md)
- [测试、发布与部署计划](docs/TEST_RELEASE_PLAN.md)
- [架构决策记录](docs/DECISIONS.md)

## 原始资料

- 本地 `docs/KC761x编程手册.pdf`，适用软件版本 V1.8，2025 年 5 月第一版。
- 本地 `docs/KCSDI_1.png` 至 `docs/KCSDI_5.png`，官方上位机网络配置、主界面、设备设置、显示设置和设备信息截图。

原始二进制资料保留在开发机，不纳入可能公开发布的 Git 历史；Git 只记录经过审阅的结论和 SHA-256。当前实现的 `manual_v1_8_candidate` 明确 `hardware_verified=false`。对手册没有明确说明、截图与手册不一致、或尚未从真实设备抓包确认的内容，均标为 `hardware-unverified`，不得直接固化为写操作。

## 规划中的仓库结构

实现目录如下，协议层仍保持与 Home Assistant 无关：

```text
custom_components/kc761/
  protocol/          # 与 Home Assistant 无关的帧、编解码和 TCP 客户端
  config_flow.py     # 首次配置与重新配置
  coordinator.py     # 连接生命周期、轮询、推送帧和可用性
  sensor.py          # 只读测量与累计量
  binary_sensor.py   # 只读状态
  diagnostics.py     # 脱敏诊断
  manifest.json
  strings.json
  translations/
tests/               # 纯协议测试与 asyncio 模拟器
```

协议层保留为集成内部的纯 Python 子包，以便独立测试；首版不拆分额外 PyPI 包，避免 HACS 安装和双版本发布的耦合。

## 安全原则

- 未取得真实设备应答证据前，不推断多字节端序、FP16 表示、累计剂量单位或谱图终止条件。
- 所有写操作都必须经过显式启用、字段白名单、范围校验、ACK 校验和读回验证；首版不提供写操作。
- 不把主机地址、完整设备 ID、原始通信帧或凭据写入默认诊断输出。
- 不在正式环境进行探索性协议测试。

## RC 验证边界

- GitHub Actions 已通过 Ruff、JSON、编译、32 项 pytest、98.81% branch coverage、Hassfest 和 HACS 9/9 checks；这是软件 RC 证据，不是实机协议证据。
- 模拟器允许验证 TCP 分片、粘包、重连、自动上传交错和畸形帧恢复；模拟结果不能替代实机 L 级证据。
- 默认不创建完整谱图实体、不注册 service/action，也不会发送 `0x62`、`0x63`、`0x64`、`0x66`。
- 端序、FP16、累计单位、TCP 端口、固件兼容性和谱图完成条件在获得真实抓包前均保持 `hardware-unverified`。

## 外部设计依据

- [Home Assistant - Fetching data](https://developers.home-assistant.io/docs/integration_fetching_data/)
- [Home Assistant - Config flow](https://developers.home-assistant.io/docs/core/integration/config_flow/)
- [Home Assistant - Sensor entity](https://developers.home-assistant.io/docs/core/entity/sensor/)
- [HACS - Integration publishing requirements](https://hacs.xyz/docs/publish/integration/)
