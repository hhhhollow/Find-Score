"""
AES-CBC 密码加密（与教务前端 JS 一致）。
"""

import base64
import secrets
import string

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

_RAND_ALPHABET = string.ascii_letters + string.digits


def _rand_str(n: int) -> str:
    """生成 n 位随机字母数字串。"""
    return "".join(secrets.choice(_RAND_ALPHABET) for _ in range(n))


def encrypt_password(password: str, salt: str) -> str:
    """用 CAS 返回的 salt 对密码做 AES-CBC 加密，与前端 JS 保持一致。"""
    if not salt:
        return password
    key = salt.encode("utf-8")
    iv = _rand_str(16).encode("utf-8")
    plaintext = (_rand_str(64) + password).encode("utf-8")
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return base64.b64encode(cipher.encrypt(pad(plaintext, AES.block_size))).decode()
