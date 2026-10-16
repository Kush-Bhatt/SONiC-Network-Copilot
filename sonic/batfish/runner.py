import os
import subprocess

from langchain_core.tools import tool

from sonic.config.settings import BATFISH_DIR, BASE_DIR, REPO_ROOT

@tool
def run_batfish_script(script_name: str = "assertions.py") -> dict:
    """
    Runs a generated PyBatfish assertions script via subprocess and
    returns whether all assertions passed.

    Args:
      script_name : filename of the script to run, relative to BATFISH_DIR
                    (default "assertions.py" — the file BatfishAgent writes to)

    Returns:
      dict with keys:
        success : bool — True if the script exited 0 (all assertions passed)
        stdout  : str  — captured standard output
        stderr  : str  — captured standard error
    """
    script_path = BATFISH_DIR / script_name

    if not os.path.exists(script_path):
        raise FileNotFoundError(f"Batfish script missing at {script_path}")


    cmd = ["python", "-m", f"sonic.batfish.{script_name.replace('.py', '')}"]

    print(f"[*] Executing: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=os.environ.copy()
    )

    print("\n--- PyBatfish Output ---")
    print(result.stdout if result.stdout.strip() else "<No output>")

    if result.stderr:
        print("\n--- Errors ---")
        print(result.stderr)

    return {"success": result.returncode == 0, "stdout": result.stdout, "stderr": result.stderr}