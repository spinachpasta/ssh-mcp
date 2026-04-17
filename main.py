import os
import sys
import json
import subprocess
import threading
import uuid
import queue

# ==========================================
# HOST SECURITY CONFIGURATION
# ==========================================
# We removed the target allowlists, but kept host-level protections to 
# ensure the MCP server itself cannot be crashed by the sandbox.
MAX_BUFFER_SIZE = 50000    # Prevent AI context overflow (50KB limit)
MAX_QUEUE_ITEMS = 1000     # Prevent HOST memory exhaustion (bounded queue)
HOSTS_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hosts.txt")

def load_allowed_hosts():
    try:
        with open(HOSTS_FILE_PATH, 'r') as f:
            hosts = []
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    hosts.append(line)
            return set(hosts)
    except FileNotFoundError:
        return set()

sessions = {}

# ==========================================
# CORE SSH LOGIC
# ==========================================
class SSHSession:
    def __init__(self, host: str):
        self.host = host
        self.output_buffer = queue.Queue(maxsize=MAX_QUEUE_ITEMS)
        
        # Spawn in default BINARY mode to prevent host crashes from weird data
        # Explicitly disable port forwarding to prevent the sandbox from pivoting back
        self.process = subprocess.Popen(
            [
                "ssh", 
                "-F", "/dev/null",              # Ignore ~/.ssh/config
                "-o", "ForwardAgent=no",        # Disable agent forwarding
                "-o", "ClearAllForwardings=yes",# Disable port forwarding
                "-T", "-q", "--", host
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, 
            bufsize=0 # Unbuffered for binary streams
        )

        self.reader_thread = threading.Thread(target=self._read_stream, daemon=True)
        self.reader_thread.start()

    def _read_stream(self):
        """Continuously reads raw bytes and safely decodes them."""
        try:
            for chunk in iter(lambda: self.process.stdout.read(1024), b''):
                if chunk:
                    safe_text = chunk.decode('utf-8', errors='replace')
                    try:
                        if self.output_buffer.full():
                            self.output_buffer.get_nowait()
                        self.output_buffer.put_nowait(safe_text)
                    except queue.Empty:
                        pass
                    except queue.Full:
                        pass
        except Exception:
            pass 

    def read_all_output(self) -> str:
        """Flushes the current queue into a single string."""
        lines = []
        chars_read = 0
        while not self.output_buffer.empty():
            try:
                chunk = self.output_buffer.get_nowait()
                if chars_read + len(chunk) > MAX_BUFFER_SIZE:
                    lines.append("\n[TRUNCATED: Output exceeded safety buffer limit]\n")
                    break
                lines.append(chunk)
                chars_read += len(chunk)
            except queue.Empty:
                break
        return "".join(lines)

    def close(self):
        if self.process.poll() is None:
            try:
                self.process.stdin.write(b"exit\n")
                self.process.stdin.flush()
            except OSError:
                pass 
            self.process.terminate()

# ==========================================
# TOOL HANDLERS
# ==========================================
def handle_open_session(args):
    host = args.get("host")
    
    allowed_hosts = load_allowed_hosts()
    if host not in allowed_hosts:
        return f"Error: Host '{host}' is not in the allowed hosts list. Please verify your host against hosts.txt."
        
    session_id = str(uuid.uuid4())
    try:
        sessions[session_id] = SSHSession(host)
        return f"Success: Session opened. session_id: {session_id}"
    except Exception as e:
        return f"Error opening session: {str(e)}"

def handle_make_input(args):
    session_id = args.get("session_id")
    base_command = args.get("base_command")
    parameters = args.get("parameters", [])
    
    if session_id not in sessions:
        return "ERROR: Invalid or expired session_id."

    # Pass parameters as raw strings without escaping.
    # The remote sandbox environment relies on Unix permissions.
    raw_args = [str(arg) for arg in parameters]
    full_command = f"{base_command} {' '.join(raw_args)}\n"
    
    try:
        sessions[session_id].process.stdin.write(full_command.encode('utf-8'))
        sessions[session_id].process.stdin.flush()
        return "Command sent safely. Use read_output to see results."
    except OSError:
        return "Error sending input: Connection to remote host was lost (Broken Pipe)."
    except Exception as e:
        return f"Error sending input: {str(e)}"

def handle_read_output(args):
    session_id = args.get("session_id")
    if session_id not in sessions:
        return "ERROR: Invalid or expired session_id."
    output = sessions[session_id].read_all_output()
    return output if output else "[No new output generated yet]"

def handle_close_session(args):
    session_id = args.get("session_id")
    if session_id not in sessions:
        return "ERROR: Invalid or expired session_id."
    sessions[session_id].close()
    del sessions[session_id]
    return f"Session {session_id} successfully closed."

# ==========================================
# MCP JSON-RPC PROTOCOL IMPLEMENTATION
# ==========================================
def send_response(request_id, result=None, error=None):
    response = {"jsonrpc": "2.0", "id": request_id}
    if error:
        response["error"] = error
    else:
        response["result"] = result
    
    sys.stdout.write(json.dumps(response) + "\n")
    sys.stdout.flush()

def handle_request(req):
    req_id = req.get("id")
    method = req.get("method")
    params = req.get("params", {})

    if method == "initialize":
        send_response(req_id, result={
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "zerodep-unrestricted-ssh", "version": "1.1.0"}
        })
    
    elif method == "notifications/initialized":
        pass 
        
    elif method == "tools/list":
        send_response(req_id, result={
            "tools": [
                {
                    "name": "open_session",
                    "description": "Open an SSH session to any host.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"host": {"type": "string"}},
                        "required": ["host"]
                    }
                },
                {
                    "name": "make_input",
                    "description": "Execute any parameterized command on the host. For raw strings/pipes, use base_command 'bash' and parameters ['-c', 'your command'].",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "session_id": {"type": "string"},
                            "base_command": {"type": "string"},
                            "parameters": {"type": "array", "items": {"type": "string"}}
                        },
                        "required": ["session_id", "base_command", "parameters"]
                    }
                },
                {
                    "name": "read_output",
                    "description": "Reads all pending output from the session.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"session_id": {"type": "string"}},
                        "required": ["session_id"]
                    }
                },
                {
                    "name": "close_session",
                    "description": "Terminates the SSH session.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"session_id": {"type": "string"}},
                        "required": ["session_id"]
                    }
                }
            ]
        })

    elif method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments", {})
        
        try:
            if tool_name == "open_session":
                result_text = handle_open_session(tool_args)
            elif tool_name == "make_input":
                result_text = handle_make_input(tool_args)
            elif tool_name == "read_output":
                result_text = handle_read_output(tool_args)
            elif tool_name == "close_session":
                result_text = handle_close_session(tool_args)
            else:
                send_response(req_id, error={"code": -32601, "message": f"Unknown tool: {tool_name}"})
                return
                
            send_response(req_id, result={
                "content": [{"type": "text", "text": result_text}]
            })
        except Exception as e:
             send_response(req_id, error={"code": -32000, "message": str(e)})

# ==========================================
# MAIN EVENT LOOP
# ==========================================
if __name__ == "__main__":
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            handle_request(request)
        except json.JSONDecodeError:
            continue