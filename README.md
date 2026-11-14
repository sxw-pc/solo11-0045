# 量纲契约台（unit-contract）

一个**纯本地、完全离线、可一条命令跑通**的字段量纲 / 单位契约校验与转换登记台。
它把「字段应当是什么物理量纲、允许哪些单位、换算到什么规范单位、保留几位小数、
如何舍入、合理范围是多少」写成声明式 YAML 契约，再对本地 CSV / JSONL 数据做：

1. 契约 schema 校验（错误码稳定、定位到文件与字段）；
2. 逐行逐字段的静态契约校验（错误与警告分离）；
3. 精确有理数的确定性单位换算（含温度偏移特殊路径）；
4. 差异归因、SQLite 台账、JSON / Markdown / HTML 归档报告；
5. 仅监听 loopback 的本地只读查看服务。

技术栈：Python 3.10+、标准库、PyYAML、pytest，可选 Flask。运行期不访问网络。

## 1. 定位与边界

### 是

- 物理量纲与单位契约工具：长度、质量、能量、功率、时间、温度、体积、计数。
- 面向「同一业务字段，A 源用 kWh、B 源用 MWh、C 源用 GJ」这类跨源口径漂移。
- 本地批处理 + 本地 HTTP 服务 + 可归档明细台账。
- 精确有理数换算：注册表使用 `fractions.Fraction`，输出时才按契约舍入。
- 演示数据、测试 fixture、golden 全部内置于仓库。

### 不是

- **不是指标口径校准**：不讨论「活跃用户怎么算」等业务定义，只检查物理单位是否合法。
- **不是实体解析 / 去重**、不是采集管道、不是站点巡检、不是数据脱敏。
- **不是湖仓版本管理**，也不管理表分层、快照或血缘。
- **不是通用单位词典**：只做契约与内置注册表声明的闭环换算，不做全量单位库。
- **不是分布式 / 流式平台**：无消息队列、无外部数据库、无云服务。

## 2. 合规与安全

- 仅处理你本人拥有、且已获授权处理的本地数据。
- 运行期不发起任何网络请求；HTML / CSS / fixture 全部本地化，不引用 CDN。
- HTTP 服务只允许 `127.0.0.1`、`localhost`、`::1`；拒绝 `0.0.0.0` 等非 loopback 地址。
- 端口被占用时明确报错并退出，不静默换端口、不回退到公网接口。
- 仓库与 fixture 不含真实凭据、真实公司名、真实域名或非 loopback IP 样例。
- 演示数据全部合成；台账只保存输入文件名、内容哈希、统计与报告目录。

## 3. 快速上手

```bash
python -m pip install -r requirements.txt

# 全量测试（离线，不启动真实端口）
python -m pytest -q

# 生成合成数据、契约、台账和三份报告
python -m unitcontract demo --out ./_demo_out --no-timestamp

# 本地只读服务
python -m unitcontract serve --host 127.0.0.1 --port 8731 \
  --db ./_demo_out/ledger.db
```

打开 `http://127.0.0.1:8731/` 查看最近 run；接口：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 本地首页，最近 run 摘要与问题列表 |
| GET | `/api/runs` | run 列表，支持 `limit` / `offset` |
| GET | `/api/runs/<run_id>` | run 摘要、契约与分页明细 |
| GET | `/report/<run_id>/json` | 下载 JSON 报告 |
| GET | `/report/<run_id>/md` | 下载 Markdown 报告 |
| GET | `/report/<run_id>/html` | 下载自包含 HTML 报告 |

其他命令：

```bash
python -m unitcontract validate --contract c.yaml --data d.csv --db ledger.db
python -m unitcontract convert --contract c.yaml --data d.csv --out normalized.jsonl
python -m unitcontract report --contract c.yaml --data d.csv --out ./_report_out

# 链式叠加多份 overlay（靠后者覆盖重叠键）
python -m unitcontract validate --contract c.yaml --data d.csv \
  --overlay team-defaults.yaml --overlay site-special.yaml

# 对比同一基线下两条 overlay 链的口径与结论差异
python -m unitcontract compare --contract c.yaml --data d.csv \
  --left-overlay team-defaults.yaml --right-overlay site-special.yaml \
  --out ./_compare_out
```

`validate` 在存在违规行时返回退出码 1，契约 / 数据读取错误返回 2。
`demo`、`convert`、`report`、`compare` 成功返回 0。

## 4. 契约 YAML 语法

```yaml
schema_version: "1"
name: facility-readings
description: 合成示例
defaults:                  # 可选：字段默认值，可被单个字段覆盖
  precision: 2
  rounding: half_even
fields:
  - field: energy
    dimension: energy
    canonical_unit: J
    allowed_units: [J, kJ, kWh, MWh, GJ]
    precision: 2
    rounding: half_even
    range: {min: 0, max: 5000000000}
  - field: temperature
    dimension: temperature
    canonical_unit: C
    allowed_units: [C, K, F]
    precision: 1
    rounding: half_even
    range: {min: -50, max: 60}
```

- `dimension`：`length` / `mass` / `energy` / `power` / `time` / `temperature` / `volume` / `count`。
- `canonical_unit`：必须属于该量纲，且必须在 `allowed_units` 中。
- `allowed_units`：契约允许出现的单位集合，必须都能在本地注册表解析。
- `precision`：非负整数，输出固定保留的小数位。
- `rounding`：`half_up`（四舍五入）、`half_even`（银行家舍入）、`truncate`（向零截断）。
- `range`：可选，按**规范单位**判断；`min` / `max` 可只给一侧。
- 顶层 `defaults` 与字段合并；loader 也支持 overlay 文件按 `field` 名覆盖字段。

### Overlay 链与字段来源

`validate` / `convert` / `report` 接受重复的 `--overlay PATH`，按给定顺序链式叠加；
`load_contract(..., overlay_paths=[...])` 是等价的编程接口，旧的单份
`overlay_path` 参数继续可用。合并规则确定且与手工合成等价：

- overlay 只覆盖显式给出的键，未给出的键继续从基线继承；
- 字段级条目优先于任何一层的 `defaults`；同类来源之间靠后的 overlay 覆盖重叠键；
- `defaults` 自身也按链序逐键合并，靠后者优先；
- 逐份折叠叠加与先把多份 overlay 合成一份再叠加，得到的契约完全相同。

每个字段最终生效的键都会记录来源（基线文件名、overlay 文件名或 `<builtin>`），
落在 JSON 报告的 `extensions.field_sources` 扩展段（契约段原有结构不变）；应用了
overlay 时，Markdown / HTML 报告额外渲染「字段来源（overlay 链）」小节。来源标签只取
文件名不含目录，因此同一组输入在任何目录下都生成逐字节一致的报告。

`compare` 子命令在同一基线契约与同一份数据上分别应用两条 overlay 链，输出
`compare.json` / `compare.md`：字段级口径差异（含两侧各自来源）、validate 行级结论
不一致计数（通过/违规状态与错误码集合两个口径）及错误码计数差、convert 规范值不一致
的 (行, 字段) 对数。两侧数据行与数据哈希均走原有 pipeline，退出码语义与其他命令一致。

数据约定：

- CSV：每个字段一列；单位列名为 `<field>__unit`。示例：`energy,energy__unit`。
- JSONL 扁平形式：`{"energy": 1.2, "energy__unit": "kWh"}`。
- JSONL 嵌套形式：`{"energy": {"value": 1.2, "unit": "kWh"}}`。
- 报告中的 `row` 是数据记录序号（CSV 第一条数据为 1，不含表头）。
- 空值判 `E_MISSING`；有值但无单位判 `E_UNIT_MISSING`。

### 错误码

| 错误码 | 阶段 | 含义 |
|---|---|---|
| `E_YAML_INVALID` | 加载 | YAML 无法解析或文件不可读 |
| `E_SCHEMA_ROOT` | 契约 | 根节点 / 条目结构非法 |
| `E_SCHEMA_VERSION` | 契约 | `schema_version` 不受支持 |
| `E_CONTRACT_NAME` | 契约 | 契约名缺失或为空 |
| `E_FIELDS_MISSING` | 契约 | `fields` 缺失或为空 |
| `E_FIELD_NAME` | 契约 | 字段名为空 |
| `E_FIELD_DUPLICATE` | 契约 | 字段名重复 |
| `E_DIM_UNKNOWN` | 契约 | 量纲缺失或未知 |
| `E_CANON_MISSING` | 契约 | 规范单位缺失 |
| `E_UNIT_UNKNOWN` | 契约/校验 | 单位不在本地注册表 |
| `E_UNIT_DIM_MISMATCH` | 契约/校验 | 单位属于另一量纲 |
| `E_ALLOWED_MISSING` | 契约 | 允许单位集合为空 |
| `E_ALLOWED_ITEM` | 契约 | 允许单位条目不是字符串 |
| `E_CANON_NOT_IN_ALLOWED` | 契约 | 规范单位不在允许集合 |
| `E_PRECISION_INVALID` | 契约 | 精度不是非负整数 |
| `E_ROUNDING_UNKNOWN` | 契约 | 未知舍入模式 |
| `E_RANGE_INVALID` | 契约 | 范围不是映射、非数值或上下限颠倒 |
| `E_MISSING` | 校验 | 字段值缺失 |
| `E_UNIT_MISSING` | 校验 | 有值但缺单位 |
| `E_NOT_NUMERIC` | 校验 | 值不是有限十进制数 |
| `E_UNIT_NOT_ALLOWED` | 校验 | 单位已注册但契约不允许 |
| `E_OUT_OF_RANGE` | 校验 | 规范单位值超出范围 |
| `E_TEMP_BELOW_ABSOLUTE` | 校验 | 温度低于绝对零度 |
| `W_PRECISION_EXCEEDED` | 校验 | 源小数位超过契约精度（警告，不阻断换算） |

## 5. 支持的量纲与单位

| 量纲 | 基准单位 | 已注册单位（规范单位不必是基准单位） |
|---|---|---|
| length | m | mm, cm, m, km, inch, ft, yd, mile |
| mass | kg | mg, g, kg, t, lb |
| energy | J | J, kJ, kWh, MWh, GJ, cal |
| power | W | W, kW, MW |
| time | s | s, min, h, day |
| volume | L | mL, L, m3 |
| count | count | count（无量纲，仅精度与范围校验） |
| temperature | K | C, K, F（偏移换算，见下节） |

换算因子全部是精确有理数，例如 `inch = 127/5000 m`、`lb = 45359237/100000000 kg`、
`kWh = 3600000 J`。注册表由 `unitcontract/contract/defaults.yaml` 声明。
`cal` 等已注册但未必被契约允许的单位，用于演示 `E_UNIT_NOT_ALLOWED`。

## 6. 温度偏移换算

温度是唯一带偏移的量纲，必须走独立模块 `unitcontract/units/temperature.py`：

- 统一先换到绝对温标 K：`K = C + 273.15`；`K = (F - 32) × 5/9 + 273.15`。
- 再从 K 反向换到目标单位，**不使用比例因子**，避免把偏移量误当倍率。
- 输入换算后若低于绝对零度，判 `E_TEMP_BELOW_ABSOLUTE`。
- `range` 在所有温度字段上按契约的 `canonical_unit`（通常是 C）判断，而不是按原始单位。
- 常数用 `Fraction` 表示（273.15 = 5463/20），避免浮点漂移。

## 7. 目录结构

```
unit-contract/
├─ README.md
├─ requirements.txt
├─ pyproject.toml
├─ CHANGELOG.md
├─ unitcontract/
│  ├─ __main__.py / __init__.py
│  ├─ cli.py                    # 子命令参数与分发
│  ├─ pipeline.py               # 读取→校验→换算→审计的统一流程
│  ├─ compare.py                # 同一基线下两条 overlay 链的对比
│  ├─ demo.py                   # 合成数据与 demo 编排
│  ├─ logging_utils.py          # 控制台 + 本地文件日志
│  ├─ contract/                 # schema.py, loader.py, defaults.yaml
│  ├─ units/                    # registry.py, temperature.py, rounding.py
│  ├─ engine/                   # reader.py, validator.py, converter.py, auditor.py
│  ├─ ledger/storage.py         # SQLite runs / findings
│  ├─ report/                   # json_report.py, md_report.py, html_report.py
│  ├─ server/app.py + static/   # loopback Flask 服务与本地 CSS
│  └─ fixtures/                 # contracts / data / expected golden
└─ tests/                       # 11 个测试文件，全部离线
```

`_demo_out/` 与 `_report_out/` 是运行产物，已在 `.gitignore` 中忽略；
`unitcontract/fixtures/expected/` 的 golden 文件必须入库。

## 8. 台账 schema

单文件 SQLite，默认 demo 路径 `_demo_out/ledger.db`。

**runs**

| 列 | 说明 |
|---|---|
| `run_id` | 由契约哈希、数据哈希、契约名等派生的 16 位稳定 ID |
| `created_at` | UTC 时间；`--no-timestamp` 时为 NULL |
| `contract_name` / `contract_hash` | 契约名与原始 YAML 字节的 SHA-256 |
| `data_hash` / `source_name` | 数据字节哈希与文件名 |
| `data_format` | `csv` / `jsonl` |
| `rows_total` / `passed_rows` / `violation_rows` | 行级统计 |
| `warning_rows` / `error_findings` / `warning_findings` | 警告行与条数 |
| `summary_json` / `contract_json` | 稳定 JSON 快照 |
| `report_dir` | 该 run 的三份报告所在目录 |
| `schema_version` | 台账 schema 版本 `"1"` |

**findings**

| 列 | 说明 |
|---|---|
| `id` / `run_id` | 自增主键与所属 run |
| `row_num` / `field` / `code` / `severity` | 定位与错误码 |
| `message` / `raw_value` / `unit` / `dimension` | 原始上下文 |
| `category` / `cause` / `suggestion` | 固定归因目录中的分类、原因、建议 |

同一 `run_id` 再次落库会原子替换旧 findings，不会重复累积。

## 9. 明确不实现

- 复合单位与派生量纲自动推导（如 `kg·m/s²` → `N`）。
- 货币、汇率与金融口径。
- 时区语义换算（属于时间语义，不属于量纲）。
- 跨文件 schema 推断与自动补契约。
- 流式 / 增量处理与消息队列接入。
- 分布式执行、任务调度。
- 用户体系、鉴权、多租户。
- 云端存储、外部数据库、外部 API、任何联网能力。
- 图形化契约编辑器（本地服务只读展示与下载）。
- 百万行以上数据集的专门性能优化。

## 10. 测试组织与 golden 比对

`python -m pytest -q` 覆盖：

- `test_contract_schema.py`：正例、各错误码反例、默认值与 overlay 合并、YAML 定位。
- `test_units_registry.py`：各量纲单位往返、精确有理因子、未知单位。
- `test_temperature.py`：C/K/F 两两换算、绝对零点与越界、规范单位范围。
- `test_validator.py`：缺失、非数值、单位缺失/未知/不允许、量纲错配、范围、精度警告。
- `test_converter.py`：换算确定性、三种舍入、非法值跳过、温度路径。
- `test_auditor.py`：错误码归因、计数、Top-N、空结果。
- `test_ledger.py`：建表、写入、按 run_id 读回、幂等、删除。
- `test_reports_golden.py`：JSON 片段与完整 Markdown golden；HTML 自包含检查。
- `test_cli_demo.py`：临时目录跑 demo、两次输出逐字节一致、convert / validate / report。
- `test_server_smoke.py`：Flask `test_client` 冒烟，不监听真实端口。

Golden 使用 `--no-timestamp` 生成。运行 ID 与报告中的契约哈希、数据哈希均来自内容，
因此同一契约和输入在任意目录重复运行，三份报告应逐字节一致；时间戳是唯一例外。
Golden 文件位于 `unitcontract/fixtures/expected/`。

## 11. 已知限制

- 只支持单字段单一单位，不支持复合单位、单位幂次与派生量纲。
- 仅支持内置注册表中的单位；扩展需修改 `defaults.yaml` 并补测试。
- CSV 单位列固定为 `<field>__unit`，不支持多表头或合并单元格。
- 精度按源文本字面小数位统计；科学计数法按数量级折算，不做有效数字语义推断。
- 范围比较使用规范单位下的精确有理值，不处理测量不确定度。
- SQLite 台账与报告目录分离，移动目录后需用 `serve --reports-dir` 重新指定报告根目录。
- 服务是单机开发用途的只读视图，不提供并发写入、鉴权或生产部署配置。
