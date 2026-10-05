"""Harness hygiene: a server spawned by a test or bench never reads the owner's nx env file
(~/.config/nx/env) and never writes metrics to ~/.local/state/nx. Import this module before spawning: it points
XDG_STATE_HOME and XDG_CONFIG_HOME of this process (and so of every child) at a private temp dir, removed at exit.
A caller that wants its own dirs sets both variables and NX_TEST_XDG=1; child harnesses then keep them."""
import atexit, os, shutil, tempfile


def apply():
    if os.environ.get("NX_TEST_XDG") == "1":
        return
    d = tempfile.mkdtemp(prefix="nx-xdg-")
    atexit.register(shutil.rmtree, d, True)
    for k, sub in (("XDG_STATE_HOME", "state"), ("XDG_CONFIG_HOME", "config")):
        os.environ[k] = os.path.join(d, sub)
        os.makedirs(os.environ[k])
    os.environ["NX_TEST_XDG"] = "1"


apply()
