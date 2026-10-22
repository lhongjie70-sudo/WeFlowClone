# 参与开发 / 二次创作

先把三件事分清楚，后面就不会乱：

| 位置 | 是什么 | 谁用 |
| --- | --- | --- |
| **本仓库（源码）** | 完整可改的代码，含 `vendor/WeClone` 上游源码 | 想改功能、换数据源、自己训练的人 |
| **Releases（产物）** | CI 在打 tag 时构建的 Windows zip / macOS dmg | 只想下载就用的人 |
| **`docs/`** | 10 篇设计与使用文档 | 所有人 |

仓库里**不提交任何构建产物**（`dist/`、`data/`、`release/` 均已忽略），
`scripts/release_check.py` 会强制检查这一点。

---

## 1. 五分钟把开发环境跑起来

```powershell
git clone <你的 fork> WeFlowClone
cd WeFlowClone
python -m venv .venv
.\.venv\Scripts\activate            # macOS: source .venv/bin/activate
pip install -e .

# 跑测试（不需要微信、不需要 GPU、不需要 torch）
python -m unittest discover -s tests -t tests -v

# 交互式体验：内置模拟 WeFlow，合成聊天数据
wfc demo
wfc mock --port 5031                # 另开一个终端，模拟 WeFlow API
```

开发时不一定需要 `vendor/WeClone`：**数据链路与机器人完全不依赖它**，
只有 `wfc prepare --make-dataset` / `train` / `profile` / `serve` 才会调用上游。

---

## 2. 目录即架构

```
weflow_clone/
├─ cli.py                 命令与参数（新增子命令从这里开始）
├─ sync.py                选会话 / 拉取 / 监听推送
├─ config.py              weflow.jsonc 的解析（自带 JSONC）
├─ paths.py               项目根；打包后自动切到用户数据目录
├─ weflow/
│  ├─ client.py           WeFlow HTTP API（鉴权 / 分页 / 媒体 / SSE）
│  ├─ schema.py           原始 JSON 与 ChatLab 两种消息模型
│  ├─ mapping.py          localType → type_name、文本抽取、CSV 转义
│  ├─ exporter.py         写 dataset/csv + users.json + 媒体
│  └─ importer.py         导入 WeFlow 手工导出
├─ pipeline/
│  ├─ weclone.py          以正确的 cwd/env 调用 weclone-cli
│  ├─ bootstrap.py        wfc init、首次运行目录
│  ├─ dataset.py          统计与校验
│  └─ doctor.py           自检
├─ bot/
│  ├─ persona.py          从聊天记录提炼说话习惯 → system prompt
│  ├─ retriever.py        BM25 检索历史问答
│  ├─ llm.py              任意 OpenAI 兼容接口（流式）
│  ├─ service.py          拼装上下文
│  ├─ server.py           网页聊天 + OpenAI 兼容 /v1
│  └─ web/index.html      单文件界面（无外链）
└─ tools/                 模拟 WeFlow + 合成数据（测试与演示）
```

依赖方向是单向的：`cli → sync/pipeline/bot → weflow/config`。
`weflow/` 不知道 `bot/` 的存在，`bot/` 也不依赖 `pipeline/`。

---

## 3. 常见改造点

### 3.1 换一个聊天数据源（不是微信）

比如从 Telegram、QQ、自建 IM 导入：

1. 实现 `weflow_clone/<source>/client.py`，产出 `schema.Message` 列表；
2. 复用 `weflow/exporter.py`（它会处理类型映射、媒体、分卷）；
3. 在 `cli.py` 加一个子命令（或复用 `pull`，把 client 做成可注入的）。

**不要改 WeClone**：只要最终落成那 8 列 CSV，上游就能吃。

### 3.2 换模型 / 服务商

`bot/llm.py` 只要求 `/chat/completions`。已有 OpenAI 兼容协议的服务
（DeepSeek、Moonshot、通义、OpenAI、Ollama、vLLM、LM Studio）**不用改代码**，
在设置面板里换 `api_base` / `model` 即可。

要接非 OpenAI 协议（如 Anthropic 原生、Gemini 原生）：在 `LLMClient` 里加一个
`_open()` 分支，保持 `chat()` / `chat_stream()` 签名不变，上层零改动。

### 3.3 改「像不像」

* 说话习惯的措辞与规则：`bot/persona.py::compose_prompt`
* 统计口径（句长、语气词、表情）：`build_persona`
* 检索召回（BM25 参数、top_k、分词）：`bot/retriever.py`
* 上下文拼装（历史轮数、截断）：`bot/service.py`

改完跑 `wfc bot --reindex` 立刻生效，**不需要重新训练**。

### 3.4 改界面

`bot/web/index.html` 是单文件、零依赖、零构建。改完刷新浏览器即可。
新增接口时在 `bot/server.py` 的 `do_GET / do_POST` 里加路由。

### 3.5 加一条命令

1. `cli.py` 里写 `cmd_xxx(args)`；
2. `build_parser()` 里 `xxx = add("xxx", help="…")` 并 `set_defaults(func=cmd_xxx)`；
3. 在 `tests/test_cli.py` 加一条参数解析测试。

### 3.6 走训练路线

`pipeline/weclone.py` 只是「传参 + 设 cwd + 设环境变量」。要改训练行为，
优先改 `vendor/WeClone/settings.jsonc`（由 `wfc init` 生成）；
确实要动上游代码，就在 fork 里改 `vendor/WeClone` 并保留其 LICENSE。

---

## 4. 代码约定

* **标准库优先**：整合层不引入第三方运行时依赖，保证「没装 torch 也能跑数据链路」；
* **错误要说人话**：所有异常继承 `WeFlowCloneError`，消息里写「为什么 + 怎么办」；
* **不要在日志里打印 Token / Key**；
* **新增行为要有测试**：`tests/` 里用标准库 `unittest`，不依赖网络与 GPU；
* **中文注释与文档**：目标用户是中文用户；
* 提交前跑：

```powershell
python scripts/release_check.py --no-artifacts
```

---

## 5. 版本与发布（维护者）

```powershell
python scripts/bump_version.py 1.1.0     # 同步 5 处版本号 + 文档示例名
python scripts/release_check.py --build  # 测试 + 构建 + 产物校验 + 出发布物料
git commit -am "release: v1.1.0"
git tag v1.1.0
git push origin main --tags              # 触发三平台构建并自动发 Release
```

CI（`.github/workflows/release.yml`）会：

1. 校验 tag 与 `pyproject.toml` 版本一致（不一致直接失败）；
2. 在 Windows / macOS Intel / macOS ARM 上跑测试并构建；
3. 生成 `SHA256SUMS.txt`；
4. 把 zip / dmg / 校验和挂到 Releases。

产物命名带版本号与架构，便于长期并存：

```
WeFlowClone-1.1.0-win64.zip
WeFlowClone-1.1.0-macos-arm64.dmg
WeFlowClone-1.1.0-macos-x86_64.dmg
SHA256SUMS.txt
```

---

## 6. 许可义务（重要）

* 本项目整体 **AGPL-3.0**：你 fork 后分发（包括作为网络服务提供）必须
  以同许可公开源码，并保留版权与许可声明；
* `vendor/WeClone` 是上游 AGPL-3.0 源码，**不要删除它的 LICENSE**，
  修改它等于修改 AGPL 作品，同样要开源；
* WeFlow 是 **CC BY-NC-SA 4.0（禁止商用）**，本项目只通过其本地 HTTP API 对接、
  未分发其源码。基于本项目做商业产品前，必须先解决 WeFlow 的授权问题；
* 用他人聊天记录训练/生成内容，需取得对方同意，并遵守当地隐私法规。

---

## 7. PR 检查清单

- [ ] `python scripts/release_check.py --no-artifacts` 全绿
- [ ] 新增/修改的行为有测试
- [ ] 没有提交 `dist/`、`data/`、`weflow.jsonc`、`settings.jsonc`、任何 Key
- [ ] 文档同步更新（README / `docs/` 对应章节）
- [ ] 面向用户的报错信息是中文且可执行
- [ ] 涉及界面改动时，附一张截图
