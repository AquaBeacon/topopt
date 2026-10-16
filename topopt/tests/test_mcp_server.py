"""Exercise a real stdio client and detached CLI worker, without running optimization."""
import asyncio
import json
from pathlib import Path
import sys
import threading
import time

import pytest

from topopt.mcp_server import Workspace, environment, save


def logged_job(tmp_path, content=b"", complete=True):
    ws = Workspace(tmp_path)
    job_id = "a" * 32
    directory = ws.jobs / job_id
    directory.mkdir(parents=True)
    save(directory / "request.json", {"argv": ["validate", "missing.json"], "cwd": str(tmp_path)})
    (directory / "output.log").write_bytes(content)
    if complete:
        save(directory / "result.json", {"state": "finished", "exit_code": 2})
    return ws, job_id, directory


def test_log_budget_and_status_only(tmp_path):
    ws, job_id, _ = logged_job(tmp_path, b"x" * 10000)
    result = ws.status(job_id, tail_bytes=50)
    assert len(result["log_tail"].encode()) == 50
    assert result["log_offset"] == 10000
    assert not result["log_more"]
    assert ws.status(job_id, tail_bytes=0)["log_tail"] == ""
    assert len(ws.status(job_id)["log_tail"]) == 2000


def test_incremental_log_no_replay_and_pagination(tmp_path):
    ws, job_id, directory = logged_job(tmp_path, b"old\n")
    first = ws.status(job_id)
    unchanged = ws.status(job_id, log_offset=first["log_offset"])
    assert unchanged["log_tail"] == ""
    with (directory / "output.log").open("ab") as stream:
        stream.write(b"new lines\n")
    changed = ws.status(job_id, tail_bytes=4, log_offset=first["log_offset"])
    assert changed["log_tail"] == "new "
    assert changed["log_more"]
    remaining = ws.status(job_id, log_offset=changed["log_offset"])
    assert remaining["log_tail"] == "lines\n"
    assert not remaining["log_more"]


def test_log_replacement_and_invalid_cursor(tmp_path):
    ws, job_id, _ = logged_job(tmp_path, b"replaced\n")
    result = ws.status(job_id, log_offset=1000)
    assert result["log_restarted"]
    assert result["log_tail"] == "replaced\n"
    with pytest.raises(ValueError):
        ws.status(job_id, log_offset=-1)


def test_wait_wakes_on_completion_without_new_log(tmp_path):
    ws, job_id, directory = logged_job(tmp_path, complete=False)
    before = ws.status(job_id, log_offset=0)
    assert before["state"] == "pending_or_running"
    assert before["exit_code"] is None

    def finish():
        time.sleep(0.05)
        save(directory / "result.json", {"state": "finished", "exit_code": 1})

    thread = threading.Thread(target=finish)
    thread.start()
    try:
        started = time.monotonic()
        after = ws.status(job_id, log_offset=0, wait_seconds=3)
        assert after["state"] == "finished"
        assert after["exit_code"] == 1
        assert after["log_tail"] == ""
        assert time.monotonic() - started < 2
    finally:
        thread.join()


def test_paths_and_commands(tmp_path):
    ws = Workspace(tmp_path)
    with pytest.raises(ValueError):
        ws.path("../outside.json")
    with pytest.raises(ValueError):
        ws.start("powershell", [])
    with pytest.raises(ValueError):
        ws.status("../")


def test_detached_job_persists(tmp_path):
    ws = Workspace(tmp_path)
    job = ws.start("validate", ["missing.json"])
    # A new Workspace instance simulates reconnecting to the server.
    reconnected = Workspace(tmp_path)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        result = reconnected.status(job["job_id"])
        if result["state"] == "finished":
            break
        time.sleep(0.1)
    assert result["state"] == "finished", result
    assert result["exit_code"] == 2
    assert "missing.json" in result["log_tail"]


def test_stdio_roundtrip(tmp_path):
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def run():
        params = StdioServerParameters(command=sys.executable,
            args=["-m", "topopt.mcp_server", "--workspace", str(tmp_path)], env=environment())
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                listing = await client.list_tools()
                assert len(listing.tools) == 7
                result = await client.call_tool("topopt_help", {"command": "setup"})
                assert not result.isError
                assert "--no-estimate" in str(result)
                result = await client.call_tool("topopt_write_study", {"path": "model/study.json", "study": {"name": "日本語"}})
                assert not result.isError
                result = await client.call_tool("topopt_write_study", {"path": "model/study.json", "study": {}})
                assert result.isError
                assert json.loads((tmp_path / "model/study.json").read_text(encoding="utf-8"))["name"] == "日本語"
                document = {"schema": "topopt/report/1", "verdict": "FAIL",
                            "fail": ["mesh convergence"], "unknown": ["finished shape"],
                            "pass": False, "history": list(range(10000))}
                (tmp_path / "result.json").write_text(json.dumps(document), encoding="utf-8")
                result = await client.call_tool("topopt_read", {"path": "result.json",
                    "json_keys": ["verdict", "fail", "unknown", "pass", "absent"]})
                assert not result.isError
                projected = result.structuredContent
                assert projected["data"] == {key: document[key] for key in ["verdict", "fail", "unknown", "pass"]}
                assert projected["missing_keys"] == ["absent"]
                assert "history" in projected["available_keys"]
                assert "history" not in projected["data"]
                result = await client.call_tool("topopt_read", {"path": "result.json", "json_keys": []})
                assert result.structuredContent["data"] == {}
                result = await client.call_tool("topopt_read", {"path": "result.json", "json_keys": ["history"], "limit": 100})
                assert result.isError  # never silently truncate a selected JSON field
                result = await client.call_tool("topopt_read", {"path": "result.json", "json_keys": [], "offset": 1})
                assert result.isError
                result = await client.call_tool("topopt_read", {"path": "result.json", "offset": 1, "limit": 10})
                assert not result.isError
                assert result.structuredContent["text"] == (tmp_path / "result.json").read_text()[1:11]
                result = await client.call_tool("topopt_read", {"path": "../outside"})
                assert result.isError
                result = await client.call_tool("topopt_start", {"command": "validate", "arguments": ["missing.json"]})
                assert not result.isError
                job = result.structuredContent
                for _ in range(100):
                    result = await client.call_tool("topopt_job", {"job_id": job["job_id"]})
                    if result.structuredContent["state"] == "finished":
                        break
                    await asyncio.sleep(0.1)
                assert result.structuredContent["exit_code"] == 2
                result = await client.call_tool("topopt_job", {"job_id": job["job_id"],
                    "log_offset": result.structuredContent["log_offset"], "wait_seconds": 30})
                assert result.structuredContent["log_tail"] == ""
                assert result.structuredContent["exit_code"] == 2
    asyncio.run(run())
