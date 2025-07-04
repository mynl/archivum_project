from pathlib import Path
from typing import List, Literal
from pydantic import BaseModel, Field


class ArchivumConfig(BaseModel):
    name: str = Field(..., description="Human-readable name of the library")
    description: str = Field("", description="Optional longer description")

    ref_columns: List[str] = Field(..., description="List of fields to include in reference output")

    bibtex_file: Path = Field(..., description="Path to BibTeX output file")
    pdf_dir_name: Path = Field(..., description="Path where PDFs are stored")

    full_text: bool = Field(True, description="Whether to extract and store full text from PDFs")
    text_dir_name: str = Field("pdf-full-text", description="Subdirectory for extracted text files")
    extractor: Literal["pdftotext", "pymupdf"] = Field("pdftotext", description="PDF text extraction backend")

    btree: bool = Field(False, description="Whether to build a btree index for full-text search")
    btree_depth: int = Field(8, ge=1, le=32, description="Depth of the btree index if enabled")

    watched_dirs: List[Path] = Field([], description="Directories to watch for new files")
    file_formats: List[str] = Field(["*.pdf"], description="Glob patterns for acceptable file types")

    hash_files: bool = Field(True, description="Whether to compute hash values for file identity")
    hash_workers: int = Field(4, ge=1, description="Number of threads to use for hashing")

    last_indexed: int = Field(0, description="Unix timestamp of the last index operation")
    timezone: str = Field("UTC", description="Timezone to use for timestamp parsing and display")

    tablefmt: str = Field("mixed_grid", description="Table format for display (see tabulate)")
    max_table_width: int = Field(80, gt=0, description="Maximum width for table display in characters")
