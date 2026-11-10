# Fork 镜像发布

修复基线为官方标签 `1.9.0`，修复在 `1.9.0-fix` 分支维护。普通分支 push 不触发镜像构建。推送匹配 `*.*.*` 的版本标签（如 `1.9.0-fix-1`）时，release 工作流先验证后端和前端，再构建并发布 amd64/arm64 镜像；同时保留手动发布入口。

在 fork 的 Settings → Secrets and variables → Actions 配置：

| 类型 | 名称 | 内容 |
| --- | --- | --- |
| Repository variable | `DOCKERHUB_NAMESPACE` | 镜像命名空间（个人账号或组织），例如 `wangyulie`；未配置默认 `sunxiao0721` |
| Repository secret | `DOCKERHUB_USERNAME` | 用于登录 Docker Hub 的账号，需有目标命名空间的推送权限 |
| Repository secret | `DOCKERHUB_TOKEN` | 登录账号的 Docker Hub Token |

镜像地址使用 `${{ vars.DOCKERHUB_NAMESPACE || 'sunxiao0721' }}/beecount-cloud`。登录保持上游方式，使用 `DOCKERHUB_USERNAME` 和 `DOCKERHUB_TOKEN` 两个 Secret；命名空间与登录账号独立配置。Fork 不会继承上游 Secret；如果 Actions 尚未启用，先在 Actions 页面启用工作流。请在首次发布前配置自己的命名空间和登录凭据。

发布示例：

```bash
git switch 1.9.0-fix
git push origin 1.9.0-fix
git tag -a 1.9.0-fix-1 -m "PostgreSQL compatibility fixes"
git push origin 1.9.0-fix-1
```

标签标记当前提交；继续提交 1.9.0-fix 不会移动已有标签。后续发布使用新标签，如 `1.9.0-fix-2`，不强制覆盖旧标签。发布后镜像示例：`wangyulie/beecount-cloud:1.9.0-fix-1`。标签发布沿用上游行为，同时更新 latest 并创建 GitHub Release；手动发布可关闭 push_latest。

构建成功后核对镜像 revision 与 Git 提交，拉取发布的镜像并固定 RepoDigest，再通过 Compose 更新本机。构建未成功时保留当前服务；不能用本地构建替代发布。凭据、账本、数据库和家庭实例私有配置不得提交。

当前修正包含 PostgreSQL upsert、JSON 长度统计，以及 WebSocket 鉴权和周期规则初始扫描的线程调度。没有修改历史迁移脚本，也未实现 PostgreSQL 的内置备份恢复；新库初始化与 PostgreSQL 备份仍需独立流程。
