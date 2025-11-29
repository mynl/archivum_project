"""
Configuration model for archivum.
"""
import datetime as dt
import logging
from pathlib import Path
from typing import List, Literal, Optional, Callable, Any
from pydantic import BaseModel, Field, ConfigDict
import yaml

from . import LIBRARIES_DIR

logger = logging.getLogger(__name__)

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

    tag_name_mapper: dict[str, str] = Field(default_factory=dict,
        description="Optional mapping of longer names to abbreviations.")

    def write_template(self, path: Path):
        """Generate a clean default config file at the given path."""
        path = Path(path)
        yaml_str = yaml.dump(self.model_dump(), sort_keys=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml_str, encoding="utf-8")

    def save(self, config_path: Path, backup: bool = True) -> None:
            """Save config into Path as config.yaml and optionally back up."""
            file_path = config_path / "config.yaml"
            # 1. Handle Backup (Only if source exists)
            if backup and config_path.exists():
                bak_path = config_path / 'config.bak'
                # Windows allows overwriting hardlinks only if we remove the target first
                if bak_path.exists():
                    bak_path.unlink()
                # Create hardlink (atomic-ish on Windows NTFS)
                bak_path.hardlink_to(file_path)

            # 2. Write File
            # Do not unlink() first; "w" truncates.
            # For true atomicity on Windows, write to temp and replace,
            # but direct write is usually sufficient for configs.
            with file_path.open("w", encoding="utf-8") as f:
                yaml.safe_dump(
                    self.model_dump(),
                    f,
                    sort_keys=False,
                    default_flow_style=False,
                    width=100,
                    indent=2
                )


def create_config_interactive(
    target_path: Path,
    input_func: Callable[[str], str] = input
) -> None:
    """
    Interactively create a Configurator instance and save it.

    Saved to LIBRARIES_DIR unless target_path has a root (i.e., starts /).

    Args:
        target_path: Destination for the config file.
        input_func: Function to capture input (allows injection of mock
                    for testing or rich.prompt).
    """

    def rooted(path):
        """If path not rooted, make relative to LIBRARIES_DIR."""
        path = LIBRARIES_DIR / path
        path.mkdir(parents=True, exist_ok=True)
        return path.absolute()

    def prompt(label: str, default: Any) -> str:
        """Helper to handle defaults in the prompt."""
        response = input_func(f"{label} [{default}]: ")
        return response.strip() or str(default)

    target_path = rooted(target_path)
    logger.info(f"Generating configuration at: {target_path}")

    # Only prompt for critical paths that likely vary per user
    # Rely on Pydantic defaults for the rest
    name = prompt("Library Name", target_path.stem.replace("-", " "))

    timestamp = dt.datetime.now().strftime("%Y-%m-%d_at_%H-%M-%S")
    description = prompt("Description", f"New library created {timestamp}")

    # is what now??
    pdf_dir = prompt("PDF Directory", "pdfs")

    bib_output = prompt("BibTeX output file", f"{target_path.name}.bib")

    # Create instance
    config = Configurator(
        name=name,
        ref_columns=["tag", "type", "title", "year", "author", "journal", "volume",
                    "number", "month", "pages", "booktitle", "editor", "edition",
                    "chapter", "doi", "isbn", "publisher", "institution", "address",
                    "url", "mendeley-tags", "arc-citations", "arc-source"],
        description=description,
        pdf_dir_name=pdf_dir,
        bibtex_file=bib_output,
        # Add other fields here if you want to prompt for them
    )

    # Save
    try:
        config.save(target_path, backup=False)
        print(f"Success. Config saved to {target_path}")
    except Exception as e:
        print(f"Error saving config: {e}")

    return config

