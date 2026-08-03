
"""
process tool — Hermes-aligned background process management.
"""
import json, os, subprocess, time, signal, logging

logger = logging.getLogger("dragon.tool.process")

_procs = {}  # session_id -> subprocess.Popen

async def tool_process(
    action: str,
    session_id: str = "",
    command: str = "",
    timeout: int = 300,
    workdir: str = "",
    data: str = "",
):
    """Manage background processes. Hermes-aligned.

    Actions: 'list', 'start', 'poll', 'log', 'wait', 'kill', 'write', 'submit', 'close'

    Args:
        action: One of list, start, poll, log, wait, kill, write, submit, close
        session_id: Process session ID (required for all except list, start)
        command: Shell command (for 'start')
        timeout: Timeout seconds (for 'start' and 'wait')
        workdir: Working directory
        data: Data to send to stdin (for 'write'/'submit')
    """
    try:
        if action == "list":
            procs = []
            for sid, p in _procs.items():
                poll = p.poll()
                procs.append({
                    "session_id": sid,
                    "pid": p.pid,
                    "status": "running" if poll is None else f"exited({poll})",
                    "command": p.args if isinstance(p.args, str) else " ".join(p.args) if p.args else "?",
                })
            return json.dumps({"processes": procs})

        elif action == "start":
            if not command:
                return json.dumps({"error": "command required for start"})
            import uuid
            sid = session_id or uuid.uuid4().hex[:8]
            cwd = workdir or "."
            
            proc = subprocess.Popen(
                command, shell=True, cwd=cwd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE if data else None,
                text=True, bufsize=1,
            )
            _procs[sid] = proc
            return json.dumps({
                "status": "started",
                "session_id": sid,
                "pid": proc.pid,
                "command": command,
            })

        elif not session_id:
            return json.dumps({"error": "session_id required"})

        proc = _procs.get(session_id)
        if not proc:
            return json.dumps({"error": f"Process not found: {session_id}"})

        if action == "poll":
            poll = proc.poll()
            if poll is None:
                # Read available output
                out = ""
                if proc.stdout:
                    try:
                        import select
                        while select.select([proc.stdout], [], [], 0.01)[0]:
                            line = proc.stdout.readline()
                            if line:
                                out += line
                    except Exception:
                        pass
                return json.dumps({"status": "running", "output": out[-5000:] if out else "(no output yet)"})
            else:
                out = proc.stdout.read() if proc.stdout else ""
                return json.dumps({"status": f"exited({poll})", "output": out[-5000:]})

        elif action == "log":
            # For already exited processes, return saved output
            out = ""
            if proc.stdout:
                try:
                    out = proc.stdout.read()
                except Exception:
                    out = "(cannot read stdout)"
            return json.dumps({"output": out[-10000:] if out else "(empty)"})

        elif action == "wait":
            try:
                proc.wait(timeout=timeout)
                out = proc.stdout.read() if proc.stdout else ""
                return json.dumps({"status": f"exited({proc.returncode})", "output": out[-5000:]})
            except subprocess.TimeoutExpired:
                return json.dumps({"status": "timeout", "message": f"Still running after {timeout}s"})

        elif action == "kill":
            proc.kill()
            del _procs[session_id]
            return json.dumps({"status": "killed", "session_id": session_id})

        elif action == "close":
            if proc.stdin:
                proc.stdin.close()
            return json.dumps({"status": "stdin closed"})

        elif action == "write":
            if not proc.stdin:
                return json.dumps({"error": "Process has no stdin"})
            proc.stdin.write(data)
            proc.stdin.flush()
            return json.dumps({"status": "written", "bytes": len(data)})

        elif action == "submit":
            if not proc.stdin:
                return json.dumps({"error": "Process has no stdin"})
            proc.stdin.write(data + "\n")
            proc.stdin.flush()
            return json.dumps({"status": "submitted", "bytes": len(data) + 1})

        else:
            return json.dumps({"error": f"Unknown action: {action}"})

    except Exception as e:
        return json.dumps({"error": str(e)})
