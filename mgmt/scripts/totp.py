#!/usr/bin/env python3
"""TOTP 验证码计算（测试/自动化用，RFC6238 SHA1 6位 30s）。用法：totp.py <base32-secret> [偏移秒]"""
import base64, hashlib, hmac, struct, sys, time


def totp(secret: str, t: int | None = None) -> str:
    key = base64.b32decode(secret + "=" * ((8 - len(secret) % 8) % 8), casefold=True)
    t = int(time.time()) if t is None else t
    counter = struct.pack(">Q", t // 30)
    digest = hmac.new(key, counter, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1000000
    return f"{code:06d}"


if __name__ == "__main__":
    print(totp(sys.argv[1]))
