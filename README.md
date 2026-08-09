# KC761x Home Assistant 集成

本仓库用于开发 KC761A、KC761B、KC761C、KC761CN 便携式核辐射分析仪的 Home Assistant 自定义集成，并以 HACS 兼容仓库形式发布。

当前处于“架构与协议证据基线”阶段。仓库已经初始化为 Git 仓库，本阶段只包含设计文档、官方编程手册和官方上位机截图，不包含任何集成实现代码，也未连接或修改测试、生产环境。

## 已确定的首版范围

- 集成域名规划为 `kc761`。
- 首版传输仅支持局域网 TCP；设备作为 TCP 服务端，Home Assistant 主动连接。
- 配置通过 Home Assistant Config Flow 完成，以设备返回的 `DEVICE_ID` 作为唯一标识。
- 首版只读：提供实时辐射数据、环境数据、设备信息和累计统计。
- 默认每 5 秒读取一次实时状态，每 60 秒读取一次设备信息。
- 能够解析设备已经启用的自动上传帧，但不会主动开启、关闭或依赖自动上传。
- 完整谱图不写入 Home Assistant 实体状态或 Recorder；后续采用按需获取方案。
- BLE、串口、科创云、校准写入和清除统计暂不进入首版。

## 环境分工

| 环境 | 用途 | 本阶段动作 |
| --- | --- | --- |
| 开发机 | Git、协议实现、单元测试、文档 | 已建立文档基线 |
| 测试 Home Assistant | 集成安装、设备联调、故障与升级验证 | 本阶段不连接、不修改 |
| 正式 Home Assistant | 通过测试门禁后的固定版本部署 | 本阶段不连接、不修改 |

任何运行时验证都必须先在测试环境完成；正式环境只接收已经固定版本、可回滚且通过验收的构建。

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

原始二进制资料保留在开发机，不纳入可能公开发布的 Git 历史；Git 只记录经过审阅的结论和 SHA-256。对手册没有明确说明、截图与手册不一致、或尚未从真实设备抓包确认的内容，均在协议证据文档中明确标为“待验证”，不得直接固化为写操作。

## 规划中的仓库结构

以下目录是后续实现目标，本阶段尚未创建代码文件：

```text
custom_components/kc761/
  protocol/          # 与 Home Assistant 无关的帧、编解码和 TCP 客户端
  config_flow.py     # 首次配置与重新配置
  coordinator.py     # 连接生命周期、轮询、推送帧和可用性
  sensor.py          # 只读测量与累计量
  binary_sensor.py   # 只读状态；后续可选
  diagnostics.py     # 脱敏诊断
  manifest.json
  strings.json
  translations/
tests/
```

协议层保留为集成内部的纯 Python 子包，以便独立测试；首版不拆分额外 PyPI 包，避免 HACS 安装和双版本发布的耦合。

## 安全原则

- 未取得真实设备应答证据前，不推断多字节端序、FP16 表示、累计剂量单位或谱图终止条件。
- 所有写操作都必须经过显式启用、字段白名单、范围校验、ACK 校验和读回验证；首版不提供写操作。
- 不把主机地址、完整设备 ID、原始通信帧或凭据写入默认诊断输出。
- 不在正式环境进行探索性协议测试。

## 外部设计依据

- [Home Assistant - Fetching data](https://developers.home-assistant.io/docs/integration_fetching_data/)
- [Home Assistant - Config flow](https://developers.home-assistant.io/docs/core/integration/config_flow/)
- [Home Assistant - Sensor entity](https://developers.home-assistant.io/docs/core/entity/sensor/)
- [HACS - Integration publishing requirements](https://hacs.xyz/docs/publish/integration/)
