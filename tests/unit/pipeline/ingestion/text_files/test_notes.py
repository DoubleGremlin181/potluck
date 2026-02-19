"""Tests for text files / Obsidian vault ingester."""

from pathlib import Path

from potluck.models.base import EntityType, SourceType
from potluck.models.documents import Document
from potluck.pipeline.ingestion.text_files import TextFilesStage
from potluck.pipeline.ingestion.text_files.documents import (
    _strip_front_matter,
    _strip_html_tags,
    is_obsidian_vault,
)


class TestTextFilesDetection:
    """Tests for TextFilesStage.detect()."""

    def test_detect_txt_files(self, tmp_path: Path) -> None:
        """Detection counts .txt files."""
        (tmp_path / "note1.txt").write_text("Hello")
        (tmp_path / "note2.txt").write_text("World")
        (tmp_path / "data.csv").write_text("not a note")

        stage = TextFilesStage()
        result = stage.detect(tmp_path)

        assert EntityType.DOCUMENT in result.entity_counts
        assert result.entity_counts[EntityType.DOCUMENT] == 2

    def test_detect_md_files(self, tmp_path: Path) -> None:
        """Detection counts .md files."""
        (tmp_path / "readme.md").write_text("# Title")
        (tmp_path / "notes.md").write_text("Some notes")

        stage = TextFilesStage()
        result = stage.detect(tmp_path)

        assert result.entity_counts[EntityType.DOCUMENT] == 2

    def test_detect_obsidian_vault(self, tmp_path: Path) -> None:
        """Obsidian vaults are detected and labeled in metadata."""
        (tmp_path / ".obsidian").mkdir()
        (tmp_path / "note.md").write_text("# My Note")

        stage = TextFilesStage()
        result = stage.detect(tmp_path)

        assert result.metadata.get("source") == "Obsidian Vault"

    def test_detect_empty_directory(self, tmp_path: Path) -> None:
        """Empty directories return no counts."""
        stage = TextFilesStage()
        result = stage.detect(tmp_path)
        assert result.entity_counts == {}


class TestTextFileIngestion:
    """Tests for text file ingestion."""

    def test_ingest_simple_files(self, tmp_path: Path) -> None:
        """Simple text files are ingested as Documents."""
        (tmp_path / "note1.txt").write_text("First note content")
        (tmp_path / "note2.md").write_text("# Second Note\n\nMarkdown content")

        stage = TextFilesStage()
        entities = list(stage.execute(tmp_path))

        docs = [e for e in entities if isinstance(e, Document)]
        assert len(docs) == 2
        assert all(d.source_type == SourceType.GENERIC for d in docs)
        assert all(d.file_extension is not None for d in docs)

    def test_document_has_title_and_extension(self, tmp_path: Path) -> None:
        """Documents have title from filename stem and file_extension."""
        (tmp_path / "my-note.md").write_text("Some content")

        stage = TextFilesStage()
        entities = list(stage.execute(tmp_path))

        docs = [e for e in entities if isinstance(e, Document)]
        assert len(docs) == 1
        assert docs[0].title == "my-note"
        assert docs[0].file_extension == ".md"

    def test_relative_source_id(self, tmp_path: Path) -> None:
        """Source IDs use relative paths."""
        sub_dir = tmp_path / "subfolder"
        sub_dir.mkdir()
        (sub_dir / "deep.md").write_text("Deep note")

        stage = TextFilesStage()
        entities = list(stage.execute(tmp_path))

        docs = [e for e in entities if isinstance(e, Document)]
        assert len(docs) == 1
        assert docs[0].source_id == "subfolder/deep.md"

    def test_skip_empty_files(self, tmp_path: Path) -> None:
        """Empty files are skipped."""
        (tmp_path / "empty.txt").write_text("")
        (tmp_path / "has_content.txt").write_text("Content here")

        stage = TextFilesStage()
        entities = list(stage.execute(tmp_path))

        assert len(entities) == 1

    def test_skip_large_files(self, tmp_path: Path) -> None:
        """Files over 1MB are skipped."""
        large_file = tmp_path / "large.txt"
        large_file.write_text("x" * 1_100_000)
        (tmp_path / "small.txt").write_text("Small note")

        stage = TextFilesStage()
        entities = list(stage.execute(tmp_path))

        assert len(entities) == 1

    def test_skip_hidden_directories(self, tmp_path: Path) -> None:
        """Files in hidden directories are skipped."""
        hidden = tmp_path / ".hidden"
        hidden.mkdir()
        (hidden / "secret.md").write_text("Secret content")
        (tmp_path / "visible.md").write_text("Visible content")

        stage = TextFilesStage()
        entities = list(stage.execute(tmp_path))

        docs = [e for e in entities if isinstance(e, Document)]
        assert len(docs) == 1
        assert docs[0].source_id == "visible.md"

    def test_content_hash_deterministic(self, tmp_path: Path) -> None:
        """Content hashes are deterministic."""
        (tmp_path / "note.md").write_text("Same content")

        stage = TextFilesStage()
        entities1 = list(stage.execute(tmp_path))
        entities2 = list(stage.execute(tmp_path))

        doc1 = next(e for e in entities1 if isinstance(e, Document))
        doc2 = next(e for e in entities2 if isinstance(e, Document))
        assert doc1.content_hash == doc2.content_hash


class TestObsidianVault:
    """Tests for Obsidian-specific functionality."""

    def test_skip_obsidian_config(self, tmp_path: Path) -> None:
        """The .obsidian/ config directory is skipped."""
        obsidian_dir = tmp_path / ".obsidian"
        obsidian_dir.mkdir()
        (obsidian_dir / "workspace.json").write_text("{}")
        (obsidian_dir / "app.json").write_text("{}")
        (tmp_path / "my-note.md").write_text("A real note")

        stage = TextFilesStage()
        entities = list(stage.execute(tmp_path))

        docs = [e for e in entities if isinstance(e, Document)]
        assert len(docs) == 1
        assert docs[0].source_id == "my-note.md"

    def test_skip_trash_directory(self, tmp_path: Path) -> None:
        """The .trash/ directory is skipped."""
        trash = tmp_path / ".trash"
        trash.mkdir()
        (trash / "deleted.md").write_text("Deleted note")
        (tmp_path / "active.md").write_text("Active note")

        stage = TextFilesStage()
        entities = list(stage.execute(tmp_path))

        assert len(entities) == 1

    def test_is_obsidian_vault(self, tmp_path: Path) -> None:
        """Obsidian vaults are detected by .obsidian/ directory."""
        assert not is_obsidian_vault(tmp_path)

        (tmp_path / ".obsidian").mkdir()
        assert is_obsidian_vault(tmp_path)

    def test_yaml_front_matter_stripped(self, tmp_path: Path) -> None:
        """YAML front matter is stripped from note content."""
        (tmp_path / "note.md").write_text(
            "---\ntitle: My Note\ntags: [python, coding]\n---\n\nActual content here."
        )

        stage = TextFilesStage()
        entities = list(stage.execute(tmp_path))

        docs = [e for e in entities if isinstance(e, Document)]
        assert len(docs) == 1
        assert docs[0].content.strip() == "Actual content here."
        assert "---" not in docs[0].content


class TestFrontMatterParsing:
    """Tests for YAML front matter stripping."""

    def test_strip_front_matter(self) -> None:
        """Front matter is stripped, body is returned."""
        content = "---\ntitle: Test\n---\n\nBody content"
        assert _strip_front_matter(content).strip() == "Body content"

    def test_strip_no_front_matter(self) -> None:
        """Text without front matter is returned unchanged."""
        assert _strip_front_matter("Just text") == "Just text"


class TestHTMLStripping:
    """Tests for HTML tag stripping."""

    def test_strip_simple_html(self) -> None:
        """Simple HTML tags are stripped."""
        html = "<p>Hello <b>world</b></p>"
        assert _strip_html_tags(html) == "Hello world"

    def test_strip_nested_html(self) -> None:
        """Nested HTML tags are stripped."""
        html = "<div><h1>Title</h1><p>Some <em>emphasized</em> text</p></div>"
        assert _strip_html_tags(html) == "TitleSome emphasized text"

    def test_strip_html_with_no_tags(self) -> None:
        """Plain text without HTML tags is returned unchanged."""
        assert _strip_html_tags("Just plain text") == "Just plain text"

    def test_ingest_html_file(self, tmp_path: Path) -> None:
        """HTML files are ingested with tags stripped."""
        (tmp_path / "page.html").write_text(
            "<html><body><h1>Title</h1><p>Content here</p></body></html>"
        )

        stage = TextFilesStage()
        entities = list(stage.execute(tmp_path))

        docs = [e for e in entities if isinstance(e, Document)]
        assert len(docs) == 1
        assert docs[0].file_extension == ".html"
        assert "<" not in docs[0].content
        assert "Content here" in docs[0].content

    def test_detect_html_files(self, tmp_path: Path) -> None:
        """Detection counts .html files."""
        (tmp_path / "page.html").write_text("<p>Hello</p>")

        stage = TextFilesStage()
        result = stage.detect(tmp_path)

        assert EntityType.DOCUMENT in result.entity_counts
        assert result.entity_counts[EntityType.DOCUMENT] == 1


class TestEncodingHandling:
    """Tests for file encoding handling."""

    def test_utf8_file(self, tmp_path: Path) -> None:
        """UTF-8 files are read correctly."""
        (tmp_path / "utf8.txt").write_text("Hello 🌍 Ünïcödé", encoding="utf-8")

        stage = TextFilesStage()
        entities = list(stage.execute(tmp_path))
        assert len(entities) == 1

    def test_latin1_fallback(self, tmp_path: Path) -> None:
        """Latin-1 files are read with encoding fallback."""
        (tmp_path / "latin.txt").write_bytes("Caf\xe9 latt\xe9".encode("latin-1"))

        stage = TextFilesStage()
        entities = list(stage.execute(tmp_path))
        assert len(entities) == 1
