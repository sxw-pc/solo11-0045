# Changelog

## v0.1.1 — 契约语义指纹

### 新增

- 台账 `runs.contract_fingerprint`：基于规范化语义（defaults 展开、字段名排序、
  `allowed_units` 去重排序、数值精确有理化、`range` 换算到量纲基准单位）的 SHA-256 指纹。
- `unitcontract.contract.fingerprint`：`compute_fingerprint` / `build_fingerprint_payload`
  与机器可读 `FINGERPRINT_SPEC`（判定口径）。
- JSON 报告 `extensions.contract_fingerprint` 扩展段：语义指纹 + 原始字节哈希，
  既有 `run` / `contract` / `summary` 结构不变。
- `--overlay` 参数可用于 `validate` / `convert` / `report`；带 overlay 时原始哈希按
  base+overlay 字节做域分隔哈希。
- `find_runs_by_fingerprint`：按语义指纹归并历史 run。
- 老台账自动迁移：`init_db` 检测并 `ALTER TABLE` 补 `contract_fingerprint` 列，历史行为 NULL。

### 明确的口径

- 温度 range：换算到 K 后比较，但 `canonical_unit` 不同即判不等价（它影响校验与输出语义）。
- `name` / `description` / 注释 / 字段顺序 / `allowed_units` 顺序 / 未知顶层键不参与指纹。

### 不变

- `contract_hash`、`run_id` 派生方式、`validate` / `convert` / `report` 的字段结构、
  错误码、退出码、Markdown / HTML 报告格式与全部 golden 均保持原样。

## v0.1.0 — 初始版本

### 已实现

- 声明式 YAML 量纲契约：字段、量纲、规范单位、允许单位、精度、舍入模式、物理范围。
- 契约 schema 严格校验，稳定错误码并定位到文件、字段和下标。
- 契约语义指纹（`contract_fingerprint`），与原始字节哈希并存。
- 精确有理数单位注册表（`fractions.Fraction`），覆盖长度、质量、能量、功率、时间、体积、计数与温度。
- 独立的 C/K/F 偏移换算路径，含绝对零度校验；温度范围在规范单位下判断。
- CSV / JSONL 逐行校验：缺失、非数值、单位缺失、单位未知、量纲不匹配、单位不允许、越界、精度警告。
- 确定性换算、三种舍入模式与规范化 JSONL 输出。
- 差异归因审计：错误码目录、分类计数、字段 Top-N、通过/违规行统计。
- SQLite 台账（`runs`、`findings`），记录契约 SHA-256、数据 SHA-256 和报告目录，支持按 run_id 查询。
- 稳定 JSON、Markdown、自包含 HTML 三份报告；报告无脚本、无外部链接、无远程样式。
- 本地 Flask 只读服务，仅监听 loopback；首页、运行列表、运行详情和报告下载。
- `demo`、`validate`、`convert`、`report`、`serve` 五个子命令。
- 240 行确定性合成演示数据、18 行回归 fixture、golden JSON 片段与 Markdown 报告。
- 离线 pytest 用例覆盖 schema、注册表、温度、校验、换算、审计、台账、报告、CLI 与服务冒烟。

### 明确不实现

- 复合单位与派生量纲自动推导。
- 货币、汇率与金融口径。
- 时区语义换算。
- 跨文件 schema 推断与自动补契约。
- 流式/增量处理、消息队列、分布式执行与调度。
- 用户体系、鉴权、多租户、云端存储和任何联网能力。
- 图形化契约编辑器。
- 百万行以上数据集的专门性能优化。
