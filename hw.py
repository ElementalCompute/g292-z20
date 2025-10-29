import shlex
import subprocess

class Cmd:
    """Small helper to run shell commands safely and capture output."""
    @staticmethod
    def run(cmd, check=True, text=True):
        # Accept either a string or a list; avoid 'shell=True' for safety
        if isinstance(cmd, str):
            cmd = shlex.split(cmd)
        return subprocess.run(cmd, capture_output=True, text=text, check=check)

    @staticmethod
    def out(cmd):
        """Return stdout (str). If the command fails, return stdout+stderr for debugging."""
        try:
            return Cmd.run(cmd).stdout.strip()
        except subprocess.CalledProcessError as e:
            return (e.stdout or "") + (e.stderr or "")
