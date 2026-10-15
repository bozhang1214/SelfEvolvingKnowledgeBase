# 变更说明

<!-- 一句话说清"改了什么 / 为什么"。 -->

## 验收（按改动范围勾选 —— **不适用就写"未改"**）

> 为什么要有这张清单：端侧有三个"重环境"门（iOS / Mac / 鸿蒙）在 Linux CI 上跑不了，
> 只能靠人跑。本项目**真漏过**：主线汇报过 iOS，实际只跑了 framework 冒烟（`ios.sh smoke`，
> 验的是共享层纯逻辑），**没跑 app 自检**，而同一次验收里 Mac 跑的是真 app —— 两端强度不对等，谁也没发现。
> 所以这里把"该跑什么"写在 PR 上，让它**在评审时可见**。

- [ ] **文档守卫**：`bash docs/tech/.validation/doc_guard.sh --no-warn`（改了 docs/ 必跑）
- [ ] **共享层**：`bash scripts/verify_all_ends.sh quick`（改了 `apps/shared/**` 必跑）
- [ ] **Android**：`bash scripts/android.sh test`（改了 `apps/android/**` 或共享层必跑）
- [ ] **iOS app**：`bash scripts/ios_record.py run`（改了 `apps/ios/**` 或共享层必跑；会**自动写入记录**）
- [ ] **Mac app**：`bash scripts/mac_app.sh run`（改了 `apps/ios/App/**`（与 Mac 同源）或共享层必跑）
- [ ] **鸿蒙**：`bash scripts/harmony.sh h8selftest 300`（改了 `apps/harmony/**` 或共享层必跑）
- [ ] **四端一次跑齐**：`bash scripts/verify_all_ends.sh full`（发版/收尾必跑）

## iOS 自检记录

<!-- 改了 iOS 相关文件时，`verify_all_ends.sh full` 会提示"记录已陈旧"。补记录后把结论贴在这里。 -->

- 最新记录：<!-- 例：PASS=42 FAIL=0 SKIP=1 @ dd383db（SKIP=keychain_roundtrip，需真实签名身份） -->
- 若 SKIP/FAIL 非空，说明原因：<!-- 环境限制请写清"是什么限制"，不要写"已知问题" -->

## 协作检查（见 AGENTS.md）

- [ ] 没有 `git add -A`；改动是**逐文件** `git add` 的
- [ ] `git diff --cached` 里**没有别人的改动**（共享工作目录下最容易发生）
- [ ] CHANGELOG 走的是**碎片文件**（`scripts/changelog_add.py`），没有直接改 `docs/CHANGELOG.md`
- [ ] 若改了 `napi_init.cpp`（主线独占文件）：已在说明里写明，或确认没动
