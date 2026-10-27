# SmartFileOrganizer v7 —— 智能归档与任务助手

本地运行、可解释、可撤销的文件归档工具。它做两件事：

1. **归档**：学习你现有资料库的目录结构，读取新文件的**文件名 + 正文**，把它放进最合适的目录；
2. **出任务**：从正文里识别期限、文号、金额、责任主体，生成任务卡，提醒你建待办、排计划。

不联网、不上传文件、不依赖云服务。核心逻辑零第三方依赖，装好 Python 就能跑。

> 这是对 v6.2（单文件 2103 行）的重写版。v6.2 的源码保留在 git 标签 `v6.2`，可随时检出对照。

---

## 一、为什么重写，而不是接着改

v6.2 的骨架（监听、扫描、GUI）是好的，但几个问题让它很难真正投入使用：

| v6 的问题 | v7 的做法 |
|---|---|
| 只读文件名，不读正文；"扫描件001.pdf" 无法分类 | `docx/xlsx/pptx/txt/pdf` 正文抽取，文件名清洗后与正文一起进特征 |
| 矫正弹窗用移动前的旧路径判断，实际永远打不开 | 判定与移动解耦：待确认队列存在 SQLite，重启不丢 |
| 每学一条就全量重编码，越用越卡；启动也全量编码 | 稀疏倒排 TF-IDF，重建毫秒级；不再调用大模型编码 |
| 自动模式开关是摆设，未确认也会移动，且无法撤销 | 三模式（观察/先确认/自动）+ 移动前校验 + journal 撤销 |
| 下载中的 `.crdownload` 会被搬走 | 临时后缀过滤 + 大小/修改时间双次稳定 + 独占检测 |
| JSON 非原子写、无单实例，可能静默丢数据 | SQLite WAL + 原子配置 + 单实例锁 |
| 没有任务/提醒，最终目标没落地 | 任务卡 + 待办状态机 + 到期提醒 + ICS 日历导出 |

---

## 二、借鉴了哪些开源项目

做这一版之前对比了同类项目，取的是「机制」而不是代码：

| 项目 | 借鉴点 | 在本项目中的落点 |
|---|---|---|
| [paperless-ngx](https://github.com/paperless-ngx/paperless-ngx) | 消费目录（consume）、校验和去重、审计日志、按规则打标签/归档 | `journal` 操作日志、SHA256 隔离区、待确认队列 |
| [organize](https://github.com/tfeldmann/organize) | 规则引擎 + `sim` 预演，先看结果再执行 | 「仅观察」模式、`classify --dry-run` |
| [ai-file-organizer](https://github.com/pneelamr/ai-file-organizer) / [classifai](https://github.com/fjacquet/classifai) | 本地模型分类 + 人工复核，而不是全自动黑箱 | 低置信度进人工队列；默认本地 TF-IDF，可选更强的 ST 后端 |
| [paperless-ai](https://github.com/DHDDS/paperless-ai) | 用 LLM 补标题/标签，但保留人工确认 | 任务卡只做「候选」，确认后才进待办 |
| [beets](https://github.com/beetbox/beets) / [calibre](https://github.com/kovidgoyal/calibre) | 元数据驱动的库管理：先有 schema，再有自动化 | 文档表 + 类目树 + 任务表的数据模型 |
| Hazel / DropIt（商业软件） | 监听目录 + 条件动作 + 预览 | 轮询监听 + 判定策略 + 待确认列表 |

一个共同的教训：**这类工具失败通常不是因为"不够智能"，而是因为"移错了还撤不回来"**。所以 v7 默认是「先确认」，自动模式要显式开启，且所有移动都能撤销。

---

## 三、核心能力

### 1. 读正文，而不是猜文件名

- 支持 `.txt/.md/.csv/.log/.json`、`.docx`、`.xlsx`、`.pptx`、`.pdf`（需 `pypdf`）
- 抽不到就自动降级为「文件名 + 扩展名」，绝不因单个文件失败中断整批
- 文件名先清洗：去掉日期、`V2`、`最终版`、`(1)`、`扫描件` 等噪声
- 大文件/超页数有上限；扫描件 PDF 会明确提示"需 OCR"

### 2. 分类：轻量、可解释、增量

- 字符级 1-gram + 2-gram TF-IDF，稀疏倒排索引；几千份文档重建约 1 秒，单次预测毫秒级
- 训练与推理共用同一个特征函数（避免 v6 那种分布错位）
- 输出 Top-5 目录 + 相似度 + 匹配到的历史文件，方便判断"为什么是这个目录"
- 可选 `sentence-transformers` 后端（`model_backend: "st"`），不装也能用

### 3. 三种模式，默认不冒险

| 模式 | 行为 | 适用 |
|---|---|---|
| 仅观察 `observe` | 只记录建议，文件不动 | 第一次接入、评估准确率 |
| 先确认 `suggest`（默认） | 进「待确认」列表，点确认才移动 | 日常使用 |
| 自动归档 `auto` | 高分 + 与第二名有间隔才移动，可撤销 | 稳定后提效 |

### 4. 安全移动

- 复制到目标盘临时文件 → 校验 SHA256 → 同卷 `os.replace` 原子改名 → 记 journal → 再删源
- 跨盘、网络盘失败不会留下"半截文件"
- 目标路径严格限制在资料库内（拒绝绝对路径、`..`、符号链接逃逸）
- 重复文件只隔离、不删除；隔离区按日期分目录
- 「归档记录」里可撤销最近一次移动

### 5. 任务中心（v6 没有的部分）

从正文中抽取：

| 信号 | 例子 |
|---|---|
| 明确期限 | 「请于 2026 年 12 月 31 日前完成整改」 |
| 相对期限 | 「30 日内」「3 个工作日内」「本月底前」 |
| 文号 | 「眉府〔2026〕15 号」 |
| 金额 | 「合同金额：120 万元」 |
| 主体 | 「甲方：…」「乙方：…」 |
| 动作 | 验收 / 付款 / 反馈 / 报送 / 整改 / 签订… |

生成候选任务卡（标题、类型、截止日、优先级、证据片段、来源文件），确认后进入待办；
支持「今日 / 本周 / 逾期 / 候选 / 全部」视图，可导出 `.ics` 到 Outlook / 手机日历。

---

## 四、快速开始

要求：Windows 10/11，Python 3.10+（已在 3.12 测试）。

```bat
cd /d F:\SmartFileOrganizer
python main.py
```

首次启动：

1. 打开「设置」，选择**资料库根目录**（你现有分类好的文件夹，例如 `G:\...\公司资料`）；
2. 添加**监听目录**（例如 `C:\Users\你\Downloads`），注意不能与资料库互相包含；
3. 保存 → 到「训练与评测」点「扫描资料库并训练」；
4. 点「留出集评测」，看 Top-1 / 推荐阈值；建议先把模式设为「仅观察」跑一天；
5. 确认建议靠谱后切到「先确认」，稳定后再考虑「自动归档」。

可选增强（正文抽取）：

```bat
python -m pip install -r requirements.txt
```

不安装也能运行，只是 Office/PDF 文件只能用文件名分类。

命令行：

```bat
python main.py --data-dir D:\sfo-data status
python main.py scan
python main.py eval
python main.py classify "D:\Downloads\xx.pdf" --dry-run
python main.py tasks --scope week
python main.py undo
```

---

## 五、目录结构

```
SmartFileOrganizer/
├── main.py                 入口（无参数=GUI，有参数=CLI）
├── sfo/
│   ├── config.py           配置、路径校验、原子保存
│   ├── db.py               SQLite（doc/label/pending/task/journal）
│   ├── textutil.py         文件名清洗、中文分词、哈希
│   ├── extract.py          正文抽取（docx/xlsx/pptx/pdf/txt）
│   ├── classifier.py       TF-IDF 稀疏模型 + 可选 ST 后端 + 评测
│   ├── policy.py           分数 → 动作（auto/suggest/observe）
│   ├── mover.py            校验移动、撤销、隔离
│   ├── watcher.py          轮询监听 + 就绪检测
│   ├── tasks.py            期限/文号/金额抽取、ICS
│   ├── service.py          后台编排（单 worker 串行）
│   ├── ui.py               Tkinter 六页签界面
│   └── cli.py              命令行
├── tests/                  47 个 unittest（配置/分类/抽取/移动/任务/端到端）
├── requirements.txt        可选正文抽取依赖
├── 启动程序.bat / 命令行.bat
└── scripts/build_exe.bat   PyInstaller 打包（需先 pip install pyinstaller）
```

数据目录默认 `%LOCALAPPDATA%\SmartFileOrganizer`：

```
config.json     设置
sfo.db          SQLite（文档、样本、待确认、任务、journal）
logs\app.log    滚动日志
reports\        评测报告
quarantine\     兜底隔离区（资料库里的「重复文件」优先）
```

> 不放在 `Documents`，避免被 OneDrive 同步公司文件，也避免同步客户端锁库。

---

## 六、配置说明（config.json）

| 键 | 默认 | 说明 |
|---|---|---|
| `watch_dirs` | `[]` | 监听目录，可多个 |
| `library_root` | `""` | 资料库根目录（分类目标） |
| `mode` | `suggest` | observe / suggest / auto |
| `auto_threshold` | 0.55 | 自动归档的最低相似度 |
| `suggest_threshold` | 0.30 | 进入待确认的最低相似度 |
| `margin` | 0.05 | 第一名需领先第二名的分差 |
| `include_content` | true | 是否抽取正文 |
| `model_backend` | `tfidf` | `tfidf` / `st` |
| `poll_interval` | 2.0 | 轮询间隔（秒） |
| `duplicate_action` | `quarantine` | 重复文件隔离 / 忽略 |
| `task_reminder_days` | `[7,3,1,0]` | 预留：提醒提前量 |

阈值不要凭感觉定：先跑「留出集评测」，用「应用评测阈值」把自动阈值调到"精度 ≥95% 时覆盖率最高"的位置。

---

## 七、从 v6.2 迁移

v6 的数据在 `%USERPROFILE%\Documents\SmartOrganizerData\learning_data.db`（其实是 JSON）。

- GUI：「训练与评测」→「导入 v6 数据」→ 选择该文件；
- CLI：`python main.py import-v6 "C:\Users\你\Documents\SmartOrganizerData\learning_data.db"`。

导入后只保留「文件名 → 目录」映射，正文需要重新扫描一次资料库才能补上。

---

## 八、已知限制

1. **扫描件/图片 OCR 未内置**：纯图片 PDF 只能靠文件名；后续计划接 Windows OCR（零额外依赖）或 PaddleOCR。
2. `.doc`、`.wps`、`.dwg` 不抽正文；`.doc` 需先另存为 `.docx`。
3. 任务抽取是规则式的，对自由格式的长合同可能漏；可在 `sfo/tasks.py` 的 `DOC_TYPE_RULES` / `DEADLINE_HINTS` 里扩展。
4. 监听是轮询（默认 2 秒），不是实时事件；这是为了避开网络盘漏事件和下载未完成。监听**不递归子目录**，只需要盯下载/收件目录即可。
5. 未做安装包签名、自动更新、系统托盘；`scripts/build_exe.bat` 只提供最小打包流程。
6. ST 后端需要自行安装 `sentence-transformers` 并下载模型，未随包提供；首次使用会明显变慢。
7. 任务提醒在应用内显示（日志 + 状态栏 + 任务中心），暂未接 Windows 系统通知。

---

## 九、测试

```bat
cd /d F:\SmartFileOrganizer
python -m unittest discover -s tests -v
```

覆盖：配置与路径校验、文件名清洗/分词、docx/xlsx/txt 抽取、TF-IDF 训练与评测、三模式判定、
跨目录移动与撤销、路径逃逸拦截、期限/金额/文种抽取、ICS、端到端（扫描→建议→确认→撤销→去重→自动）。

---

## 十、隐私

- 所有处理在本机完成，没有任何网络请求；
- 不上传文件名、正文或统计；
- 日志只记路径与动作，位于数据目录，可随时删除；
- 数据库里保存正文摘要（默认前 1500 字）用于预览与任务抽取，可在设置里关闭正文读取。

---

## 十一、下一步（按价值排序）

1. 扫描件 OCR（Windows.Media.Ocr，零依赖）；
2. 分类体检报告：目录层级、命名一致性、近重复目录合并建议；
3. 主动学习：优先把"最不确定"的文件推给你确认；
4. 托盘 + 开机自启 + 安装包；
5. 可选本地 LLM（Ollama）做任务摘要，离线、可关闭。

---

## 许可证

MIT。见 [LICENSE](LICENSE)。
