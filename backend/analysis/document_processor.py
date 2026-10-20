"""
DocumentProcessor: файл → изображения для vision-модели.

Интерфейс: document_to_images(file_data: bytes, filename: str) -> list[str].
Вся сложность внутри: PDF рендерится в JPEG через PyMuPDF (poppler не нужен),
картинки отдаются как есть. OCR и анонимизация удалены — распознаёт сама модель.
"""
import base64

import fitz  # PyMuPDF

# Ограничение на страницы: каждая = отдельное изображение в запросе (токены + время).
# Больше — ошибка, а не молчаливое усечение: расшифровка по части бланка опасна.
MAX_PAGES = 5

# Matrix(2, 2) ≈ 144 dpi: мелкий шрифт таблиц ещё читаем, файл — сотни КБ.
_RENDER_MATRIX = fitz.Matrix(2, 2)
_JPEG_QUALITY = 85

# DeepSeek vision: JPEG / PNG / GIF / WebP (формат определяется по содержимому).
_MIME_BY_EXT = {"png": "image/png", "webp": "image/webp", "gif": "image/gif"}
_DEFAULT_MIME = "image/jpeg"


def _data_url(mime: str, data: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def document_to_images(file_data: bytes, filename: str) -> list[str]:
    """PDF/изображение → список data-URL для vision-модели."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext != "pdf":
        return [_data_url(_MIME_BY_EXT.get(ext, _DEFAULT_MIME), file_data)]

    doc = fitz.open(stream=file_data, filetype="pdf")
    try:
        if doc.page_count > MAX_PAGES:
            raise ValueError(f"PDF содержит {doc.page_count} страниц, максимум {MAX_PAGES}")
        return [
            _data_url("image/jpeg", doc[index].get_pixmap(matrix=_RENDER_MATRIX).tobytes("jpeg", jpg_quality=_JPEG_QUALITY))
            for index in range(doc.page_count)
        ]
    finally:
        doc.close()
