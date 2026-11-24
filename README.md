<div align="center">
<img src="docs/icon.png" alt="health-db 个人健康数据库" height="120" width="120">

<h1>health-db · 个人健康数据库技能</h1>

<p>把散落在手机截图、化验单、处方里的健康数据，收进一个属于你自己的 SQLite 文件，<br>再变成能给医生看的离线看板与周报。数据不出本机。</p>

<a href="https://github.com/kele551/health-db-skill/releases"><img alt="Downloads" src="https://img.shields.io/github/downloads/kele551/health-db-skill/total?style=flat-square&label=downloads&color=blue" height="30"></a>
<a href="https://github.com/kele551/health-db-skill"><img alt="浏览量" src="https://hits.sh/github.com/kele551/health-db-skill.svg?label=%E6%B5%8F%E8%A7%88%E9%87%8F&color=2563eb&style=flat-square" height="30"></a>

<a href="https://github.com/kele551/health-db-skill/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-MIT-blue?style=flat-square"></a>
<img alt="Platform" src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-blue?style=flat-square">
<img alt="Version" src="https://img.shields.io/badge/%E7%89%88%E6%9C%AC-v1.0-blue?style=flat-square&labelColor=3d4552">
<img alt="Last Commit" src="https://img.shields.io/github/last-commit/kele551/health-db-skill?style=flat-square&label=last%20commit&color=blue">

<a href="https://github.com/kele551/health-db-skill/stargazers"><img alt="GitHub Star" src="https://img.shields.io/github/stars/kele551/health-db-skill?style=flat-square&logo=github&label=GitHub%20Star&color=gold"></a>
<a href="https://github.com/kele551/health-db-skill/network/members"><img alt="GitHub Fork" src="https://img.shields.io/github/forks/kele551/health-db-skill?style=flat-square&label=GitHub%20Fork&color=blue"></a>
<a href="https://github.com/kele551"><img alt="Follow Author" src="https://img.shields.io/github/followers/kele551?style=flat-square&logo=github&label=follow%20author&color=blue"></a>

<img alt="Category" src="https://img.shields.io/badge/%E5%88%86%E7%B1%BB-%E5%81%A5%E5%BA%B7%E6%95%B0%E6%8D%AE-blue?style=flat-square&labelColor=3d4552">
<img alt="Form" src="https://img.shields.io/badge/%E5%BD%A2%E6%80%81-%E5%BC%80%E6%BA%90%E6%8A%80%E8%83%BD%E5%8C%85-blue?style=flat-square&labelColor=3d4552">
<img alt="Free" src="https://img.shields.io/badge/%E8%B4%B9%E7%94%A8-%E5%85%8D%E8%B4%B9%E6%97%A0%E5%B9%BF%E5%91%8A-brightgreen?style=flat-square&labelColor=3d4552">
<img alt="No Tracking" src="https://img.shields.io/badge/%E7%94%A8%E6%88%B7%E6%95%B0%E6%8D%AE-%E4%B8%8D%E9%87%87%E9%9B%86%E4%B8%8D%E4%B8%8A%E4%BC%A0-green?style=flat-square&labelColor=3d4552">

<a href="https://github.com/kele551/health-db-skill/releases"><img alt="Download from GitHub" src="https://img.shields.io/badge/%F0%9F%93%A5%20%E4%B8%8B%E8%BD%BD-GitHub%E5%8F%91%E8%A1%8C-c71d24?style=flat-square&labelColor=3d4552&logo=github&logoColor=white"></a>
<a href="https://github.com/kele551/health-db-skill"><img alt="GitHub source" src="https://img.shields.io/badge/%F0%9F%90%99%20%E6%BA%90%E7%A0%81-GitHub%E4%B8%BB%E6%BA%90-24292f?style=flat-square&labelColor=3d4552&logo=github&logoColor=white"></a>
<a href="https://kele551.github.io/health-db-skill/"><img alt="Live demo" src="https://img.shields.io/badge/%F0%9F%93%BA%20%E6%BC%94%E7%A4%BA-%E5%9C%A8%E7%BA%BF%E4%B8%BB%E9%A1%B5-00a1d6?style=flat-square&labelColor=3d4552"></a>
<a href="https://github.com/kele551/health-db-skill/issues"><img alt="Issues" src="https://img.shields.io/badge/%F0%9F%90%9E%20%E5%8F%8D%E9%A6%88-%E6%8F%90Issue-0969da?style=flat-square&labelColor=3d4552"></a>
<a href="#支持项目"><img alt="Sponsor" src="https://img.shields.io/badge/%E2%98%95%20%E8%B5%9E%E8%B5%8F-%E8%AF%B7%E5%96%9D%E5%92%96%E5%95%A1-ff69b4?style=flat-square&labelColor=3d4552"></a>

<p>🏥 <b>个人健康数据库技能</b>　·　截图 / 化验单 / 一句话入库，看板与周报离线可看</p>

<p>作者 <b>海风（kele551）</b>　·　<a href="https://github.com/kele551/health-db-skill">GitHub（主）</a>　·　<a href="https://gitcode.com/kele551/health-db-skill">GitCode（镜像）</a>　·　<a href="https://kele551.github.io/health-db-skill/">在线主页</a></p>
</div>

## 这是什么

一套「个人健康数据」工具 + 一个 Agent 技能（Claude Code / Codex / 豆包等支持 `SKILL.md` 的客户端都能用）：

- **一人一库**：一个 SQLite 文件（25 张标准表），姓名只写在 `meta`，换人换库，互不串。
- **四种录入**：一句话、手机截图、化验单、处方/检查单；认不准就停「待确认」，不猜不编。
- **规则提示**：按参考区间与个人目标标出越界项，只说「需留意」，**不做诊断**。
- **两个页面**：系统分组看板 + 一周周报，单文件 HTML（内嵌 CSS/SVG），断网可看，直接发给医生。
- **FHIR R4**：导出 Patient / Observation / Condition / MedicationStatement / DiagnosticReport / Encounter，接得上开源医疗项目。
- **全程离线**：脚本默认不联网；要不要备份、备份到哪，由你自己决定。

> 演示数据全部虚构。仓库里没有、也不会有任何真人的病历。

**关键词**：个人健康数据库 / 个人健康档案 / 健康数据管理 / 健康看板 / 健康周报 / 运动数据 / 步数 / 睡眠 / 心率 / 血氧 / 穿戴数据 / 华为运动健康导出包 / 化验单识别 / 处方识别 / 单据入库 / 血压血糖体重记录 / FHIR R4 / Agent Skill / Claude Code Skill / SQLite 单文件 / 离线 / 隐私优先 / self-hosted health record / personal health record / health dashboard / health data ingestion / wearable data / FHIR export

## 长这样

| 健康看板 | 健康周报 |
|---|---|
| ![看板](docs/images/看板.png) | ![周报](docs/images/周报.png) |

## 快速开始

```bash
git clone https://github.com/kele551/health-db-skill.git
cd health-db-skill

# ① 建自己的空库
python tools/健康库.py init data/医疗与运动健康数据库.db "你的名字"

# ② 写入指标字典（单位 / 参考区间 / LOINC）
python tools/指标字典导入.py

# ③ 出第一份看板
python tools/健康档案看板.py --out data/看板.html
```

想先看效果，不建库也行：

```bash
python demo/make_demo.py      # 生成虚构演示库 + 演示看板 + 演示周报
```

## 录入

| 你手上是什么 | 命令 |
|---|---|
| 一句话 / 语音转写 | `python tools/健康文字录入.py --text "今天早上八点血压128/82，心率76"` |
| 手机截图（血压 / 血糖 / 体重 / 步数 / 睡眠 / 心率） | `python tools/健康截图入库.py --file "<图片>" --no-cloud` |
| 化验单 / 检验单 | `python tools/化验单入库.py --file "<图片>"` |
| 处方 / 检查单 / 缴费单 | `python tools/单据识别.py --file "<图片>"` |
| 新的一类指标 | `python tools/健康指标扩展.py add --category 血氧 --value 96 --unit % --ref "95–100"` |

- `--dry-run` 只识别不写盘；`单据台账.py check "<日期/流水号/药名>"` 查重，避免同一张单入两遍。
- OCR 依赖 `onnxruntime`（`pip install onnxruntime`）；装在别的解释器里时用 `HEALTH_OCR_PY` 指过去。

### 运动与穿戴数据

手表的运动/活动数据不用一屏一屏截图，两条通道都行：

```bash
# ① 华为运动健康导出包（HUAWEI_HEALTH.zip）整包入库：睡眠、心率、血氧、体温、步数、活动热量、锻炼分钟、活动小时
python tools/华为导出入库.py --zip "<HUAWEI_HEALTH.zip>" --apply

# ② 手机里的活动 / 运动截图（步数、活动热量、锻炼时长、活动小时）
python tools/健康截图入库.py --file "<截图>" --no-cloud
```

- 落库口径：睡眠按「间隔 > 30 分钟断开」聚会话、归到醒来那天；步数 / 活动热量 / 锻炼分钟取每日目标达成记录；静息心率取当日最低；血氧取当日均值；活动小时按 `isActive` 的小时数计。
- 数据去向：每日活动量进 `daily`（步数、睡眠、静息心率、活动热量、中高强度、活动小时、距离）；原始明细进 `wearable_daily`（类型、指标、均值/极值、首次/末次时间）；单次运动进 `exercise`（日期、项目、分钟、千卡）。
- 专门的运动/穿戴页面：

```bash
python tools/穿戴数据看板.py --out data/穿戴看板.html   # 睡眠 / 心率 / 血氧 / 压力情绪 / 活动 / 体重 六件事
python tools/华为数据总览.py --out data/数据总览.html   # 总量、分类明细、指标清单
```

## 页面与导出

```bash
python tools/健康档案看板.py --out data/看板.html     # 系统分组 + 区间标尺 + 三色提示
python tools/健康周报.py --out data/周报.html        # 一周达标率、变化、待办
python tools/导出FHIR.py --out data/fhir            # FHIR R4 六类资源
```

页面是纯本地文件：内嵌 CSS 与自写 SVG，没有外部 CDN、没有统计脚本。

## 作为 Agent 技能安装

`skill/` 就是技能目录（`SKILL.md` + 4 份 references）。把它放进客户端的技能目录即可：

```bash
# Claude Code
cp -r skill ~/.claude/skills/health-db

# 其它客户端：把 skill/ 放进它的 skills 目录，或直接让 Agent 读 SKILL.md 的绝对路径
```

装好后可以直接说：「把这张化验单记进健康库」「出这周的周报」「导一份 FHIR 给 Fasten Health」。

## 目录

```
tools/      采集与入库脚本（健康库.py 是唯一数据层）
overview/   schema.json、看板定义.json、总览模板.html、FHIR 映射
data/       指标字典.json；你自己的库与产物放这里
demo/       虚构演示库与生成脚本
skill/      技能正文 + references
docs/       使用说明、主页、截图
```

## 隐私与边界

- 数据只在你自己的磁盘上；脚本不埋点、不上传、不请求任何第三方接口。
- 所有判定都是规则计算（参考区间 / 个人目标），**不是医疗建议**；诊断与用药以主诊医生为准。
- 识别不出的字段留空，缺日期不用今天顶替，宁可「待确认」也不编。
- 想备份就复制 `.db`；仓库与代码里不写任何人的姓名、医院、病案号。

## 常见问题

**Q：能不能多人共用一个库？**
不建议。一人一个 `.db`，用 `HEALTH_DB` 或 `--db` 指向各自的库，天然隔离。

**Q：数据从哪来？**
自己测的（血压计/血糖仪/体重秤）、手表的（华为/小米等导出包）、医院的（化验单/处方/检查单）。截图识别后写库，原始图自己留存。

**Q：为什么不内置就诊摘要？**
摘要版式与病种强相关，天然个性化。这个仓库给的是数据层、看板与周报；摘要按 `references/看板规范.md` 的口径自建模板即可。

## 支持项目

如果这套工具帮你省了整理化验单的时间，可以请我喝杯咖啡（微信扫码，金额随意，不影响任何功能）：

<p align="center"><img src="docs/qr-sponsor.png" alt="海风的微信赞赏码" width="240"></p>

也欢迎点个 star、提 Issue，或者把它推荐给需要长期记录指标的人。

## 许可

MIT © 海风（kele551）
