"""
Scraper tool schema definition.
"""
from typing import Dict, Any
from ..base_tool import BaseTool
from ..tool_names import ToolName


class ScraperTool(BaseTool):
    """Tool for scraping structured content from academic papers and documents"""
    
    def __init__(self, tool_state=None, grobid_url=None):
        super().__init__()
        self.tool_state = tool_state
        self.grobid_url = grobid_url
        
    @property
    def name(self) -> str:
        return ToolName.SCRAPER
    
    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of sources to scrape (URLs, file paths, or arXiv IDs). When you think a url contains relevant information (based on the snippet of `WebSearch` tool), be sure to add it to the list."
                }
            },
            "required": ["sources"],
            "additionalProperties": False
        }
