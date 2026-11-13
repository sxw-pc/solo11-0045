# 量纲契约台运行报告

## 运行参数

- Run ID: `a703c6b899208318`
- 契约名称: `synthetic-facility-readings`
- 契约 SHA-256: `a670bce2d9b6ecc5ce5382809871735c760fd262ed6001728bbd7695f7ba7507`
- 数据 SHA-256: `54cc03c096a35a172bedcc55cc52b3aad025c6b2014688c30a6770b152c31315`
- 数据文件: `demo_readings.csv`
- 数据格式: `csv`
- 时间戳: 未启用（--no-timestamp）
- 总行数: 18

## 契约摘要

| 字段 | 量纲 | 规范单位 | 允许单位 | 精度 | 舍入 | 范围 |
| --- | --- | --- | --- | --- | --- | --- |
| energy | energy | J | J, kJ, kWh, MWh, GJ | 2 | half_even | [0, 5000000000] |
| power | power | W | W, kW, MW | 2 | half_even | [0, 2000000] |
| mass | mass | kg | g, kg, t | 3 | half_up | [0, 100000] |
| length | length | m | mm, cm, m, km | 2 | half_even | [0, 10000] |
| temperature | temperature | C | C, K, F | 1 | half_even | [-50, 60] |
| volume | volume | L | mL, L, m3 | 2 | half_even | [0, 5000] |
| count | count | count | count | 0 | half_up | [0, 100000] |

## 统计

| 指标 | 值 |
| --- | --- |
| 总行数 | 18 |
| 通过行数（无 error） | 4 |
| 违规行数 | 14 |
| 含警告行数 | 2 |
| error findings | 15 |
| warning findings | 2 |
| 规范化记录数 | 114 |
| 跳过换算数 | 12 |

## 错误码分布

| 错误码 | 类别 | 数量 | 原因 | 建议 |
| --- | --- | --- | --- | --- |
| E_MISSING | completeness | 2 | A declared field has no value in the source row. | Populate the value or make the field optional upstream. |
| E_NOT_NUMERIC | type | 1 | The source text is not a finite decimal number. | Normalize placeholders such as n/a to empty values or numbers. |
| E_OUT_OF_RANGE | range | 3 | The canonical value lies outside the declared physical range. | Investigate the source reading before accepting the value. |
| E_TEMP_BELOW_ABSOLUTE | temperature | 2 | The temperature is below absolute zero after affine conversion. | Check the sign, unit label and sensor calibration. |
| E_UNIT_DIM_MISMATCH | dimension | 1 | The unit exists but measures a different physical dimension. | Correct the unit or move the value to the matching field. |
| E_UNIT_MISSING | metadata | 1 | A value is present but its unit is absent. | Add a unit column or nested unit object to the record. |
| E_UNIT_NOT_ALLOWED | contract | 4 | The unit is registered but excluded by the field contract. | Convert upstream or extend allowed_units deliberately. |
| E_UNIT_UNKNOWN | unit | 1 | The unit is not in the local registry. | Use a registered unit or add an explicit catalogue entry. |
| W_PRECISION_EXCEEDED | precision | 2 | The source has more decimal places than the contract allows. | Confirm this is intentional; output will be rounded deterministically. |

## 违规明细

| 行 | 字段 | 错误码 | 级别 | 原始值 | 单位 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| 2 | mass | E_UNIT_NOT_ALLOWED | error | 500 | lb | unit lb is registered but not allowed by this contract |
| 2 | length | E_UNIT_NOT_ALLOWED | error | 12 | inch | unit inch is registered but not allowed by this contract |
| 3 | energy | E_OUT_OF_RANGE | error | 1000 | GJ | canonical value 1000000000000 J exceeds maximum 5000000000 |
| 4 | energy | E_MISSING | error |  | kWh | value is missing |
| 5 | energy | E_UNIT_NOT_ALLOWED | error | 12 | cal | unit cal is registered but not allowed by this contract |
| 6 | energy | W_PRECISION_EXCEEDED | warning | 1.234 | kWh | 3 decimal places exceed declared precision 2 |
| 7 | temperature | E_TEMP_BELOW_ABSOLUTE | error | -5 | K | -5 K is below absolute zero |
| 8 | temperature | E_TEMP_BELOW_ABSOLUTE | error | -300 | C | -300 C is below absolute zero |
| 9 | temperature | E_OUT_OF_RANGE | error | 85.0 | C | canonical value 85 C exceeds maximum 60 |
| 10 | length | E_UNIT_UNKNOWN | error | 3 | furlong | unit furlong is not in the local registry |
| 11 | length | E_UNIT_MISSING | error | 3 |  | unit is missing; declare a unit column or nested unit |
| 12 | volume | E_UNIT_DIM_MISMATCH | error | 3 | kg | unit kg belongs to mass, not volume |
| 13 | mass | E_NOT_NUMERIC | error | n/a | kg | value cannot be parsed as a finite decimal number |
| 14 | count | E_OUT_OF_RANGE | error | 200000 | count | canonical value 200000 count exceeds maximum 100000 |
| 15 | power | E_MISSING | error |  | kW | value is missing |
| 17 | mass | E_UNIT_NOT_ALLOWED | error | 500 | lb | unit lb is registered but not allowed by this contract |
| 18 | energy | W_PRECISION_EXCEEDED | warning | 1.2345 | kWh | 4 decimal places exceed declared precision 2 |

## 规范化值

| 行 | 字段 | 原始值 | 原始单位 | 换算值 | 规范单位 |
| --- | --- | --- | --- | --- | --- |
| 1 | energy | 12.50 | kWh | 45000000.00 | J |
| 1 | power | 2.50 | kW | 2500.00 | W |
| 1 | mass | 3.250 | kg | 3.250 | kg |
| 1 | length | 1.25 | m | 1.25 | m |
| 1 | temperature | 21.5 | C | 21.5 | C |
| 1 | volume | 12.50 | L | 12.50 | L |
| 1 | count | 5 | count | 5 | count |
| 2 | energy | 1 | MWh | 3600000000.00 | J |
| 2 | power | 900 | W | 900.00 | W |
| 2 | temperature | 70.0 | F | 21.1 | C |
| 2 | volume | 2 | m3 | 2000.00 | L |
| 2 | count | 10 | count | 10 | count |
| 3 | energy | 1000 | GJ | 1000000000000.00 | J |
| 3 | power | 800 | kW | 800000.00 | W |
| 3 | mass | 7.500 | kg | 7.500 | kg |
| 3 | length | 2.50 | m | 2.50 | m |
| 3 | temperature | 19.0 | C | 19.0 | C |
| 3 | volume | 40.00 | L | 40.00 | L |
| 3 | count | 8 | count | 8 | count |
| 4 | power | 1.20 | kW | 1200.00 | W |
| 4 | mass | 2.000 | kg | 2.000 | kg |
| 4 | length | 3.00 | m | 3.00 | m |
| 4 | temperature | 18.5 | C | 18.5 | C |
| 4 | volume | 9.00 | L | 9.00 | L |
| 4 | count | 2 | count | 2 | count |
| 5 | power | 2.50 | kW | 2500.00 | W |
| 5 | mass | 2.000 | kg | 2.000 | kg |
| 5 | length | 4.00 | m | 4.00 | m |
| 5 | temperature | 22.0 | C | 22.0 | C |
| 5 | volume | 8.00 | L | 8.00 | L |
| 5 | count | 4 | count | 4 | count |
| 6 | energy | 1.234 | kWh | 4442400.00 | J |
| 6 | power | 2.50 | kW | 2500.00 | W |
| 6 | mass | 2.000 | kg | 2.000 | kg |
| 6 | length | 5.00 | m | 5.00 | m |
| 6 | temperature | 23.0 | C | 23.0 | C |
| 6 | volume | 7.00 | L | 7.00 | L |
| 6 | count | 6 | count | 6 | count |
| 7 | energy | 5.00 | kWh | 18000000.00 | J |
| 7 | power | 1.00 | kW | 1000.00 | W |
| 7 | mass | 2.000 | kg | 2.000 | kg |
| 7 | length | 6.00 | m | 6.00 | m |
| 7 | volume | 6.00 | L | 6.00 | L |
| 7 | count | 3 | count | 3 | count |
| 8 | energy | 5.00 | kWh | 18000000.00 | J |
| 8 | power | 1.00 | kW | 1000.00 | W |
| 8 | mass | 2.000 | kg | 2.000 | kg |
| 8 | length | 6.00 | m | 6.00 | m |
| 8 | volume | 6.00 | L | 6.00 | L |
| 8 | count | 3 | count | 3 | count |

仅显示前 50 条，共 114 条规范化记录。

## Golden 说明

Golden 输出通过 `--no-timestamp` 生成；相同契约、相同输入与相同参数应得到逐字节一致的报告。时间戳是唯一允许变化的内容。
