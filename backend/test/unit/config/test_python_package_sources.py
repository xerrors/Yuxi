"""依赖来源由项目配置与锁文件共同拥有，安装不依赖地域镜像。"""

import tomllib

from test.unit.config._shared import project_root


def test_default_python_index_uses_official_pypi():
    """未配置默认索引时使用 uv 默认 PyPI，显式设置也只能指向官方源。"""
    with (project_root() / "backend/pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)

    for index in project["tool"]["uv"].get("index", []):
        if index.get("default"):
            assert index["url"] == "https://pypi.org/simple", "项目默认索引必须使用官方 PyPI"


def test_locked_python_packages_use_official_registry_and_files():
    """冻结安装直接消费锁定 URL，不能仅修改默认索引而保留镜像下载地址。"""
    with (project_root() / "backend/uv.lock").open("rb") as stream:
        lock = tomllib.load(stream)

    for package in lock["package"]:
        if "registry" not in package["source"]:
            continue
        assert package["source"]["registry"] == "https://pypi.org/simple", package["name"]
        artifacts = package.get("wheels", [])
        if "sdist" in package:
            artifacts = [package["sdist"], *artifacts]
        for artifact in artifacts:
            assert artifact["url"].startswith("https://files.pythonhosted.org/packages/"), artifact["url"]
