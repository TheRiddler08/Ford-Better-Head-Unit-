import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch, MagicMock

ROOT=Path(__file__).resolve().parents[1]
# Load just the proxy definitions so these tests need no installed HA runtime.
import ast, asyncio
from aiohttp import ClientError, ClientTimeout, web
tile=types.ModuleType('car_tile_test')
tile.__dict__.update(asyncio=asyncio,ClientError=ClientError,ClientTimeout=ClientTimeout,web=web,HomeAssistantView=type('HomeAssistantView',(),{}))
source=ast.parse((ROOT/'home_assistant/custom_components/car_console/__init__.py').read_text())
source.body=[node for node in source.body if isinstance(node,(ast.FunctionDef,ast.ClassDef)) and node.name in ('coordinates','CarTileView')]
exec(compile(source,'tile_proxy','exec'),tile.__dict__)

class Response:
    def __init__(self,data,status=200):self.data=data;self.status=status;self.content=self
    async def __aenter__(self):return self
    async def __aexit__(self,*_):pass
    async def iter_chunked(self,size):
        for offset in range(0,len(self.data),size):yield self.data[offset:offset+size]

class TileProxy(unittest.IsolatedAsyncioTestCase):
    async def test_authenticated_scoped_local_proxy(self):
        session=MagicMock();session.get.return_value=Response(b'\x89PNG\r\n\x1a\nfixture')
        view=tile.CarTileView(session,'http://127.0.0.1:8787')
        self.assertTrue(view.requires_auth)
        result=await view.get(None,'13','123','456')
        self.assertEqual(result.status,200)
        self.assertEqual(session.get.call_args.args[0],'http://127.0.0.1:8787/tiles/13/123/456.png')
        self.assertFalse(session.get.call_args.kwargs['allow_redirects'])
        self.assertEqual((await view.get(None,'13','../123','456')).status,400)
        self.assertEqual((await view.get(None,'99','0','0')).status,400)
    async def test_missing_invalid_and_oversized_tiles(self):
        session=MagicMock();view=tile.CarTileView(session,'http://127.0.0.1:8787')
        for data,status,expected in [(b'',404,404),(b'not a png',200,502),(b'\x89PNG\r\n\x1a\n'+b'x'*2097152,200,502),(b'',302,404)]:
            session.get.return_value=Response(data,status)
            self.assertEqual((await view.get(None,'1','0','0')).status,expected)
