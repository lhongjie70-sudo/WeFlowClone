# NOTICE / 第三方许可

WeFlowClone 自身是整合层代码，但它**依赖并分发**了第三方项目的成果，许可是分层的。

## 1. 本项目代码

`weflow_clone/`、`scripts/`、`docs/`、`tests/`、`integrations/` 等由本项目编写的内容，
以 **AGPL-3.0** 发布（见 `LICENSE`）—— 因为本项目分发了 AGPL-3.0 的 WeClone 源码，
必须整体遵循 AGPL-3.0。

## 2. WeClone（vendored）

* 项目：https://github.com/xming521/WeClone
* 作者：xming521 及贡献者
* 版本：v0.4.01（`vendor/WeClone`，**未做任何修改**）
* 许可：**GNU Affero General Public License v3.0**（`vendor/WeClone/LICENSE`）

AGPL-3.0 要求：分发本软件（含修改版）时必须以 AGPL-3.0 提供完整源码，
且通过网络提供服务时也要向使用者提供源码。

## 3. WeFlow（未分发源码，仅通过本地 HTTP API 对接）

* 项目：https://github.com/hicccc77/WeFlow
* 作者：hicccc77 及贡献者
* 版本：v5.0.0
* 许可：**Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International**
  （CC BY-NC-SA 4.0）

关键限制：

* **禁止商业使用（NonCommercial）**；
* 若修改/再分发 WeFlow 的内容，需以相同许可共享（ShareAlike）并署名。

本项目**没有**包含、修改或再分发 WeFlow 的源代码；它只在用户本机上通过
`http://127.0.0.1:5031` 调用用户自己安装的 WeFlow。但既然本项目的用途与
WeFlow 强绑定，请将整体使用场景视为**非商业**。

如果你的场景需要商用，需要：

1. 替换 WeFlow 为自有的数据获取方案（例如 WeClone 上游支持的
   Telegram / 其他数据源），或
2. 取得 WeFlow 作者的商业授权。

## 4. 模型与数据集

* 默认模型 Qwen2.5-7B-Instruct 由其自身许可（Apache-2.0）约束；
* **用你的聊天记录微调出来的模型，版权与责任归你**；
* 请勿在未取得对方同意的情况下，把包含他人隐私的模型或数据集公开分发。

## 5. 合规提醒

* 只处理你**有权访问**的聊天记录（自己的账号、自己的设备）；
* 导出与训练他人在不知情下的对话，可能违反当地隐私法规；
* 数字分身不应被用于冒充他人身份、诈骗或误导性用途。

---

本项目作者不对使用者的合规性负责。使用即表示你理解并接受上述许可与风险。
