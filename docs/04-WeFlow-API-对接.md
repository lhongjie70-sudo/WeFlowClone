# WeFlow API 对接

本文档记录整合层实际使用的 WeFlow 接口（对应 WeFlow 官方 `docs/HTTP-API.md`），
以及我们在客户端里的处理方式。

## 1. 基础

| 项 | 值 |
| --- | --- |
| 地址 | `http://127.0.0.1:5031`（仅本机） |
| 启用 | WeFlow → 设置 → API 服务 → 启动服务 |
| 鉴权 | `Authorization: Bearer <Token>`；SSE 也可用 `?access_token=` |
| 格式 | `application/json`；SSE 为 `text/event-stream` |
| 状态记忆 | 服务状态与端口会随 WeFlow 重启自动恢复 |

客户端实现：`weflow_clone/weflow/client.py`，纯 `urllib`，无第三方依赖。

## 2. 用到的端点

| 端点 | 用途 | 关键参数 / 字段 |
| --- | --- | --- |
| `GET /health` | 连通性检查（免鉴权） | `{"status":"ok"}` |
| `GET /api/v1/sessions` | 会话列表 | `keyword`、`limit`；`sessions[].username/displayName/type/lastTimestamp` |
| `GET /api/v1/messages` | 会话消息（核心） | `talker`、`limit`(1~10000)、`offset`、`start`、`end`、`chatlab`、`media`、`image/voice/video/emoji` |
| `GET /api/v1/contacts` | 联系人（取备注名） | `keyword`、`limit`；`contacts[].remark/nickName/displayName` |
| `GET /api/v1/group-members` | 群成员（取群昵称） | `chatroomId`、`includeMessageCounts`；`members[].groupNickname/displayName` |
| `GET /api/v1/media/<path>` | 下载媒体 | 需先对该消息请求过 `media=1` |
| `GET /api/v1/push/messages` | SSE 实时推送 | 事件 `message.new` / `message.revoke` |

未使用（保留给未来功能）：`/api/v1/sessions/:id/messages`（ChatLab Pull）、
`/api/v1/sns/*`（朋友圈）。

## 3. 消息字段映射

WeFlow 原始 JSON → 我们的 `Message`：

| WeFlow 字段 | 含义 | 我们的处理 |
| --- | --- | --- |
| `localId` | 本地自增 ID | 去重兜底键 |
| `serverId` | 平台消息 ID | → `MsgSvrID` |
| `localType` | 消息类型数字 | → `type_name`（见字段映射文档） |
| `createTime` | 秒级时间戳 | 归一化到秒（兼容毫秒/微秒）后格式化 |
| `isSend` | 1 = 我发出 | → `is_sender` |
| `senderUsername` | 发送者 wxid | 查备注/群昵称 → `talker` |
| `content` | 原文（appmsg 为 XML） | 文本抽取的输入 |
| `parsedContent` | WeFlow 解析后的文本 | 优先用作文本 |
| `mediaLocalPath` | WeFlow 已导出的本地文件 | 优先**直接复制**（不重复下载） |
| `mediaUrl` | 可访问的 HTTP 地址 | 本地文件不存在时下载 |
| `mediaFileName` | 文件名 | `src` 的 basename |
| `mediaType` | `image` / `voice` / ... | 辅助判断 |
| `replyToMessageId` | 引用回复目标 | 目前不额外使用（引用文本已并入 `content`） |

**为什么优先用 `mediaLocalPath`？**
WeFlow 在 `media=1` 时已经把媒体导出到它自己的目录，再走 HTTP 下载等于复制两遍。
只有跨机器或文件被清理时才退回 HTTP。

## 4. 分页

WeFlow 用 `limit` + `offset`，响应里 `hasMore` 表示还有没有。

```python
offset = 0
while True:
    page = messages(talker, limit=1000, offset=offset)
    ...
    offset += len(page.messages)
    if not page.hasMore:
        break
```

我们的处理细节：

* `page_size` 可配（`weflow.page_size`，上限 10000）；
* 按 `serverId` 去重（分页边界可能重复）；
* 如果一页里全是重复（`new_count == 0`）就停止，防止 WeFlow 的 `hasMore` 异常导致死循环；
* `--limit N` 时达到 N 条就提前结束。

## 5. 时间过滤

`start` / `end` 支持 `YYYYMMDD` 或时间戳。整合层的 `--since 2024-01-01`
会在客户端先规范成 `20240801` 再发出去（`sync.normalize_date`）。

注意：`end` 为纯 `YYYYMMDD` 时，WeFlow 会扩展到当天 `23:59:59`。

## 6. 媒体

```
GET /api/v1/messages?talker=...&media=1&image=1&voice=0&video=0&emoji=0
```

响应里每条媒体消息带 `mediaLocalPath` / `mediaUrl`。
整合层按 `export.media.*` 决定下载哪些类型，默认只下图片（WeClone 多模态只认图片）。

文件名冲突：WeFlow 用内容哈希命名，天然去重；我们落盘时也按 basename 去重。

大小限制：`export.media.max_mb`（默认 20MB），超限直接丢弃并留空 `src`。

## 7. SSE 实时推送

```
GET /api/v1/push/messages?access_token=<Token>
```

事件形如：

```
event: message.new
data: {"event":"message.new","sessionId":"wxid_xxx","sessionType":"other",
       "rawid":"...","sourceName":"张三","content":"在吗","timestamp":1760000123}
```

`wfc watch` 的行为：

1. 收到事件 → 记下 `sessionId`；
2. 距上次同步超过 `watch.debounce_seconds`（默认 20s）就起一个后台线程；
3. 线程对该会话做「全量重拉」（理由见架构文档 §3.3）；
4. 连接断开后 5 秒重连。

> 推送只用于「知道哪个会话变了」，**不用事件里的 `content` 造数据** ——
> 它只是摘要（图片会变成 `[图片]`），以数据库拉取结果为准才不会有偏差。

## 8. 错误处理

| 情况 | 客户端行为 |
| --- | --- |
| `401/403` | 抛 `WeFlowAuthError`，提示 Token 位置 |
| `5xx` | 退避重试（`weflow.retries`，默认 2 次） |
| `URLError/refused` | 抛 `WeFlowUnavailable`，提示「启动 WeFlow API 服务」 |
| 超时 | 重试后提示调大 `timeout_seconds` |
| 非 JSON 响应 | 抛 `WeFlowApiError`，附路径 |

所有错误都是 `WeFlowCloneError` 子类，CLI 统一捕获后打印中文建议，不打印 traceback。

## 9. 与 ChatLab 的关系

WeFlow 同时支持 ChatLab 标准格式（`chatlab=1`）。本项目：

* **拉取**用原始 JSON（字段更全：`localType`、`mediaLocalPath`、`isSend`）；
* **导入**支持 ChatLab（人手工导出的常见格式），其中 `type` 数字按
  WeFlow 的 `MESSAGE_TYPE_MAP` 反查成 `localType`：

| ChatLab `type` | 含义 | → `localType` |
| --- | --- | --- |
| 0 | 文本 | 1 |
| 1 | 图片 | 3 |
| 2 | 语音 | 34 |
| 3 | 视频 | 43 |
| 4 / 7 | 文件 / 链接 | 49 |
| 5 | 表情 | 47 |
| 8 | 位置 | 48 |
| 80 | 系统 | 10000 |

## 10. 联调技巧

没有微信 / 不想动真实数据时：

```powershell
# 启动一个行为一致的模拟服务（内置合成聊天数据）
python -m weflow_clone.tools.mock_weflow --port 5031 --token demo-token

# 另开一个终端
wfc pull --groups
```

模拟服务实现了 `health / sessions / messages / contacts / group-members / media / SSE`，
支持鉴权、分页、时间过滤、ChatLab 输出 —— 足够覆盖客户端的全部分支。
