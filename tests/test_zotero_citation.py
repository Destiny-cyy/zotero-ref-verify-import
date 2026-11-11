import importlib.util
import io
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

from lxml import etree


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "zotero_citation.py"
SPEC = importlib.util.spec_from_file_location("zotero_citation", MODULE_PATH)
zotero_citation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(zotero_citation)


class ParagraphRewriteTests(unittest.TestCase):
    def test_field_is_inserted_before_the_nearest_sentence_end(self):
        paragraph = etree.Element(zotero_citation.Q("p"))
        run = etree.SubElement(paragraph, zotero_citation.Q("r"))
        text = etree.SubElement(run, zotero_citation.Q("t"))
        text.text = "论述。[REF:X]后文"

        located = zotero_citation.locate_placeholder(paragraph, "X", "。")
        zotero_citation.rebuild_paragraph(
            paragraph,
            *located[1:],
            zotero_citation.make_field("TEST", "[1]"),
        )

        self.assertEqual(zotero_citation.para_text(paragraph), "论述[1]。后文")

    def test_placeholder_can_span_runs(self):
        paragraph = etree.Element(zotero_citation.Q("p"))
        for value in ("论述。[REF:", "X]后文"):
            run = etree.SubElement(paragraph, zotero_citation.Q("r"))
            text = etree.SubElement(run, zotero_citation.Q("t"))
            text.text = value

        located = zotero_citation.locate_placeholder(paragraph, "X", "。")
        zotero_citation.rebuild_paragraph(
            paragraph,
            *located[1:],
            zotero_citation.make_field("TEST", "[1]"),
        )

        self.assertEqual(zotero_citation.para_text(paragraph), "论述[1]。后文")

    def test_multiple_placeholders_preserve_existing_fields(self):
        paragraph = etree.Element(zotero_citation.Q("p"))
        run = etree.SubElement(paragraph, zotero_citation.Q("r"))
        text = etree.SubElement(run, zotero_citation.Q("t"))
        text.text = "甲。[REF:A]乙。[REF:B]"

        for key, number in (("A", 1), ("B", 2)):
            located = zotero_citation.locate_placeholder(paragraph, key, "。")
            zotero_citation.rebuild_paragraph(
                paragraph,
                *located[1:],
                zotero_citation.make_field("TEST", f"[{number}]"),
            )

        self.assertEqual(zotero_citation.para_text(paragraph), "甲[1]。乙[2]。")
        instructions = zotero_citation.instr_texts(paragraph)
        self.assertEqual(len(instructions), 2)


class BibliographyTests(unittest.TestCase):
    def test_single_entry_uses_one_bibliography_paragraph(self):
        root = etree.Element(zotero_citation.Q("document"))
        body = etree.SubElement(root, zotero_citation.Q("body"))
        etree.SubElement(body, zotero_citation.Q("sectPr"))

        count = zotero_citation.append_bibliography_field(root, ["[1] only"])
        paragraphs = body.findall(zotero_citation.Q("p"))

        self.assertEqual(count, 1)
        self.assertEqual(len(paragraphs), 1)
        self.assertEqual(zotero_citation.para_text(paragraphs[0]), "[1] only")

    def test_multiple_entries_use_one_paragraph_per_entry(self):
        root = etree.Element(zotero_citation.Q("document"))
        body = etree.SubElement(root, zotero_citation.Q("body"))
        etree.SubElement(body, zotero_citation.Q("sectPr"))

        count = zotero_citation.append_bibliography_field(root, ["[1] one", "[2] two"])

        self.assertEqual(count, 2)
        self.assertEqual(zotero_citation.bibliography_entries(root), ["[1] one", "[2] two"])


class OutputSafetyTests(unittest.TestCase):
    def test_source_cannot_be_used_as_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.docx"
            cfg = {
                "_paths": {"source_docx": source, "deliver_docx": source},
                "verify_copy_docx": "",
            }

            with self.assertRaisesRegex(ValueError, "原稿"):
                zotero_citation.validated_output_paths(cfg)


class BuildIntegrationTests(unittest.TestCase):
    def test_build_and_verify_minimal_docx(self):
        with tempfile.TemporaryDirectory() as directory:
            root_dir = Path(directory)
            source = root_dir / "source.docx"
            deliver = root_dir / "deliver.docx"

            document = etree.Element(zotero_citation.Q("document"), nsmap={"w": zotero_citation.W})
            body = etree.SubElement(document, zotero_citation.Q("body"))
            paragraph = etree.SubElement(body, zotero_citation.Q("p"))
            run = etree.SubElement(paragraph, zotero_citation.Q("r"))
            text = etree.SubElement(run, zotero_citation.Q("t"))
            text.text = "论述。[REF:A]"
            etree.SubElement(body, zotero_citation.Q("sectPr"))
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("word/document.xml", etree.tostring(document, encoding="UTF-8"))
                archive.writestr("marker.txt", b"unchanged")

            cfg = {
                "source_docx": str(source),
                "deliver_docx": str(deliver),
                "verify_copy_docx": "",
                "api_base": "http://127.0.0.1:23119",
                "user_id": 0,
                "style": "example-style",
                "cid_prefix": "TEST",
                "sentence_end": "。",
                "citation_plan": [
                    {"para": 1, "seq": 1, "num": 1, "key": "A", "itemkey": "ITEMKEY", "cid": "C01"}
                ],
                "key_to_itemkey": {"A": "ITEMKEY"},
                "_paths": {"source_docx": source, "deliver_docx": deliver},
            }

            original_fetch_csl = zotero_citation.fetch_csl
            original_fetch_render = zotero_citation.fetch_render
            zotero_citation.fetch_csl = lambda _cfg, _key: {"type": "article-journal", "title": "Example"}
            zotero_citation.fetch_render = lambda _cfg, _key: ("[1]", "<div>[1] Reference</div>")
            try:
                with redirect_stdout(io.StringIO()):
                    zotero_citation.cmd_build(cfg, apply=True)
                    result = zotero_citation.cmd_verify(cfg, deliver)
            finally:
                zotero_citation.fetch_csl = original_fetch_csl
                zotero_citation.fetch_render = original_fetch_render

            self.assertEqual(result, 0)
            with zipfile.ZipFile(deliver) as archive:
                generated = etree.fromstring(archive.read("word/document.xml"))
            self.assertEqual(zotero_citation.para_text(generated.find(".//w:p", zotero_citation.NS)), "论述[1]。")


if __name__ == "__main__":
    unittest.main()
