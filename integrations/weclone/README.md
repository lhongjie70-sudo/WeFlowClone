# WeClone 侧对接说明

本项目对 `vendor/WeClone` **零改动**。本文说明「凭什么能不改」以及「改了会怎样」。

---

## 1. WeClone 的两个硬约束

| 约束 | 出处 | 本项目的应对 |
| --- | --- | --- |
| cwd 必须有 `pyproject.toml` 且 `project.name == "WeClone"` | `weclone/cli.py::_check_project_root` | 所有调用都在 `cwd=vendor/WeClone` 下执行 |
| `settings.jsonc` 必须在包目录的上一级 | `_check_versions`：`ROOT_DIR = Path(__file__).parent.parent` | 配置写在 `vendor/WeClone/settings.jsonc`（由 `wfc init` 生成） |

如果按「把 weclone 包复制进本项目根」的方式整合，第一条会直接 `sys.exit(1)`。

---

## 2. 调用的子命令

| 本项目命令 | 实际执行 | 说明 |
| --- | --- | --- |
| `wfc prepare --make-dataset` | `weclone-cli make-dataset` | 读 `csv_folder`，产出 `dataset_dir` |
| `wfc profile` | `weclone-cli build-profile` | 抽取画像 / 记忆 |
| `wfc train` | `weclone-cli train-sft` | LoRA 微调 |
| `wfc serve` | `weclone-cli server --host --port [--inference]` | 服务 + Web UI |
| `wfc weclone <任意>` | `weclone-cli <任意>` | 完整透传 |

实现：`weflow_clone/pipeline/weclone.py`。
所有调用都会设置：

```python
env["WECLONE_CONFIG_PATH"] = "<项目根>/vendor/WeClone/settings.jsonc"
cwd = "<项目根>/vendor/WeClone"
env["PYTHONUTF8"] = "1"
```

---

## 3. 生成的 `settings.jsonc` 改了什么

`wfc init` 以 `vendor/WeClone/settings.template.jsonc` 为底，只改**路径**与**存储模式**：

```jsonc
{
  "security_args": { "storage_mode": "plaintext" },

  "common_args": {
    "model_name_or_path": "<项目根>/data/models/Qwen2.5-7B-Instruct",
    "adapter_name_or_path": "<项目根>/data/model_output",
    "media_dir": "<项目根>/data/dataset/media"
  },

  "make_dataset_args": {
    "platform": "chat",
    "csv_folder": "<项目根>/data/dataset/csv"
  },

  "train_sft_args": {
    "dataset_dir": "<项目根>/data/res_csv/sft"
  }
}
```

为什么用绝对路径：WeClone 以 cwd 解析相对路径，而我们的 cwd 固定是 `vendor/WeClone`；
写相对路径会导致数据被写进 `vendor/WeClone/dataset`，找都找不到。

其余参数（`lora_rank`、`cutoff_len`、`blocked_words`、`clean_dataset`…）保持官方默认，
按需自行修改 —— 这些属于 WeClone 的领域，本项目不代管。

---

## 4. 数据契约

`dataset/csv/<会话>/<会话>_<起>_<止>.csv`，8 列：
`id,MsgSvrID,type_name,is_sender,talker,msg,src,CreateTime`。

字段语义见 [`docs/03-数据流与字段映射.md`](../../docs/03-数据流与字段映射.md)。

`platform` 仍用 `"chat"`（即 WeClone 的「微信/PyWxDump」分支）—— 我们的 CSV 与它结构一致，
不需要新增平台枚举，这也是「零改动」能成立的前提。

---

## 5. 升级 WeClone 时要注意

```powershell
cd vendor\WeClone && git pull && cd ..\..
wfc init --force
wfc doctor
```

* `config_version` 变化 → 必须重新生成配置（`--force`）；
* CLI 子命令改名 → 改 `pipeline/weclone.py` 对应方法；
* CSV 契约变化 → 改 `weflow_clone/weflow/mapping.py`，测试会兜底。

详见 [`docs/07-与上游同步.md`](../../docs/07-与上游同步.md)。
