# Home Assistant 实体模型

状态：`0.2.0-rc.1` 当前实现

## 1. 通用规则

- 一台物理 KC761x 对应一个 HA Device。
- 实体 unique ID 为 `<DEVICE_ID>_<stable_key>`；地址、显示名称和翻译不参与标识。
- 只为设备报告“已安装”的 Gamma、Neutron、PIN 通道创建通道实体。
- 通道关闭时，手册指定的测量值 `-1` 转为 `unknown`；其它合法负数保留。
- 实体 getter 只读取 Coordinator 快照，不执行网络 I/O。
- 所有实体都公开 `hardware_verified=false` 和协议档案标识；这表示没有跨型号/跨固件整体认证，不否定已记录的字段级实机证据。
- 当前设备信息和校准字段统一显示在实体列表，不再使用 integration-disabled 的“诊断实体”分组。

## 2. Device Registry

| 属性 | 来源 |
| --- | --- |
| identifiers | `(kc761, DEVICE_ID)` |
| name | 型号 + ID 尾段 |
| model | `DEVICE_MODEL` 映射 |
| model_id | 原始型号数值（HA 支持时） |
| hw_version | `HW_VER` |
| sw_version | 主处理器 `FW_VER` |
| serial_number | 原始 `DEVICE_ID` |
| manufacturer | 暂不填写；现有资料不足以确认标准制造商名称 |

协处理器版本和三通道传感器型号使用普通 sensor 显示。

## 3. 设备级只读传感器

| key | 内容 | 单位/类型 |
| --- | --- | --- |
| `battery_percentage` | 电池低 7 位百分比 | `%`, battery, measurement |
| `air_pressure` | 气压 | `hPa`, atmospheric pressure, measurement |
| `device_temperature` | 设备温度 | `°C`, temperature, measurement |
| `device_time` | 设备时间 | timestamp |
| `clock_offset` | 设备时间减当前快照接收时间 | `s`, duration, measurement |
| `coprocessor_firmware` | 协处理器固件版本 | 文本 |
| `device_id` | 完整设备唯一 ID | 文本 |
| `gamma_sensor_model` | 通道 0 传感器型号 | 文本映射 |
| `neutron_sensor_model` | 通道 1 传感器型号 | 文本映射 |
| `pin_sensor_model` | 通道 2 传感器型号 | 文本映射 |

传感器型号映射：`0x00` 未安装，`0x01` KC7601.21 CsI，`0x02` KC7601.24 CsI，`0x03` KC7601.25 CsI，`0x04` KC7601.26 CsI，`0x05–0x07` PIN，`0x08` KC7601.31 6Li；未知值显示 `Unknown (0xNN)`。

## 4. A6 校准只读传感器

| key | 字段 | 单位 |
| --- | --- | --- |
| `rad0_trigger_offset` | RAD0 触发偏移 | 原始整数 |
| `rad2_trigger_offset` | RAD2 触发偏移 | 原始整数 |
| `rad0_dose_zoom` | RAD0 剂量倍率 | 原始浮点 |
| `rad1_dose_zoom` | RAD1 剂量倍率 | 原始浮点 |
| `rad2_dose_zoom` | RAD2 剂量倍率 | 原始浮点 |
| `neu_window_center` | 中子窗口中心 | `ch` |
| `altitude_m_offset` | 海拔修正 | `m` |

这些值只从 `0x55`/A6 读取。集成不实现 `0x64`，因此不创建校准 number、select 或写按钮。

## 5. 每通道测量传感器

下表以 `<channel>` 表示 `gamma`、`neutron` 或 `pin`。

| key | 来源 | 单位 | 创建条件 |
| --- | --- | --- | --- |
| `<channel>_avg_cps` | 设备端 AVG CPS | `cps` | 通道已安装 |
| `<channel>_avg_dose_rate` | 设备端 AVG dose rate | `mGy/h` | 通道已安装 |
| `<channel>_avg_dose_equivalent_rate` | 设备端 AVG equivalent rate | `mSv/h` | 通道已安装 |
| `<channel>_raw_cps` | 上一秒 CPS | `cps` | 通道已安装且 Options 创建 raw 实体 |
| `<channel>_raw_dose_rate` | 上一秒 dose rate | `mGy/h` | 同上 |
| `<channel>_raw_dose_equivalent_rate` | 上一秒 equivalent rate | `mSv/h` | 同上 |

所有测量值使用 `measurement` state class。Home Assistant 没有核辐射剂量率的专用 device class，因此只发布明确单位，不套用不相关设备类。

`KI-AVG-001`：一台实机的三个设备端 AVG 字段在全部对齐样本中为 0，而官方上位机用实时序列计算 30 秒平均。AVG 实体忠实显示线协议值；需要实时测量时启用 raw 实体。RC 不把上位机算法伪装成设备字段。

## 6. 每通道累计传感器

| key | 来源 | 单位 | state class |
| --- | --- | --- | --- |
| `<channel>_spectrum_runtime` | 谱图累计时间 | `s` duration | 无 |
| `<channel>_dose_runtime` | 剂量累计时间 | `s` duration | 无 |
| `<channel>_cumulative_dose` | 累计剂量 | `mGy` | 无 |
| `<channel>_cumulative_dose_equivalent` | 累计剂量当量 | `mSv` | 无 |

这些值可由设备清零，故统一作为普通传感器，不使用 `total` 或 `total_increasing`。手册表格的 `uGy/uSv` 与正文 `mGy/mSv` 存在矛盾；实现采用正文和 rate 字段一致的 `mGy/mSv`，但不声称跨固件已确认。

## 7. 结构化谱图实体

每台设备创建一个 `spectrum` sensor：

- state：当前完整谱图的总计数；
- 属性：source、获取时间、累计时间、校准档案、segment 数及 2048 个 `[channel, energy_kev, counts]` 点；
- `points` 被列入 `_unrecorded_attributes`，Recorder 不保存点阵；
- 没有完整谱图时 state 为 `unknown`、`points=[]`；
- A0 主动谱图和完整 A3/A1 自动上传轮次复用同一实体。

谱图卡、WebSocket、CSV 和本地历史存档消费相同的 `SpectrumSnapshot`，不为每个道址创建实体。

## 8. 始终可用的按钮

| key | 行为 | 是否写设备 |
| --- | --- | --- |
| `refresh_spectrum` | 主动读取并发布当前谱图 | 否，发送 `0x52` |
| `save_spectrum` | 读取谱图并保存到 HA 本地有界存档 | 否，设备侧仍是读取 |

CSV 导出由谱图卡/已认证 WebSocket 执行，不创建额外实体。

## 9. 可选设备控制实体

只有 `enable_device_controls=true` 时才创建：

### Select

- `active_sensor`：Gamma / Neutron / PIN；
- `global_volume`：静音 / 小 / 大；
- `backlight_brightness`：微亮 / 最亮。

### Switch

- `auto_upload`；
- `key_tone`、`count_tone`、`dense_count_effect`；
- `count_flash`、`backlight`；
- 每个已安装通道的 `<channel>_spectrum_accumulating`。

### Button

- `sync_device_time`：立即把当前 UTC epoch 写入设备并读回刷新。

定时校时不是实体；在 Options 中由 `time_sync_enabled` 和 1–168 小时间隔控制，并受同一设备控制门禁约束。

## 10. 可选破坏性按钮

只有 `enable_device_controls=true` 且 `enable_destructive_actions=true` 时，才为每个已安装通道创建：

- `clear_<channel>_spectrum`；
- `clear_<channel>_dose_curve`；
- `clear_<channel>_cumulative_dose`。

谱图卡的相同入口也由后端返回的门禁状态控制。无论实体/UI 是否残留，Coordinator 在发送 `0x66` 前都会再次拒绝未授权调用。

## 11. 数值与迁移

- 电池只接受 0–100；NaN/Inf 拒绝；时间戳必须可解析。
- 未安装通道不创建实体；通道清单变化通过受控 reload 更新集合。
- 旧 binary sensor 条目会迁移移除；当前设备信息/AVG 实体恢复为普通可见实体，但不覆盖用户自己禁用的选择。
- 关闭“创建 raw 实体”时，旧 raw registry 项按迁移规则清理；重新启用后使用同一稳定 unique ID 创建。
