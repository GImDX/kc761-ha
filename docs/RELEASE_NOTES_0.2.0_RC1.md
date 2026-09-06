# KC761 `0.2.0-rc.1` 发布说明

状态：预发布候选；不是稳定版

## 主要变化

- 从 v0.1 的 TCP 只读基线扩展为 TCP/BLE 传输、主动/自动上传谱图、A5 累计字段和 A6 校准只读字段。
- 增加结构化谱图实体、本地有界历史存档、CSV 导出和自动注册的 ECharts Lovelace 卡片。
- 卡片支持 Gamma/Neutron/PIN、道址/能量轴、线性/对数轴、ROI、数据游标、自动峰值、Ctrl+滚轮横向缩放、视图状态持久化和原生可视化编辑器。
- 控制实体和定时校时纳入显式安全门禁；破坏性清空需要第二层独立授权。所有写功能默认关闭，`0x64` 仍禁止。
- ROI/Data cursor 的高频交互改为 ECharts 增量 overlay 更新，不再每帧重新提交 2048 点数据。
- CI 固定第三方 Action 的完整提交 SHA，使用 Python 3.14、Node 24，并运行 Ruff、前端契约、pytest branch coverage、Hassfest 和 HACS 验证。

## 升级说明

- 最低 Home Assistant：`2026.7.0`。
- 配置条目 schema 维持版本 6；缺少 `transport` 的旧条目继续按 TCP 解释。
- 现有实体 unique ID、谱图 gzip 格式、`.metadata.json` 索引版本和浏览器视图状态 schema 不变。
- 升级后设备控制默认关闭。需要控制实体时，在集成选项中显式开启“启用设备控制”；清空操作还需开启“启用破坏性清空操作”。
- 浏览器可能缓存旧卡片资源；版本化 URL 会生成新缓存键，必要时对 HA 前端执行一次硬刷新。

## 本地候选验证

在 Windows 开发机、Python 3.14.7、Home Assistant 测试依赖 2026.7.4 上完成：

- Ruff：通过；
- Node 语法检查与前端契约：通过；
- pytest：`100 passed`，另有 `3 subtests passed`；
- branch coverage：`95.26%`，门槛 `95%`；
- `pip check`：无依赖冲突；
- Git whitespace 检查：通过。

上述结果是本地软件证据。GitHub CI、Hassfest/HACS 远端检查、测试 HA 部署和真实设备回归在发布分支推送后才可记录为通过；未执行的门禁不能由历史 alpha 或 v0.1 结果替代。

## 已知问题和限制

- `hardware_verified=false` 保持不变。
- `KI-AVG-001`：一台已验证设备的设备端 AVG 字段恒为 0，官方上位机使用本地 30 秒平均。用户可显式创建 raw 实体获取实时值。
- BLE 服务 UUID、MTU、通知丢包恢复和跨固件兼容性仍需扩大样本。
- 累计量单位存在手册表格/正文/UI 矛盾。2026-09-06 用户明确确认原始单位为 `mGy/mSv`，D-012 据此保留现有标签和数值，取代早期“只改 µGy/µSv 标签”的方案；原始证据保留。可选微单位显示必须同时将数值乘 1000，并单独验证 Recorder/统计兼容性。A2 `mGy/h/mSv/h` 速率单位的既有 Recorder 证据不受此项影响。
- 结构化谱图点阵在 HA 当前状态属性中可见，但明确排除 Recorder；大量历史谱图仍会占用 HA 配置目录磁盘空间。
- 写命令只完成软件/模拟器验证，生产设备应保持两个控制门禁关闭。

## 发布门禁

只有以下条件全部完成后才创建本 RC 的 GitHub prerelease：

1. 发布分支工作树干净，所有提交使用 GitHub noreply 作者/提交者；
2. 本地全量测试、JSON/编译和隐私扫描通过；
3. GitHub CI 的 validate、Hassfest 和 HACS 均通过；
4. `main` 只通过可审计快进推进到同一提交；
5. tag `v0.2.0-rc.1` 与 GitHub prerelease 指向同一提交；
6. 不部署或修改生产 Home Assistant，不对真实设备发送写命令。
