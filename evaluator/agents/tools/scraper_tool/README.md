# ScraperTool

A comprehensive tool for extracting structured content from academic papers and documents.

## Features

- **PDF Processing**: Extracts content from PDF files using GROBID service
- **arXiv Integration**: Processes arXiv papers through ar5iv.org conversion
- **Smart Detection**: Automatically detects source type (arXiv ID, URL, file path)
- **Structured Output**: Returns organized content with sections, references, tables, and images
- **LaTeX Citations**: Automatically converts references to LaTeX `~\cite{}` format
- **Special Content**: Extracts and appends tables/images to relevant sections

## Usage

### Basic Usage

```python
from scraper_tool import ScraperTool

# Initialize tool
scraper = ScraperTool()

# Process different types of sources
result = await scraper.execute({
    "source": "2301.00001"  # arXiv ID
})

result = await scraper.execute({
    "source": "https://arxiv.org/abs/2301.00001"  # arXiv URL
})

result = await scraper.execute({
    "source": "/path/to/paper.pdf"  # PDF file
})
```

### Manual Scraper Type Selection

```python
# Force specific scraper type
result = await scraper.execute({
    "source": "https://example.com/article",
    "scraper_type": "html"  # Use HTML scraper for web page
})

result = await scraper.execute({
    "source": "document.xml",
    "scraper_type": "pdf"  # Use PDF scraper for XML file
})
```

## Output Format

The tool returns a structured result with:

```json
{
  "success": true,
  "result": "Summary text...",
  "display_result": {
    "success": true,
    "scraper_type": "arxiv|pdf",
    "source": "input_source",
    "all_content": {
      "title": "Paper Title",
      "abstract": "Abstract content...",
      "Introduction": "Introduction content...",
      "Methodology": "Method content...",
      "References": "Formatted references..."
    },
    "structure": "Hierarchical table of contents",
    "meta": {
      "tables": {"id": "table_content"},
      "images": {"id": "image_description"}
    },
    "stats": {
      "num_sections": 10,
      "total_chars": 50000,
      "num_tables": 3,
      "num_images": 5
    }
  }
}
```

## Supported Sources

### arXiv Papers
- **arXiv IDs**: `2301.00001`, `cs-AI/0101001`
- **arXiv URLs**: `https://arxiv.org/abs/2301.00001`
- Uses ar5iv.org for HTML conversion and processing

### PDF Documents  
- **Local files**: `/path/to/paper.pdf`
- **PDF URLs**: `https://example.com/paper.pdf`
- **XML files**: Pre-processed GROBID XML files
- Uses `GROBID_URL` from the environment when set, falling back to `http://localhost:8070/api/processFulltextDocument`
- Supports optional Basic Auth via `GROBID_USERNAME` and `GROBID_PASSWORD`

### Web Pages
- **HTML files**: `/path/to/document.html`
- **Web URLs**: `https://example.com/article`
- **arXiv pages**: `https://arxiv.org/abs/2301.00001` (alternative to ar5iv)
- Uses BeautifulSoup for HTML parsing and content extraction

## Dependencies

- `requests`: HTTP requests
- `beautifulsoup4`: XML/HTML parsing  
- `urllib.parse`: URL parsing
- `re`: Regular expressions

## GROBID Service

For PDF processing, ensure GROBID service is running:

```bash
# Using Docker
docker run -t --rm -p 8070:8070 lfoppiano/grobid:0.7.3

# Or download and run locally
# See: https://grobid.readthedocs.io/
```

## Testing

```bash
# Test basic functionality
python3 test_tool.py

# Test XML parsing (no GROBID required)  
python3 test_xml_parsing.py

# Test integration (requires GROBID)
python3 test_integration.py
```

## Architecture

```
scraper_tool/
├── tool.py                    # Main ScraperTool class
├── description.md             # Claude tool description
├── scrapers/
│   ├── ar5iv.py              # arXiv paper scraper
│   ├── general_pdf.py        # PDF/XML scraper
│   ├── beautiful_soup.py     # HTML/Web scraper
│   └── utils/                # Utility functions
│       ├── arxiv_utils.py
│       ├── general_pdf_utils.py
│       └── beautiful_soup_utils.py
└── test/                     # Test files and examples
```

## Reference Processing

The tool automatically processes citations:
- **bibr references**: Converted to LaTeX `~\cite{id1,id2,id3}` format
- **Figure/table references**: Keep original text + `[id]` format
- **Special content**: Tables and images appended to sections that reference them