STEP4_SKILLS_ROOT={skills_root}
官方 HyperFrames skills 版本：{hyperframes_version}（与 CLI 同版本）

读取规则：
- 写本段所需的技术契约、风格和检查命令已内置在本提示词中：直接规划并写 `index.html`，不要先浏览 skills。
- 只有内置内容确实不够时才读参考文件，整个任务最多读取 {reference_limit} 个；直接 Read 下列绝对路径。
- 不要用 Glob/Grep/ls 浏览 skills 目录，不要为了“找路”先读 SKILL.md 路由文件。

可选参考 1：所选 motionMotif 的动效配方（最多读 1 个）：
{recipe_paths}

可选参考 2：按需读取：
{reference_paths}

完整官方 skills（只有以上参考都不覆盖时才读取对应 entry_path）：
{skill_entries}
