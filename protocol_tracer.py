"""
protocol_tracer.py - shows the RAW JSON-RPC 2.0 messages between client and server (Lab 8).
No SDK, no AI, no API key - just Python's subprocess and json.

Run:  python protocol_tracer.py
Take screenshots of the output for your report (handshake, tools/list, tools/call).
"""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def send(proc, message: dict, step: int, title: str, expect_reply: bool = True):
    print(f"\n{'=' * 70}\nSTEP {step}: {title.upper()}\n{'=' * 70}")
    print("CLIENT >>> SERVER")
    print(json.dumps(message, indent=2))
    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()
    if not expect_reply:
        print("(notification - the server sends no reply)")
        return None
    while True:                                   # skip any server notifications/log lines
        reply = json.loads(proc.stdout.readline())
        if reply.get("id") == message.get("id"):
            break
    print("\nSERVER >>> CLIENT")
    text = json.dumps(reply, indent=2)
    print(text if len(text) < 2500 else text[:2500] + "\n  ... (truncated for display)")
    return reply


def main():
    print("Launching MCP server subprocess: p2p_server.py")
    proc = subprocess.Popen(
        [sys.executable, str(HERE / "p2p_server.py")], cwd=str(HERE),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", bufsize=1,
    )
    try:
        send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "ProcureGuardTracer", "version": "1.0.0"}}},
             1, "Handshake & capability negotiation")
        send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"},
             2, "Client confirms handshake", expect_reply=False)
        reply = send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                     3, "Discovery: list the server's tools")
        print(f"\n-> {len(reply['result']['tools'])} tools discovered: "
              f"{[t['name'] for t in reply['result']['tools']]}")
        send(proc, {"jsonrpc": "2.0", "id": 3, "method": "resources/read",
                    "params": {"uri": "policy://p2p-heuristics"}},
             4, "Read a resource (passive primitive)")
        send(proc, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                    "params": {"name": "three_way_match", "arguments": {"invoice_id": "INV-1002"}}},
             5, "Tool execution: three-way match on INV-1002")
        send(proc, {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                    "params": {"name": "search_p2p_knowledge",
                               "arguments": {"query": "price variation clause", "doc_type": "contract", "vendor_id": "V002"}}},
             6, "RAG tool: search the knowledge base for Apex Steel's price clause")
        send(proc, {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                    "params": {"name": "no_such_tool", "arguments": {}}},
             7, "Error handling: calling a tool that does not exist")
    finally:
        print("\nTerminating server subprocess.")
        proc.terminate()


if __name__ == "__main__":
    main()
