"""
FastAPI 应用入口（uvicorn 启动点）。

通用启动脚本通过 `uvicorn app.api.main:app` 导入 ASGI 应用实例。
实际的 app 创建逻辑在 ``server.py`` 中，通过 ``get_app()`` 延迟构建。

使用方式：
    uvicorn app.api.main:app --host 0.0.0.0 --port 8000 --reload
"""

from app.api.server import get_app

# 模块级 app 实例：uvicorn 导入时触发 get_app() 完成初始化
app = get_app()
