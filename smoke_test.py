"""
smoke_test.py - checks the MCP server works BEFORE any AI is involved (Lab 7 pattern).
No API key needed.      Run:  python smoke_test.py

It walks through the MCP lifecycle:
  [1] spawn server  [2] handshake  [3] discover tools / resources / prompts  [4] call tools
"""
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).resolve().parent


def show(title: str, result) -> None:
    data = json.loads(result.content[0].text)
    print(f"\n--- {title}")
    print(json.dumps(data, indent=2)[:1500])


async def main() -> None:
    print("=" * 64)
    print(" PROCUREGUARD - MCP SERVER SMOKE TEST (no AI, no API key)")
    print("=" * 64)
    server = StdioServerParameters(command=sys.executable, args=[str(HERE / "p2p_server.py")], cwd=str(HERE))

    print("\n[1] Spawning MCP server subprocess over stdio...")
    (HERE / "logs").mkdir(exist_ok=True)
    with open(HERE / "logs" / "server_stderr.log", "w") as server_log:
        async with stdio_client(server, errlog=server_log) as (read, write):
            async with ClientSession(read, write) as session:
                print("[2] Handshake (initialize)...")
                init = await session.initialize()
                print(f"    -> Connected to: {init.serverInfo.name} | protocol {init.protocolVersion}")

                print("\n[3] Discovery")
                tools = (await session.list_tools()).tools
                print(f"    Tools ({len(tools)}):")
                for t in tools:
                    print(f"      - {t.name:30} needs {t.inputSchema.get('required', [])}")
                resources = (await session.list_resources()).resources
                print(f"    Resources: {[str(r.uri) for r in resources]}")
                prompts = (await session.list_prompts()).prompts
                print(f"    Prompts:   {[p.name for p in prompts]}")

                print("\n[4] Calling tools")
                show("three_way_match INV-1001 (should be MATCHED)",
                     await session.call_tool("three_way_match", {"invoice_id": "INV-1001"}))
                show("three_way_match INV-1002 (should be EXCEPTION: price + quantity)",
                     await session.call_tool("three_way_match", {"invoice_id": "INV-1002"}))
                show("check_duplicate_invoice INV-1004 (should be SUSPECTED DUPLICATE)",
                     await session.call_tool("check_duplicate_invoice", {"invoice_id": "INV-1004"}))
                show("calculate_vendor_risk_score V002 (should be HIGH)",
                     await session.call_tool("calculate_vendor_risk_score", {"vendor_id": "V002"}))
                show("calculate_payment_schedule INV-1003 (MSME 45-day deadline)",
                     await session.call_tool("calculate_payment_schedule", {"invoice_id": "INV-1003"}))
                show("get_cash_position",
                     await session.call_tool("get_cash_position", {}))

                policy = await session.read_resource("policy://p2p-heuristics")
                print(f"\n--- Resource policy://p2p-heuristics: {len(policy.contents[0].text)} characters loaded")

    print("\nSMOKE TEST PASSED - your MCP server is working.")


if __name__ == "__main__":
    asyncio.run(main())
