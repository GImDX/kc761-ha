# Home Assistant 实体模型

> 本文主体记录 0.1 RC 的只读实体基线。0.2 分支新增的统一设备信息实体、控制平台、单一结构化谱图实体、独立历史存档和谱图卡，以 [V0_2_EXPANSION.md](V0_2_EXPANSION.md) 为当前规范；冲突处以该文档为准。

状态：`0.1.0-rc.1` 模拟候选已实现，受协议实机验证门禁约束

日期：2026-08-14

## 1. 建模原则

- 一台物理 KC761x 对应一个 Home Assistant Device。
- 只为设备实际安装的通道创建实体；RAD0=Gamma、RAD1=Neutron、RAD2=PIN。
- 通道已安装但当前关闭并返回 `-1` 时，实体值为 `unknown`，不伪装为 0。
- 设备/实体唯一标识来自 `DEVICE_ID` 和稳定字段 key，不使用 IP、端口或用户名称。
- 测量实体只读取 Coordinator 内存快照，属性访问不得触发网络 I/O。
- 当前代码候选默认开启设备端 AVG 字段；实机证据表明本设备的三个 AVG 字段恒为 0，而官方上位机在本地计算 30 秒平均值。该差异登记为已知问题 `KI-AVG-001`，按限制接受，本设备本轮实机验收通过。
- 累计量可以被设备清零，因此即使手册存在单位矛盾，也按裁决创建普通实体，不声明长期统计 state class。
- 完整谱图和脉冲数组不作为实体属性。

## 2. Device Registry

| 属性 | 来源 | 处理 |
| --- | --- | --- |
| identifiers | `DEVICE_ID` | `(kc761, DEVICE_ID)`；不以地址代替、不做大小写或符号归一化 |
| name | 型号 + ID 尾段 | 用户可在 HA 重命名 |
| model | `DEVICE_MODEL` | 映射为 KC761/KC761A/B/KC761C/KC761CN |
| hw_version | `HW_VER` | 按十进制一位小数格式化 |
| sw_version | `FW_VER` | 按十进制两位格式化 |
| serial_number | `DEVICE_ID` | 仅在 HA 支持且诊断脱敏规则明确时使用 |
| manufacturer | 待确认 | 不把印行方“科新社”直接当制造商 |

协处理器版本和传感器型号不是 Device Registry 的标准版本字段，保留为诊断实体或诊断输出。

## 3. 设备级只读传感器

| 稳定 key | 显示名称 | 来源 | 单位/设备类 | state class | 默认 | 分类 |
| --- | --- | --- | --- | --- | --- | --- |
| `battery_percentage` | 电池电量 | `BAT_PERCENT` | `%` / battery | measurement | 开启 | 普通 |
| `air_pressure` | 气压 | `AIR_PRESSURE3BA` | `hPa` / atmospheric pressure | measurement | 开启 | 普通 |
| `device_temperature` | 设备温度 | `DEVICE_TEMP / 10` | `°C` / temperature | measurement | 开启 | 普通 |
| `device_time` | 设备时间 | `DEVICE_TIME` | timestamp | 无 | 关闭 | diagnostic |
| `clock_offset` | 设备时钟偏差 | 设备时间减 HA 时间 | `s` / duration | measurement | 关闭 | diagnostic |
| `coprocessor_firmware` | 协处理器固件 | `CO_FW_VER` | 文本 | 无 | 关闭 | diagnostic |
| `auto_upload_enabled` | 自动上传 | `AUTO_UPLOAD_STATUS` | binary sensor | 无 | 关闭 | diagnostic |

`BAT_PERCENT` 只有在 0-100 范围内才有效；其它值记为未知并增加协议数据错误计数。设备时钟偏差只用于诊断，不触发自动校时。

## 4. 每通道测量实体

对每个已安装通道，以 `gamma`、`neutron`、`pin` 替换下表的 `<channel>`。

| 稳定 key | 显示名称 | 来源 | 原生单位 | state class | 默认 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| `<channel>_avg_cps` | 平滑计数率 | `RADn_AVG_CPS` | `cps` | measurement | 开启（已知问题） | 本设备恒为 0；忠实反映线协议字段，不代替上位机本地 30 秒平均 |
| `<channel>_avg_dose_rate` | 平滑剂量率 | `RADn_AVG_DOSE_RATE` | `mGy/h` | measurement | 开启（已知问题） | 本设备恒为 0；需要实时值时启用 raw 实体 |
| `<channel>_avg_dose_equivalent_rate` | 平滑剂量当量率 | `RADn_AVG_DOSE_EQ_RATE` | `mSv/h` | measurement | 开启（已知问题） | 本设备恒为 0；需要实时值时启用 raw 实体 |
| `<channel>_raw_cps` | 上一秒计数率 | `RADn_RAW_CPS` | `cps` | measurement | 默认不创建 | 本设备已与上位机 CSV 逐帧确认；高频、随机波动较大 |
| `<channel>_raw_dose_rate` | 上一秒剂量率 | `RADn_RAW_DOSE_RATE` | `mGy/h` | measurement | 默认不创建 | binary16 已解码；尚无独立上位机导出列确认其物理量 |
| `<channel>_raw_dose_equivalent_rate` | 上一秒剂量当量率 | `RADn_RAW_DOSE_EQ_RATE` | `mSv/h` | measurement | 默认不创建 | 本设备已与上位机 µSv/h CSV 逐帧确认 |

Home Assistant 当前没有针对核辐射剂量率的专用 device class，因此这些量使用明确的原生单位、`measurement` state class 和放射性图标，不错误套用功率、频率或其它不相干设备类。

当前 Options 默认不创建上一秒原始读数实体。用户显式开启“创建高频原始读数实体”后，才为已安装通道创建并启用三类 raw 实体；它们仍归类为 diagnostic。实机已证明这些 raw 字段携带本设备的实时测量，而设备端 AVG 字段恒为 0。该行为作为 `KI-AVG-001` 接受，不阻塞本设备实机验收；用户需要实时测量时启用 raw 实体，后续是否调整默认值或新增 HA 侧派生平均另行评审。

单位显示策略：

- 解码层始终按手册原生单位输出，不在不同实体间混用 `m` 和 `µ`。
- 如果 HA 的单位转换系统不支持辐射单位，实体固定发布一种单位；翻译只改变名称，不改变单位。
- 在确认 FP16 和缩放前，剂量相关实体不能进入稳定版本。

## 5. 每通道累计普通实体与诊断值

| 稳定 key | 显示名称 | 来源 | 候选语义 | 当前 RC | 发布门禁 |
| --- | --- | --- | --- | --- | --- |
| `<channel>_spectrum_runtime` | 谱图累计时间 | `RADn_MC_RUNTIME` | duration `s` | 已安装通道创建、普通实体 | 清空谱图时归零 |
| `<channel>_dose_runtime` | 剂量累计时间 | `RADn_SUM_DOSE_TIME` | duration `s` | 已安装通道创建、普通实体 | 清空累计剂量时归零 |
| `<channel>_cumulative_dose` | 累计剂量 | `RADn_SUM_DOSE` | `mGy` | 已安装通道创建、普通实体 | 不设置 state class，避免重置污染长期统计 |
| `<channel>_cumulative_dose_equivalent` | 累计剂量当量 | `RADn_SUM_DOSE_EQ` | `mSv` | 已安装通道创建、普通实体 | 不设置 state class，避免重置污染长期统计 |
| `<channel>_spectrum_accumulating` | 谱图正在累计 | `RAD_SENSOR_STATUS` 位 | binary sensor | 已安装通道创建、默认禁用/diagnostic | 位定义实机确认 |
| `<channel>_sensor_type` | 传感器型号 | `RADn_SENSOR_TYPE` | enum/text | 不创建实体，仅 diagnostics | 型号映射确认 |

这些累计字段都会因设备端清空操作而重置，按用户裁决统一作为普通实体，不使用 `TOTAL_INCREASING` 或其它 state class。表格仍写 `uGy/uSv`、正文写 `mGy/mSv` 的手册矛盾保留在协议证据中；实现采用正文和当前 rate 一致的 `mGy/mSv` 显示，不声称跨固件倍率已硬件确认。

## 6. A6 校准只读字段

| key | 手册字段 | 类型/单位 | 当前实现 |
| --- | --- | --- | --- |
| `rad0_trigger_offset` / `rad2_trigger_offset` | `RAD0/RAD2_TRIGGER_OFFSET` | uint16 | 普通 sensor，只读 |
| `rad0_dose_zoom` / `rad1_dose_zoom` / `rad2_dose_zoom` | `RAD0/1/2_DOSE_ZOOM` | float | 普通 sensor，只读 |
| `neu_window_center` | `NEU_WINDOW_CENTER` | uint16，`ch` | 普通 sensor，保留 CENTER 命名 |
| `altitude_m_offset` | `ALTITUDE_M_OFFSET` | int16，`m` | 普通 sensor，只读 |

当前安全写协议仅覆盖 `0x62/0x63/0x66`；虽然手册描述 `0x64`，集成继续禁止其写入，因此 A6 新字段不创建 number/control 实体。实体值在最近一次 A6 读取后可用，未读取时为 `unknown`。

## 6. 可选控制实体的后续设计

首版不创建任何控制实体。只有 Options Flow 显式启用设备控制、测试环境完成 ACK 与读回验证后，才评估以下平台。

### 6.1 Select

| key | 候选项 | 线协议字段 | 风险 |
| --- | --- | --- | --- |
| `active_sensor` | Gamma / Neutron / PIN | `RAD_SENSOR_STATUS` bit 0-1 | PIN 会关闭其它通道；中子与伽玛可能绑定 |
| `global_volume` | 静音 / 小 / 大 | `VOLUME_STATUS` bit 0-1 | 告警音不受此设置完全控制 |
| `backlight_brightness` | 微亮 / 最亮 | `LED_SCREEN_STATUS` bit 2 | 部分固件不支持 |

### 6.2 Switch

| key | 字段 |
| --- | --- |
| `key_tone` | `VOLUME_STATUS` bit 3 |
| `count_tone` | `VOLUME_STATUS` bit 2 |
| `dense_count_effect` | `VOLUME_STATUS` bit 4 |
| `count_flash` | `LED_SCREEN_STATUS` bit 0 |
| `backlight` | `LED_SCREEN_STATUS` bit 1 |

`AUTO_UPLOAD_STATUS` 不规划为普通控制开关。它直接影响协议数据流，也可能影响同时连接的官方上位机，应该由传输策略统一管理；首版只观察不修改。

### 6.3 Button

`sync_device_time` 可作为后续高级按钮，但必须：

- 使用 HA 当前 UTC 时间生成 epoch；
- 明确设备本地显示的时区语义；
- 校验 ACK；
- 立即读回设备时间；
- 不自动周期执行。

清除谱图、剂量曲线和累计剂量不建模为普通按钮。按钮单击缺少足够的强确认，不适合破坏性操作。

## 7. 高风险动作

以下能力即使未来实现，也只能作为显式 action/service，并要求目标设备 ID 确认、字段白名单、范围校验、ACK 和读回：

- 写入校准数据；
- 切换工厂/自定义能量刻度；
- 写入能量多项式；
- 修改触发阈值、中子窗口、剂量缩放；
- 清除某通道谱图；
- 清除剂量曲线；
- 清除累计剂量。

首个稳定版本不注册这些 action，避免“隐藏但可调用”的未验证写入口。

## 8. 谱图呈现

### 8.1 不采用的方案

- 不把完整数组放入 sensor attributes；
- 不为每个道址创建一个实体；
- 不每秒产生带完整谱图的 event；
- 不默认把谱图写入本地文件系统；
- 不让 Recorder 保存原始脉冲流。

### 8.2 后续候选

在谱图重组完成条件、能量刻度和数据量经过实机验证后，可分阶段评估：

1. `capture_spectrum` 按需 action，以响应数据返回当前谱图；
2. 小型派生实体，例如峰值道址、峰值能量、谱图总计数和谱图累计时间；
3. 单独的 Lovelace 卡片，通过受限 API 请求数据，不借用状态机持久化大数组。

任何派生实体都必须定义算法版本、输入谱图来源、是否应用 `MC_RATIO`、能量校准段选择和空/缺包处理。

## 9. 实体唯一 ID 与迁移

实体 unique ID 格式固定为：

```text
<DEVICE_ID>_<stable_key>
```

`stable_key` 使用英文协议语义，不包含翻译、用户名称、单位或 IP。例如：

- `<DEVICE_ID>_gamma_avg_cps`
- `<DEVICE_ID>_device_temperature`

一旦发布不得因中文名称调整而改变。若后续修正单位，只迁移单位和数值，不重建 unique ID。

## 10. 数值质量规则

- `-1` 仅按手册在关闭通道字段中转换为 `None`；不能把所有负数都丢弃，因为温度可为负。
- 电池只接受 0-100。
- `MC_RATIO` 必须大于等于 1。
- 浮点值拒绝 NaN 和无穷；合理量程告警阈值需由实机数据确定，不凭空截断高辐射值。
- 时间戳必须能转换为合理日期；异常值不触发自动校时。
- 版本号按整数位拆分，避免 `1.80` 被展示成 `1.8` 而失去固件辨识度。
- 未安装传感器不创建通道实体；未知传感器索引保留诊断原值并采用通用通道名称。

## 11. 默认启用摘要

当前代码候选在对应通道存在时默认启用以下实体，但所有实体固定公开 `hardware_verified=false`。实机逐帧证据已经证明其中三个设备端 AVG 实体在本设备上恒为 0；该限制已作为 `KI-AVG-001` 接受，以下清单同时描述当前实现和本轮验收口径：

- 电池、气压、设备温度；
- 各已安装通道的平滑计数率；
- 各已安装通道的平滑剂量率；
- 各已安装通道的平滑剂量当量率。

原始读数目前仅在 Options 显式开启后创建；时钟、固件附加信息和谱图累积状态默认禁用/diagnostic；累计时间、累计剂量和传感器型号不创建实体，只保留在内存模型或脱敏 diagnostics。上位机的 30 秒平滑设置不能解释为设备协议参数；如果以后改变平均/原始实体策略，届时再执行 Recorder、迁移和实机回归，不影响当前按已知问题限制通过的验收结论。
