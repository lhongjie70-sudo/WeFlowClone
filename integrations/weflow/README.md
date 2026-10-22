# WeFlow 侧对接说明

本项目**不修改 WeFlow**，只做两件事：

1. 通过 WeFlow 的 HTTP API 自动拉数据（默认方式）；
2. 兼容 WeFlow 的手工导出，作为 API 不可用时的退路。

---

## 方式一：API 直连（推荐）

### 在 WeFlow 里打开

1. 打开 WeFlow，确认已连接微信；
2. **设置 → API 服务** → 启动服务（默认 `127.0.0.1:5031`）；
3. 复制 **Access Token**；
4. （可选）打开 **主动推送**，`wfc watch` 依赖它。

### 在 WeFlowClone 里填

```jsonc
// weflow.jsonc
"weflow": {
  "base_url": "http://127.0.0.1:5031",
  "access_token": "粘贴 Token",
  "include_groups": false
}
```

或者设置环境变量：`$env:WEFLOW_TOKEN = "..."`。

### 验证

```powershell
curl http://127.0.0.1:5031/health     # {"status":"ok"}
wfc sessions                          # 列出会话
wfc pull --session 张三               # 单会话试拉
```

用到的端点、字段映射、错误处理见 [`docs/04-WeFlow-API-对接.md`](../../docs/04-WeFlow-API-对接.md)。

---

## 方式二：用 WeFlow 自带的「WeClone」导出

WeFlow 的导出面板里已经有 **WeClone** 格式（`electron/services/export/formatters/WeCloneFormatter.ts`），
它产出的 CSV 表头与 WeClone 完全一致：

```
id,MsgSvrID,type_name,is_sender,talker,msg,src,CreateTime
```

所以你可以：

1. 在 WeFlow 里选联系人 → **导出** → 格式选 **WeClone** → 选一个目录；
2. 回到 WeFlowClone：

```powershell
wfc import "D:\我的导出\weclone"
wfc prepare
```

`wfc import` 会：

* 校验表头（缺列直接跳过并提示）；
* 重新编号 `id`，按 `_1_N` 规则生成目标文件名；
* 把同目录下的图片按 basename 复制进 `data/dataset/media/images`；
* 处理多个会话文件夹。

这样即使 API 服务打不开（端口冲突、版本差异），数据链路也不中断。

---

## 方式三：ChatLab / 原始 JSON

WeFlow 还能导出 ChatLab 标准格式和原始 JSON。同样可以：

```powershell
wfc import "D:\导出\chatlab.json"
wfc import "D:\导出\raw.json"
```

ChatLab 缺 `isSend`，我们用 `meta.ownerId` 反推 `is_sender` ——
如果导出的 ChatLab 里没有 `ownerId`，会全部被判成「对方」，
`wfc prepare` 会报「没有任何 is_sender=1 的消息」，这时请改用方式一或二。

---

## 实时同步

WeFlow 的推送（`GET /api/v1/push/messages`）只在**开启主动推送**后可用。
本项目不用事件里的 `content` 造数据（它是摘要，图片会变成 `[图片]`），
只用它判断「哪个会话变了」，然后整份重拉，保证与数据库一致。

```powershell
wfc watch        # 默认 20 秒安静期后重拉
```

调整：

```jsonc
"watch": { "debounce_seconds": 60 }
```

---

## 常见对接问题

| 现象 | 解决 |
| --- | --- |
| `连接被拒绝` | API 服务没启动，或端口不是 5031 |
| `HTTP 401` | Token 错；重新复制 |
| 只有部分会话 | `weflow.include` / `exclude`；群聊需 `include_groups: true` |
| 拉到的消息很少 | WeFlow 侧数据库只保留近期消息，或用 `--since` 限定范围 |
| 图片没导出来 | `export.media.images: true`；WeFlow 里该图片需能正常预览 |
