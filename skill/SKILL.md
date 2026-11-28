---
name: health-db
description: 建立和使用「个人健康数据库」：用 SQLite 单文件库（一人一库）存血压血糖、体重、日指标、化验、处方与检查结果，按参考区间做规则监测（只提示、不诊断），生成离线健康看板与周报，导出 FHIR R4 供开源医疗项目接入。也负责把手机截图、化验单、处方/检查单识别入库（认不准或无可见日期一律停「待确认」，不猜不编）。触发词：健康数据库、健康档案、建健康库、健康看板、健康周报、记录血压、记录血糖、记录体重、步数、睡眠、心率、化验单入库、检查报告入库、处方识别、单据识别、FHIR 导出、给医生看。
---

# 健康数据库（Health DB）

一个不依赖任何云服务的个人健康数据工具：**数据在自己电脑上的一个 SQLite 文件里**，脚本把截图/单据/一句话变成结构化记录，再渲染成可以直接给医生看的离线 HTML。

- 指标定义在 `overview/schema.json` 与 `overview/看板定义.json`，代码里不写死任何人的数据。
- 判定只给「规则提示」（参考区间 / 个人目标），**不做诊断**，最终以主诊医生意见为准。
- 全程离线：除你自己选择的备份动作外，不上传任何数据。

## 1. 目录

```
health-db-skill/
├─ tools/        采集与入库脚本（健康库.py 是唯一数据层）
├─ overview/     schema.json、看板定义.json、总览模板.html、FHIR 映射
├─ data/         指标字典.json（你自己的库与产物放这里）
├─ demo/         虚构演示库与生成脚本（不含任何真实数据）
├─ skill/        本技能正文与 references/
└─ docs/         使用说明与主页
```

## 2. 三分钟开始

```bash
# ① 建自己的空库（25 张标准表，姓名只写进 meta，不影响别人）
python tools/健康库.py init data/医疗与运动健康数据库.db "你的名字"

# ② 把指标字典写进库（参考区间、单位、LOINC 都从这里来）
python tools/指标字典导入.py

# ③ 出第一份看板（离线 HTML，双击就能看）
python tools/健康档案看板.py --out data/看板.html
```

空库也能出页面（显示 0 组 0 行，不报错）。要看演示效果，直接打开 `demo/演示看板.html`。

> 一人一库：给别人建库就把 `.db` 放到他自己的目录，用环境变量 `HEALTH_DB` 指向它，或用 `--db` 参数；脚本只读你指的那一份，互不串。

## 3. 录入

四种输入，四条命令，写库前都会先查台账去重。

| 输入 | 命令 |
|---|---|
| 一句话 / 语音转写 | `python tools/健康文字录入.py --text "今天早上八点血压128/82，心率76"` |
| 手机截图（血压/血糖/体重/步数/睡眠/心率） | `python tools/健康截图入库.py --file "<图片>" --no-cloud` |
| 化验单 / 检验单 | `python tools/化验单入库.py --file "<图片>"` |
| 处方 / 检查单 / 缴费单 | `python tools/单据识别.py --file "<图片>"` |

- `--dry-run` 只识别不写盘，先看结果再决定入不入库。
- 识别不出的字段留空，**不猜、不编**；单据上没有的日期不要用今天顶替。
- 查某张单是否已入库：`python tools/单据台账.py check "<日期或流水号或药名>"`；重建台账：`python tools/单据台账.py build`。
- 截图入库用的 OCR 需要 `onnxruntime`（`pip install onnxruntime`）；装在别的解释器里时，用环境变量 `HEALTH_OCR_PY` 指过去。
- 一类资料一类表：新指标用 `python tools/健康指标扩展.py add --category 血氧 --value 96 --unit % --ref "95–100"`，看板会自动多出一行与时间线。

### 3.1 运动与穿戴数据

- 手表整包：`python tools/华为导出入库.py --zip "<HUAWEI_HEALTH.zip>" --apply` → `daily`（睡眠/静息心率/平均心率/血氧/体温/步数/活动热量/锻炼分钟/活动小时）+ `wearable_daily`（原始明细）；解压目录用 `--dir`。
- 截图也行：活动/运动页截图走 `健康截图入库.py`，逐项解析活动热量、锻炼时长、活动小时。
- 口径写死：睡眠「间隔 > 30 分钟断开」聚会话、归醒来那天、午睡不计；静息心率取当日最低；血氧取当日均值；步数/热量/锻炼分钟取每日目标达成记录；活动小时按 `isActive` 计。
- 单次运动进 `exercise` 表（`date, kind, minutes, kcal, note`）。
- 专门页面：`穿戴数据看板.py`（睡眠/心率/血氧/压力/活动/体重 六件事）、`华为数据总览.py`（总量与分类明细）。

## 4. 看板与周报

```bash
python tools/健康档案看板.py --out data/看板.html      # 按身体系统分组 + 区间标尺 + 三色提示
python tools/健康周报.py --out data/周报.html         # 一周一页：达标率、变化、待办
```

- 看板一行 = 一个指标：最新值、参考区间、标尺位置、是否越界；数据来自 `schema.json` + `看板定义.json`，加指标只改这两份。
- 周报默认取库里最近 7 天；数据没跟上今天时按最后一条数据往回数，不硬凑空日子。
- 两个页面都是单文件 HTML（内嵌 CSS/SVG，无外链），断网可看，直接发给医生本人。

### 4.1 病程时间线与手机访问

```bash
python tools/timeline_report.py --db data/xxx.db --out data/病程时间线.html
python tools/timeline_report.py --db data/xxx.db --pdf-mobile --pdf-out data/病程时间线-手机版.pdf
python tools/timeline_server.py --dir data          # 同 WiFi 手机只读访问 HTML/PDF
```

- 时间线只读不写库：就诊 / 病理 / 影像 / 化验 / 用药 / 中药 / 诊断 / 随访计划与日常测量按时间聚合；姓名默认读库 `profile.name` / `HEALTH_NAME`，可用 `--name` 覆盖，不内置任何真实病历。
- `--pdf-mobile` 出 390px 窄纸 PDF（需本机 Microsoft Edge）；手机转发医生优先用 PDF（微信对本地 HTML 打开有限制）。
- `timeline_server.py` 是纯标准库局域网只读服务：白名单路由（`/`、`/pdf`），无目录列表、不按 URL 拼路径、不写盘，默认绑定 `0.0.0.0:8910`（被占自动顺延），关窗即停；仅限可信局域网，不做公网映射。

## 5. 导出 FHIR

```bash
python tools/导出FHIR.py --out data/fhir       # 六类资源：Patient / Observation / Condition / MedicationStatement / DiagnosticReport / Encounter
python tools/健康数据库.py fhir --out data/fhir.json
```

导出的 JSON 可直接喂给支持 FHIR R4 的开源项目（如 Fasten Health、Open Health 生态）。映射表在 `overview/fhir映射.json`，字段对不上就改这份，不动代码。

## 6. 换人与扩展

- **换人**：新建一个 `.db` + 自己的档案卡（`python tools/健康问诊.py --name 张三`），脚本一套不变。
- **加指标**：`overview/schema.json` 的 `indicators` 加一行（key、单位、取自哪张表哪列、参考区间、方向），再跑 `指标字典导入.py`。
- **加分组**：`overview/看板定义.json` 的 `分组` 里加一组，看板立即生效。
- **加表/加列**：`python tools/健康库.py add-table <表> <列:类型,...>`、`add-column <表> <列> <类型>`，不改业务代码。
- 改完模板或看板，跑 `python tools/看板一致性校验.py <基线.html> <新.html>` 确认只差数据、不差版式。

## 7. 边界与隐私

- 数据只在本机文件里；脚本默认不联网。想备份就自己复制 `.db` 或放进自己的网盘目录。
- 只提示、不诊断：所有「需留意」都是规则判定，不是医疗结论。
- 不猜数据：识别不出、图上没日期、字段缺失，一律留空或进「待确认」。
- 演示数据是虚构的（`demo/`），截图与页面里没有任何真人信息。

## 8. 参考件

| 文件 | 内容 |
|---|---|
| `references/单据判定表.md` | 八类单据怎么认（日常测量 / 化验 / 影像 / 病理 / 医保结算 / 费用清单 / 中药方 / 症状文字） |
| `references/库结构与扩展.md` | 单文件库的表与列、扩展规则、华为导出包入库 |
| `references/看板规范.md` | 看板生成器拆分、模板与校验口径 |
| `references/截图入库细则.md` | 入库六步、四种输入、看图流程、验收清单 |
