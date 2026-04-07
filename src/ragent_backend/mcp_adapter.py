from __future__ import annotations

from typing import Any, Dict, List, Optional

from mcp import types

from src.mcp_server.protocol_handler import create_mcp_server, get_protocol_handler


class RAGMCPClient:
    """Adapter that invokes the local RAG MCP tools through protocol handler."""

    def __init__(self) -> None:
        server = create_mcp_server(
            server_name="ragent-backend-mcp-bridge",
            server_version="0.1.0",
            register_tools=True,
        )
        self._handler = get_protocol_handler(server)

    async def query_knowledge_hub(
        self,
        query: str,
        top_k: int = 5,
        collection: Optional[str] = None,
    ) -> str:
        args: Dict[str, Any] = {"query": query, "top_k": top_k}
        if collection:
            args["collection"] = collection
        result = await self._handler.execute_tool("query_knowledge_hub", args)
        return self._text_from_result(result)

    async def list_collections(self, include_stats: bool = True) -> str:
        result = await self._handler.execute_tool(
            "list_collections", {"include_stats": include_stats}
        )
        return self._text_from_result(result)

    async def get_document_summary(self, doc_id: str, collection: Optional[str] = None) -> str:
        args: Dict[str, Any] = {"doc_id": doc_id}
        if collection:
            args["collection"] = collection
        result = await self._handler.execute_tool("get_document_summary", args)
        return self._text_from_result(result)

    def _text_from_result(self, result: types.CallToolResult) -> str:
        if result.isError:
            return "MCP tool call failed"

        texts: List[str] = []
        for item in result.content:
            text = getattr(item, "text", None)
            if text:
                texts.append(text)

        return "\n".join(texts).strip()
