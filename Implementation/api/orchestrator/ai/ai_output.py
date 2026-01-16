"""
Pydantic schemas for AI provider output validation.

Defines the expected structure of AI-generated template packages.
Used to validate AI output before further processing.
"""

from typing import Any, Optional, List
from pydantic import BaseModel, Field, field_validator, model_validator
import re


class PageOutput(BaseModel):
    """A single page in the template output."""
    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Page filename (e.g., 'index.html')",
    )
    html: str = Field(
        ...,
        min_length=10,
        description="HTML content of the page",
    )
    
    @field_validator("name")
    @classmethod
    def validate_filename(cls, v: str) -> str:
        """Validate filename is safe and has correct extension."""
        # Only allow safe characters
        if not re.match(r"^[a-zA-Z0-9_\-]+\.html$", v):
            raise ValueError(
                f"Invalid page name '{v}'. Must be alphanumeric with .html extension"
            )
        return v


class AssetOutput(BaseModel):
    """An optional asset file (CSS, images, etc.)."""
    path: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Asset path relative to bundle (e.g., 'assets/style.css')",
    )
    content: str = Field(
        ...,
        description="Asset content (text for CSS/JS, base64 for images)",
    )
    content_type: Optional[str] = Field(
        default=None,
        description="MIME type (auto-detected if not provided)",
    )
    
    @field_validator("path")
    @classmethod
    def validate_path(cls, v: str) -> str:
        """Validate asset path is safe."""
        # Prevent directory traversal
        if ".." in v or v.startswith("/"):
            raise ValueError(f"Invalid asset path '{v}'. Path traversal not allowed")
        
        # Only allow safe characters
        if not re.match(r"^[a-zA-Z0-9_\-/\.]+$", v):
            raise ValueError(f"Invalid asset path '{v}'. Contains unsafe characters")
        
        # Validate extension
        allowed_extensions = {
            ".css", ".js", ".json", ".txt", ".svg", ".png", ".jpg", ".jpeg",
            ".gif", ".webp", ".ico", ".woff", ".woff2", ".ttf", ".eot"
        }
        ext = "." + v.split(".")[-1].lower() if "." in v else ""
        if ext not in allowed_extensions:
            raise ValueError(f"Invalid asset extension '{ext}'. Allowed: {allowed_extensions}")
        
        return v


class TemplateMetadataOutput(BaseModel):
    """Metadata about the generated template."""
    name: str = Field(
        default="Generated Template",
        max_length=100,
        description="Template name",
    )
    description: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Template description",
    )
    page_type: Optional[str] = Field(
        default=None,
        description="Page type (landing, product, category, etc.)",
    )


class AITemplateOutput(BaseModel):
    """
    Complete AI template output structure.
    
    This is the expected format from the AI provider after structured output.
    All fields are validated before further processing.
    """
    pages: List[PageOutput] = Field(
        ...,
        min_length=1,
        max_length=20,
        description="List of HTML pages (must include at least one)",
    )
    assets: Optional[List[AssetOutput]] = Field(
        default=None,
        max_length=50,
        description="Optional list of asset files",
    )
    metadata: Optional[TemplateMetadataOutput] = Field(
        default=None,
        description="Optional template metadata",
    )
    
    @model_validator(mode="after")
    def validate_has_index(self) -> "AITemplateOutput":
        """Ensure there's an index.html page."""
        page_names = [p.name for p in self.pages]
        if "index.html" not in page_names:
            raise ValueError("Template must include 'index.html' page")
        return self
    
    def get_index_html(self) -> str:
        """Get the index.html content."""
        for page in self.pages:
            if page.name == "index.html":
                return page.html
        raise ValueError("No index.html found")


# JSON Schema for AI providers that support it
AI_OUTPUT_JSON_SCHEMA = {
    "type": "object",
    "required": ["pages"],
    "properties": {
        "pages": {
            "type": "array",
            "minItems": 1,
            "maxItems": 20,
            "items": {
                "type": "object",
                "required": ["name", "html"],
                "properties": {
                    "name": {
                        "type": "string",
                        "pattern": "^[a-zA-Z0-9_\\-]+\\.html$",
                        "description": "Page filename ending in .html"
                    },
                    "html": {
                        "type": "string",
                        "minLength": 10,
                        "description": "Complete HTML content"
                    }
                }
            }
        },
        "assets": {
            "type": "array",
            "maxItems": 50,
            "items": {
                "type": "object",
                "required": ["path", "content"],
                "properties": {
                    "path": {
                        "type": "string",
                        "pattern": "^[a-zA-Z0-9_\\-/\\.]+$",
                        "description": "Asset path (e.g., assets/style.css)"
                    },
                    "content": {
                        "type": "string",
                        "description": "Asset content"
                    },
                    "content_type": {
                        "type": "string",
                        "description": "Optional MIME type"
                    }
                }
            }
        },
        "metadata": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "maxLength": 100},
                "description": {"type": "string", "maxLength": 500},
                "page_type": {"type": "string"}
            }
        }
    }
}
