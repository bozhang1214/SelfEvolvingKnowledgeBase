"""Phase 3 集成验证脚本"""
import sys

def check(module_name, status, detail=""):
    mark = "✓" if status else "✗"
    print(f"  {mark} {module_name} {detail}")
    return status

passed = 0
failed = 0

print("=" * 50)
print("Phase 3 集成验证")
print("=" * 50)

# 1. 模块导入验证
print("\n1. 模块导入验证")
try:
    from app.core.auth import hash_password, verify_password, create_jwt, verify_jwt
    check("app.core.auth", True)
    passed += 1
except Exception as e:
    check(f"app.core.auth: {e}", False)
    failed += 1

try:
    from app.models.user import User, UserPublic, RegisterRequest, LoginRequest
    check("app.models.user", True)
    passed += 1
except Exception as e:
    check(f"app.models.user: {e}", False)
    failed += 1

try:
    from app.storage.user_storage import UserStorage
    check("app.storage.user_storage", True)
    passed += 1
except Exception as e:
    check(f"app.storage.user_storage: {e}", False)
    failed += 1

try:
    from app.api.routes.auth import router as auth_router
    check("app.api.routes.auth", True, f"({len(auth_router.routes)} routes)")
    passed += 1
except Exception as e:
    check(f"app.api.routes.auth: {e}", False)
    failed += 1

try:
    from app.api.routes.knowledge import router as knowledge_router
    check("app.api.routes.knowledge", True, f"({len(knowledge_router.routes)} routes)")
    passed += 1
except Exception as e:
    check(f"app.api.routes.knowledge: {e}", False)
    failed += 1

try:
    from app.api.routes.conversations import router as conv_router
    check("app.api.routes.conversations", True, f"({len(conv_router.routes)} routes)")
    passed += 1
except Exception as e:
    check(f"app.api.routes.conversations: {e}", False)
    failed += 1

try:
    from app.api.middleware import setup_cors, RateLimitMiddleware
    from fastapi import FastAPI
    app = FastAPI()
    setup_cors(app)
    app.add_middleware(RateLimitMiddleware, default_limit=60)
    check("app.api.middleware", True)
    passed += 1
except Exception as e:
    check(f"app.api.middleware: {e}", False)
    failed += 1

# 2. 功能验证
print("\n2. 功能验证")

# 2.1 密码哈希
try:
    h = hash_password("test123456")
    assert verify_password("test123456", h), "密码验证失败"
    assert not verify_password("wrong", h), "错误密码应验证失败"
    check("密码哈希 (PBKDF2-SHA256)", True)
    passed += 1
except Exception as e:
    check(f"密码哈希: {e}", False)
    failed += 1

# 2.2 JWT 
try:
    token = create_jwt("user_001")
    payload = verify_jwt(token)
    assert payload["sub"] == "user_001", f"JWT sub 错误: {payload['sub']}"
    check("JWT 签发/验证", True)
    passed += 1
except Exception as e:
    check(f"JWT: {e}", False)
    failed += 1

# 2.3 User 模型
try:
    h2 = hash_password("pass1234")
    user = User(email="test@example.com", password_hash=h2)
    assert user.user_id.startswith("user_"), f"user_id 格式错误: {user.user_id}"
    assert user.email == "test@example.com"
    check("User 模型创建", True)
    passed += 1
except Exception as e:
    check(f"User 模型: {e}", False)
    failed += 1

# 2.4 UserStorage CRUD
try:
    import tempfile, os
    tmpdir = tempfile.mkdtemp()
    storage = UserStorage(tmpdir)
    # 创建用户
    user = User(email="crud@test.com", password_hash=h2)
    storage.create(user)
    # 查询
    found = storage.find_by_email("crud@test.com")
    assert found is not None, "find_by_email 返回 None"
    assert found.email == "crud@test.com"
    found2 = storage.find_by_id(user.user_id)
    assert found2 is not None
    # 更新
    storage.update(user.user_id, {"name": "TestUser"})
    updated = storage.find_by_id(user.user_id)
    assert updated and updated.name == "TestUser"
    # 公开信息
    public = storage.to_public(user)
    assert not hasattr(public, "password_hash"), "公开信息泄露密码"
    # 删除
    assert storage.delete(user.user_id)
    assert storage.find_by_id(user.user_id) is None
    # 清理
    os.unlink(storage._users_file)
    os.rmdir(tmpdir)
    check("UserStorage CRUD", True)
    passed += 1
except Exception as e:
    check(f"UserStorage: {e}", False)
    failed += 1

# 2.5 配置加载
try:
    from app.core.config import get_config
    config = get_config()
    assert config.api.auth.token_expire_hours == 72
    check("配置加载 (auth config)", True)
    passed += 1
except Exception as e:
    check(f"配置加载: {e}", False)
    failed += 1

# 3. 总结
print("\n" + "=" * 50)
print(f"结果: {passed} passed, {failed} failed, {passed + failed} total")
print("=" * 50)

if failed > 0:
    sys.exit(1)
else:
    print("Phase 3 后端验证全部通过!")