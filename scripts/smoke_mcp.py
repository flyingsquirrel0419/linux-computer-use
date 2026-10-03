import asyncio, base64, pathlib
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    params = StdioServerParameters(command="uv", args=["--directory", str(pathlib.Path(__file__).resolve().parents[1]), "run", "lcu"])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = (await s.list_tools()).tools
            print("tools:", [t.name for t in tools])
            print((await s.call_tool("screen_info", {})).content[0].text)
            tree = (await s.call_tool("ui_tree", {"app": "gedit"})).content[0].text
            print(tree)
            res = await s.call_tool("list_windows", {})
            print(res.content[0].text[:300])
            res = await s.call_tool("click_element", {"id": 5, "screenshot_after": True})
            for c in res.content:
                if c.type == "image":
                    pathlib.Path("/tmp/lcu_smoke.png").write_bytes(base64.b64decode(c.data)); print("image ok", c.mimeType)
                else: print(c.text)
asyncio.run(main())
