## 2026-09-18（Gitea 手册：`last_update` 不能用来判断"提交即同步"）

- **背景**：给新仓 `sekb-ondevice-agent` 配好推送镜像后，为确认 `sync_on_commit` 生效，
  去查 `push_mirrors` 的 `last_update` —— 却停在**上一次手动同步**的时间，看起来像没生效。

- **实测结论**：`sync_on_commit` 触发的自动推送**不刷新** `last_update`。
  真正的判据是去 GitHub 侧独立核对（`git ls-remote` 与本地 `git rev-parse HEAD` 比对）——
  实测 GitHub 侧已是本地最新 commit。同理 `last_error` 为空只说明"上次同步没报错"，
  不等于"这次已经同步"。

- **改动**：`docs/ops/12-GITEA.md` 镜像章节补上这条陷阱与核对命令。
