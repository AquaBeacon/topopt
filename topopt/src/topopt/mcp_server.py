"""Local stdio MCP adapter. CLI subprocesses keep solver output off stdio.

This is a trusted local integration, not a filesystem sandbox: CLI arguments
and study geometry references have the same access as the launching user.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from typing import Any

from topopt.cli import COMMANDS


def environment():
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1]) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def save(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def worker(directory):
    """Detached worker persists completion even after the MCP client disconnects."""
    directory = Path(directory)
    request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
    result = {"state": "finished", "exit_code": None}
    try:
        with (directory / "output.log").open("wb") as log:
            proc = subprocess.Popen([sys.executable, "-m", "topopt", *request["argv"]],
                                    cwd=request["cwd"], env=environment(), stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT)
            result["exit_code"] = proc.wait()
    except Exception as exc:
        result.update(state="failed", error=str(exc))
    result["finished_at"] = time.time()
    save(directory / "result.json", result)


class Workspace:
    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)
        self.jobs = self.root / ".topopt-mcp" / "jobs"

    def path(self, name):
        path = (self.root / name).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("File tools require a path inside the configured workspace.")
        return path

    def start(self, command, arguments):
        if command not in COMMANDS:
            raise ValueError("Unknown command; use topopt_help to list commands.")
        if any("\x00" in arg for arg in arguments):
            raise ValueError("NUL is not allowed in arguments.")
        job_id = uuid.uuid4().hex
        directory = self.jobs / job_id
        directory.mkdir(parents=True)
        save(directory / "request.json", {"argv": [command, *arguments], "cwd": str(self.root),
                                          "created_at": time.time()})
        options = {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        with (directory / "worker.log").open("wb") as log:
            proc = subprocess.Popen([sys.executable, "-m", "topopt.mcp_server", "--worker", str(directory)],
                                    cwd=self.root, env=environment(), stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=log, **options)
        return {"job_id": job_id, "state": "started", "pid": proc.pid,
                "log": str(directory / "output.log")}

    def status(self, job_id, tail_bytes=2000, log_offset=None, wait_seconds=0):
        if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
            raise ValueError("Use the job_id returned by topopt_start.")
        directory = self.jobs / job_id
        request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
        result_path = directory / "result.json"
        log_path = directory / "output.log"
        if log_offset is not None and log_offset < 0:
            raise ValueError("log_offset must be non-negative.")
        # Wait only when the caller supplies a cursor. Completion wakes the
        # call even if the CLI writes no final line. Never wait indefinitely.
        deadline = time.monotonic() + max(0, min(wait_seconds, 30))
        while log_offset is not None and not result_path.exists():
            size = log_path.stat().st_size if log_path.exists() else 0
            if size != log_offset or time.monotonic() >= deadline:
                break
            time.sleep(min(0.2, max(0, deadline - time.monotonic())))
        result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {
            "state": "pending_or_running", "exit_code": None,
            "note": "No completion record yet. A killed worker also leaves this state; do not infer success."}
        tail = ""
        size = log_path.stat().st_size if log_path.exists() else 0
        budget = max(0, min(tail_bytes, 50000))
        restarted = log_offset is not None and log_offset > size
        start = (0 if restarted else log_offset) if log_offset is not None else max(0, size - budget)
        end = start
        if log_path.exists():
            with log_path.open("rb") as stream:
                stream.seek(start)
                data = stream.read(budget)
                end = start + len(data)
                tail = data.decode("utf-8", errors="replace")
        return {"job_id": job_id, **request, **result, "log_tail": tail,
                "log_offset": end, "log_total_bytes": size,
                "log_more": end < size, "log_restarted": restarted}


def create_server(root):
    from mcp.server.fastmcp import FastMCP
    from mcp.server.fastmcp.utilities.types import Image

    ws = Workspace(root)
    server = FastMCP("topopt_mcp", instructions=(
        "Units mm/N/MPa. Start with docs/mcp.md; consult only relevant spec/workflow sections. "
        "Check setup preview/runtime before opt. Use a fresh run directory; keep study outside outputs. "
        "Poll with log_offset and wait_seconds; read JSON with json_keys to omit histories. "
        "Exit 0 is command success, not structural approval. Check FAIL/UNKNOWN, convergence, "
        "provenance and the actual finished shape before claiming safety. CLI inputs are trusted local inputs."))
    read = {"readOnlyHint": True, "openWorldHint": False}
    write = {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False}

    @server.tool(annotations=read)
    def topopt_help(command: str = "") -> dict[str, Any]:
        """List all design/FEA/optimization/print commands, or get exact CLI options for one command."""
        if command and command not in COMMANDS:
            raise ValueError("Unknown command: " + command)
        args = [command, "--help"] if command else ["--help"]
        proc = subprocess.run([sys.executable, "-m", "topopt", *args], cwd=ws.root,
                              env=environment(), stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, encoding="utf-8", timeout=30)
        return {"workspace": str(ws.root), "exit_code": proc.returncode, "help": proc.stdout + proc.stderr}

    @server.tool(annotations=write)
    def topopt_start(command: str, arguments: list[str]) -> dict[str, Any]:
        """Start any topopt CLI command as a persistent background job. Arguments are separate tokens,
        e.g. command='setup', arguments=['model/study.json','--out','model/02_setup'].
        Relative paths use the configured workspace. Read help for options, then use topopt_job
        with its returned log_offset and wait_seconds=30 rather than repeating the full log tail.
        Commands may overwrite output files; use separate model/run directories.
        """
        return ws.start(command, arguments)

    @server.tool(annotations=read)
    def topopt_job(job_id: str, tail_bytes: int = 2000, log_offset: int | None = None,
                   wait_seconds: int = 0) -> dict[str, Any]:
        """Read completion and bounded UTF-8 logs, including after reconnect.
        Pass the returned log_offset for new bytes only; wait_seconds (0-30) waits for
        new bytes or completion. log_more means unread bytes remain. tail_bytes=0 reads
        status only. No completion record means unknown, including a killed worker.
        """
        return ws.status(job_id, tail_bytes, log_offset, wait_seconds)

    @server.tool(annotations=read)
    def topopt_list(path: str = ".", offset: int = 0, limit: int = 50) -> dict[str, Any]:
        """List a workspace directory (models, input geometry, artifacts or .topopt-mcp/jobs). Paginated."""
        entries = sorted(ws.path(path).iterdir(), key=lambda p: p.name)
        offset, limit = max(0, offset), max(1, min(limit, 200))
        return {"entries": [{"name": p.name, "directory": p.is_dir()} for p in entries[offset:offset+limit]],
                "total": len(entries), "next_offset": offset+limit if offset+limit < len(entries) else None}

    @server.tool(annotations=read)
    def topopt_read(path: str, offset: int = 0, limit: int = 20000,
                    json_keys: list[str] | None = None) -> dict[str, Any]:
        """Read UTF-8 by byte offset (max 50000), or select top-level JSON fields.
        json_keys=[] lists keys; select verdict/fail/unknown/pass or termination_reason,
        checks and outputs without loading history into the conversation. Missing fields
        are listed explicitly. Projection never computes or changes a verdict.
        """
        target = ws.path(path)
        offset, limit = max(0, offset), max(1, min(limit, 50000))
        if json_keys is not None:
            if offset:
                raise ValueError("JSON field selection cannot be combined with a byte offset.")
            if target.stat().st_size > 50_000_000:
                raise ValueError("JSON file exceeds 50 MB; use bounded byte reads.")
            value = json.loads(target.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("JSON field selection requires an object.")
            projected = {"schema": value.get("schema"), "available_keys": list(value),
                         "data": {key: value[key] for key in json_keys if key in value},
                         "missing_keys": [key for key in json_keys if key not in value]}
            if len(json.dumps(projected, ensure_ascii=False).encode("utf-8")) > limit:
                raise ValueError("Selected JSON fields exceed limit; select fewer fields or use byte reads.")
            return projected
        with target.open("rb") as stream:
            stream.seek(offset)
            data = stream.read(limit)
        end = offset + len(data)
        return {"text": data.decode("utf-8", errors="replace"), "next_offset": end if end < target.stat().st_size else None}

    @server.tool(annotations=write)
    def topopt_write_study(path: str, study: dict, overwrite: bool = False) -> dict[str, Any]:
        """Write study JSON inside the workspace. Existing files require overwrite=true.
        This saves an input, not a validation verdict; run setup to validate geometry and feasibility.
        """
        target = ws.path(path)
        if target.suffix.lower() != ".json":
            raise ValueError("Study path must end in .json.")
        content = json.dumps(study, ensure_ascii=False, indent=2, allow_nan=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w" if overwrite else "x", encoding="utf-8") as stream:
            stream.write(content)
        return {"path": str(target), "validated": False, "next": "Run setup and inspect preview."}

    @server.tool(annotations=read)
    def topopt_preview(path: str) -> Image:
        """Return an existing PNG preview as an MCP image for visual inspection (maximum 10 MB)."""
        target = ws.path(path)
        if target.suffix.lower() != ".png" or target.stat().st_size > 10_000_000:
            raise ValueError("Expected a PNG no larger than 10 MB.")
        return Image(path=str(target))

    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.getcwd())
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker(args.worker)
        return
    try:
        server = create_server(args.workspace)
    except ImportError as exc:
        print(f"MCP dependency missing: {exc}. Install pip install -e 'topopt[mcp]'", file=sys.stderr)
        raise SystemExit(3)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
