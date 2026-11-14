# Changelog

## Unreleased

### 新增

- 契约加载支持多份 overlay 按顺序链式叠加：`load_contract(..., overlay_paths=[...])`
  与 CLI 重复的 `--overlay` 标志；旧单份 `overlay_path` 参数保持兼容。
  重叠键以靠后 overlay 为准，字段级条目优先于各层 `defaults`，链式叠加与手工合成等价。
- `ContractField.key_sources` 记录每个生效键的来源（基线文件名 / overlay 文件名 /
  `<builtin>`）；JSON 报告新增 `extensions.field_sources` 扩展段落盘来源信息，
  契约段原有结构与 `schema_version` 语义不变。
- 应用 overlay 时 Markdown / HTML 报告渲染「字段来源（overlay 链）」小节；
  无 overlay 的报告保持逐字节不变，老 golden 继续通过。
- 新增 `compare` 子命令：同一基线 + 同一份数据上对比两条 overlay 链，输出
  `compare.json` / `compare.md`，含字段级口径差异（含两侧来源）、validate 行级结论
  不一致计数与错误码计数差、convert 规范值不一致对数。
- `validate` / `convert` / `report` 的既有输出格式与退出码不变；数据行与数据哈希
  仍走原有 pipeline。

## v0.1.0 — 初始版本

### 已实现

- 声明式 YAML 量纲契约：字段、量纲、规范单位、允许单位、精度、舍入模式、物理范围。
- 契约 schema 严格校验，稳定错误码并定位到文件、字段和下标。
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
