import asyncio
import websockets
import json
import os
from dotenv import load_dotenv
load_dotenv()

async def test_model(model):
    url = f'wss://api.openai.com/v1/realtime?model={model}'
    headers = {'Authorization': f'Bearer {os.getenv("OPENAI_API_KEY")}'}
    try:
        async with websockets.connect(url, additional_headers=headers) as ws:
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            data = json.loads(msg)
            print(f'{model}: SUCCESS - got {data.get("type")}')
    except Exception as e:
        print(f'{model}: FAILED - {str(e)[:80]}')

async def main():
    models = [
        'gpt-realtime-2',
        'gpt-realtime-2025-08-28',
        'gpt-realtime-1.5',
        'gpt-realtime-mini',
        'gpt-realtime',
    ]
    for m in models:
        await test_model(m)

asyncio.run(main())
