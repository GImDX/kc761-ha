# Home Assistant 实体模型

状态：发布前补全候选；验证与部署状态见 [PRE_RELEASE_COVERAGE.md](PRE_RELEASE_COVERAGE.md)。

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

协处理器版本和三通道传感器型号使用默认启用的普通 sensor 显示。`coprocessor_firmware` 保留既有 unique ID；已观察到原始 `CO_FW_VER=11` 时显示 `V1.1`，不是主固件的 `/100` 显示规则。用户历史手动禁用状态不被升级强制覆盖。

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

补全候选新增以下 34 个默认启用的普通只读 sensor，与上表 7 项合计 41 项 A6 业务字段：

| key / key 组 | 内容 | 数量 |
| --- | --- | --- |
| `energy_calibration_version` | 工厂校准版本 | 1 |
| `energy_calibration_selection` | 工厂/自定义能量刻度选择 | 1 |
| `<channel>_energy_zoom`、`<channel>_energy_offset` | 三通道能量倍率和偏移 | 6 |
| `gamma_custom_coefficient_a/b/c/d` | Gamma 自定义系数 | 4 |
| `neutron_factory_coefficient_a/b/c/d` | Neutron 工厂系数 | 4 |
| `pin_factory_coefficient_a/b/c/d` | PIN 工厂系数 | 4 |
| `gamma_factory_low/middle/high_coefficient_a/b/c/d` | Gamma 工厂低、中、高三段系数 | 12 |
| `gamma_factory_energy_node1/2` | Gamma 工厂分界道址 | 2 |

表中的 `/` 表示分别独立的实体 key，不是数组属性。系数遵循现有多项式 `((a * ch + b) * ch + c) * ch + d` 顺序；不将它们标成辐射读数，也不设置长期统计 state class。字段来自设备级完整 A6 档案，即使某组刻度未被选用也可读取；有值不等于正在参与当前通道换算。

### 4.1 独立只读状态

状态直接读取已有 A2/A3 快照，不执行额外网络 I/O，不依赖 `enable_device_controls`，也不使用诊断分类。默认启用以下实体：

| 平台 | key | 内容 |
| --- | --- | --- |
| sensor | `active_sensor_state` | 活动通道 |
| sensor | `global_volume_state` | 全局音量档位 |
| sensor | `backlight_brightness_state` | 背光亮度档位 |
| binary_sensor | `auto_upload_status` | 自动上传 |
| binary_sensor | `key_tone_status` | 按键音 |
| binary_sensor | `count_tone_status` | 计数音 |
| binary_sensor | `dense_count_effect_status` | 密集计数效果 |
| binary_sensor | `count_flash_status` | 计数闪光 |
| binary_sensor | `backlight_status` | 背光 |
| binary_sensor | `<channel>_spectrum_accumulating_status` | 各已安装通道的谱图累积 |

独立的 `_state`/`_status` 标识避免与可选 select/switch 或旧版实体迁移键混淆。开启控制后，只读状态和控制实体可以同时存在。

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

速率单位不随累计量单位复核而改变。2026-08-27 对真实条目的 Recorder 做只读查询，`gamma_raw_dose_equivalent_rate` 的 80,453 个数值记录中位数为 `0.000120639801025391 mSv/h`，即 `0.120639801025391 µSv/h`，与设备约 `0.1 µSv/h` 的本底量级一致。这是等价数值的离线对照，不证明 HA 原生前端具备任意辐射单位前缀换算。当前实体与 Recorder 固定使用 m 单位，不提供该显示切换。

`KI-AVG-001`：一台实机的三个设备端 AVG 字段在全部对齐样本中为 0，而官方上位机用实时序列计算 30 秒平均。AVG 实体忠实显示线协议值；需要实时测量时启用 raw 实体。RC 不把上位机算法伪装成设备字段。

## 6. 每通道累计传感器

| key | 来源 | 协议档案/目标单位 | 当前 RC 代码标签 | state class |
| --- | --- | --- | --- | --- |
| `<channel>_spectrum_runtime` | 谱图累计时间 | `s` duration | `s` | 无 |
| `<channel>_dose_runtime` | 剂量累计时间 | `s` duration | `s` | 无 |
| `<channel>_cumulative_dose` | 累计剂量 | `mGy` | `mGy` | 无 |
| `<channel>_cumulative_dose_equivalent` | 累计剂量当量 | `mSv` | `mSv` | 无 |

这些值可由设备清零，故统一作为普通传感器，不使用 `total` 或 `total_increasing`。2026-09-06 用户确认累计原始数值单位为 `mGy/mSv`，本项目保留当前标签、原始值与历史；撤回此前“仅改为 µGy/µSv 标签”的方案。PDF 表格、正文和官方 UI 的矛盾作为原始证据保留，不表述为本轮已消除的跨固件疑点。rc8 取消可选微单位显示，rc9 停止加载旧微单位实体；核心统计验证与完整 Recorder 端到端验证的区别见 D-012 和 [发布前补全记录](PRE_RELEASE_COVERAGE.md)。

### 6.1 固定记录单位与旧实体停用

rc8 依据 2026-09-08 用户最终裁定取消 `dose_display_unit` 选项，忽略旧保存值，不创建新的微单位伴随实体，也不新增卡片单位选择项。原有 canonical 实体始终使用 mGy/mSv（速率为 mGy/h、mSv/h），原始数值和标识不变，Recorder 不因本次更新产生单位切换。CPS、时间和校准系数不换算。

rc8 仍加载已经注册的旧 `_micro` 实体并持续输出微单位数值，未达到统一后续记录的目标。rc9 不再加载任何 `_micro` 伴随实体，包括 raw 速率伴随项；旧配置值和旧注册项均不得使它们重新加载。不修改 registry 的禁用状态，不删除实体注册记录或改写历史，不将旧微单位实体改标签为 m 单位。旧实体可能显示不可用，历史中仍可存在此前的 µ 数值；后续不再由本集成为旧实体生成新的数值读数，但 HA 可能记录不可用等状态变化。原始 raw 实体仍受 `create_raw_entities` 控制。

此前的同实体动态单位显示方案不再实施；HA 2026.7.4 对自定义辐射单位没有通用前缀转换器，不能承诺混合单位统计和原生曲线正确归一化。固定单位避免新增该风险，不代表对既有历史做了迁移或修复。历次决策与验证边界见 D-012 和 [发布前补全记录](PRE_RELEASE_COVERAGE.md)。

## 7. 结构化谱图实体

每台设备创建一个 `spectrum` sensor：

- state：当前可用谱图快照的总计数（长度的观测/推断状态另行公开）；
- 属性：source、获取时间、累计时间、校准档案、segment 数、`point_count` 及对应数量的 `[channel, energy_kev, counts]` 点；道数支持 1024、2048、4096；
- `points` 被列入 `_unrecorded_attributes`，Recorder 不保存点阵；
- 没有可用谱图快照时 state 为 `unknown`、`points=[]`；内部缺洞、冲突数据不生成可用快照；
- A0 主动谱图和 A3/A1 自动上传轮次复用同一实体和长度判定元数据。

谱图卡、WebSocket、CSV 和本地历史存档消费相同的 `SpectrumSnapshot`，不为每个道址创建实体。

rc7 不再显示或使用 `gamma_spectrum_channels`、`neutron_spectrum_channels`、`pin_spectrum_channels` 强制长度；每个谱图根据接收片段自动适配，`point_count` 与实际点阵长度一致。实体、卡片、CSV 和存档共同保留 `spectrum_status`、`length_confidence`、`completion_basis` 等信息；观测/推断长度不得表述为硬件能力已确认。无法排除长谱缺失尾片的情况，卡片需明确提示；没有相关元数据的旧存档标为 legacy。历史存档使用自身保存的长度，不用最新实时谱图长度裁剪旧谱图。

CSV 保留原有 7 列的顺序，在其后追加 `spectrum_status`、`length_confidence`、`completion_basis`、`received_count`、`received_span`、`padding_count` 六列，并在每个道址记录上保留相同的快照判定信息。消费者不应再假定 CSV 恰有 7 列；点阵中的原始计数不因新增元数据而改变。

可用候选的 `spectrum_status=complete` 仅指候选范围连续覆盖，不是硬件总长度确认。1024/2048 道在请求窗口或上传轮次结束时为 `inferred`；4096 道无填充时为 `observed/max_bound`；合法末包填充候选为 `inferred/padding_candidate`。`received_count` 记录候选点数，`received_span` 保留线上片段覆盖跨度，`padding_count` 记录被判为末包填充的数量。读取与导出时必须同时解释这些字段，不能单独把 `complete` 当成“已排除尾片丢失”。

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
- 旧版被弃用的 binary sensor 条目按原迁移规则处理；新增 `_status` 只读实体必须保留。当前设备信息/AVG 实体恢复为普通可见实体，但不覆盖用户自己禁用的选择。
- 仅配置条目版本低于 5 的旧迁移路径可能清理关闭状态下的 raw registry 项。当前版本关闭 raw 选项只重载并停止加载 raw 实体，不删除其注册项；重新启用时使用同一稳定 unique ID。此旧迁移规则不授权删除 rc9 停用的 `_micro` 注册项或任何历史。
