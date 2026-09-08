"""
国密 SM2 密码加密（与全新 SSO 前端 JS 一致）。
采用 SM2(C1C3C2) 椭圆曲线公钥密码学，密文输出为 Base64 编码。
"""

import base64
from gmssl import sm2


def encrypt_sm2(plaintext: str, public_key_b64: str) -> str:
    """用 SSO 返回的 SM2 公钥对密码进行加密。

    Args:
        plaintext: 待加密的明文密码。
        public_key_b64: Base64 编码的 SM2 公钥。

    Returns:
        Base64 编码的 SM2 (C1C3C2) 密文。
    """
    if not plaintext or not public_key_b64:
        raise ValueError("明文或公钥不能为空")

    raw_pub = base64.b64decode(public_key_b64)
    hex_pub = raw_pub.hex()
    if hex_pub.startswith("04"):
        hex_pub = hex_pub[2:]

    if len(hex_pub) != 128:
        raise ValueError(f"无效的 SM2 公钥长度: {len(hex_pub)} (应为 128 字符十六进制)")

    crypt = sm2.CryptSM2(public_key=hex_pub, private_key="", mode=1)
    cipher_bytes = crypt.encrypt(plaintext.encode("utf-8"))
    if not cipher_bytes:
        raise RuntimeError("SM2 加密失败（返回空密文）")

    return base64.b64encode(cipher_bytes).decode("ascii")

