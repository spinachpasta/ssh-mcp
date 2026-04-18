# SSH MCP Server for Gemini CLI

The SSH MCP (Model Context Protocol) server allows the Gemini CLI to execute commands on remote hosts via SSH. It provides a stateful, interactive SSH session interface with host-level allowlisting.

## Prerequisites

- **Python 3.x**
- **Existing SSH Access:** The server uses the system's default `ssh`. Therefore, your hostnames or IP addresses must be directly reachable, and authentication must work via default SSH keys (e.g., `~/.ssh/id_rsa`, `~/.ssh/id_ed25519`) or an active SSH agent without requiring password prompts.

## 1. Setup Allowed Hosts

Before the AI can connect to any host, you must explicitly allowlist the destinations to prevent unauthorized access. 

Create or edit the `hosts.txt` file located in the same directory as `main.py`, and add the allowed hostnames or IP addresses (one per line):

```text
# hosts.txt
192.168.1.100
my-remote-server.local
ubuntu@10.0.0.5
```

## 2. Configure Gemini CLI

To expose this server to the Gemini CLI, you need to register it in your Gemini settings.

1. Open your Gemini settings file. This is typically located at:
   - Global: `~/.gemini/settings.json` (macOS/Linux)
   - Project-level: `.gemini/settings.json` in your project root

2. Add or update the `mcpServers` object to include the `ssh-mcp` server. Make sure to use the **absolute path** to the `main.py` script (example below):

```json
{
  "mcpServers": {
    "ssh-mcp": {
      "command": "python3",
      "args": [
        "/path/to/ssh-mcp/main.py"
      ]
    }
  }
}
```
*(Be sure to replace the path with your actual absolute path to `main.py`)*

## 3. Usage

After saving the configuration, start (or restart) your Gemini CLI. 

You can verify that the server is successfully connected by typing the following command in the Gemini chat:
```bash
/mcp
```

The Gemini CLI will now automatically use the following tools when you ask it to perform remote tasks:
- **`open_session`**: Initiates an SSH connection to a host listed in `hosts.txt`.
- **`make_input`**: Sends raw commands and arguments to the remote host.
- **`read_output`**: Reads the resulting execution output buffer.
- **`close_session`**: Safely closes the SSH connection.

You can now ask the AI to perform remote tasks, for example:
> *"Connect to 192.168.1.100 and check the available disk space."*
> *"View the system logs on my-remote-server.local for any errors."*
