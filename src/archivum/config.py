"""
Configuration model for archivum.
"""
import logging
from pathlib import Path
from typing import List, Literal, Optional, Dict
from pydantic import BaseModel, Field, ConfigDict, ValidationError
import yaml

logger = logging.getLogger(__name__)


def _get_config_diff(current: dict, base: dict) -> dict:
    """
    Returns a dict containing only keys from 'current' that are
    different from or missing in 'base'. Handles nested dicts recursively.
    """
    diff = {}
    for key, value in current.items():
        # Case 1: Key does not exist in base -> New setting, must save.
        if key not in base:
            diff[key] = value
            continue

        base_value = base[key]

        # Case 2: Both are dicts -> Recurse to find partial overrides.
        if isinstance(value, dict) and isinstance(base_value, dict):
            nested_diff = _get_config_diff(value, base_value)
            if nested_diff:
                diff[key] = nested_diff

        # Case 3: Values differ -> Override, must save.
        elif value != base_value:
            diff[key] = value

        # Case 4: Values are identical -> Do nothing (inherit from site).

    return diff


class Configurator(BaseModel):
    model_config = ConfigDict(
        # make model immutable (no attribute reassignment)
        frozen=True,
        extra="forbid"        # raise error on unexpected/extra fields
    )

    name: str = Field(description="Human-readable name of the library.")
    description: str = Field("", description="Optional longer description.")
    ref_columns: Optional[List[str]] = Field(default_factory=list, description="List of fields to include in reference output.")
    bibtex_file: str = Field(..., description="Name of BibTeX output file, created as a link, in addition to saving in library folder.")
    doc_dir_name: str = Field(..., description="Dir name where actual document files (PDF, etc) are expected to be stored. "
        "Used only to truncate pdf file names, which are made relative to doc_dir_name.")
    full_text: bool = Field(True, description="Whether to extract and store full text from PDFs.")
    text_dir_name: str = Field("pdf-full-text", description="Dir name for extracted text files. Used by the built in ripgrep function and to create text extracts.")
    extractor: Literal["pdftotext", "pymupdf"] = Field("pdftotext", description="PDF text extraction backend.")
    file_formats: List[str] = Field(["*.pdf"], description="Glob patterns for acceptable file types. NOT PLUGGED IN!")
    hash_workers: int = Field(4, ge=1, description="Number of threads to use for hashing.")
    last_indexed: int = Field(0, description="Unix timestamp of the last full-text index operation")
    timezone: str = Field("Europe/London", description="Timezone to use for timestamp parsing and display")
    tablefmt: str = Field("mixed_grid", description="Table format for display (see tabulate).")
    max_table_inch_width: int = Field(12, gt=0, description="Maximum width for table display in inches.")
    tag_name_mapper: dict[str, str] = Field(default_factory=dict,
        description="Optional mapping of longer names to abbreviations, eg to map Casualty Actuarial Society to CAS. (Used!)")

    enhancement_strategies: Dict[str, str] = Field(
        default_factory=lambda: {
            'year': 'mode',
            'journal': 'longest',
            'default': 'longest'
        }
    )
    enhancement_cutoff_score: float = 75.0
    enhancement_tag_regex: str = r'[a-z]*$'

    def write_template(self, path: Path):
        """Generate a clean default config file at the given path."""
        path = Path(path)
        yaml_str = yaml.dump(self.model_dump(), sort_keys=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml_str, encoding="utf-8")

    def save(self, config_path: Path, backup: bool = True) -> None:
        """
        Saves the configuration to config.yaml, writing ONLY values that differ
        from the site defaults (site-config.yaml).

        Args:
            config_path (Path): The folder containing the library configuration.
            backup (bool): If True, creates a .bak copy of the existing config.
        """
        file_path = config_path / "config.yaml"
        site_path = config_path.parent / "site-config.yaml"

        # 1. Load Site Defaults (if available) for comparison
        site_defaults = {}
        if site_path.exists():
            try:
                with site_path.open('r', encoding='utf-8') as f:
                    site_defaults = yaml.safe_load(f) or {}
            except Exception as e:
                logger.warning(f"Could not load site config for comparison: {e}")

        # 2. Calculate Diff (Current vs Site)
        # We only want to save specific overrides, not the whole blob.
        current_data = self.model_dump()
        data_to_save = _get_config_diff(current_data, site_defaults)

        # 3. Handle Backup
        if backup and file_path.exists():
            bak_path = config_path / 'config.bak'
            if bak_path.exists():
                bak_path.unlink()
            bak_path.hardlink_to(file_path)

        # 4. Write Minimal Config
        with file_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(
                data_to_save,
                f,
                sort_keys=False,
                default_flow_style=False,
                width=100,
                indent=2
            )


def _deep_merge(base: dict, overrides: dict) -> dict:
    """
    Recursively merges 'overrides' into 'base'.
    """
    for key, value in overrides.items():
        if isinstance(value, dict) and key in base and isinstance(base[key], dict):
            base[key] = _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def load_configuration(lib_path: Path, **overrides) -> dict:
    """
    Load library config combining site, library and kw overrides.

    Site path lives in lib_path.parent / "site-config.yaml"
    """
    config = {}

    site_path = lib_path.parent / "site-config.yaml"

    # Level 1: Site Config
    if site_path.exists():
        with open(site_path, 'r') as f:
            config = yaml.safe_load(f)

    # Level 2: Library Overrides
    lib_path = lib_path / "config.yaml"
    if lib_path.exists():
        with open(lib_path, 'r') as f:
            lib_config = yaml.safe_load(f) or {}
            config = _deep_merge(config, lib_config)

    # Level 3: kw arguments
    config = _deep_merge(config, overrides)

    try:
        return Configurator(**config)
    except (ValidationError, OSError) as e:
        print(e)
        raise ValueError(f"Failed to load config {lib_path}") from e
