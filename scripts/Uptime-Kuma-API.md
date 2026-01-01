# Uptime-Kuma 自动部署到 HuggingFace Space

通过 GitHub Actions 一键部署 Uptime-Kuma 监控服务到 HuggingFace Space，支持自动备份到 GitHub。

---

## ✨ 功能特点

- 🚀 一键部署 Uptime-Kuma 到 HuggingFace Space
- 🔄 自动备份数据到 GitHub 仓库
- 🔐 支持备份加密
- 🌐 支持 Cloudflare Tunnel 穿透
- 🧹 自动清理旧的工作流记录

---

## 📁 文件结构

```
├── .github/
│   └── workflows/
│       └── Uptime-Kuma-API.yml    # 工作流配置
└── scripts/
    └── Uptime-Kuma-API.py         # 部署脚本
    └── Uptime-Kuma-API.md
```

---

## 🚀 使用方法

### 方式一：手动触发

1. 进入仓库的 **Actions** 页面
2. 选择 **使用抱脸SDK创建Uptime-Kuma监控**
3. 点击 **Run workflow**
4. 填写参数后运行

### 方式二：Webhook 触发

配置 Webhook 实现自动触发部署。

#### Webhook 配置

| 配置项 | 值 |
|--------|-----|
| **类型** | Webhook |
| **显示名称** | 触发部署 Uptime-Kuma |
| **Post URL** | `https://api.github.com/repos/{用户名}/{仓库名}/actions/workflows/Uptime-Kuma-API.yml/dispatches` |

#### 请求体

```json
{
  "ref": "main",
  "inputs": {
    "HF_TOKEN": "hf_xxxxx",
    "IMAGE": "docker镜像地址",
    "HF_SPACE_NAME": "uk",
    "GITHUB_REPO": "owner/repo",
    "GITHUB_TOKEN": "ghp_xxxxx",
    "GITHUB_BRANCH": "main",
    "BACKUP_HOUR": "4",
    "KEEP_BACKUPS": "5",
    "BACKUP_PASS": "",
    "CF_TUNNEL_TOKEN": ""
  }
}
```

#### 请求头

```json
{
  "Content-Type": "application/json",
  "Accept": "application/vnd.github.v3+json",
  "Authorization": "token ghp_xxxxx"
}
```

![Webhook 配置](https://raw.githubusercontent.com/oyz8/action/refs/heads/main/img/Uptime-Kuma-Webhook%E9%85%8D%E7%BD%AE.png)

---

## 📝 参数说明

### 工作流输入参数

| 参数 | 必填 | 默认值 | 说明 |
|------|:----:|--------|------|
| `HF_TOKEN` | ✅ | - | HuggingFace Token（需要写权限） |
| `IMAGE` | ✅ | - | Uptime-Kuma Docker 镜像地址 |
| `HF_SPACE_NAME` | ❌ | `uk` | HuggingFace Space 名称 |
| `GITHUB_REPO` | ✅ | - | 备份仓库（格式：`owner/repo`） |
| `GITHUB_TOKEN` | ✅ | - | GitHub 访问令牌 |
| `GITHUB_BRANCH` | ❌ | `main` | 备份分支 |
| `BACKUP_HOUR` | ❌ | `4` | 自动备份时间（小时，0-23） |
| `KEEP_BACKUPS` | ❌ | `5` | 保留备份数量 |
| `BACKUP_PASS` | ❌ | - | 备份加密密码 |
| `CF_TUNNEL_TOKEN` | ❌ | - | Cloudflare Tunnel Token |

### Space Secrets（自动配置）

部署时会自动在 HuggingFace Space 中配置以下 Secrets：

| Secret | 说明 |
|--------|------|
| `GITHUB_TOKEN` | GitHub 访问令牌 |
| `GITHUB_REPO` | 备份仓库地址 |
| `GITHUB_BRANCH` | 备份分支 |
| `BACKUP_HOUR` | 备份时间 |
| `KEEP_BACKUPS` | 保留数量 |
| `BACKUP_PASS` | 加密密码（可选） |
| `CF_TUNNEL_TOKEN` | CF Tunnel（可选） |

---

## 🔑 Token 获取

### HuggingFace Token

1. 登录 [HuggingFace](https://huggingface.co/)
2. 进入 **Settings** → **Access Tokens**
3. 创建新 Token，选择 **Write** 权限

### GitHub Token

1. 登录 GitHub
2. 进入 **Settings** → **Developer settings** → **Personal access tokens**
3. 创建 Token，勾选以下权限：
   - `repo`（完整仓库访问）
   - `workflow`（工作流权限）

---

## ⚠️ 注意事项

1. **文件名大小写**：确保 `Uptime-Kuma-API.py` 大小写一致
2. **参数名称**：Webhook 中的 `inputs` 参数名必须完全匹配（区分大小写）
3. **Token 权限**：
   - HF Token 需要 **Write** 权限
   - GitHub Token 需要 **repo** 和 **workflow** 权限
4. **备份仓库**：需提前创建好备份用的 GitHub 仓库
5. **Space 重建**：每次运行会删除已存在的同名 Space 后重新创建

---

## 📋 部署流程

```
1. 触发工作流（手动/Webhook）
     ↓
2. 验证 HuggingFace Token
     ↓
3. 删除已存在的同名 Space
     ↓
4. 创建新 Space 并配置 Secrets
     ↓
5. 上传 README.md 和 Dockerfile
     ↓
6. HuggingFace 自动构建镜像
     ↓
7. 部署完成 ✅
```

---

## 🔗 相关链接

- [Uptime-Kuma](https://github.com/louislam/uptime-kuma)
- [HuggingFace Spaces](https://huggingface.co/spaces)
- [HuggingFace Hub Python SDK](https://huggingface.co/docs/huggingface_hub/)

---

## 📄 License

MIT License
