import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from open_webui.models.access_grants import AccessGrantModel
from open_webui.utils import collection_synthesis


class CollectionSynthesisTestCase(unittest.TestCase):
    def test_build_incremental_document_chunks_prefers_page_windows(self):
        fake_file = SimpleNamespace(
            id="file-1",
            filename="atlas.pdf",
            data={"content": ""},
            meta={},
        )

        with patch.object(
            collection_synthesis,
            "_get_document_chunks",
            return_value=[
                ("Texto da pagina 1", {"page": 0}),
                ("Texto da pagina 2", {"page": 1}),
                ("Texto da pagina 3", {"page": 2}),
            ],
        ):
            chunks = collection_synthesis.build_incremental_document_chunks(
                fake_file,
                {
                    "mode": "pages",
                    "size": 2,
                    "fallback_mode": "chars",
                    "fallback_size": 1000,
                },
            )

        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0]["sourceRange"], "p.1-2")
        self.assertIn("pagina 1", chunks[0]["text"].lower())
        self.assertEqual(chunks[1]["sourceRange"], "p.3")

    def test_build_incremental_document_chunks_falls_back_to_char_ranges(self):
        fake_file = SimpleNamespace(
            id="file-2",
            filename="relatorio.txt",
            data={"content": "A" * 6500 + " " + "B" * 6500},
            meta={},
        )

        with patch.object(collection_synthesis, "_get_document_chunks", return_value=[]):
            chunks = collection_synthesis.build_incremental_document_chunks(
                fake_file,
                {
                    "mode": "pages",
                    "size": 5,
                    "fallback_mode": "chars",
                    "fallback_size": 6000,
                },
            )

        self.assertGreaterEqual(len(chunks), 2)
        self.assertTrue(chunks[0]["sourceRange"].startswith("chars "))
        self.assertTrue(chunks[0]["text"])

    def test_render_synthesis_markdown_includes_main_sections(self):
        collection = SimpleNamespace(id="kb-1", name="Acervo Atlas")
        markdown = collection_synthesis.render_synthesis_markdown(
            collection,
            {
                "overview": "Visao panoramica da colecao.",
                "recurringThemes": ["Cartografia", "Instituicoes"],
                "mainFindings": ["Os documentos destacam mapas e infraestrutura."],
                "convergences": ["Ha alinhamento sobre o papel institucional."],
                "divergences": ["Os enfoques variam entre tecnica e gestao."],
                "gaps": ["Faltam datas em parte do corpus."],
                "conclusion": "A colecao e coerente, mas com cobertura desigual.",
                "futureQuestions": ["Quais pastas merecem uma sintese dedicada?"],
            },
            generated_at=1_712_345_678,
            model_name="gemma-3-12b",
            documents_processed=4,
            documents_failed=1,
            warnings=["Um documento ficou sem texto processavel."],
        )

        self.assertIn("# Síntese da coleção: Acervo Atlas", markdown)
        self.assertIn("## Visão geral", markdown)
        self.assertIn("## Temas recorrentes", markdown)
        self.assertIn("## Conclusão", markdown)
        self.assertIn("## Avisos", markdown)

    def test_persist_synthesis_note_keeps_note_when_vector_indexing_times_out(self):
        collection = SimpleNamespace(
            id="kb-1",
            name="Acervo Atlas",
            description="",
            meta={"linked_note_ids": ["note-1"]},
            access_grants=[],
        )
        report_record = SimpleNamespace(
            id="report-1",
            note_id=None,
            model_name="gemma-3-12b",
            documents_processed=2,
            documents_failed=0,
            included_document_ids=["file-1", "file-2"],
        )
        user = SimpleNamespace(id="user-1")
        note = SimpleNamespace(
            id="note-1",
            title="Síntese - Acervo Atlas",
            meta={},
            data={"content": {"md": ""}},
            created_at=1_700_000_000,
            updated_at=1_700_000_000,
        )

        with patch.object(
            collection_synthesis.Notes, "insert_new_note", return_value=note
        ) as mock_insert, patch.object(
            collection_synthesis,
            "upsert_linked_note_vector",
            side_effect=TimeoutError("Embedding generation timed out after 300 seconds"),
        ):
            note_id, note_warnings, final_status = asyncio.run(
                collection_synthesis.persist_synthesis_note(
                    request=SimpleNamespace(),
                    collection=collection,
                    report_record=report_record,
                    user=user,
                    report={"overview": "Resumo final."},
                    final_status=collection_synthesis.SYNTHESIS_COMPLETED_STATUS,
                    warnings=[],
                )
            )

        insert_form = mock_insert.call_args.args[1]
        self.assertEqual(note_id, "note-1")
        self.assertEqual(
            final_status,
            collection_synthesis.SYNTHESIS_COMPLETED_WITH_WARNINGS_STATUS,
        )
        self.assertEqual(len(note_warnings), 1)
        self.assertTrue(insert_form.data["content"]["html"])
        self.assertIn("<h1>", insert_form.data["content"]["html"])
        self.assertIsNone(insert_form.data["content"]["json"])
        self.assertEqual(insert_form.data["versions"], [])
        self.assertIsNone(insert_form.data["files"])
        self.assertIn("indexacao vetorial", note_warnings[0].lower())
        self.assertIn("300 seconds", note_warnings[0])

    def test_persist_synthesis_note_serializes_access_grants_for_collection_update(self):
        grant = AccessGrantModel(
            id="grant-1",
            resource_type="knowledge",
            resource_id="kb-1",
            principal_type="user",
            principal_id="user-2",
            permission="read",
            created_at=1_776_365_960,
        )
        collection = SimpleNamespace(
            id="kb-1",
            name="Acervo Atlas",
            description="",
            meta={},
            access_grants=[grant],
        )
        report_record = SimpleNamespace(
            id="report-1",
            note_id=None,
            model_name="gemma-3-12b",
            documents_processed=2,
            documents_failed=0,
            included_document_ids=["file-1", "file-2"],
        )
        user = SimpleNamespace(id="user-1")
        note = SimpleNamespace(
            id="note-1",
            title="Síntese - Acervo Atlas",
            meta={},
            data={"content": {"md": ""}},
            created_at=1_700_000_000,
            updated_at=1_700_000_000,
        )

        with patch.object(
            collection_synthesis.Notes, "insert_new_note", return_value=note
        ), patch.object(
            collection_synthesis.Knowledges, "update_knowledge_by_id"
        ) as mock_update, patch.object(
            collection_synthesis.Knowledges,
            "get_knowledge_by_id",
            return_value=collection,
        ), patch.object(
            collection_synthesis, "upsert_linked_note_vector"
        ):
            note_id, note_warnings, final_status = asyncio.run(
                collection_synthesis.persist_synthesis_note(
                    request=SimpleNamespace(),
                    collection=collection,
                    report_record=report_record,
                    user=user,
                    report={"overview": "Resumo final."},
                    final_status=collection_synthesis.SYNTHESIS_COMPLETED_STATUS,
                    warnings=[],
                )
            )

        update_form = mock_update.call_args.kwargs["form_data"]
        self.assertEqual(note_id, "note-1")
        self.assertEqual(note_warnings, [])
        self.assertEqual(final_status, collection_synthesis.SYNTHESIS_COMPLETED_STATUS)
        self.assertEqual(len(update_form.access_grants), 1)
        self.assertIsInstance(update_form.access_grants[0], dict)
        self.assertEqual(update_form.access_grants[0]["id"], "grant-1")
        self.assertEqual(update_form.access_grants[0]["principal_id"], "user-2")


if __name__ == "__main__":
    unittest.main()
