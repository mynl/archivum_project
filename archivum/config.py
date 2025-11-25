"""
Configuration model for archivum.
"""
from pathlib import Path
from typing import List, Literal, Optional
from pydantic import BaseModel, Field, ConfigDict
import yaml

from . import APP_NAME

class Configurator(BaseModel):
    model_config = ConfigDict(
        # make model immutable (no attribute reassignment)
        frozen=True,
        extra="forbid"        # raise error on unexpected/extra fields
    )

    name: str = Field(description="Human-readable name of the library")
    description: str = Field("", description="Optional longer description")

    ref_columns: Optional[List[str]] = Field(default_factory=list, description="List of fields to include in reference output")

    bibtex_file: str = Field(..., description="Name of BibTeX output file")
    pdf_dir_name: str = Field(..., description="Dir name where PDFs are stored")

    full_text: bool = Field(True, description="Whether to extract and store full text from PDFs")
    text_dir_name: str = Field("pdf-full-text", description="Dir name for extracted text files")
    extractor: Literal["pdftotext", "pymupdf"] = Field("pdftotext", description="PDF text extraction backend")

    watched_dirs: List[str] = Field(default_factory=list, description="Dir name list to  watch for new files")
    file_formats: List[str] = Field(["*.pdf"], description="Glob patterns for acceptable file types")

    hash_files: bool = Field(True, description="Whether to compute hash values for file identity")
    hash_workers: int = Field(4, ge=1, description="Number of threads to use for hashing")

    last_indexed: int = Field(0, description="Unix timestamp of the last index operation")
    timezone: str = Field("UTC", description="Timezone to use for timestamp parsing and display")

    tablefmt: str = Field("mixed_grid", description="Table format for display (see tabulate)")
    max_table_inch_width: int = Field(12, gt=0, description="Maximum width for table display in inches")

    imports_dir_name: str = Field(
        "imports",
        description="Root directory for BibTeX import runs (relative to BASE_DIR or absolute)",
    )

    def write_template(self, path: Path):
        """Generate a clean default config file at the given path."""
        path = Path(path)
        yaml_str = yaml.dump(self.model_dump(), sort_keys=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml_str, encoding="utf-8")

    def save(self, config_path: Path, backup: bool = True) -> None:
            """Save config to Path and optionally back up."""
            # Ensure parent exists
            config_path.parent.mkdir(parents=True, exist_ok=True)

            # 1. Handle Backup (Only if source exists)
            if backup and config_path.exists():
                bak_path = config_path.with_suffix(f'.{APP_NAME}-config-bak')
                # Windows allows overwriting hardlinks only if we remove the target first
                if bak_path.exists():
                    bak_path.unlink()
                # Create hardlink (atomic-ish on Windows NTFS)
                bak_path.hardlink_to(config_path)

            # 2. Write File
            # Do not unlink() first; "w" truncates.
            # For true atomicity on Windows, write to temp and replace,
            # but direct write is usually sufficient for configs.
            with config_path.open("w", encoding="utf-8") as f:
                yaml.safe_dump(
                    self.model_dump(),
                    f,
                    sort_keys=False,
                    default_flow_style=False,
                    width=100,
                    indent=2
                )
