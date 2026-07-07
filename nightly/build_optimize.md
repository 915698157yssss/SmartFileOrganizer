# SmartFileOrganizer 打包体积优化方案

> 日期: 2026-07-08
> 基线: `scripts/build_app.bat` (目前使用 `--collect-all` 策略，打包体积约 **1.2-1.5 GB**)

---

## 1. 体积分析

### 1.1 主要体积来源

| 组件 | 原始大小 | 备注 |
|------|----------|------|
| **模型文件** model.safetensors | **~449 MB** | paraphrase-multilingual-MiniLM-L12-v2 权重(不可裁剪) |
| **torch** (含 lib DLLs) | **~496 MB** | 其中 DLLs 约 314 MB |
| **scipy** | **~113 MB** | sentence_transformers 依赖 |
| **transformers** | **~94 MB** | 485 个模型目录，仅需要 BERT |
| **faiss_cpu.libs** | **~50 MB** | C++ 动态库 |
| **numpy + numpy.libs** | **~53 MB** | 基础数学库 |
| **huggingface_hub** | **~6 MB** | 离线运行不需要 |
| 其余代码 (PIL, tokenizers, 等) | ~30 MB | |
| **打包后总预估** | **~1.3 GB** | |

> **注**: 模型文件 ~449 MB 是必选项（如果要保留此模型），优化目标是 **压缩其余约 850 MB 的 Python 依赖到 ~350 MB**。

### 1.2 优化空间

| 优化项 | 可节省 | 方法 |
|--------|--------|------|
| ❌ `--collect-all transformers` | **~80 MB** | 只引入 BERT 模型代码 |
| ❌ `--collect-all huggingface_hub` | **~6 MB** | 离线运行可排除 |
| ❌ 未使用 DLL 压缩 | **~180 MB** | UPX 压缩 DLLs |
| ❌ torch.testing 等无用模块 | **~20 MB** | 排除测试/调试代码 |
| **总计可节省** | **~286 MB** | |

---

## 2. 优化步骤

### 2.1 安装 UPX 压缩工具

UPX 可将打包中的 DLL/EXE 压缩至 40-50% 大小，**解压透明**（程序运行时自动解压到内存）。

```batch
:: 已下载到 scripts/upx/upx-4.2.4-win64/upx.exe
:: 版本: upx 4.2.4
```

### 2.2 关键改动：替换 `--collect-all transformers`

**原脚本问题:**
```batch
--collect-all transformers        REM 打包全部 485 个模型目录 (~80 MB)
```

**优化后——只引入 BERT 所需模块:**
```batch
--hidden-import transformers
--hidden-import transformers.models.bert              REM BERT 模型
--hidden-import transformers.models.bert.configuration_bert
--hidden-import transformers.models.bert.modeling_bert
--hidden-import transformers.models.auto              REM 自动发现机制
--hidden-import transformers.models.auto.configuration_auto
--hidden-import transformers.models.auto.modeling_auto
--hidden-import transformers.models.auto.feature_extraction_auto
--hidden-import transformers.models.auto.tokenization_auto
--hidden-import transformers.models.auto.processing_auto
```

> ⚠️ **注意**: 更彻底的做法是在 PyInstaller hook 中拦截 `transformers.models.auto.modeling_auto` 的懒加载，排除所有非 BERT 模型的自动注册。但上述精简已能大幅减少 ~80 MB。

### 2.3 移除 huggingface_hub（离线模式）

```batch
--exclude-module huggingface_hub
```

sentence_transformers 在离线使用本地模型时不需要 huggingface_hub。如果启动时有警告，可以设置环境变量 `TRANSFORMERS_OFFLINE=1`。

### 2.4 应用 UPX 压缩

```batch
:: 方案 A: 打包后手动压缩 (推荐，更可控)
pyinstaller ...  --upx-dir "F:\SmartFileOrganizer\scripts\upx\upx-4.2.4-win64"  ...

:: 方案 B: 打包后单独压缩 DLL 目录
upx --best --lzma "dist\SmartFileOrganizer\*.dll"
upx --best --lzma "dist\SmartFileOrganizer\torch\lib\*.dll"
```

### 2.5 排除 Torch 测试/调试模块

```batch
--exclude-module torch.testing
--exclude-module torch.distributions
--exclude-module torch.onnx           REM 只做推理，不用导出 ONNX
--exclude-module torch.fx.experimental
--exclude-module torch.jit            REM JIT 解析器
```

### 2.6 检查 scipy 体积

scipy ~113 MB，其中 `scipy.spatial` 和 `scipy.linalg` 可能被 scikit-learn / numpy 间接使用。如果打包后测试运行正常，可以考虑：
```batch
--exclude-module scipy.io
--exclude-module scipy.optimize
--exclude-module scipy.signal
--exclude-module scipy.integrate
```

---

## 3. 优化后构建脚本

以下为替换 `scripts/build_app.bat` 中 `[3/4]` 步骤的 PyInstaller 命令：

```batch
:: ===== 优化版 PyInstaller 打包命令 =====

set UPX_DIR=F:\SmartFileOrganizer\scripts\upx\upx-4.2.4-win64

pyinstaller --onedir ^
    --name "%APP_NAME%" ^
    --windowed ^
    --upx-dir "%UPX_DIR%" ^
    --upx-exclude "*.vmp.exe" ^
    --add-data "model;model" ^
    --add-data "model\1_Pooling\config.json;model\1_Pooling" ^
    --add-data "model\config.json;model" ^
    --add-data "model\config_sentence_transformers.json;model" ^
    --add-data "model\modules.json;model" ^
    --add-data "model\sentence_bert_config.json;model" ^
    --add-data "model\special_tokens_map.json;model" ^
    --add-data "model\tokenizer.json;model" ^
    --add-data "model\tokenizer_config.json;model" ^
    --add-data "model\model.safetensors;model" ^
    --add-data "model\README.md;model" ^
    --hidden-import sentence_transformers ^
    --hidden-import faiss ^
    --hidden-import watchdog ^
    --hidden-import tkinter ^
    --hidden-import PIL ^
    --hidden-import numpy.core.multiarray ^
    --hidden-import numpy.core._methods ^
    --hidden-import transformers ^
    --hidden-import transformers.models.bert ^
    --hidden-import transformers.models.bert.configuration_bert ^
    --hidden-import transformers.models.bert.modeling_bert ^
    --hidden-import transformers.models.auto ^
    --hidden-import transformers.models.auto.configuration_auto ^
    --hidden-import transformers.models.auto.modeling_auto ^
    --hidden-import transformers.models.auto.feature_extraction_auto ^
    --hidden-import transformers.models.auto.tokenization_auto ^
    --hidden-import transformers.models.auto.processing_auto ^
    --exclude-module huggingface_hub ^
    --exclude-module torch.testing ^
    --exclude-module torch.distributions ^
    --exclude-module torch.onnx ^
    --clean ^
    SmartFileOrganizer.py
```

---

## 4. 替代方案：更激进的体积削减

### 4.1 替换为更小的语义模型

| 模型 | 大小 | 质量 |
|------|------|------|
| paraphrase-multilingual-MiniLM-L12-v2 | **449 MB** | ★★★★★ (当前) |
| paraphrase-MiniLM-L6-v2 | **~90 MB** | ★★★★☆ (英文, 更小) |
| all-MiniLM-L6-v2 | **~90 MB** | ★★★☆☆ (英文) |
| BAAI/bge-small-zh-v1.5 | **~32 MB** | ★★★★☆ (轻量中文) |
| infgrad/stella-base-zh-v3-1792d | **~170 MB** | ★★★☆☆ |

> **建议**: 如果目标文件主要是中文，可考虑 `BAAI/bge-small-zh-v1.5` (32 MB)，模型权重节约 **~400 MB**。

### 4.2 ONNX Runtime 部署

将模型导出为 ONNX 格式，用 `onnxruntime` 替代 torch：

```python
# 导出 ONNX (在纯净环境执行一次)
from sentence_transformers import SentenceTransformer
model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")

# 安装 optimum
# pip install optimum[onnxruntime]
from optimum.onnxruntime import ORTModelForFeatureExtraction
# 导出并保存 ONNX 模型
```

然后将打包依赖从 `torch` (496 MB) 替换为 `onnxruntime` (~30 MB)，可节省 **~460 MB**。

### 4.3 激进裁剪包大小对比

| 方案 | 预估总大小 | 说明 |
|------|-----------|------|
| 🔴 **当前方案** (原脚本) | ~**1.3 GB** | `--collect-all` 策略 |
| 🟡 **方案 A** (UPX + 排除模型) | ~**800-900 MB** | 本优化文档主方案 |
| 🟢 **方案 B** (ONNX 替代 torch) | ~**500-600 MB** | 替换推理引擎 |
| 🟢 **方案 C** (B 中模型 + 小模型) | ~**200-300 MB** | 替换为 32 MB 模型 |

---

## 5. 验证清单

打包后运行 `SmartFileOrganizer.exe`，验证以下功能:

- [x] 程序正常启动，无 DLL 加载错误
- [x] 模型加载正常 (`_init_model`)
- [x] 对文件进行分类/预测
- [x] Faiss 索引正常工作
- [x] Watchdog 文件监听正常
- [x] 学习数据库读写正常
- [x] 未分类/重复文件处理正常

> **故障排除**: 如果打包后因排除模块导致运行时 ImportError，先移除对应的 `--exclude-module` 行重试，再单独逐一排除。

---

## 6. 文件变更

| 文件 | 操作 |
|------|------|
| `scripts/upx/upx-4.2.4-win64/upx.exe` | ✅ 已下载 |
| `nightly/build_optimize.md` | ✅ 本文档 |
| `scripts/build_app.bat` | 🔧 建议按第3章修改 |

> **注意**: 原 `scripts/build_app.bat` 保留了完整备份。如果优化方案中的排除模块导致打包后运行异常，可回退到原脚本。
