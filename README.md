# KC761x Home Assistant 集成

KC761x 是一个面向 Home Assistant 的 HACS 自定义集成，支持通过局域网 TCP 或 Home Assistant Bluetooth 连接 KC761 系列便携式核辐射分析仪。

当前开发候选：`0.2.0-rc.9`。最低 Home Assistant 版本为 `2026.7.0`；本轮补全的验证和部署状态见[发布前补全记录](docs/PRE_RELEASE_COVERAGE.md)。

> 这是 RC，不是稳定版。协议档案仍固定公开 `hardware_verified=false`。真实设备写命令尚未完成系统性实机验收，默认关闭；生产环境不应使用本候选执行设备控制或清空操作。

## 功能

- TCP 和 Bluetooth LE 配置流；TCP 仅显示主机与端口，BLE 可从 HA 的可连接广播列表选择地址，也可手动输入。
- 设备信息、环境数据、实时/平均辐射读数、累计时间与累计剂量、41 项 A6 校准只读字段。
- 独立只读状态实体，无需开启设备控制即可查看通道、音量、灯光及自动上传等状态。
- 根据收到的谱图片段自动适配 1024、2048、4096 道快照，无需手动选择道数；内置 ECharts 卡片、ROI、数据游标、Ctrl+滚轮横向缩放、线性/对数轴和 CSV 导出。
- 剂量实体及 Recorder 固定保留原始 mGy/mSv 单位，不提供单位选项；既有历史不迁移或改写。
- 手动或定时保存谱图；历史谱图保存在配置条目的本地有界存档中，卡片按需读取。
- `DataUpdateCoordinator` 管理单条连接、轮询、自动上传谱图重组、重连、卸载和 HA 停止。
- 默认脱敏诊断：不输出主机、完整设备 ID、原始帧、谱图点阵或凭据。

## 安装

1. 在 HACS 中把 `https://github.com/GImDX/kc761-ha` 添加为“集成”类型的自定义仓库。
2. 下载 KC761x，重启 Home Assistant。
3. 在“设置 → 设备与服务 → 添加集成”中搜索 `KC761x`。
4. 选择 `TCP` 或 `BLE`，完成连接预检。设备返回的 `DEVICE_ID` 用作唯一标识；地址变更应使用“重新配置”。

谱图卡随集成自动注册，可从仪表盘的“添加卡片”选择器中选择 `KC761 Spectrum`，无需手写资源 URL 或 YAML。

## 默认安全状态

安装后默认只启用读取、谱图刷新/保存和本地 CSV 导出：

- `0x52`、`0x53`、`0x54`、`0x55` 为读取命令；
- `0x62`、`0x63`、`0x66` 的实现只通过模拟器和软件门禁，默认不可调用；
- `0x64` 校准写入未实现且始终禁止；
- “启用设备控制”开启后才创建普通控制实体；
- 清空谱图、剂量曲线或累计剂量还必须额外开启“启用清除辐射统计数据”。名称简化不改变清空操作不可恢复的风险和双重门禁。

设备控制会改变辐射计状态，也可能影响官方上位机或其它客户端。启用前请阅读[安全模型](docs/SAFETY.md)。

## 读数选项说明

“创建高频原始读数实体”会为每个已安装的辐射通道增加 3 个只读实体：原始 CPS、原始剂量率、原始剂量当量率。这些是设备报告的上一秒读数，不是原始协议帧，也不包括谱图点阵。实体随既有状态更新，不因为勾选此项就提高轮询频率。

剂量与剂量当量实体固定使用 mGy/mSv，速率固定使用 mGy/h、mSv/h，不提供集成或卡片单位选择项。HA 2026.7.4 不会对任意自定义单位自动进行 m/µ 前缀换算，故不在同一实体的历史序列中切换单位。协议原始值及物理量级保持不变，不因取消选项而迁移或删除历史。rc9 停止加载旧 `_micro` 伴随实体，其注册记录和历史仍保留，界面可能显示不可用；修正与实际部署状态见[发布前记录](docs/PRE_RELEASE_COVERAGE.md)。

## 已知限制

- 真实设备证据仅覆盖有限型号/固件和字段，不能外推为全部 KC761x；BLE 服务 UUID 的跨固件一致性仍未知。
- 一台已验证设备的三个设备端 AVG 字段持续为 `0`，而官方上位机自行计算 30 秒平均值（`KI-AVG-001`）。需要实时数据时可在选项中创建 raw 实体。
- 完整谱图点阵存在于当前谱图实体属性和已认证 WebSocket 响应中，但以 `_unrecorded_attributes` 排除 Recorder；本地历史存档最多保留 256 份。
- 累计值可由设备清零，因此作为普通传感器发布，不声明 `total_increasing`。2026-09-06 用户确认原始累计量为 `mGy/mSv`，项目据此保留数值和单位；手册/UI 矛盾仍作为历史证据保留，不据此提升跨固件认证。
- A0/A1 没有总道数或结束字段。自动适配得到的是本次观测/推断长度，不是设备分辨率设置或硬件最大道数确认；无法排除缺失尾片，卡片、存档与导出保留判定信息。内部缺洞或冲突的谱图不发布。这些规格的软件模拟测试不代表实机验证。
- 偶发 Lovelace“配置错误”仍记为开放问题 `KI-FE-001`，一次冷启动成功不等于已根治。
- A4 脉冲流、串口、科创云、区间谱和校准写入不在本版本范围内。

## 开发验证

```powershell
& '.\.venv\Scripts\python.exe' -m ruff check --no-cache custom_components tests
& '.\.venv\Scripts\python.exe' -m pytest -q -p no:cacheprovider --cov=custom_components/kc761 --cov-branch --cov-report=term-missing --cov-fail-under=95
node --check custom_components/kc761/frontend/kc761-spectrum-card.js
node tests/frontend_card_contract.mjs
```

CI 还运行 JSON/编译、Hassfest 和 HACS 验证。当前候选的本地结果与尚未通过的门禁见[发布前补全记录](docs/PRE_RELEASE_COVERAGE.md)；历史发布说明不代替当前候选 CI。

## 文档

- [文档索引](docs/README.md)
- [当前架构](docs/ARCHITECTURE.md)
- [实体模型](docs/ENTITY_MODEL.md)
- [安全模型](docs/SAFETY.md)
- [协议证据与待验证项](docs/PROTOCOL_EVIDENCE.md)
- [测试与发布计划](docs/TEST_RELEASE_PLAN.md)
- [发布前功能补全与验证](docs/PRE_RELEASE_COVERAGE.md)
- [0.2.0-rc.1 发布说明](docs/RELEASE_NOTES_0.2.0_RC1.md)

厂商原始 PDF、上位机截图、真实通信捕获、设备完整标识和测试凭据不属于公开发布物。仓库中的协议转录与脱敏结论不代表厂商背书。

## 许可

集成代码使用 [MIT License](LICENSE)。随谱图卡分发的 Apache ECharts 及其第三方许可文件位于 `custom_components/kc761/frontend/`；MIT 许可不覆盖厂商资料或第三方组件。
