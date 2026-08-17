# Python 3.11 环境升级与依赖安装执行计划

> 文档版本：v1.0
> 创建日期：2026-08-17
> 背景：当前系统 Python 3.9.6 不满足项目 `requires-python >= 3.11` 要求，`langchain-core>=1.0` 等核心依赖无 3.9 兼容版本

---

## 一、现状分析

| 项目 | 当前状态 | 目标状态 |
|------|---------|---------|
| 系统 Python | 3.9.6（macOS 内置） | 3.11.x（Homebrew 安装） |
| 虚拟环境 | `.venv` 基于 3.9 创建 | 基于 3.11 重建 |
| langchain-core | 安装失败（无 3.9 兼容版本） | ≥1.0 正常安装 |
| pip | 21.2.4（过旧） | 最新版（≥24.0） |
| 测试运行 | 无法执行 | 全量通过 |

### 阻塞依赖链

```
requires-python >= 3.11
  └── langchain-core >= 1.0 (仅提供 3.11+ wheel)
       └── langchain-deepseek >= 1.0
       └── langgraph >= 1.0
       └── langchain-text-splitters >= 1.0
```

---

## 二、执行步骤

### 步骤 1：安装 Python 3.11

```bash
# 通过 Homebrew 安装（推荐）
brew install python@3.11

# 验证安装
/opt/homebrew/bin/python3.11 --version
# 预期输出：Python 3.11.x
```

**验证点**：
- `python3.11` 命令可用
- `pip3.11` 命令可用
- 版本号 ≥ 3.11.0

**备选方案**（Homebrew 不可用时）：
```bash
# 方案 A：pyenv
curl https://pyenv.run | bash
pyenv install 3.11.9
pyenv local 3.11.9

# 方案 B：官方安装包
# 从 https://www.python.org/downloads/ 下载 macOS Universal2 安装包
```

### 步骤 2：重建虚拟环境

```bash
cd /Users/zhangbo/VSCodeSpace/SelfEvolvingKnowledgeBase/backend

# 删除旧的 3.9 虚拟环境
rm -rf .venv

# 用 Python 3.11 创建新虚拟环境
/opt/homebrew/bin/python3.11 -m venv .venv

# 激活虚拟环境
source .venv/bin/activate

# 升级 pip 到最新版
pip install --upgrade pip setuptools wheel

# 验证 Python 版本
python --version
# 预期输出：Python 3.11.x
```

**验证点**：
- `python --version` 输出 3.11.x
- `pip --version` 版本 ≥ 24.0

### 步骤 3：安装项目依赖

```bash
# 先安装核心依赖（分批安装，便于定位问题）
pip install fastapi uvicorn[standard] sse-starlette python-multipart
pip install pydantic pydantic-settings python-dotenv pyyaml
pip install langchain-core>=1.0 langchain-deepseek>=1.0 langchain-openai>=1.0
pip install langgraph>=1.0 langgraph-checkpoint-sqlite>=2.0
pip install langchain-mcp-adapters>=0.3.0 mcp>=1.1.0
pip install openai httpx aiohttp requests
pip install typer rich structlog tiktoken tenacity
pip install langchain-text-splitters pypdf python-docx
pip install jieba rank-bm25
pip install chromadb sentence-transformers
pip install asyncpg psycopg2-binary redis

# 安装开发依赖
pip install pytest pytest-asyncio pytest-cov respx ruff mypy

# 或一次性安装全部
pip install -r requirements.txt
pip install pytest pytest-asyncio pytest-cov respx ruff mypy
```

**验证点**：
- `pip list` 中包含 `langchain-core ≥ 1.0`、`langgraph ≥ 1.0`、`chromadb ≥ 0.5`
- 无 `ERROR: No matching distribution found` 错误

### 步骤 4：验证核心模块导入

```bash
cd /Users/zhangbo/VSCodeSpace/SelfEvolvingKnowledgeBase/backend
source .venv/bin/activate

# 逐个验证关键导入
python -c "from app.core.config import get_config; print('config OK')"
python -c "from app.memory.knowledge_base import ChromaKnowledgeBase; print('knowledge_base OK')"
python -c "from app.memory.base import KnowledgeBaseBackend; print('base OK')"
python -c "from app.agents.knowledge_ingestor import KnowledgeIngester; print('ingestor OK')"
python -c "from app.tools.rag.retriever import RAGRetriever; print('rag OK')"
python -c "from app.tools.direct.vector_store import DirectVectorStore; print('vector_store OK')"
python -c "from app.tools.file_processor import FileProcessor; print('file_processor OK')"
python -c "from app.graph.builder import GraphBuilder; print('graph OK')"
python -c "from app.core.bootstrap import initialize_app; print('bootstrap OK')"
```

### 步骤 5：运行单元测试

```bash
cd /Users/zhangbo/VSCodeSpace/SelfEvolvingKnowledgeBase/backend
source .venv/bin/activate

# 全量运行
pytest tests/unit/ -v --tb=short

# 仅运行 Phase 2 核心模块测试
pytest tests/unit/test_knowledge_ingestor.py tests/unit/test_rag_retriever.py \
      tests/unit/test_vector_store.py tests/unit/test_knowledge_entry.py \
      tests/unit/test_file_processor.py -v --tb=short

# 带覆盖率
pytest tests/unit/ --cov=app --cov-report=term-missing
```

**验证点**：
- 402 个测试全部通过（0 failed）
- 无 ImportError / ModuleNotFoundError
- 覆盖率报告正常生成

### 步骤 6：验证接口一致性修复

```bash
# 专门验证 KnowledgeBaseBackend 抽象接口修复
pytest tests/unit/test_knowledge_ingestor.py tests/unit/test_vector_store.py \
      tests/unit/test_knowledge_entry.py -v -k "test_" --tb=long

# 验证抽象方法完整性
python -c "
from app.memory.base import KnowledgeBaseBackend
# 检查所有抽象方法均已声明
abstract_methods = KnowledgeBaseBackend.__abstractmethods__
print(f'Abstract methods: {abstract_methods}')
assert 'add' in abstract_methods
assert 'retrieve' in abstract_methods
assert 'delete' in abstract_methods
assert 'count' in abstract_methods
assert 'get' in abstract_methods
assert 'find_similar' in abstract_methods
print('All abstract methods verified.')
"
```

---

## 三、回滚方案

如果升级后出现问题，快速回滚：

```bash
# 1. 保留旧虚拟环境备份
cp -r .venv .venv_backup_3.9

# 2. 回滚命令
rm -rf .venv
mv .venv_backup_3.9 .venv
source .venv/bin/activate
```

---

## 四、已知风险与应对

| 风险 | 概率 | 影响 | 应对 |
|------|------|------|------|
| `sentence-transformers` 首次下载模型慢 | 高 | 首次测试变慢 | 提前手动下载：`python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-zh-v1.5')"` |
| `chromadb` 在 ARM Mac 上编译失败 | 低 | L3 知识库不可用 | 确保 Xcode Command Line Tools 已安装：`xcode-select --install` |
| `langchain-core 1.0` API 变更 | 中 | 导入路径变化 | 检查 `from langchain_core.messages import ...` 是否正常 |
| `asyncpg` 需要 PostgreSQL 客户端库 | 低 | 安装失败 | `brew install postgresql` |
| 旧 `.venv` 中残留 3.9 编译的 `.pyc` | 中 | 缓存冲突 | 删除 `__pycache__` 目录：`find . -type d -name __pycache__ -exec rm -rf {} +` |

---

## 五、验收检查清单

- [ ] `python --version` 输出 3.11.x
- [ ] `pip install -r requirements.txt` 无错误完成
- [ ] 所有核心模块导入无异常
- [ ] `pytest tests/unit/ -v` 全部通过（402 tests, 0 failed）
- [ ] `KnowledgeBaseBackend.__abstractmethods__` 包含 6 个方法
- [ ] `from app.core.bootstrap import initialize_app` 正常导入
- [ ] ChromaDB 初始化成功（`import chromadb` 无报错）
- [ ] sentence-transformers 模型加载成功
