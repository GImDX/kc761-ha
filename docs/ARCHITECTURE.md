# KC761x Home Assistant 集成架构

状态：发布前补全候选规范，验证状态见 [PRE_RELEASE_COVERAGE.md](PRE_RELEASE_COVERAGE.md)。

## 1. 范围

本集成把 KC761x 的局域网 TCP 或 Bluetooth LE 接口映射为一个 Home Assistant 设备。当前范围包括：

- 设备信息、实时状态、累计字段和 A6 校准数据读取；
- 主动谱图和 A3/A1 自动上传谱图重组；
- 标量传感器、结构化谱图实体和本地谱图卡；
- 配置条目级有界谱图存档与 CSV 导出；
- 默认关闭的设备控制和单独门控的清空操作。

不实现串口、科创云、区间谱、任意原始命令或 `0x64` 校准写入。RC 不用于生产设备写入。

## 2. 分层

```text
KC761 TCP server / BLE GATT server
        │
protocol/transport.py       连接、收发、BLE 通知队列
        │
protocol/framing.py         增量拆帧、长度/缓冲上限、重同步
        │
protocol/client.py          SYNC、事务匹配、推送路由、超时
        │
protocol/codec.py/models.py 字段编解码和不可变值对象
        │
coordinator.py              单连接所有权、轮询、谱图、写锁、生命周期
        ├── sensor.py / binary_sensor.py / switch.py / select.py / button.py
        ├── websocket_api.py ── frontend/kc761-spectrum-card.js
        └── diagnostics.py
```

协议子包不依赖 Home Assistant。实体 getter 只读取 Coordinator 的内存快照，不发起网络 I/O。

## 3. 连接与生命周期

- 每个 Config Entry 只拥有一个 `KC761Coordinator` 和一条活动 TCP 或 BLE 连接。
- 配置流的临时预检连接在流程结束时关闭；正式连接由 Coordinator 重新创建。
- 周期轮询、主动谱图、校时、控制和清空共用同一异步锁，避免同一连接上事务交错失控。
- 长寿命任务通过 `ConfigEntry.async_create_background_task` 注册；卸载、重载、配置条目失败和 HA 停止最终进入同一幂等 `async_shutdown()`。
- 连接失败保留最近快照但把实体标为不可用；成功轮询后恢复。重连使用有界退避和抖动。
- 硬件/通道清单变化只调度一次配置条目重载，不在每轮轮询动态增删实体。

KC761 可能只能稳定服务一条客户端。集成不做无标识自动连接，不启动独立 TCP 探针或私有 BLE 扫描器；BLE 地址选择复用 HA Bluetooth manager 的广播缓存和短时扫描。

## 4. 帧与事务

TCP 和 BLE 通知都被当作字节流，解析器支持分片、粘包、未知 FLAG、异常长度、半帧断线和有界重同步：

- 最大帧：16384 字节；
- 最大缓冲：32768 字节；
- 普通响应按 `SYNC + 预期 FLAG` 路由；
- 写 ACK 额外校验回显命令；
- 超时事务使用代次/冷却隔离迟到帧；
- A0 谱图片段按请求、source 和 offset 重组；
- A3 划分 A1 自动上传轮次；按同一 source 的实际覆盖范围自动适配 1024/2048/4096，道数判定保留观测/推断依据。内部缺洞、冲突、非法越界和断线数据不发布；无线上轮次 ID 的迟到片段不能仅凭本地 token 可靠识别，边界与不确定性见 D-015；
- A4 保持有界分类但不解释、不发布。

真实上位机流已证明请求和谱图片段会交错，因此客户端不依赖“一个请求的全部帧连续到达”。

## 5. 数据获取

| 数据 | 默认策略 | 命令 |
| --- | --- | --- |
| 实时状态 | 每 5 秒，可配 5–60 秒 | `0x53` |
| 设备信息/累计值 | 每 60 秒，可配 60–3600 秒 | `0x54` |
| 校准数据 | 首次主动谱图需要时读取并缓存 | `0x55` |
| 完整谱图 | 配置条目启动时捕获一次；随后手动、定时保存或自动上传更新 | `0x52` / A3+A1 |

一次轮询生成不可变 `KC761Snapshot`，所有标量实体消费同一快照。设备自动上传数据被动接收；只有显式启用设备控制后，集成才允许修改自动上传状态。

## 6. Config Flow

首次配置分为两个步骤：

1. 选择 `TCP` 或 `BLE`。
2. TCP 仅显示 host/port；BLE 显示 HA 可连接广播地址选择器和可选服务 UUID 覆盖。

预检必须依次成功读取设备信息和状态。`DEVICE_ID` 去除尾部 NUL/空格后用作 Config Entry unique ID；IP、端口、MAC 和用户名称都不是设备身份。

Reconfigure 可改变传输和端点，但新端点必须返回相同 `DEVICE_ID`。流程在验证成功前不替换 Coordinator 的连接所有权，失败时保留旧配置。

Options Flow 当前包含：

- 状态轮询间隔、设备信息轮询间隔；
- 是否创建高频 raw 实体；
- 是否增加固定微单位剂量实体（原始实体保留）；
- 是否启用设备控制；
- 是否启用破坏性清空；
- 定时校时开关和 1–168 小时间隔；
- 定时保存谱图开关和保存周期。

选项更新后由 HA 重载配置条目。关闭设备控制会同时关闭定时校时和破坏性操作。

## 7. Device Registry 与实体

- Device identifiers：`(kc761, DEVICE_ID)`；
- model：设备型号映射；
- hw_version / sw_version：主设备硬件和固件；
- serial_number：原始 `DEVICE_ID`；
- manufacturer：资料不足，暂不填写。

只为已安装通道创建通道实体。关闭通道的测量字段 `-1` 转为 `unknown`；温度等其它合法负数保留。未知但结构兼容的型号/传感器值保留原值或 `Unknown (0xNN)`，不伪装成未安装。

A2 剂量率字段保留线协议原生单位 `mGy/h`、`mSv/h`；A5 累计剂量/剂量当量依照 2026-09-06 用户确认保留原始 `mGy`、`mSv` 数值与单位。可选微单位显示同时将数值乘 1000、单位换成对应 `µGy`、`µSv`（速率保留 `/h`）；不能仅替换标签，也不在协议层缩放。补全候选采用独立固定微单位实体，保留原始实体与历史，避免 HA 不支持的同实体自定义统计单位切换。手册/UI 单位矛盾与本次裁定的证据等级见 D-012；验证范围和实施状态见 [PRE_RELEASE_COVERAGE.md](PRE_RELEASE_COVERAGE.md)。

实体完整列表及默认门禁见 [ENTITY_MODEL.md](ENTITY_MODEL.md)。

## 8. 谱图

`SpectrumSnapshot` 包含获取时间、source、累计秒数、总计数、校准档案、`point_count` 和对应数量的 `[channel, energy_kev, counts]` 点。rc7 移除手动道数选项，旧道数选项不再强制组帧长度。根据同一事务/轮次的片段覆盖形成 1024、2048、4096 道候选，保存长度判定依据及不确定性；旧存档根据自身长度和历史判定信息读取，不因当前接收长度改变而裁剪。

A0/A1 无总道数或 EOF 字段。A0 在有界接收窗口结束、A1 在轮次边界结算连续候选；不能因为中途恰好收齐 1024/2048 点就提前完成。达到协议最大边界或末包位置满足填充候选时仍须核验覆盖。`0xFFFF` 也可能是实际计数，不按数值直接裁尾。允许有界的末包填充超出候选长度，但不能把缺洞或冲突数据补零发布。

连续候选可用于显示、存档和导出，同时携带 `spectrum_status`、`length_confidence`、`completion_basis` 及接收覆盖信息。这里的完整性只描述软件可核验的覆盖和边界，不能证明设备没有遗漏尾片，也不能证明硬件最大道数；没有判定元数据的旧存档标为 legacy。具体门禁见 D-015 与发布前实施记录。

- 最新谱图实体的 state 是总计数，`points` 位于属性中；
- `_unrecorded_attributes = {"points"}` 阻止 Recorder 保存点阵；
- 状态/可用性/谱图对象均未变化时，实体跳过大属性重复序列化；
- 每个配置条目的 `.storage/kc761/<entry_id>/` 最多保留 256 个 gzip 快照；
- `.metadata.json` 是可重建索引，gzip 是数据权威；保存和索引更新使用同目录临时文件加原子替换；
- WebSocket API 只对已认证用户开放，捕获、保存和清空要求管理员；清空还必须通过后端破坏性门禁。

内置卡片使用本地 vendored ECharts，不访问 CDN。普通数据更新重建完整 option；ROI/Data cursor 高频交互仅合并 markArea/markLine/markPoint overlay，不重复提交全谱点数组。ROI、数据游标和缩放恢复按实际道数限制有效范围；测试和部署门禁见 [PRE_RELEASE_COVERAGE.md](PRE_RELEASE_COVERAGE.md)。

## 9. 写入安全

后端门禁位于 Coordinator，而不是只依赖 UI：

- `enable_device_controls=false`：拒绝 `0x62`、`0x63`，不创建控制 switch/select/校时按钮；
- `enable_destructive_actions=false`：拒绝 `0x66`，不创建清空按钮，卡片不显示清空入口；
- 第二项只有第一项同时开启时有效；
- `0x64` 不属于客户端允许的写命令集合；
- 所有写入先做字段白名单和值域检查，串行发送，校验 ACK 后刷新读侧状态；失败时不乐观更新实体。

完整边界见 [SAFETY.md](SAFETY.md)。

## 10. 可用性与错误

| 类型 | 例子 | 行为 |
| --- | --- | --- |
| Transport | 拒绝、超时、EOF、BLE 通知溢出 | 关闭连接，实体不可用，退避重连 |
| Framing | 长度异常、未知 FLAG、缓冲失步 | 有界丢弃并计数；连续失败重连 |
| Data | NaN/Inf、电池越界、身份变化 | 拒绝字段或轮次，记录脱敏错误 |
| Device ACK | status 失败、回显命令不符 | 操作失败，不更新状态，随后读回 |

端口可连接不等于协议健康；只有成功解析预期响应才算一次更新成功。

## 11. 诊断与隐私

默认诊断包括版本、传输类型、型号/版本、ID 尾段、协议档案、`hardware_verified`、门禁状态、连接/重连/协议错误计数和最近成功时间。

默认排除 host、完整 ID、原始帧、完整谱图、凭据和私有路径。浏览器诊断默认关闭，启用时仍只保留有界脱敏事件。

## 12. 兼容与发布

- 最低 Home Assistant 为 2026.7.0；当前测试依赖固定 2026.7.4 和 Python 3.14.2+。
- 手册覆盖 KC761A/B/C/CN，但真实证据样本有限；`hardware_verified=false` 在 RC 中保持不变。
- 公开版本必须通过 Ruff、Node 前端契约、pytest branch coverage ≥95%、Hassfest 和 HACS。
- 测试环境、真实设备和生产环境的证据必须分开记录；模拟器、历史 alpha 或旧 RC 结果不能替代当前候选验收。

## 13. 后续 TODO

### A4 流模式与瀑布图

- 为 `STREAM_DATA_FLAG=0xA4` 增加语义解析：每个 16-bit `PULSE` 的高 2 bit 是 source，低 14 bit 是道址；按 A3/SYNC 轮次隔离后续一个或多个 A4 包。
- 使用有界时间桶维护各通道的增量直方图，为谱图卡提供时间-道址/能量-计数瀑布图；窗口长度、时间分辨率和内存上限必须显式可控。
- 原始脉冲数组不得写入普通 HA state、attributes 或 Recorder；前端通过有界后端接口读取聚合后的瀑布图切片。
- 在实现前取得自动上传开启态的真实 A3/A4/A1 捕获，覆盖低计数流模式、同轮多 A4、SYNC 回卷、丢包和流/谱模式切换。
- 当前版本继续只对 A4 有界拆帧和分类，本 TODO 不改变现有运行行为。
