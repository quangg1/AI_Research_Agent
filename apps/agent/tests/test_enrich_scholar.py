from app.domain.verify_citations import arxiv_pdf_fallback


def test_arxiv_pdf_fallback_maps_abs_and_html():
    assert arxiv_pdf_fallback("https://arxiv.org/abs/2401.12345") == "https://arxiv.org/pdf/2401.12345.pdf"
    assert arxiv_pdf_fallback("https://arxiv.org/html/2401.12345") == "https://arxiv.org/pdf/2401.12345.pdf"
    assert arxiv_pdf_fallback("https://example.com/paper") == ""
