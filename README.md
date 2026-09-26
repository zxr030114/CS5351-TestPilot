# CS5351-TestPilot 团队协作仓库

这是我们团队任务的共享仓库。所有成员都可以在这里**上传（push）**和**下载（pull）**文件，包括代码、文档、数据等。

## 仓库地址

```
https://github.com/zxr030114/CS5351-TestPilot
```

## 一、第一次使用前的准备

### 1. 安装 Git

- **Windows**：到 https://git-scm.com/download/win 下载安装，一路默认选项即可。
- **macOS**：终端运行 `xcode-select --install`，或用 `brew install git`。
- **Linux**：`sudo apt install git`（Debian/Ubuntu）或 `sudo dnf install git`（Fedora）。

安装完成后，打开终端（Windows 可用 Git Bash），运行 `git --version` 能显示版本号即成功。

### 2. 设置你的身份（只需做一次）

```bash
git config --global user.name "你的名字"
git config --global user.email "你的邮箱"
```

这个名字和邮箱会出现在每次提交记录里，方便大家知道是谁改的。

## 二、把仓库下载到本地（clone）

```bash
git clone https://github.com/zxr030114/CS5351-TestPilot.git
cd CS5351-TestPilot
```

首次克隆时 Git 会要求登录 GitHub。输入你的 GitHub 用户名，密码处填 **Personal Access Token**（不是账号密码）：

1. 打开 https://github.com/settings/tokens
2. 点 **Generate new token (classic)**，勾选 `repo` 权限，生成后复制这串 token；
3. 在 Git 提示输入密码时粘贴它。

Windows 用户也可以安装 [Git Credential Manager](https://github.com/git-ecosystem/git-credential-manager)（Git for Windows 自带），它会弹出浏览器让你登录，之后就不用再输 token 了。

## 三、日常协作流程（最重要）

每次开始工作前和上传前，都先"拉取"一下别人的最新改动，避免冲突：

```bash
# 1. 先拉取最新内容（下载）
git pull

# 2. 添加或修改你的文件（用编辑器、资源管理器直接操作即可）

# 3. 查看改了哪些文件
git status

# 4. 把改动加入待提交列表（. 表示全部）
git add .

# 5. 写一句说明，提交到本地
git commit -m "简述这次改了什么，例如：添加需求分析文档"

# 6. 推送到 GitHub（上传）
git push
```

**口诀：先 pull，再干活，干完 add → commit → push。**

## 四、只想上传一两个文件（网页操作）

不熟悉命令行时，可以直接在网页上操作：

1. 打开 https://github.com/zxr030114/CS5351-TestPilot
2. 点 **Add file → Upload files**
3. 把文件拖进去，下方填一句说明，点 **Commit changes**

## 五、只想下载文件

- **下载整个仓库**：仓库页面点绿色 **Code** 按钮 → **Download ZIP**。
- **网页单个文件**：打开该文件，点右上角下载图标。
- **命令行同步最新版**：在本地仓库目录运行 `git pull`。

## 六、大家同时改了同一个文件怎么办？

`git pull` 或 `git push` 时若提示冲突（conflict）：

1. 打开提示的文件，会看到 `<<<<<<<` 和 `>>>>>>>` 标记的冲突段落；
2. 和队友商量保留哪部分，手动删掉标记符号，整理成最终内容；
3. 然后：

```bash
git add .
git commit -m "解决冲突"
git push
```

## 七、常用命令速查

| 命令 | 作用 |
| --- | --- |
| `git pull` | 下载别人的最新改动 |
| `git status` | 查看当前改动状态 |
| `git add .` | 把所有改动加入待提交 |
| `git commit -m "说明"` | 提交到本地并写说明 |
| `git push` | 上传到 GitHub |
| `git log --oneline` | 查看提交历史 |

## 八、小贴士

- **每次开工前先 `git pull`**，能避免绝大多数冲突。
- 提交说明写清楚做了什么，方便队友回溯。
- 不要把密码、密钥、个人隐私文件放进仓库（公开仓库所有人都能看到）。
- Windows 下若中文文件名显示为乱码，运行一次 `git config --global core.quotepath false` 即可。
- 大文件（>100MB）GitHub 会拒绝，请先压缩或与团队商量存放方式。

## 团队成员

| 姓名 | GitHub 用户名 | 负责部分 |
| --- | --- | --- |
| 待填写 | 待填写 | 待填写 |
