"""The real Claude Code CLI still has everything tabmancer runs it with.

Every week in CI against the latest release (.github/workflows/claude-cli.yml), so a flag or a
command that goes away shows up there before someone reports it. Locally: set TABMANCER_REAL_CLAUDE=1.
"""

import json
import os
import re
import shutil
import subprocess

import pytest

from tabmancer import doctor

pytestmark = pytest.mark.skipif(not os.environ.get("TABMANCER_REAL_CLAUDE"),
                                reason="the real claude CLI: set TABMANCER_REAL_CLAUDE=1")


@pytest.fixture
def claude():
    found = shutil.which("claude")
    assert found, "claude is not on PATH"
    return found


def test_every_flag_and_command_tabmancer_uses_is_still_there(claude):
    assert doctor.missing_from_cli(claude) == []


def test_the_version_reads(claude):
    assert re.search(r"\d+\.\d+\.\d+", doctor._claude_version(claude))


def test_agents_json_is_a_list(claude):
    # conftest points CLAUDE_CONFIG_DIR at an empty folder: no sessions, no sign-in needed.
    result = subprocess.run([claude, "agents", "--json"], capture_output=True, text=True, timeout=60, check=True)
    assert isinstance(json.loads(result.stdout), list)
