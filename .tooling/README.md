# .tooling · 本机构建状态（不入库）

这个目录存在的唯一目的：**把构建工具的状态放在仓库内**，从而不需要在仓库外写文件。

为什么在意：DSH（以及任何受限的构建沙箱）默认只允许写工作区内的文件。Gradle 默认把
缓存写到 `~/.gradle`（本机实测 1.4G），因此每次构建都要额外授权一次。把
`GRADLE_USER_HOME` 指到这里之后，构建、测试、打包都只在这个目录里写。

内容（都被 `.gitignore` 忽略）：

| 路径 | 说明 |
|---|---|
| `gradle-home/` | `GRADLE_USER_HOME`：依赖缓存、wrapper 分发包、daemon 注册表、native 库 |

用 `scripts/android.sh` 自动化（见根 README「多端构建」）。删掉本目录不会损坏仓库，
下次构建会重新下载依赖。
