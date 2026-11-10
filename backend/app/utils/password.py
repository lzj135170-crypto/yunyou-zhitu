import bcrypt

def hash_password(password: str) -> str:
    """ 密码加密：把明文密码用 bcrypt 加盐哈希后返回字符串。

    注意必须写 return。这里曾经漏写，函数会隐式返回 None，
    调用方拿到的就是"空密码"——写进 users.password_hash 这列时，
    MySQL 的宽松模式会把它悄悄转成空字符串（表面不报错，实际谁也无法登录），
    PostgreSQL 的 NOT NULL 约束则会直接抛异常，把这个错误暴露出来。
    """
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verity_password(password: str, hash_password: str) -> bool:
    """ 验证密码有效性 """
    return bcrypt.checkpw(password.encode("utf-8"), hash_password.encode("utf-8"))
