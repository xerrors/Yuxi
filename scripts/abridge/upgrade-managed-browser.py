"""仅供部署人员在原 Cube 内安装经过核验的浏览器发行物。"""

import argparse
import fcntl
import hashlib
import importlib.metadata
import os
import pwd
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

INSTALLATION = Path("/opt/yuxi-managed-browser")
ACCOUNT = "managed-browser"
ACCOUNT_HOME = Path("/var/lib/yuxi-managed-browser")

COPY_PROFILE = """
import os, re, shutil, sys
from pathlib import Path
from tempfile import TemporaryDirectory

os.umask(0o077)
home = Path.home()
legacy = home / sys.argv[1] / "profile"
state = home / ".local/state/abridge-client-browser"
if not legacy.is_dir() and any(
    item.is_dir() and re.fullmatch(r"[a-f0-9]{24}", item.parent.name) for item in home.glob("*/profile")
):
    sys.exit("指定的公开地址与已有浏览器资料不匹配")
if legacy.is_dir() and not (state / "profile").exists() and not (state / "binding.json").exists():
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with TemporaryDirectory(dir=state) as temp:
        copied = Path(temp) / "profile"
        shutil.copytree(legacy, copied, symlinks=True, ignore=shutil.ignore_patterns("Singleton*"))
        copied.rename(state / "profile")
"""


def install(archive: Path, checksum: str, origin: str) -> None:
    """保持旧安装、浏览器资料与普通用户目录，发布新安装后由既有服务入口启动。"""
    prior = INSTALLATION.with_name(INSTALLATION.name + ".before-upgrade")
    _check_trusted_installation()
    origin_dir = _origin_key(origin)
    marker = INSTALLATION / "service-v2"
    installed = f"{checksum} {origin_dir}"
    if marker.is_file() and marker.read_text().strip() == installed:
        return
    if prior.exists():
        raise ValueError("已有升级备份，请先核对当前安装")

    with TemporaryDirectory(
        dir=INSTALLATION.parent, prefix=".browser-upgrade-"
    ) as temp:
        staged_archive = Path(temp) / "client.tgz"
        shutil.copyfile(archive, staged_archive)
        with staged_archive.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != checksum:
                raise ValueError("浏览器发行物校验失败")
            stream.seek(0)
            with tarfile.open(fileobj=stream, mode="r:gz") as package:
                package.extractall(temp, filter="data")
        bundle = Path(temp) / "abridge-client-browser"
        _prepare_dependencies(bundle)
        _prepare_account()
        subprocess.run(
            [
                "runuser",
                "-u",
                ACCOUNT,
                "--",
                sys.executable,
                "-I",
                "-c",
                COPY_PROFILE,
                origin_dir,
            ],
            check=True,
        )
        (bundle / "service-v2").write_text(installed + "\n")
        subprocess.run(["chmod", "-R", "a+rX,go-w", str(bundle)], check=True)
        if INSTALLATION.exists():
            INSTALLATION.rename(prior)
        try:
            bundle.rename(INSTALLATION)
        except OSError:
            if prior.exists():
                prior.rename(INSTALLATION)
            raise


def _origin_key(origin: str) -> str:
    """按旧启动器使用的规范来源定位原浏览器资料。"""
    url = urlsplit(origin)
    host = url.hostname
    if url.scheme not in {"http", "https"} or not host:
        raise ValueError("请提供原浏览器使用的 HTTP(S) 公开根地址")
    address = f"[{host}]" if ":" in host else host
    port = f":{url.port}" if url.port is not None else ""
    if origin != f"{url.scheme}://{address}{port}" or any(
        ch.isspace() or ch in "%\\@*" for ch in host
    ):
        raise ValueError("公开根地址需要使用规范化的主机和端口")
    return hashlib.sha256(origin.encode()).hexdigest()[:24]


def _check_trusted_installation() -> None:
    """旧目录由系统拥有，普通用户的 Chromium 缓存不进入专用进程。"""
    if not INSTALLATION.exists() and not INSTALLATION.is_symlink():
        return
    for entry in (INSTALLATION, INSTALLATION / "chromium"):
        if (
            entry.is_symlink()
            or not entry.is_dir()
            or entry.stat().st_uid != 0
            or entry.stat().st_mode & 0o022
        ):
            raise ValueError("旧浏览器安装或 Chromium 目录不受系统保护")


def _prepare_dependencies(bundle: Path) -> None:
    """复用旧托管模板的可信 Chromium，普通模板用固定 Playwright 下载同版。"""
    current = "playwright==" + importlib.metadata.version("playwright")
    if (bundle / "requirements.txt").read_text().strip() != current:
        raise ValueError("当前环境的 Playwright 版本与发行物不一致")
    if (
        not (bundle / "abridge-client-browser").is_file()
        or not (bundle / "extension/manifest.json").is_file()
    ):
        raise ValueError("浏览器发行物不完整")
    existing = INSTALLATION / "chromium"
    chromium = bundle / "chromium"
    if existing.is_dir():
        shutil.copytree(existing, chromium, symlinks=True, copy_function=os.link)
    else:
        subprocess.run(
            [sys.executable, "-I", "-m", "playwright", "install", "chromium"],
            env={**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(chromium)},
            check=True,
        )


def _prepare_account() -> None:
    """启用专用账号，只在该账号的浏览器进程全部停止时迁移状态。"""
    try:
        account = pwd.getpwnam(ACCOUNT)
    except KeyError:
        subprocess.run(
            [
                "useradd",
                "--system",
                "--create-home",
                "--home-dir",
                str(ACCOUNT_HOME),
                "--shell",
                "/usr/sbin/nologin",
                ACCOUNT,
            ],
            check=True,
        )
        account = pwd.getpwnam(ACCOUNT)
        ACCOUNT_HOME.chmod(0o700)
    if (
        account.pw_uid in {0, pwd.getpwnam("user").pw_uid}
        or Path(account.pw_dir) != ACCOUNT_HOME
        or ACCOUNT_HOME.stat().st_mode & 0o077
    ):
        raise ValueError("浏览器专用账号或私有目录不符合安装要求")
    running = subprocess.run(
        ["pgrep", "-u", str(account.pw_uid)], capture_output=True, check=False
    )
    if running.returncode != 1:
        raise ValueError("请停止浏览器专用账号的进程后再升级")


def main() -> None:
    """在原 VM 的管理员会话中执行，安装期间保持运行环境空闲。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--public-origin", required=True)
    args = parser.parse_args()
    if os.getuid() != 0:
        parser.exit(1, "升级需要 VM 管理员权限\n")
    try:
        with Path("/run/yuxi-managed-browser-upgrade.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            install(args.archive, args.sha256, args.public_origin)
    except ValueError as exc:
        parser.exit(1, f"浏览器升级失败：{exc}\n")
    except (OSError, subprocess.SubprocessError, tarfile.TarError):
        parser.exit(1, "浏览器升级失败，请核对安装目录、依赖及管理员权限\n")


if __name__ == "__main__":
    main()
