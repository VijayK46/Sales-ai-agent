"""Guards the deployment environment itself.

The Dockerfile once ran Python 3.9 while yt-dlp required >= 3.10. pip did not
fail - it quietly installed a months-old yt-dlp that then broke against
YouTube. This test makes that mismatch loud.
"""

import os
import re
from importlib.metadata import PackageNotFoundError, metadata

import pytest
from packaging.specifiers import SpecifierSet
from packaging.version import Version

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def docker_python_version():
    with open(os.path.join(ROOT, "Dockerfile")) as f:
        match = re.search(r"^FROM python:(\d+)\.(\d+)", f.read(), re.MULTILINE)
    assert match, "Dockerfile must pin a python:X.Y base image"
    return Version(f"{match.group(1)}.{match.group(2)}")


def declared_requirements():
    with open(os.path.join(ROOT, "requirements.txt")) as f:
        lines = [line.strip() for line in f if line.strip()]
    return [re.split(r"[<>=!\[]", line)[0].strip() for line in lines]


def test_dockerfile_pins_a_python_version():
    assert docker_python_version() >= Version("3.10")


@pytest.mark.parametrize("package", declared_requirements())
def test_dependency_supports_the_docker_python(package):
    try:
        requires = metadata(package)["Requires-Python"]
    except PackageNotFoundError:
        pytest.skip(f"{package} is not installed in this environment")

    if not requires:
        return

    python_version = docker_python_version()
    assert SpecifierSet(requires).contains(str(python_version)), (
        f"{package} requires Python {requires}, but the Dockerfile runs "
        f"{python_version}. pip will silently install an outdated {package} "
        f"instead of failing the build."
    )
