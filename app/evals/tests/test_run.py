"""Arguments survive the Windows npx.cmd launcher."""

import os
import subprocess
import sys

import pytest

from run import cmd_escape


def test_plain_arguments_are_unchanged():
    assert cmd_escape("c2-dispute") == "c2-dispute"


def test_pipe_is_escaped_for_double_parsing():
    assert cmd_escape("a|b") == "a^^^|b"


@pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe only")
def test_escaped_pipe_reaches_the_program_through_a_cmd_launcher(tmp_path):
    launcher = tmp_path / "echo.cmd"
    launcher.write_text(
        '@"node" -e "console.log(process.argv.slice(1).join(String.fromCharCode(10)))" -- %*\r\n'
    )
    if not any(
        os.access(os.path.join(p, "node.exe"), os.X_OK) for p in os.environ["PATH"].split(";")
    ):
        pytest.skip("node not on PATH")
    out = subprocess.run(
        [str(launcher), cmd_escape("c4-theft|c5-expired")], capture_output=True, text=True
    )
    assert out.stdout.strip() == "c4-theft|c5-expired"
