# Phase 3 详细设计：前端 + 多用户 + 鉴权

> 文档版本：v1.0.0
> 最后更新：2026-08-12
> 状态：设计评审中
> 依赖文档：[01-architecture.md](./01-architecture.md)、[02-phase1-design.md](./02-phase1-design.md)、[05-phase2-design.md](./05-phase2-design.md)

---

## 一、Phase 3 目标与范围

### 1.1 核心目标

Phase 3 将项目从"单用户 CLI 工具"升级为**面向多用户的 Web 应用**，提供完整的聊天界面、文件上传管理、用户鉴权与会话隔离。

### 1.2 必做（P0）

| 编号 | 范围 | 验收标准 |
|---|---|---|
| P0-1 | 聊天 UI | 多会话列表、多轮对话、Markdown 渲染、流式响应 |
| P0-2 | 文件上传页 | 拖拽上传、上传进度、文件列表、解析状态 |
| P0-3 | 多用户系统 | 用户注册/登录、JWT 鉴权、数据隔离 |
| P0-4 | API 安全 | HTTPS、CORS、限流、输入校验 |
| P0-5 | 会话管理 | 创建/删除/重命名会话、会话搜索 |
| P0-6 | 知识库管理 | 查看/删除已入库知识、知识来源追溯 |
| P0-7 | 用户设置 | API Key 配置、模型偏好、通知设置 |

### 1.3 不做

- 生产部署（Phase 4）
- 移动端 App

---

## 二、前端技术栈

| 层 | 技术 | 版本要求 | 选型理由 |
|---|---|---|---|
| 框架 | React + TypeScript | 18+ | 生态最成熟，组件库丰富 |
| 构建工具 | Vite | 5+ | 开发体验好，HMR 快 |
| UI 组件库 | Ant Design | 5+ | 中文文档完善，企业级组件 |
| 状态管理 | Zustand | 4+ | 轻量、类型友好 |
| HTTP 客户端 | Axios | 1+ | 拦截器支持好 |
| Markdown 渲染 | ReactMarkdown + KaTeX | - | 支持数学公式 |
| 代码高亮 | highlight.js | - | 主流语言支持 |
| 表单校验 | Zod + ReactHookForm | - | 类型安全的表单 |
| 路由 | React Router | 6+ | 主流选择 |

---

## 三、页面设计

### 3.1 页面结构

```
┌─────────────────────────────────────────────────────────────┐
│  Sidebar          │           Main Content                  │
│  ┌──────────────┐ │  ┌──────────────────────────────────┐  │
│  │  Logo + 标题  │ │  │  Header: 当前页面 + 用户头像     │  │
│  ├──────────────┤ │  ├──────────────────────────────────┤  │
│  │ [聊天]       │ │  │                                  │  │
│  │ [文件管理]    │ │  │     页面内容区                  │  │
│  │ [知识库]     │ │  │                                  │  │
│  │ [设置]       │ │  │                                  │  │
│  ├──────────────┤ │  │                                  │  │
│  │ 会话列表     │ │  │                                  │  │
│  │ ├ 会话1      │ │  │                                  │  │
│  │ ├ 会话2      │ │  │                                  │  │
│  │ └ + 新对话   │ │  │                                  │  │
│  ├──────────────┤ │  ├──────────────────────────────────┤  │
│  │ 用户信息     │ │  │  Footer: 状态栏                  │  │
│  │ 头像 + 邮箱  │ │  └──────────────────────────────────┘  │
│  │ [退出登录]   │ │                                         │
│  └──────────────┘ │                                         │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 聊天页面

**功能清单**：

| 功能 | 描述 |
|---|---|
| 会话列表 | 侧边栏展示历史会话，显示标题/最后消息/时间 |
| 新建对话 | 点击"+ 新对话"创建新会话 |
| 多轮对话 | 用户输入 → Agent 回复，支持多轮 |
| 流式响应 | SSE 逐字输出，打字机动效 |
| Markdown 渲染 | 支持代码块、列表、表格、数学公式 |
| 代码高亮 | 代码块自动高亮（支持主流语言） |
| 复制消息 | 点击消息气泡复制内容 |
| 消息元信息 | 显示意图、模型、token 消耗、延迟 |
| 会话搜索 | 按关键词搜索历史会话 |
| 会话管理 | 重命名、删除会话 |
| Thumbs Up/Down | 用户反馈，写入评估体系 |

**状态管理**：

```typescript
// stores/chat.ts
interface ChatStore {
  conversations: Conversation[];
  currentConversationId: string | null;
  messages: Record<string, Message[]>;  // conv_id -> messages
  isStreaming: boolean;

  createConversation: () => Conversation;
  deleteConversation: (id: string) => void;
  sendMessage: (convId: string, content: string) => Promise<void>;
  rateMessage: (msgId: string, rating: 'up' | 'down') => void;
}
```

**SSE 流式响应**：

```typescript
// services/api.ts
export async function streamChat(
  convId: string,
  message: string,
  onChunk: (chunk: string) => void,
  onDone: (meta: ChatMeta) => void,
  onError: (error: Error) => void
): Promise<void> {
  const response = await fetch('/api/v1/chat/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${getToken()}`
    },
    body: JSON.stringify({ conv_id: convId, message })
  });

  const reader = response.body!.getReader();
  const decoder = new TextDecoder();

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    const chunk = decoder.decode(value);
    const lines = chunk.split('\n\n');
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        const data = JSON.parse(line.slice(6));
        if (data.type === 'token') {
          onChunk(data.content);
        } else if (data.type === 'done') {
          onDone(data.meta);
        }
      }
    }
  }
}
```

### 3.3 文件上传页面

**功能清单**：

| 功能 | 描述 |
|---|---|
| 拖拽上传 | 拖入文件或点击选择，支持多文件 |
| 上传进度 | 每个文件显示上传进度条 |
| 文件类型 | PDF、Word（.docx）、TXT、Markdown |
| 文件列表 | 展示已上传文件，显示名称/大小/上传时间/解析状态 |
| 解析状态 | 显示解析进度：排队中 → 解析中 → 已入库 / 失败 |
| 知识预览 | 点击文件预览解析后的知识条目 |
| 删除文件 | 删除文件及其关联的知识条目 |
| 搜索文件 | 按文件名/上传时间搜索 |

**上传组件设计**：

```tsx
// components/FileUploader.tsx
function FileUploader() {
  const [files, setFiles] = useState<UploadFile[]>([]);
  const [dragOver, setDragOver] = useState(false);

  const handleUpload = async (file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('user_id', userId);

    const xhr = new XMLHttpRequest();
    xhr.upload.onprogress = (e) => {
      const progress = (e.loaded / e.total) * 100;
      updateFileProgress(file.name, progress);
    };
    xhr.onload = () => {
      updateFileStatus(file.name, 'parsed');
    };
    xhr.open('POST', '/api/v1/files/upload');
    xhr.send(formData);
  };

  return (
    <Upload.Dragger
      onDragOver={() => setDragOver(true)}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => handleDrop(e.dataTransfer.files)}
    >
      <p className="ant-upload-drag-icon">
        <InboxOutlined />
      </p>
      <p>点击或拖拽文件到此区域上传</p>
      <p className="ant-upload-hint">
        支持 PDF、Word(.docx)、TXT、Markdown，单个文件不超过 50MB
      </p>
    </Upload.Dragger>
  );
}
```

### 3.4 知识库管理页面

**功能清单**：

| 功能 | 描述 |
|---|---|
| 知识列表 | 展示已入库知识条目，按来源/时间/重要性筛选 |
| 知识搜索 | 关键词搜索知识库 |
| 知识预览 | 点击查看知识详情，显示来源/重要性/版本 |
| 来源追溯 | 点击来源跳转到原会话或文件 |
| 删除知识 | 删除低质量或错误知识 |
| 批量操作 | 批量删除/标记重要性 |
| 统计概览 | 知识条目总数、来源分布、重要性分布 |

### 3.5 用户设置页面

**功能清单**：

| 功能 | 描述 |
|---|---|
| 个人信息 | 头像、昵称、邮箱 |
| API 配置 | DeepSeek API Key、默认模型选择 |
| 模型偏好 | 默认模型、温度、最大 token 数 |
| 通知设置 | 评估报告通知、预算告警通知 |
| 数据管理 | 导出数据、清空会话、清空知识库 |
| 账户安全 | 修改密码、注销账户 |

---

## 四、多用户系统设计

### 4.1 用户注册与登录

```python
# api/routes/auth.py
router = APIRouter(prefix="/api/v1/auth")

@router.post("/register")
async def register(body: RegisterRequest):
    """用户注册"""
    # 1. 校验邮箱格式和密码强度
    validate_email(body.email)
    validate_password(body.password)

    # 2. 检查邮箱是否已注册
    existing = await user_repository.find_by_email(body.email)
    if existing:
        raise HTTPException(400, "该邮箱已被注册")

    # 3. 创建用户
    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        name=body.name,
        created_at=datetime.now()
    )
    await user_repository.create(user)

    # 4. 生成 JWT
    token = create_jwt(user.id)
    return {"user": user_public(user), "token": token}

@router.post("/login")
async def login(body: LoginRequest):
    """用户登录"""
    user = await user_repository.find_by_email(body.email)
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "邮箱或密码错误")

    token = create_jwt(user.id)
    return {"user": user_public(user), "token": token}

@router.post("/logout")
async def logout(token: str = Depends(oauth2_scheme)):
    """用户登出（客户端清除 token 即可）"""
    return {"status": "ok"}
```

### 4.2 JWT 鉴权

```python
# core/auth.py
from datetime import datetime, timedelta
import jwt

SECRET_KEY = os.environ.get("JWT_SECRET", "dev-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 72  # 3 天

def create_jwt(user_id: str) -> str:
    """创建 JWT"""
    payload = {
        "sub": user_id,
        "exp": datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS),
        "iat": datetime.utcnow()
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def verify_jwt(token: str) -> dict:
    """验证 JWT"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "登录已过期，请重新登录")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "无效的 token")

# FastAPI 依赖项
async def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
    payload = verify_jwt(token)
    user_id = payload["sub"]
    user = await user_repository.find_by_id(user_id)
    if not user:
        raise HTTPException(404, "用户不存在")
    return user
```

### 4.3 数据隔离

**核心原则**：所有数据按 `user_id` 隔离，查询时必须带 `user_id` 过滤。

```python
# 存储层强制隔离
class PostgresStorage(StorageBackend):
    async def list_conversations(
        self,
        user_id: str,  # 必须传入
        limit: int = 50,
        offset: int = 0
    ) -> list[dict]:
        query = """
            SELECT * FROM conversations
            WHERE user_id = :user_id
            ORDER BY updated_at DESC
            LIMIT :limit OFFSET :offset
        """
        return await self._fetch_all(query, {"user_id": user_id, "limit": limit, "offset": offset})
```

**API 层强制鉴权**：

```python
# 所有业务路由都依赖 get_current_user
@router.get("/conversations")
async def list_conversations(
    user: User = Depends(get_current_user),
    limit: int = 50
):
    return await storage.list_conversations(user.id, limit)
```

### 4.4 用户配额

| 资源 | 免费额度 | 超限处理 |
|---|---|---|
| 每日 API 调用 | 100 次 | 超限返回 429 |
| 日 Token 预算 | $1.0 | 自动降级 + 告警 |
| 知识库条目 | 10,000 条 | 触发驱逐策略 |
| 文件上传 | 50MB/文件 | 拒绝上传 |
| 存储空间 | 500MB | 告警提示 |

---

## 五、API 路由设计

### 5.1 路由总览

```
/api/v1/
├── auth/                          # 鉴权
│   ├── POST /register
│   ├── POST /login
│   └── POST /logout
│
├── chat/                          # 聊天
│   ├── POST /                     # 发送消息（非流式）
│   └── POST /stream               # 发送消息（SSE 流式）
│
├── conversations/                 # 会话管理
│   ├── GET /                      # 列表
│   ├── GET /{conv_id}             # 详情
│   ├── PATCH /{conv_id}           # 更新（重命名）
│   ├── DELETE /{conv_id}          # 删除
│   └── POST /{conv_id}/rate       # 反馈
│
├── files/                         # 文件管理
│   ├── POST /upload               # 上传
│   ├── GET /                      # 列表
│   ├── GET /{file_id}             # 详情
│   └── DELETE /{file_id}          # 删除
│
├── knowledge/                     # 知识库
│   ├── GET /                      # 列表
│   ├── GET /search                # 搜索
│   ├── DELETE /{entry_id}         # 删除
│   └── POST /{entry_id}/rate      # 标记重要性
│
├── users/                         # 用户
│   ├── GET /me                    # 当前用户信息
│   ├── PATCH /me                  # 更新
│   ├── DELETE /me                 # 注销
│   └── GET /me/stats              # 使用统计
│
└── eval/                          # 评估
    ├── POST /run                  # 触发 eval
    └── GET /reports               # 报告列表
```

### 5.2 响应格式

```json
{
  "code": 0,           // 0=成功, 非0=错误
  "data": { ... },     // 响应数据
  "message": "ok",     // 错误信息
  "meta": {
    "trace_id": "...",
    "timestamp": "..."
  }
}
```

### 5.3 错误码

| 码 | 含义 | HTTP 状态 |
|---|---|---|
| 0 | 成功 | 200 |
| 1001 | 参数校验失败 | 400 |
| 1002 | 资源不存在 | 404 |
| 2001 | 未登录 | 401 |
| 2002 | 权限不足 | 403 |
| 3001 | 限流触发 | 429 |
| 4001 | LLM 调用失败 | 500 |
| 4002 | 工具调用失败 | 500 |
| 5001 | 预算超限 | 402 |

---

## 六、前端目录结构

```
frontend/
├── src/
│   ├── components/                   # 通用组件
│   │   ├── Layout/                   # 布局组件
│   │   │   ├── Sidebar.tsx
│   │   │   └── Header.tsx
│   │   ├── Chat/                     # 聊天组件
│   │   │   ├── MessageBubble.tsx
│   │   │   ├── MessageInput.tsx
│   │   │   ├── ConversationList.tsx
│   │   │   └── StreamArea.tsx
│   │   ├── File/                     # 文件组件
│   │   │   ├── FileUploader.tsx
│   │   │   ├── FileList.tsx
│   │   │   └── FilePreview.tsx
│   │   ├── Knowledge/                # 知识库组件
│   │   │   ├── KnowledgeList.tsx
│   │   │   └── KnowledgeSearch.tsx
│   │   ├── User/                     # 用户组件
│   │   │   ├── ProfileForm.tsx
│   │   │   └── ApiKeyConfig.tsx
│   │   └── Feedback/                 # 反馈组件
│   │       └── MessageRate.tsx
│   │
│   ├── pages/                        # 页面
│   │   ├── Chat.tsx                  # 聊天页
│   │   ├── Files.tsx                 # 文件管理页
│   │   ├── Knowledge.tsx             # 知识库页
│   │   ├── Settings.tsx              # 设置页
│   │   ├── Login.tsx                 # 登录页
│   │   └── Register.tsx              # 注册页
│   │
│   ├── stores/                       # Zustand 状态
│   │   ├── chat.ts
│   │   ├── user.ts
│   │   └── file.ts
│   │
│   ├── services/                     # API 调用
│   │   ├── api.ts                    # Axios 实例
│   │   ├── auth.ts
│   │   ├── chat.ts
│   │   └── file.ts
│   │
│   ├── hooks/                        # 自定义 Hooks
│   │   ├── useSSE.ts
│   │   └── useDebounce.ts
│   │
│   ├── types/                        # 类型定义
│   │   ├── api.ts
│   │   ├── chat.ts
│   │   └── user.ts
│   │
│   ├── utils/                        # 工具函数
│   │   ├── format.ts
│   │   └── validator.ts
│   │
│   ├── App.tsx
│   ├── main.tsx
│   └── index.css
│
├── public/
├── index.html
├── vite.config.ts
├── tsconfig.json
├── package.json
└── README.md
```

---

## 七、安全设计

### 7.1 前端安全

| 风险 | 防护措施 |
|---|---|
| XSS | React 默认转义 + DOMPurify 净化 HTML |
| CSRF | SameSite Cookie + Token 校验 |
| Token 存储 | HttpOnly Cookie（首选）或内存存储 |
| 敏感信息 | API Key 不在前端明文存储 |
| 输入注入 | 前端 Zod 校验 + 后端二次校验 |

### 7.2 后端安全

| 风险 | 防护措施 |
|---|---|
| SQL 注入 | ORM 参数化查询 |
| Prompt 注入 | 输入隔离 + 黑名单正则 + 系统提示词强化 |
| 越权访问 | 所有查询强制带 user_id |
| 暴力破解 | 登录限频 + 密码强度校验 |
| 敏感信息泄露 | API Key 返回时脱敏 + 日志脱敏 |
| CORS | 白名单域名 + 凭证控制 |
| HTTPS | 生产环境强制 HTTPS |

### 7.3 密码安全

```python
def hash_password(password: str) -> str:
    """使用 bcrypt 哈希密码"""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, password_hash: str) -> bool:
    """验证密码"""
    return bcrypt.checkpw(password.encode(), password_hash.encode())
```

### 7.4 API Key 存储

用户自己的 DeepSeek API Key 加密存储（AES-256），使用时解密。

---

## 八、前端性能优化

| 优化项 | 方案 |
|---|---|
| 路由懒加载 | `React.lazy(() => import('./pages/Chat'))` |
| 组件记忆化 | `React.memo` + `useMemo` + `useCallback` |
| 虚拟列表 | 会话列表/知识列表超 100 条时用虚拟滚动 |
| 图片懒加载 | 头像等图片用 `loading="lazy"` |
| 代码分割 | Vite 自动代码分割 + 手动 `import()` |
| 缓存策略 | React Query 缓存 API 响应 |
| Service Worker | 离线缓存静态资源 |
| CDN | 静态资源走 CDN |

---

## 九、Phase 3 验收标准

### 9.1 功能验收

| 验收项 | 通过标准 |
|---|---|
| 聊天 UI | 流畅多轮对话，SSE 流式响应正常 |
| Markdown 渲染 | 代码块、表格、列表渲染正确 |
| 文件上传 | 3 种文件类型上传解析成功 |
| 多用户 | 两个用户数据完全隔离 |
| 鉴权 | 未登录访问返回 401，过期 Token 返回 401 |
| 会话管理 | 创建/删除/搜索/重命名正常 |
| 知识库管理 | 列表/搜索/删除正常，来源追溯正确 |
| 用户设置 | API Key 加密存储，模型偏好生效 |

### 9.2 性能验收

| 指标 | 目标 |
|---|---|
| 首屏加载 | < 2s（3G 网络） |
| 路由切换 | < 300ms |
| 消息发送到首字显示 | < 1s |
| 文件上传（1MB） | < 5s |
| 会话列表加载 | < 500ms |

### 9.3 安全验收

| 指标 | 目标 |
|---|---|
| XSS 防护 | OWASP ZAP 扫描无高危漏洞 |
| 越权访问 | 无法通过修改 user_id 访问他人数据 |
| 密码安全 | bcrypt 哈希，不可逆 |
| Token 安全 | HttpOnly Cookie，过期即失效 |
