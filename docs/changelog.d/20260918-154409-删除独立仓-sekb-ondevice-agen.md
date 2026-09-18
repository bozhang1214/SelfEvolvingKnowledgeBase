## 2026-09-18（删除独立仓 `sekb-ondevice-agent`）

- **背景**：端侧代码已并入主仓 `apps/android/`（RFC §17），独立仓没有存在意义，
  留着反而制造"两个源码真相"。

- **改动**：
  - **Gitea 侧删除**：`DELETE /repos/bo/sekb-ondevice-agent` → 204，复核 404。
  - **GitHub 侧归档**：`PATCH {"archived": true}` → 成功（复核 `archived=true`）。
    ⚠️ 令牌缺 `delete_repo` 权限，**彻底删除需业主在 GitHub UI 操作**，或给令牌加该权限。
  - `scripts/gitea_mirror.py` 的 `REPO_MAP` 去掉该条目（否则 `status` 会对不存在的仓库报 ❌）；
    `docs/ops/12-GITEA.md` 当前状态改回**四个**仓库；`apps/android/README.md` 同步。

- **验证**：Gitea 复核 404；GitHub 复核 `archived=true`；`gitea_mirror.py status` 四仓全绿。
