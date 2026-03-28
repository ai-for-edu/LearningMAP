import os
import pytest
from httpx import AsyncClient, ASGITransport
from run.serve import app


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="No API key configured",
)
async def test_full_chat_flow():
    """Test: greeting → learn topic → get graph."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Step 1: Greeting
        r1 = await client.post("/api/chat", json={"message": "你好"}, timeout=30)
        assert r1.status_code == 200
        data1 = r1.json()
        assert data1["mode"] == "chat"
        session_id = data1["session_id"]

        # Step 2: Learn topic
        r2 = await client.post("/api/chat", json={
            "session_id": session_id,
            "message": "我想学习勾股定理",
        }, timeout=60)
        assert r2.status_code == 200
        data2 = r2.json()
        assert data2["mode"] == "graph"
        assert data2["data"]["graph"]["topic"] is not None
