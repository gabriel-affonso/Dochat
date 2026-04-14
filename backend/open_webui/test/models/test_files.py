from open_webui.models.files import FileModel


def test_file_model_normalizes_legacy_data_and_meta():
    file_model = FileModel.model_validate(
        {
            "id": "file-1",
            "user_id": "user-1",
            "hash": None,
            "filename": "certidao.pdf",
            "path": "/tmp/certidao.pdf",
            "data": "José aparece varias vezes no texto legado.",
            "meta": "metadado legado",
            "is_archived": False,
            "archived_at": None,
            "created_at": 1_710_000_000,
            "updated_at": 1_710_000_001,
        }
    )

    assert file_model.data == {"content": "José aparece varias vezes no texto legado."}
    assert file_model.meta == {"legacy_value": "metadado legado"}
