"""Protocol checks are run by the MCP workflow with requirements-mcp installed."""
import importlib.util
import unittest
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec("mcp"), "Optional MCP dependencies not installed")
class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    def test_stdio_handshake_tools_and_invalid_requests(self):
        from stdio_client import StdioClient

        with StdioClient() as client:
            listing = client.request("tools/list", {})
            tools = {tool["name"]: tool for tool in listing["tools"]}
            self.assertEqual(set(tools), {"start_shorts_transcripts", "get_transcript_job", "cancel_transcript_job"})
            schema = tools["start_shorts_transcripts"]["inputSchema"]
            self.assertEqual(schema["properties"]["count"]["maximum"], 10)
            for args in ({"target": "@creator", "count": 11}, {"target": "@creator", "count": True},
                         {"target": "https://example.com/shorts/abcdefghijk"}):
                result = client.call("start_shorts_transcripts", args)
                self.assertTrue(result["isError"])
            unknown = client.call("get_transcript_job", {"job_id": "not-a-job", "wait_seconds": 0})
            self.assertTrue(unknown["isError"])
            self.assertIn("Unknown or expired job ID", unknown["content"][0]["text"])

    async def test_tools_return_transcript_data_through_mcp(self):
        from mcp import Client
        import mcp_server
        from mcp_jobs import TranscriptJobs
        from test_mcp_jobs import FakeService

        with patch.object(mcp_server, "jobs", TranscriptJobs(FakeService)):
            async with Client(mcp_server.mcp) as client:
                started = await client.call_tool("start_shorts_transcripts", {"target": "@creator", "count": 2})
                self.assertFalse(started.is_error)
                job_id = started.structured_content["job_id"]
                result = await client.call_tool("get_transcript_job", {"job_id": job_id, "wait_seconds": 2})
                self.assertFalse(result.is_error)
                self.assertEqual(result.structured_content["state"], "completed")
                row = result.structured_content["results"][0]
                self.assertEqual(row["transcript"], "Hello world")
                self.assertEqual(row["view_count"], 0)
