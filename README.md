# WeFlowClone

> 以 **WeFlow** 为聊天数据底座、以 **WeClone** 为训练与推理引擎的数字分身工作站。
> 一句话：**把「从微信导出聊天记录」到「训练出你自己的模型」之间所有手工步骤，变成一条 `wfc` 命令。**

```
WeFlow（本机微信数据 · 实时）  ──HTTP API──▶  WeFlowClone（整合层）  ──CSV──▶  WeClone（微调 · 部署）
      数据来源                    5031                  你的项目                  数字分身
```

---

## 0. 下载即用（Windows / macOS）

> **本仓库只放源码**，方便你 fork、改功能、自己训练。
> 想直接用请到 **[Releases](../../releases)** 下载对应平台的包。

不需要 Python、不需要显卡、不需要训练——**装好就能聊，只填一个 API Key**。

| 平台 | 下载 | 使用 |
| --- | --- | --- |
| Windows 10/11 x64 | `WeFlowClone-1.0.1-win64.zip`（8.5 MB） | 解压 → 双击 `WeFlowClone\WeFlowClone.exe` |
| macOS（Intel / Apple Silicon） | `WeFlowClone-1.0.1-macos-<arch>.dmg` | 拖进「应用程序」，首次右键 → 打开 |

> 解压后的目录里，`WeFlowClone.exe` 是入口，`_internal\` 是运行时，别把它们拆开。

启动后浏览器自动打开 `http://127.0.0.1:8765`：

1. 点右上角 **设置**；
2. 填 **API 地址**（如 `https://api.deepseek.com/v1`）、**API Key**、**模型名**（如 `deepseek-chat`）；
3. 点 **测试连接** → **保存** → 开始聊天。

> 支持任何 OpenAI 兼容接口：DeepSeek / Moonshot / 通义 / OpenAI，
> 以及本地 **Ollama**（`http://127.0.0.1:11434/v1`）、vLLM、LM Studio。
> 想完全离线，就装 Ollama + 一个 7B 模型，连 API Key 都不用。

**让它像你（可选，2 分钟）**：装 [WeFlow](https://github.com/hicccc77/WeFlow)，设置里开启
API 服务并复制 Token，然后：

```powershell
# 1) 把 Token 告诉 WeFlowClone：设置环境变量最快（也可以改数据目录里的 weflow.jsonc）
setx WEFLOW_TOKEN "你的Token"

# 2) 拉取聊天记录（会生成 WeClone 格式的数据集）
WeFlowClone.exe pull

# 3) 让机器人重新学习你的说话方式
WeFlowClone.exe bot --reindex
```

机器人会用「你的说话习惯 + 历史对话检索 + 你选的模型」来回复，**不需要微调**。

> 发行包只含数据接入与机器人；要微调出自己的模型（LoRA），用源码安装（见 §4）。
> 打包与签名细节：[docs/08-打包与分发.md](docs/08-打包与分发.md)。

---

## 1. 这个项目解决什么问题

单独用 WeClone，你得到的是「**给我一个 CSV 目录，我帮你训练**」。
单独用 WeFlow，你得到的是「**我能把你微信里的聊天记录读出来、看得到、导得出**」。

中间那段没人管：导出成什么格式、放哪个目录、图片怎么办、备注怎么带、以后新消息怎么补。
**WeFlowClone 就是这一段**，并且把它做成了可重复执行的管线：

| 手工做法 | WeFlowClone |
| --- | --- |
| 在 WeFlow 里逐个人点导出，选格式，找目录 | `wfc pull` 一次拉全部（或指定会话） |
| 手动建 `dataset/csv/<人名>/`、改名 `_1_N.csv` | 自动按 WeClone 约定落盘 |
| 图片另存，还要保证文件名能对上 | 自动复制到 `dataset/media/images/`，`src` 只写相对名 |
| 备注、关系写在脑子里 | 写进 `users.json`，WeClone 用它填 `chat_with` |
| 新消息来了再重复一遍 | `wfc watch` 监听 WeFlow 推送，自动增量重拉 |
| 训练前不知道数据有没有问题 | `wfc prepare` 先校验：表头 / 类型 / 时间 / 图片 / 单向会话 |

---

## 2. 为什么是「以 WeFlow 为基础」

WeFlow 是这个组合里唯一**能持续拿到数据**的一环：

* **完全本地**：数据库解密、消息读取都在本机，不经过任何服务器；
* **实时**：能订阅 `message.new` 推送，这是「数字分身跟着你一起长大」的前提；
* **有 HTTP API**：`127.0.0.1:5031`，带 Token，官方文档齐全，可以被程序稳定调用；
* **本来就懂 WeClone 格式**：WeFlow 内置了 `WeCloneFormatter`，导出表头就是
  `id,MsgSvrID,type_name,is_sender,talker,msg,src,CreateTime` —— 这让我们可以
  **零改动**复用两边的既有契约。

WeClone 则专注于它擅长的事：数据清洗 → Q&A 构造 → LoRA 微调 → OpenAI 兼容服务 + Web 聊天界面。

> **本项目没有修改 WeClone 一行代码，也没有打包 WeFlow 的源码。**
> 前者靠「正确的 cwd + 绝对路径配置」调用，后者靠「公开 HTTP API」对接。
> 这样上游升级时，你只需要替换 `vendor/WeClone`，整合层不受影响。

---

## 3. 目录结构

```
WeFlowClone/
├─ weflow_clone/              ← 整合层（本项目写的全部代码，纯标准库）
│  ├─ cli.py                  ← wfc 命令入口
│  ├─ config.py               ← weflow.jsonc 解析（自带 JSONC 支持）
│  ├─ sync.py                 ← 选会话 / 拉取 / 监听推送
│  ├─ bot/                    ← 内置机器人（不需要显卡）
│  │  ├─ server.py            ← 网页聊天 + OpenAI 兼容 /v1
│  │  ├─ persona.py           ← 从聊天记录学说话习惯 → system prompt
│  │  ├─ retriever.py         ← BM25 检索「你以前怎么回」
│  │  ├─ llm.py               ← 任意 OpenAI 兼容接口（流式）
│  │  └─ web/index.html       ← 单文件聊天界面（无外链、可离线）
│  ├─ weflow/
│  │  ├─ client.py            ← WeFlow API 客户端（鉴权 / 分页 / 媒体 / SSE）
│  │  ├─ schema.py            ← 原始 JSON 与 ChatLab 两种消息模型
│  │  ├─ mapping.py           ← localType → WeClone type_name，文本抽取，CSV 转义
│  │  ├─ exporter.py          ← 写出 dataset/csv/*.csv + users.json + 媒体
│  │  └─ importer.py          ← 反向：导入 WeFlow 手工导出（CSV / ChatLab / JSON）
│  ├─ pipeline/
│  │  ├─ weclone.py           ← 调用 weclone-cli（cwd / 环境变量 / 依赖检查）
│  │  ├─ bootstrap.py         ← wfc init：生成两份配置 + 目录骨架
│  │  ├─ dataset.py           ← 数据集统计与校验
│  │  └─ doctor.py            ← 自检
│  └─ tools/                  ← 模拟 WeFlow 服务 + 合成数据（演示与测试）
├─ vendor/WeClone/            ← WeClone 上游源码（未修改，pip install -e 用）
├─ weflow.jsonc               ← 本项目配置（WeFlow 地址、Token、导出策略）
├─ data/                      ← 所有产物：dataset/csv、dataset/media、res_csv、model_output
├─ scripts/
│  ├─ setup.ps1               ← 建 .venv、装依赖、生成配置
│  ├─ start.ps1               ← 自检 → 同步 → 启动（默认内置机器人）
│  ├─ release_check.py        ← 发布闸门：版本/链接/密钥/workflow/测试/产物
│  └─ bump_version.py         ← 一处改版本，同步到 5 个文件
├─ integrations/              ← 两个上游各自的对接说明
├─ docs/                      ← 架构、快速开始、字段映射、API、训练、FAQ、打包
├─ packaging/                 ← PyInstaller 规格 + 双平台构建脚本 + 图标
│  ├─ wfc.spec                ← 单文件打包（只含整合层，不含 torch）
│  ├─ build.ps1 / build.sh    ← Windows / macOS 构建
│  └─ launcher.py             ← 双击 → 直接启动机器人
├─ .github/workflows/         ← ci.yml（每次提交跑检查）+ release.yml（打 tag 出三平台包）
├─ tests/                     ← 93 个测试（含端到端，跑在模拟 WeFlow 上）
├─ CONTRIBUTING.md            ← 二创指南：目录即架构、常见改造点、PR 清单
├─ 安装.bat / 启动.bat         ← 双击即可
└─ pyproject.toml             ← 安装后得到 `wfc` 命令
```

---

## 4. 五分钟上手

### 4.1 安装

```powershell
# 需要 Python 3.12（WeClone 的硬性要求）
winget install Python.Python.3.12

# 双击 安装.bat，或：
.\scripts\setup.ps1 -Mirror        # -Mirror：国内网络走清华源
```

只想先跑数据链路、不装几个 GB 的 torch：

```powershell
.\scripts\setup.ps1 -SkipTraining
```

### 4.2 接上 WeFlow

1. 打开 WeFlow → **设置 → API 服务 → 启动服务**（默认 `127.0.0.1:5031`）；
2. 复制页面上的 **Access Token**；
3. 打开 `weflow.jsonc`，填到：

```jsonc
"weflow": {
  "base_url": "http://127.0.0.1:5031",
  "access_token": "把 Token 粘在这里",
  ...
}
```

> 不想写进文件？设置环境变量 `WEFLOW_TOKEN` 即可。

### 4.3 自检 → 拉数据 → 校验

```powershell
wfc doctor              # 环境 / WeFlow 连接 / WeClone 管线 / 数据，逐项告诉你缺什么
wfc sessions            # 看看 WeFlow 里有哪些会话
wfc pull                # 默认只拉私聊（WeClone 官方建议；群聊噪声大）
wfc prepare             # 校验数据集：表头、类型、时间、图片、单向会话
```

`wfc pull` 之后看一眼产物：

```
data/dataset/csv/张三/张三_1_1523.csv
data/dataset/csv/张三/users.json
data/dataset/media/images/abc123.jpg
```

### 4.4 内置机器人（不用训练，只用 API Key）

```powershell
wfc bot              # 启动网页聊天：http://127.0.0.1:8765
wfc bot --reindex    # 重新学习聊天记录后再启动
wfc bot-config       # 查看 / 写入 API 配置（脚本化部署用）
```

它凭什么「像你」：

| 组件 | 做法 |
| --- | --- |
| 人设 | 统计你的句长、疑问比例、表情习惯、口头禅，挑真实对话当范例，拼成 system prompt |
| 检索 | 对你这句话做 BM25，从历史里找 2~3 段「对方问 → 你答」，作为 few-shot |
| 模型 | 任何 OpenAI 兼容接口（含本地 Ollama），不占显存、不下载权重 |

三者都在本机完成，只有「最终生成」请求发到你选的服务商。
同一套能力也暴露成 OpenAI 兼容接口，接自己的客户端：

```powershell
curl http://127.0.0.1:8765/v1/chat/completions -H "Content-Type: application/json" `
  -d '{"messages":[{"role":"user","content":"在吗"}]}'
```

### 4.5 训练自己的模型（可选，需要显卡）

```powershell
wfc prepare --make-dataset   # 调 weclone-cli 生成 SFT 训练数据
wfc profile                  # （可选）抽取画像 / 记忆，让分身更"记得住"
wfc train                    # LoRA 微调
wfc serve                    # 启动 WeClone 服务：Web UI + OpenAI 兼容 /v1
```

区别：`wfc bot` 是「借模型 + 你的人设」，**5 分钟能用**；
`wfc train` 是「把你的说话方式刻进权重」，**需要显卡和几个小时**。
两者可以并存：先用 bot，等数据攒够了再训。

### 4.6 没有微信也想先看看效果？

```powershell
wfc demo
```

会用内置的模拟 WeFlow（合成聊天数据）把「拉取 → 落盘 → 校验」全跑一遍，并打印真实 CSV 内容。

---

## 5. 命令一览

| 命令 | 作用 |
| --- | --- |
| `wfc init` | 生成 `weflow.jsonc`、`vendor/WeClone/settings.jsonc` 和目录骨架 |
| `wfc doctor` | 四类自检，失败项都带修复建议 |
| `wfc sessions` | 列出 WeFlow 会话（名称 / wxid / 私聊或群聊 / 最后消息时间） |
| `wfc pull` | 从 WeFlow 拉取并生成 WeClone 数据集 |
| `wfc import <路径>` | 导入 WeFlow 手工导出（WeClone CSV / ChatLab JSON / 原始 JSON） |
| `wfc prepare` | 校验数据集；`--make-dataset` 继续调用 WeClone |
| `wfc status` | 数据集统计（会话、消息、我方/对方、图片） |
| `wfc bot` | **启动内置机器人**（网页聊天 + `/v1` 接口，只需 API Key） |
| `wfc bot-config` | 查看 / 写入机器人 API 配置 |
| `wfc watch` | 监听 WeFlow 推送，安静期后自动重拉变化过的会话 |
| `wfc train` | 透传 `weclone-cli train-sft` |
| `wfc profile` | 透传 `weclone-cli build-profile` |
| `wfc serve` | 启动 WeClone 服务并自动打开浏览器 |
| `wfc weclone <任意子命令>` | 完全透传，WeClone 的功能一个都不少 |
| `wfc demo` | 用模拟 WeFlow 演示全链路 |
| `wfc mock` | 单独启动模拟 WeFlow API（联调用） |

`wfc pull` 的常用参数：

```powershell
wfc pull --session 张三            # 只拉一个人（可写多次）
wfc pull --groups                 # 连群聊一起拉
wfc pull --since 2024-01-01       # 只要这段时间之后
wfc pull --limit 5000             # 每人最多 5000 条
wfc pull --no-media               # 只要文字，不导图片
wfc pull --dry-run                # 只看看会拉哪些会话
```

---

## 6. 数据契约（最重要的 8 列）

WeClone 读的就是这个：

```csv
id,MsgSvrID,type_name,is_sender,talker,msg,src,CreateTime
1,7310000000000000000,text,0,老张,在吗？,,2024-08-16 09:00:00
2,7310000000000000001,text,1,我,在的,,"2024-08-16 09:01:00"
3,7310000000000000002,image,0,老张,[图片],images/photo.png,2024-08-16 09:02:00
```

| 列 | 含义 | 本项目的处理 |
| --- | --- | --- |
| `id` | 行号 | 每个会话从 1 递增，分片后仍连续 |
| `MsgSvrID` | 平台消息 ID | 取 WeFlow 的 `serverId`，用于去重 |
| `type_name` | 消息类型 | `localType` → `text/image/voice/video/sticker/file/location`，与 WeFlow 自带 WeClone 导出一致 |
| `is_sender` | 谁说的 | 1 = 我，0 = 对方；ChatLab 导入时用 `meta.ownerId` 反推 |
| `talker` | 发送者展示名 | 我 → `export.self_name`；对方 → 备注/群昵称/会话名 |
| `msg` | 正文 | 文本原文；链接取 `title + des + url`；文件取文件名 |
| `src` | 媒体路径 | 只写 `images/<文件名>`，WeClone 用 basename 在 `dataset/media/images` 里匹配 |
| `CreateTime` | 时间 | 默认本机时区 `YYYY-MM-DD HH:MM:SS`，可切 ISO |

同一个会话目录下还会写 `users.json`：

```json
{
  "张三": { "remark": "老张", "nickname": "张三", "username": "wxid_zhangsan" },
  "relation": "好友"
}
```

WeClone 用它填 `chat_with`，让模型知道「正在跟谁聊」。

细节与边界情况见 [docs/03-数据流与字段映射.md](docs/03-数据流与字段映射.md)。

---

## 7. 与上游的关系

| 上游 | 版本/许可 | 本项目如何对待 |
| --- | --- | --- |
| [xming521/WeClone](https://github.com/xming521/WeClone) | v0.4.01 · **AGPL-3.0** | 源码放在 `vendor/WeClone`，**未修改**；通过 `python -m weclone.cli` + `WECLONE_CONFIG_PATH` 调用 |
| [hicccc77/WeFlow](https://github.com/hicccc77/WeFlow) | v5.0.0 · **CC BY-NC-SA 4.0** | **不包含任何源码**；仅通过 `127.0.0.1:5031` 的 HTTP API 调用 |

升级 WeClone：

```powershell
cd vendor/WeClone
git pull            # 若是 clone 下来的；否则重新下载解压覆盖
cd ../..
wfc init --force    # 重新生成 settings.jsonc（数据路径是绝对路径，必须重生成）
.\scripts\setup.ps1 -SkipTraining
```

> **许可提醒**：WeClone 是 AGPL-3.0（本项目随之以 AGPL-3.0 发布，见 `LICENSE`）；
> WeFlow 是 CC BY-NC-SA 4.0，**禁止商业使用**。因此本项目同样不可用于商业用途。
> 详见 [NOTICE.md](NOTICE.md)。

---

## 8. 安全与隐私

* WeFlow 与 WeClone 都在本机运行，聊天数据不出本机；
* `weflow.jsonc` 里有 API Token，已加入 `.gitignore`，**不要提交**；
* `wfc init` 默认用 `storage_mode: plaintext` —— 因为 WeFlow 导出的是明文 CSV，
  WeClone 的加密模式需要额外密码流程。要求加密就 `wfc init --force --storage encrypted`，
  但要先跑 `weclone-cli security-init`；
* 数据清洗阶段 WeClone 会用 Presidio 过滤电话/邮箱/证件号等，但**不保证 100%**，
  另外可在 `settings.jsonc` 的 `blocked_words` 里加自己的敏感词。

---

## 9. 开发

```powershell
# 测试（不需要 WeFlow、不需要 GPU、不需要 torch）
python -m unittest discover -s tests -t tests -v

# 单独启动模拟 WeFlow，便于手动联调
python -m weflow_clone.tools.mock_weflow --port 5031
```

整合层刻意只依赖标准库，所以「数据链路」可以在一台没有 CUDA 的机器上先跑通、先验证，
再搬到训练机上。

---

## 10. 常见问题

| 现象 | 原因 / 解决 |
| --- | --- |
| `连接被拒绝` | WeFlow 没启动，或「设置 → API 服务」没开，或端口不是 5031 |
| `HTTP 401` | Token 没填 / 填错；见 `weflow.jsonc` 的 `access_token` |
| `没有选中任何会话` | 全是群聊但 `include_groups` 为 false；加 `--groups` |
| `没有任何 is_sender=1 的消息` | 导出范围内只有对方的消息；换个时间范围或确认 `talker/is_sender` 映射 |
| `缺少训练依赖` | 没装 torch/llamafactory：`.\scripts\setup.ps1`（不带 `-SkipTraining`） |
| 图片训练效果差 | WeClone 只认 `dataset/media/images`；确认 `export.media.images` 为 true 且 `wfc prepare` 没有「找不到媒体」告警 |
| exe 双击没反应 | 看 `%LOCALAPPDATA%\WeFlowClone\logs\crash.log`；或在命令行运行看报错 |
| 机器人「不像我」 | 先 `wfc pull` 拉数据，再 `wfc bot --reindex`；也可以调高「参考条数」 |
| 机器人回复很长/很客服 | 模型本身的问题：换更强的模型，或在设置里加「额外要求」 |

更多见 [docs/06-常见问题.md](docs/06-常见问题.md)。

---

## 11. 文档索引

1. [架构设计](docs/01-架构设计.md) —— 分层、为什么零改动、关键取舍
2. [快速开始](docs/02-快速开始.md) —— 从零到分身跑起来
3. [数据流与字段映射](docs/03-数据流与字段映射.md) —— 每个字段从哪来到哪去
4. [WeFlow API 对接](docs/04-WeFlow-API-对接.md) —— 端点、鉴权、分页、SSE、错误处理
5. [训练与部署](docs/05-训练与部署.md) —— 显存、模型、参数、服务化
6. [常见问题](docs/06-常见问题.md)
7. [与上游同步](docs/07-与上游同步.md)
8. [打包与分发](docs/08-打包与分发.md) —— Windows/macOS 产物、签名、CI 发布
9. [内置机器人](docs/09-内置机器人.md) —— 人设、检索、参数调优
10. [发布前检查](docs/10-发布前检查.md) —— 一条命令跑完本地闸门再推 GitHub

---

## 12. 二创与版本

**仓库是源码，Releases 是产物。** 两条链路互不干扰：

| 你想做的事 | 怎么做 |
| --- | --- |
| fork 后改功能 | `CONTRIBUTING.md` 有「目录即架构」与 6 个常见改造点（换数据源 / 换模型 / 改人设 / 改界面 / 加命令 / 走训练） |
| 自己发一个版本 | `python scripts/bump_version.py 1.1.0` → `python scripts/release_check.py --build` → `git tag v1.1.0` → `git push --tags` |
| 只想下载来用 | 直接去 Releases 拿 `win64.zip` / `macos-*.dmg`，不用碰代码 |

发布时 CI 会自动：

1. 校验 tag 与 `pyproject.toml` 版本一致（不一致直接失败，避免「版本漂移」）；
2. 在 Windows、macOS Intel、macOS ARM 上跑测试并构建；
3. 生成 `SHA256SUMS.txt`；
4. 把产物与校验和挂到 Releases，并附上下载说明。

本地想先演练一遍：

```powershell
python scripts\release_check.py            # 静态检查 + 测试 + 校验已有产物
python scripts\release_check.py --build    # 重新构建再检查
```

通过后会生成 `release/RELEASE_NOTES.md` 与 `release/SHA256SUMS.txt`，
可直接贴到 GitHub Release。

> 许可：本项目 AGPL-3.0（含 WeClone 上游），对接 CC BY-NC-SA 4.0 的 WeFlow，**不可商用**。
> fork 后分发（包括作为网络服务运行）需以 AGPL-3.0 公开源码。

---

**请负责任地使用本工具：只处理你自己有权使用的聊天记录，遵守相关法律法规。**
