"""生成 aBridge 集成配置和独立身份签名密钥，不启动或修改运行服务。"""

import argparse
import json
import os
import secrets
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values


def initialize(env_file: Path, yuxi_url: str, abridge_origin: str) -> None:
    """幂等补齐专用配置，保留已存在的凭据与其他环境变量。"""
    for value in (yuxi_url, abridge_origin):
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("公开地址必须是 HTTP(S) origin")
        if parsed.scheme == "http" and parsed.hostname not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ValueError("远程公开地址必须使用 HTTPS")
    lines = env_file.read_text().splitlines() if env_file.exists() else []
    current = {key: (value or "").replace("$$", "$") for key, value in dotenv_values(env_file, interpolate=False).items()}
    default_key = env_file.resolve().parent / ".yuxi-identity" / (env_file.name.lstrip(".") + ".pem")
    key_file = Path(current.get("YUXI_OIDC_PROVIDER_PRIVATE_KEY_FILE") or default_key).resolve()
    key_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not key_file.exists():
        subprocess.run(
            [
                "openssl",
                "genpkey",
                "-algorithm",
                "RSA",
                "-pkeyopt",
                "rsa_keygen_bits:3072",
                "-out",
                str(key_file),
            ],
            check=True,
            capture_output=True,
        )
    # Compose 的 API/worker 固定 UID 1000；只补充文件所属组的只读权限，私钥不向其他用户公开。
    os.chmod(key_file, 0o640)
    updates = {
        "YUXI_PUBLIC_URL": yuxi_url.rstrip("/"),
        "YUXI_OIDC_PROVIDER_ENABLED": "true",
        "YUXI_OIDC_PROVIDER_ISSUER": yuxi_url.rstrip("/") + "/api/auth/provider",
        "YUXI_OIDC_PROVIDER_CLIENT_ID": current.get("YUXI_OIDC_PROVIDER_CLIENT_ID") or "abridge",
        "YUXI_OIDC_PROVIDER_CLIENT_SECRET": current.get("YUXI_OIDC_PROVIDER_CLIENT_SECRET") or secrets.token_urlsafe(48),
        "YUXI_OIDC_PROVIDER_REDIRECT_URI": abridge_origin.rstrip("/") + "/api/auth/oidc/callback",
        "YUXI_OIDC_PROVIDER_PRIVATE_KEY_FILE": str(key_file),
        "YUXI_OIDC_PROVIDER_KEY_GID": str(key_file.stat().st_gid),
        "ABRIDGE_URL": current.get("ABRIDGE_URL") or "http://abridge:8765",
        "ABRIDGE_PUBLIC_ORIGIN": abridge_origin.rstrip("/"),
        "ABRIDGE_ADMIN_TOKEN": current.get("ABRIDGE_ADMIN_TOKEN") or secrets.token_urlsafe(48),
    }
    remaining = [line for line in lines if line.split("=", 1)[0].strip() not in updates]
    remaining += [f"{key}={json.dumps(value.replace('$', '$$'))}" for key, value in updates.items()]
    env_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = env_file.with_suffix(env_file.suffix + ".abridge-tmp")
    with temporary.open("x") as output:
        os.chmod(temporary, 0o600)
        output.write("\n".join(remaining) + "\n")
    temporary.replace(env_file)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--yuxi-url", required=True)
    parser.add_argument("--abridge-origin", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    initialize(args.env_file, args.yuxi_url, args.abridge_origin)
    print("aBridge 配置已保存；未启动服务。请妥善保存环境文件和独立签名密钥。")
