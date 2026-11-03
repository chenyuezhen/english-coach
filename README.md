# English Coach

日常办公用英文写作，遇到表达卡点时记一笔。系统按你的真实卡点出题、定期归纳，反复练到不再错。

不是背单词软件，也不做语法讲解。它只关心一件事：**这个场合，这句话该怎么说。**

## 怎么用

在任意 App 里选中你写的英文（或只写中文意图），按 `⌃⌥E`，补一句中文说明，回车。

结果会给出三档表达（随口 / 工作 / 正式）、解释为什么，并存入你的卡点库。

之后每天打开网页：今日练习是一份「试卷」，第 Ⅰ 卷单选、第 Ⅱ 卷填空，题目全部来自你记过的卡点。答对掌握度 +1，答错 -2 并回到题库。

## 组成

| 目录 | 作用 |
|---|---|
| `server/` | 网页 + API，纯 Python 标准库，单文件 SQLite |
| `menubar/` | macOS 菜单栏 App，全局快捷键 `⌃⌥E` 上报 |
| `cli/` | 终端上报 `ec`，以及右键「服务」菜单安装脚本 |
| `k8s.yaml` | 部署清单（Deployment / PVC / Service / CronJob） |

## 本地运行

只需要 Python 3.10+，无第三方依赖。

```bash
export LLM_BASE_URL="https://api.openai.com/v1"
export LLM_MODEL="gpt-4o-mini"
export LLM_API_KEY="sk-..."
export DB_PATH="./coach.db"
python3 server/app.py
```

打开 <http://127.0.0.1:8080>。

首次启动会自动建管理员账号。密码取 `ADMIN_PASSWORD` 环境变量；没设就随机生成并打印在日志里：

```
[init] 管理员账号 admin / 初始密码 xxxxxxxx
```

任何 OpenAI 兼容的 Chat Completions 接口都能用。模型需要能返回 JSON。

## 部署到 Kubernetes

```bash
# 1. 建模型服务的 Secret（地址、模型 id、密钥都不进仓库）
kubectl create secret generic english-coach-llm -n <namespace> \
  --from-literal=base-url="https://api.openai.com/v1" \
  --from-literal=model="gpt-4o-mini" \
  --from-literal=api-key="sk-..."

# 2. 把代码灌进 ConfigMap
kubectl create configmap english-coach-code -n <namespace> \
  --from-file=index.html=server/index.html \
  --from-file=app.js=server/app.js \
  --from-file=app.py=server/app.py \
  --dry-run=client -o yaml | kubectl apply -f -

# 3. 部署
kubectl apply -f k8s.yaml
```

清单里的 namespace 和 NodePort 按需改。容器用 `python:3.12-slim`，代码通过 ConfigMap 挂载，所以改代码只需重建 ConfigMap 再 `rollout restart`。

`CronJob` 每周一自动生成 7 天学习计划，时区在 `schedule` 里注明。

## 客户端配置

菜单栏 App 和 `ec` 从两个文件读配置，都在 `~/.english-coach/`：

```bash
mkdir -p ~/.english-coach
echo 'https://your-host' > ~/.english-coach/api   # 服务地址
echo 'ek_xxxxxxxx'        > ~/.english-coach/key   # API Key，网页「成员」页可复制
chmod 600 ~/.english-coach/key
```

也可以用环境变量 `ENGLISH_COACH_API` / `ENGLISH_COACH_KEY` 覆盖。

### 菜单栏 App

```bash
bash menubar/build.sh
```

会编译、生成图标、打包成 `/Applications/English Coach.app` 并启动。

需要授权「辅助功能」（系统设置 → 隐私与安全性 → 辅助功能），因为它要读取你在别的 App 里选中的文字。

> 签名说明：脚本会优先找机器上的 Apple Development 证书。用 ad-hoc 签名的话，每次重建 CDHash 都变，系统会认为是另一个 App，辅助功能授权要重新给。

### 终端

```bash
cp cli/ec /usr/local/bin/ec && chmod +x /usr/local/bin/ec

ec "please kindly confirm this ASAP" "催老板但要客气"
echo "赶紧把这个搞定" | ec
ec -i          # 交互式
ec -l          # 看今天记的
```

`ec` 是 Codex / 终端场景下唯一顺手的入口。

## 数据

全部存在一个 SQLite 文件里（`/data/coach.db` 或 `DB_PATH`）。

| 表 | 内容 |
|---|---|
| `users` | 账号、角色、API Key |
| `events` | 每次上报的原始句子与中文意图 |
| `cards` | 归并后的知识点，含掌握度 0–5 |
| `papers` / `questions` | 每日试卷与题目 |
| `plans` | 定期生成的学习计划 |

多用户数据按 `user_id` 隔离，所有接口强制过滤。

## 技术说明

- **无第三方依赖**：`http.server` + `sqlite3` + `urllib`，容器镜像直接用官方 slim 镜像
- **出题分两次调用**：让模型一次同时出两种题型时它常常只给选择题，所以选择题、填空题各调一次再打乱顺序
- **填空判定**：忽略大小写、多余空格和结尾标点；一个空支持多个可接受答案（`|` 分隔）
- **掌握度**：答对 +1，答错 -2，满级 5 不再出题

## License

MIT
