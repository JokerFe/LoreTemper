# 知炼 · LoreTemper

**将视频课程中的知识，沉淀为可追溯的图文笔记，再逐步提炼成可复用的 Skill。**

LoreTemper 面向 UI 设计、页面开发和操作教程等视频学习场景：结合完整语音转写与关键画面，按主题整理核心思想、判断依据、方法和案例，并保留时间戳、截图及来源，方便回看与核实。

项目已提供视频学习、笔记生成 Skill，以及两者串联的工作流，并增加内容覆盖检查、来源校验和分层 Skill 输出。正式 Skill 应基于本阶段已审阅资料进行跨视频综合与实际任务验证；队列处理结束不代表所有课程已经提供完，也不代表结论已经审阅。

> 项目名称为「知炼 · LoreTemper」。当前启动器、部分界面与命令帮助仍沿用早期名称「映知」，Python 包与命令仍为 `video_tutor` / `video-tutor`。

## 能做什么

- **学习视频**：导入单个或多个视频、选择目录，完成媒体探测、分段转写与画面分析。
- **生成图文笔记**：按知识主题组织内容，保留必要步骤、参数、适用条件和案例；字幕与详细查证材料独立保存。
- **检查知识覆盖**：建立核心知识清单，检查遗漏、无依据结论与未解释的冲突，支持一次修订和复核；仍有问题时停止并保留报告。
- **提炼 Skill**：从所选笔记提炼带条件与来源的规则，生成按任务选择的能力指南、条件库和来源索引，并检查规则覆盖与包内链接。
- **追溯来源**：通过时间戳回看视频，通过截图核对原图，保存模型原始输出和证据文件。
- **管理任务**：支持排队、暂停、继续、失败重试及检查点恢复；任务冻结配置，后续改设置不影响已有任务。
- **整理与导出**：查看笔记、字幕和截图，进行关键词检索与人工补充，导出 Markdown、HTML、JSON、SRT、图片及 ZIP。
- **选择分析通道**：使用本机 Ollama 模型，或通过已登录的 Codex 客户端进行主题分析。

学习成果遵循以下流程：

```text
视频来源 → 语音转写与截图 → 主题笔记 → 来源审阅
                                        ↓
                           跨视频比较与候选规则积累
                                        ↓
                         阶段素材齐备后综合、验证 Skill
```

生成成功与审阅通过是不同状态。课程示例、候选原则和跨视频结论分别保存；历史撤回草稿不作为正式 Skill。

## 三种工作流

| 模式 | 输入 | 结果与边界 |
| --- | --- | --- |
| 视频学习 | 视频文件或目录 | 生成主题笔记、截图和查证材料，不自动生成 Skill |
| 笔记生成 Skill | 已有 Markdown / TXT 笔记，或可识别的笔记目录 | 根据所选笔记生成待验证的 Skill 包，不重新分析视频 |
| 学习并生成 Skill | 本次选定视频 | 等待视频学习完成后，将笔记交给 Skill 生成阶段；继承待审阅状态 |

在工作流页面选择模式、输入和输出目录，为学习与 Skill 阶段分别设置引擎、模型和可用档位，检查预览后提交。两个阶段可以使用不同模型。选择生成 Skill 只授权综合本次资料，不表示未来课程也已齐备。

生成结果会显示来源份数、规则数和能力模块数，并保留「待验证」状态。生成器不自动安装或发布 Skill；来源审阅、自动内容检查、结构校验与实际任务效果验证分别记录。

## 本次核心更新：内容覆盖与来源校验

### 笔记：保留重点，查证材料单独查看

新版流程按主题整理完整讲解与所选画面证据，再建立核心知识清单，核对思想、判断依据、关键方法、适用条件、必要案例与参数。篇幅由讲清内容所需决定，不设字数、主题数或截图数目标。

发现实质遗漏、无依据结论或未解释的冲突时，流程依据来源修订一次并重新检查；仍未解决则报错，保留原稿和检查报告。自动检查由模型完成，不能代替独立回看与审阅。本地模型按上下文预算分块并融合主题，超出预算或融合不收敛时明确失败，不静默截断来源。

工作台中的「图文笔记」展示主题总结，「详细查证」单独展示操作记录；完整字幕、截图和处理记录仍可回查。生成的图片引用需指向实际选取的本地图片，拒绝外链、越界路径和未选图片。

流水线导出中的主要文件：

| 文件或目录 | 内容 |
| --- | --- |
| `document.md` / `document.html` | 主总结及必要的来源引用 |
| `detailed-records.md` | 有主题总结时另存的完整操作与查证记录 |
| `transcript.srt`、`document.json`、`images/` | 转写、结构化资料和截图 |
| `quality/` | 已生成的知识清单、覆盖检查与质量报告 |

### Skill：从来源规则生成分层指南

生成前冻结笔记指纹，去除内容完全相同的重复笔记，并记录可识别的共同视频来源。候选规则保留观点、依据、方法、条件、例外、证据类型及原文引用；引用短句和时间戳需要与输入来源一致。

规则按使用者的问题归入能力模块，每条候选规则必须且只能映射一次。能力指南保留不同条件和冲突分支，并进行内容覆盖检查；修订一次后仍有实质缺口就停止。没有可提炼规则的来源记录原因，不能静默遗漏。

输出包按需读取入口、能力指南、条件库和原始证据：

```text
<skill-name>/
├── SKILL.md
└── references/
    ├── capabilities/              能力入口与方法指南
    ├── rules/                     条件、例外和原文依据
    ├── sources/source-NNN/        来源笔记与可取得的引用图片
    └── coverage/
        ├── rules.json             规则、能力与来源的映射
        ├── source-files.json      来源指纹、图片记录和审阅状态
        ├── generation-report.json  生成与校验结果
        └── validation-plan.md     尚待执行的实际任务验证计划
```

包内文件、链接、锚点、来源哈希与规则映射均需通过校验。模块数量由资料决定，不预设课程数或规则数，也不将个人课程内容内置为默认结果。完整机制见[笔记与 Skill 生成质量流程](docs/generation-quality.md)。

### 新旧任务与用量

新建网页任务使用 `joker-ui-v2` 质量流程；旧任务继续使用冻结配置，不自动重写既有笔记或已发布 Skill。配置模型的兼容默认值仍是 `learning_quality: legacy`，传统导入或脚本创建的任务不能仅因更新代码就视为已启用新版；需要时在创建新任务前设置为 `joker-ui-v2`。`study-clip` 入口也已接入笔记覆盖检查。

新增的知识提炼、覆盖核对和修订会增加调用与耗时。页面中的视频学习基础用量来自旧流程样本，尚不包含新版完整开销；费用预估不能作为新版总价保证。

## 分析通道与数据处理

| 通道 | 处理方式 | 使用前提 |
| --- | --- | --- |
| Ollama | 本地转写、本地视觉分析与总结 | 本机运行 Ollama，并准备所需模型 |
| Codex | 本地转写与抽帧，再将讲解文本、选定画面等资料交给 Codex 分析 | 可用的 Codex 客户端、登录账户及网络连接 |

**Codex 通道涉及远程推理，不能视为完全离线处理。** 原视频从本地路径读取，不等于分析资料不会发送到模型服务。受保密或外传限制的素材，应使用符合其使用约束的处理方式。

模型列表及支持的思考档位以控制页实际发现的结果为准。用量中的 API 等价费用只是估算，不等于订阅额度扣减或实际账单。项目默认配置使用 Ollama，学习控制页可按任务选择通道。

## 环境要求

当前安装脚本、双击启动器和后台服务以 **macOS / Apple Silicon** 为主要运行环境。Windows 用户可按下方「Windows：通过 WSL2 启动」准备 Linux 运行环境；原生 Windows Python 暂不支持。WSL2 步骤已按源码依赖与官方安装文档核对，但尚未完成 Windows 实机端到端验证。

| 依赖 | 用途 |
| --- | --- |
| Python 3.11–3.13、uv | 后端及 Python 依赖管理 |
| Node.js 20 / 22、npm | 前端安装与构建；下方 WSL2 示例使用 22 |
| FFmpeg、ffprobe | 音视频探测、音轨提取、截图与播放代理 |
| whisper-cli | 默认语音转写后端，由 whisper.cpp 提供 |
| Ollama | 本地视觉与总结模型服务，使用 Ollama 通道时需要 |
| Codex 客户端 | 使用 Codex 通道时需要 |

模型、原视频、截图和导出会占用较多磁盘空间。默认任务检查要求至少有 10 GB 可用空间；这只是启动阈值，不代表完整模型与课程资料的容量需求。

## 快速开始（macOS）

### 1. 获取项目并安装依赖

先准备上表中的工具，再执行：

```sh
git clone https://github.com/JokerFe/LoreTemper.git
cd LoreTemper
./scripts/setup.sh
```

安装脚本会同步 Python 依赖、安装并构建前端、初始化本地配置，然后输出环境诊断。它不会安装全部系统工具，也不会自动下载模型；首次诊断提示模型缺失时，继续完成下一步。

### 2. 准备默认本地模型

确保 Ollama 服务已启动，然后执行：

```sh
uv run video-tutor models prepare
uv run video-tutor doctor
```

`models prepare` 会下载配置中的两个 Ollama 模型与 Whisper 权重，需要联网。默认配置为：

| 用途 | 默认模型 | 保存位置 |
| --- | --- | --- |
| 转写 | `ggml-large-v3-turbo.bin` | `work/` |
| 视觉分析 | `qwen3-vl:8b-instruct` | Ollama 管理的模型目录 |
| 总结 | `qwen3:14b` | Ollama 管理的模型目录 |

该命令准备的是本地模型组合，并非 Codex 的安装或登录命令。仅使用 Codex 分析时仍需要本地转写工具和 Whisper 权重；`doctor` 当前也会检查 Ollama，因此应结合具体检查项判断缺失依赖。

可选转写后端为 faster-whisper：执行 `uv sync --extra faster`，提前准备对应的 CTranslate2 模型目录，并在配置中设置 `asr_backend: faster_whisper` 与 `asr_model` 本地路径。该后端禁止隐式联网下载；其真实模型验证范围见[验证记录](docs/verification.md)。

### 3. 启动工作台

```sh
./启动映知.command
```

也可在 Finder 中双击该文件，或运行：

```sh
uv run video-tutor start
```

默认地址为 `http://127.0.0.1:8765`。请使用启动器打开的页面或终端给出的带会话凭据链接访问；仅输入地址可能无法通过首次鉴权。不要分享带凭据的完整链接。

### 4. 开始学习

1. 在学习控制页选择分析通道、模型及可用档位。
2. 选择视频文件或目录，指定独立的输出目录，检查预览后点击「开始学习」。
3. 在视频资料库查看进度、主题笔记、字幕和截图，核对关键内容。
4. 需要时补充笔记或导出资料，保留来源与审阅记录。

需要综合已有笔记时，使用工作流页面的「笔记生成 Skill」模式；需要串联本次视频学习与生成时，选择「学习并生成 Skill」。独立生成前先确认所选笔记与来源审阅状态，生成后按包内验证计划检查实际效果。

传统「导入视频」入口支持目录监听，文件稳定后自动入队。显式选择文件的学习入口仅导入所选素材；两种方式的目录监听行为不同。

默认接通电源后才启动新任务，可在偏好设置调整。不要将项目的 `data/`、`work/` 或生成产物目录设为视频来源。手动运行时关闭终端会停止其子进程；电脑休眠也可能暂停处理。

## Windows：通过 WSL2 启动

后端使用 `fcntl` 文件锁及 Unix 进程管理，当前不能在 PowerShell 中直接运行原生 Windows Python。这里将后端、前端构建工具与 Ollama 都安装在 **同一个 WSL2 Ubuntu 环境**中，Windows 浏览器用于访问工作台。不要复用 macOS 的 `.venv`、`node_modules` 或配置中的绝对路径。

### 1. 安装 WSL2

在 Windows 11 或支持 WSL 的 Windows 10 上，以管理员身份打开 **PowerShell**：

```powershell
wsl --install -d Ubuntu-24.04
```

按提示重启，首次打开 Ubuntu 时创建 Linux 用户。然后在 PowerShell 检查发行版的 `VERSION` 为 `2`，并进入 Ubuntu：

```powershell
wsl --list --verbose
# 仅当 VERSION 为 1 时执行：
# wsl --set-version Ubuntu-24.04 2
wsl -d Ubuntu-24.04
```

需要的 Windows 版本、虚拟化设置与安装故障处理见 [Microsoft WSL 安装说明](https://learn.microsoft.com/en-us/windows/wsl/install)。**以下 Bash 命令均在 Ubuntu 终端执行，不在 PowerShell 中执行。**

### 2. 安装 Linux 工具与项目依赖

```bash
sudo apt update
sudo apt install -y git curl ca-certificates build-essential cmake pkg-config libcurl4-openssl-dev ffmpeg zstd

# 安装 uv 与 Python
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"
uv python install 3.12

# 安装 nvm 与 Node.js
curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.8/install.sh | bash
export NVM_DIR="$HOME/.nvm"
source "$NVM_DIR/nvm.sh"
nvm install 22
nvm alias default 22

# 将代码与依赖放在 Linux 用户目录
mkdir -p "$HOME/projects"
cd "$HOME/projects"
git clone https://github.com/JokerFe/LoreTemper.git
cd LoreTemper
uv sync --frozen --python 3.12
(cd frontend && npm ci && npm run build)
uv run video-tutor init
```

工具安装方式参考 [uv 安装文档](https://docs.astral.sh/uv/getting-started/installation/)与 [nvm 安装文档](https://github.com/nvm-sh/nvm#installing-and-updating)。已克隆过项目时，进入现有目录即可，不必再次执行 `git clone`。不要运行面向 macOS 的 `scripts/setup.sh` 或 `.command` 启动器。

新克隆不含私人课程目录，而当前工作流入口需要 `learning/catalog.json`。在项目根目录创建空目录索引；下面的命令不会覆盖已有记录：

```bash
uv run python - <<'PY'
from pathlib import Path
catalog = Path("learning/catalog.json")
catalog.parent.mkdir(parents=True, exist_ok=True)
if not catalog.exists():
    with catalog.open("x", encoding="utf-8") as stream:
        stream.write('{"schema_version": 1, "batches": []}\n')
PY
```

### 3. 安装 whisper.cpp 与 Ollama

下面构建 CPU 版本的 Whisper 转写工具；GPU 加速需要另外配置，不是启动前提：

```bash
mkdir -p "$HOME/tools" "$HOME/.local/bin"
git clone --depth 1 https://github.com/ggml-org/whisper.cpp.git "$HOME/tools/whisper.cpp"
cmake -S "$HOME/tools/whisper.cpp" -B "$HOME/tools/whisper.cpp/build" -DCMAKE_BUILD_TYPE=Release
cmake --build "$HOME/tools/whisper.cpp/build" --config Release -j 2
ln -sf "$HOME/tools/whisper.cpp/build/bin/whisper-cli" "$HOME/.local/bin/whisper-cli"
export PATH="$HOME/.local/bin:$PATH"
whisper-cli --help

curl -fsSL https://ollama.com/install.sh | sh
```

构建及安装参考 [whisper.cpp](https://github.com/ggml-org/whisper.cpp) 与 [Ollama Linux 文档](https://docs.ollama.com/linux)。若已存在 `~/tools/whisper.cpp`，跳过克隆并使用已有源码构建。

检查 Ollama 是否已在 WSL 中运行：

```bash
curl --fail http://127.0.0.1:11434/api/tags
```

若无法连接，在另一个 Ubuntu 终端运行 `ollama serve` 并保持该终端打开。若安装程序已经启动服务，则无需再启动一个实例。本方案使用 WSL 内的 Ollama，不依赖 Windows 主机上的 Ollama 服务。

回到项目目录下载模型并检查环境：

```bash
cd "$HOME/projects/LoreTemper"
uv run video-tutor models prepare
uv run video-tutor doctor
```

默认视觉与总结模型占用较多内存；CPU 推理可能较慢。先用短视频验证，再处理长课程。需要 GPU 加速时，按模型工具的官方文档配置适合本机显卡的驱动与后端。

### 4. 启动并从 Windows 浏览器访问

```bash
cd "$HOME/projects/LoreTemper"
export PATH="$HOME/.local/bin:$PATH"
uv run video-tutor start --no-open-browser
```

保持终端打开，将终端输出的完整 `http://127.0.0.1:8765/#token=…` 链接复制到 Windows 浏览器。WSL 通常支持通过 Windows 的 `localhost` 访问 Linux 服务；若无法连接，先在 Ubuntu 中执行 `curl -I http://127.0.0.1:8765` 检查服务，再参考 [WSL 网络说明](https://learn.microsoft.com/en-us/windows/wsl/networking)。不要通过改为公开监听地址来绕过问题。

首次先选择 **Ollama** 通道并使用短视频测试。页面中的文件与输出路径必须采用 WSL 路径，例如：

| Windows 路径 | 页面中填写的 WSL 路径 |
| --- | --- |
| `D:\Videos\lesson.mp4` | `/mnt/d/Videos/lesson.mp4` |
| `D:\LoreTemper-output` | `/mnt/d/LoreTemper-output` |

包含空格的路径在命令行中加引号，在页面输入框中直接填写。Linux 下暂不支持本机文件选择弹窗，请手动填写路径。

WSL 使用注意事项：

- 后续启动只需确保 Ollama 正在运行，再执行本节的 `start` 命令；无需重复安装和下载模型。
- `Ctrl+C` 停止当前启动器管理的服务。关闭 WSL 或执行 `wsl --shutdown` 会终止其中的任务；当前 macOS 的 `service install` / `service uninstall` 不适用于 WSL。
- WSL 下不会读取 Windows 电池状态，因此「接通电源后启动」限制不生效；长任务请自行接通电源并避免休眠。
- 本节启动步骤以 Ollama 为起点；如使用 Codex 通道，需让同一 WSL 环境能找到并使用已登录的 Linux `codex` 命令，不能假定 Windows 客户端的登录会自动共享。该组合尚未在本项目中完成实机验证。

## 资料保存与配置

```text
LoreTemper/
├── backend/video_tutor/    后端 API、队列、转写、分析、搜索与导出
├── frontend/              React 工作台与浏览器测试
├── prompts/               模型提示词
├── scripts/               安装、验证和实验辅助脚本
├── tests/                 后端测试
├── docs/                  学习流程、设计说明与验证记录
├── AGENTS.md              项目学习与 Skill 工作约定
├── data/                  本地配置、SQLite 数据库、任务与证据
├── learning/              课程目录、笔记、审阅记录、实验与历史草稿
└── work/                  模型、缓存、日志与开发验证产物
```

- 数据目录默认是 `data/`，可用 `VIDEO_TUTOR_DATA` 环境变量覆盖；配置保存在该目录的 `config.yaml`。
- 流水线产物通常位于 `data/assets/<asset_id>/runs/<run_id>/`；自定义输出目录按视频和任务隔离保存。
- `learning/catalog.json` 是本机课程与笔记索引，进度字段只是快照；当前状态应以数据库和实际产物为准。
- 本地数据、课程资料、模型、媒体、缓存和凭据等由 [.gitignore](.gitignore) 排除。**克隆仓库不会获得已有视频、学习笔记、模型或本机配置。**
- Git 不承担这些资料的备份；需要保留学习成果时，应另行备份允许保存的资料及其来源关系。

源码与依赖锁文件保留在版本控制中。更改模型设置后，新任务使用新配置，既有任务继续使用其固化配置。

<details>
<summary>已有本地目录从 ai-video-stu 改名的说明</summary>

本次改名的工作环境将实际目录改为 `LoreTemper`，并保留同级 `ai-video-stu` 符号链接。它用于兼容运行中的任务和历史证据内的旧绝对路径，不是另一份项目。在完成历史引用迁移前请保留该链接；新克隆的项目不需要创建它。

</details>

## 常用命令

在项目根目录执行：

```sh
# 环境诊断、查看资料状态
uv run video-tutor doctor
uv run video-tutor status

# 导入视频或目录；start 会启动工作台和任务进程
uv run video-tutor import "/path/to/videos"
uv run video-tutor start

# 管理指定任务；RUN_ID 替换为实际任务 ID
uv run video-tutor pause RUN_ID
uv run video-tutor resume RUN_ID
uv run video-tutor export RUN_ID

# 查看所有命令
uv run video-tutor --help
```

macOS 用户级后台服务：

```sh
uv run video-tutor service install
uv run video-tutor service uninstall
```

卸载服务会保留资料。后台服务与手动启动应避免重复运行。仅启动 API 或任务进程时可分别使用 `serve`、`worker`；只运行 API 不等于任务队列正在处理。

`study-clip` 用于**已准备完整转录、来源截图和输入清单的资料目录**，不是直接接收视频文件的命令。单次已验证范围最多 20 分钟，使用方式及审阅要求见[按主题取证的视频学习通道](docs/adaptive-study.md)。

## 开发与验证

后端：

```sh
uv run pytest -q
uv run ruff check backend tests --select F
```

前端：

```sh
cd frontend
npm ci
npm run build
npm test
```

前端构建产物由后端提供。浏览器测试使用 Playwright，当前配置指定本机 Chrome，并在独立端口 `8876` 启动测试服务；运行前需要项目 Python 环境和 Chrome。测试接口包含模拟数据，不能将通过测试等同于真实课程内容准确。

需要验证真实本地模型流水线时，在项目根目录运行：

```sh
uv run python scripts/verify_auto_pipeline.py "/path/to/short-test-video.mp4"
```

该脚本使用隔离资料库、复制输入短片并调用真实本地模型，检查字幕、截图校验和、证据引用、检索与导出。结果保存在 `work/auto-validation-*`，会产生实际计算耗时。已有检查记录及其测试边界见[验证记录](docs/verification.md)和[分析控制页说明](docs/analysis-control.md)。

2026-10-20 核心更新（`7515b39`）提交前验证：75 项后端测试、7 项浏览器测试及前端构建通过。覆盖来源与规则校验、修订失败时停止、图片打包和主总结与查证记录分离。模型输出测试包含明确的模拟数据，不代表新版在真实长课程上的内容质量或耗时已经完成验证。

## 常见问题与边界

**任务一直等待怎么办？** 先查看页面等待原因，再运行 `doctor`。常见原因包括未接通电源、磁盘空间不足、本地工具或模型未就绪；仅启动网页服务也不会自动完成所有后台任务。

**视觉模型没有生成有效答案怎么办？** 默认使用明确的 `qwen3-vl:8b-instruct`。项目曾遇到其他版本只输出思考内容、正式答案为空的问题。更换模型配置后应创建新任务；具体修复与复验记录见 `docs/verification.md`。

**能完整识别视频里的所有操作吗？** 不能保证。截图采样可能遗漏短暂点击、小字和细微变化；录屏也没有键鼠事件日志。重要步骤必须结合原图与时间戳回看，证据文件存在不代表结论一定正确。

**长课程如何减少重复画面分析？** Ollama 流水线支持任务选项 `visual_analysis: "keyframes"`，默认是 `full`。关键画面模式保留完整转写和原始采样截图，减少送入视觉模型的重复画面；它适合课程要点学习，不保证捕获每个瞬时操作。覆盖记录保存在 `visual-plan.json` 和笔记元数据中。已有暂停任务的缓存衔接还涉及 `preserve_visual_groups`，不要随恢复任务反复改变该值。

**处理视频一定比观看视频更快吗？** 没有固定保证。耗时取决于视频长度、转写、截图数量、模型、硬件、网络、重试与审阅工作量。缓存复用的耗时不能视作从头学习速度，短片测试也不能直接外推到长课程。

**为什么生成因内容检查而失败？** 新版在自动修订后仍发现核心遗漏、无依据结论或冲突时会停止。请查看保留的原稿、覆盖记录与质量报告，核对来源完整性和模型输出，再修正输入或调整模型后重试。不要把结构检查通过或模型自查通过等同于来源已经审阅。

**什么时候生成正式 Skill？** 当用户确认本阶段素材齐备、进入综合阶段后，基于全部已审阅资料提炼规则、适用条件、例外与来源，再通过实际页面设计或开发任务验证。生成草稿或完成某个任务不会自动改变这一阶段约定。

其他限制：当前默认使用第一音轨的 ASR 转写，不自动选择内嵌字幕；特殊编码可能需要播放代理；跨视频语义问答与 embedding 尚不作为已完成能力对外承诺。

## 进一步阅读

- [学习流程与最终 Skill 约定](docs/learning-workflow.md)
- [笔记与 Skill 生成质量流程](docs/generation-quality.md)
- [主题取证、补图核验与用量计量](docs/adaptive-study.md)
- [分析控制页与验证范围](docs/analysis-control.md)
- [技术方案](docs/technical-plan.md)
- [工程与真实模型验证记录](docs/verification.md)
