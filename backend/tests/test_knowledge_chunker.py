"""Section-aware bilingual chunker (#623): app/services/knowledge/chunker.py."""

import hashlib

from app.services.knowledge.chunker import (
    MAX_TOKENS,
    MIN_TOKENS,
    TWIN_KEY,
    chunk_article,
    estimate_tokens,
    pack_paragraph_spans,
    pack_paragraphs,
    split_sections,
    strip_series_boilerplate,
)


def _para(seed: str, tokens: int) -> str:
    """A paragraph of distinct sentences, roughly ``tokens`` long."""
    sentences = []
    i = 0
    while estimate_tokens(" ".join(sentences)) < tokens:
        sentences.append(f"{seed} sentence {i} tells part of the canal story.")
        i += 1
    return " ".join(sentences)


SERIES_ABOUT_EN = (
    "**About this Article**\n\n"
    "This article is part 2 of a 3-part series about The Panama Railroad.\n\n"
    "Articles in this series:\n\n"
    "- [Gold Rush](/en/articles/gold-rush)\n"
    "- The Construction (You are here)\n"
    "- [Legacy](/en/articles/legacy)\n\n"
    "---\n\n"
)
SERIES_CONTINUE_EN = (
    "\n\n---\n\n**Continue Reading**\n\n"
    "Ready for the next part? Continue to Part 3: [Legacy](/en/articles/legacy)"
)
SERIES_ABOUT_ES = (
    "**Acerca de este artículo**\n\n"
    "Este artículo es la parte 2 de una serie de 3 partes sobre El Ferrocarril.\n\n"
    "Artículos de esta serie:\n\n"
    "- La construcción (Estás aquí)\n\n"
    "---\n\n"
)
SERIES_CONTINUE_ES = (
    "\n\n---\n\n**Continuar leyendo**\n\n"
    "¿Listo para la siguiente parte? Continúa a la Parte 3: [Legado](/es/articulos/legado)"
)


class TestSeriesBoilerplate:
    def test_series_article_drops_about_and_continue(self):
        body = "## Building the line\n\nWorkers arrived in 1850.\n\n## Cost\n\nThousands died."
        chunks = chunk_article(
            "The Construction", SERIES_ABOUT_EN + body + SERIES_CONTINUE_EN, "en"
        )
        text = "\n".join(c.content for c in chunks)
        assert "About this Article" not in text
        assert "Continue Reading" not in text
        assert "You are here" not in text
        assert [c.heading_path for c in chunks] == ["Building the line", "Cost"]

    def test_spanish_series_article_drops_boilerplate(self):
        body = "## Los obreros\n\nLlegaron miles de trabajadores en 1850."
        chunks = chunk_article("La construcción", SERIES_ABOUT_ES + body + SERIES_CONTINUE_ES, "es")
        assert len(chunks) == 1
        assert chunks[0].content == "Llegaron miles de trabajadores en 1850."
        assert "Acerca de este artículo" not in chunks[0].embedding_text
        assert "Continuar leyendo" not in chunks[0].embedding_text
        assert chunks[0].meta["language"] == "es"

    def test_non_series_content_is_untouched(self):
        body = "Intro paragraph.\n\n## A\n\nText."
        assert strip_series_boilerplate(body) == body
        assert strip_series_boilerplate(None) == ""


class TestSections:
    def test_heading_path_nests_and_resets(self):
        md = (
            "Opening words.\n\n"
            "## Spanish Era\n\nColonial text.\n\n"
            "### Portobelo\n\nFair text.\n\n"
            "### Panama Viejo\n\nCity text.\n\n"
            "## Canal Era\n\nCanal text."
        )
        sections = split_sections(md)
        assert [s.heading_path for s in sections] == [
            None,
            "Spanish Era",
            "Spanish Era > Portobelo",
            "Spanish Era > Panama Viejo",
            "Canal Era",
        ]

    def test_empty_parent_section_is_omitted_but_kept_in_path(self):
        sections = split_sections("## Parent\n\n### Child\n\nOnly child text.")
        assert [s.heading_path for s in sections] == ["Parent > Child"]

    def test_h1_repeating_title_is_dropped(self):
        sections = split_sections("# The Title\n\nBody.", "The Title")
        assert [(s.heading_path, s.paragraphs) for s in sections] == [(None, ("Body.",))]

    def test_h4_stays_in_text(self):
        sections = split_sections("## A\n\n#### Small heading\n\nText.")
        assert sections[0].heading_path == "A"
        assert sections[0].paragraphs == ("#### Small heading", "Text.")


class TestChunks:
    def test_no_headings_article(self):
        content = f"{_para('One', 200)}\n\n{_para('Two', 200)}"
        chunks = chunk_article("Casco Viejo", content, "en")
        assert len(chunks) == 1
        assert chunks[0].heading_path is None
        assert chunks[0].embedding_text.startswith("Casco Viejo\n\n")
        assert chunks[0].chunk_index == 0

    def test_embedding_prefix_hash_and_tokens(self):
        chunks = chunk_article("Title", "## Sec\n\n### Sub\n\nBody text.", "en")
        chunk = chunks[0]
        assert chunk.heading_path == "Sec > Sub"
        assert chunk.embedding_text == "Title > Sec > Sub\n\nBody text."
        assert chunk.content_hash == hashlib.sha256(chunk.embedding_text.encode()).hexdigest()
        assert chunk.token_count == estimate_tokens(chunk.embedding_text)
        assert chunk.meta["headings"] == ["Sec", "Sub"]

    def test_long_section_packs_whole_paragraphs_in_band_with_overlap(self):
        paragraphs = [_para(f"P{i}", 150) for i in range(10)]  # ~1500 tokens
        chunks = chunk_article("T", "## Long\n\n" + "\n\n".join(paragraphs), "en")
        assert len(chunks) >= 3
        for chunk in chunks:
            assert estimate_tokens(chunk.content) <= MAX_TOKENS + 60 + 5
            assert estimate_tokens(chunk.content) >= MIN_TOKENS
            assert chunk.heading_path == "Long"
        # Every paragraph lands whole in some chunk: never split mid-paragraph.
        for paragraph in paragraphs:
            assert any(paragraph in c.content for c in chunks)
        # Later chunks open with the tail of the previous chunk.
        for previous, chunk in zip(chunks, chunks[1:], strict=False):
            opening = chunk.content.split("\n\n")[0]
            assert previous.content.endswith(opening)
        assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
        assert [c.meta["section_part"] for c in chunks] == list(range(len(chunks)))

    def test_oversized_paragraph_is_split_by_sentence(self):
        huge = _para("Huge", 1500)
        pieces = pack_paragraphs([huge])
        assert len(pieces) >= 3
        for piece in pieces:
            assert estimate_tokens(piece) <= MAX_TOKENS + 60 + 5
            assert piece.rstrip().endswith(".")  # cut at a sentence boundary

    def test_run_on_sentence_is_split_by_word(self):
        run_on = " ".join(["palabra"] * 2000)  # one ~4000-token sentence
        pieces = pack_paragraphs([run_on])
        assert len(pieces) >= 6
        assert all(estimate_tokens(p) <= MAX_TOKENS + 60 + 5 for p in pieces)
        assert " ".join(pieces).count("palabra") >= 2000

    def test_spanish_article_with_accents(self):
        md = "## La época colonial\n\nEl istmo fue clave para el comercio español."
        chunks = chunk_article("Historia de Panamá", md, "es")
        assert chunks[0].embedding_text.startswith("Historia de Panamá > La época colonial")

    def test_empty_content(self):
        assert chunk_article("T", None, "en") == []
        assert chunk_article("T", "   \n\n ", "en") == []

    def test_estimate_tokens(self):
        assert estimate_tokens("") == 0
        assert estimate_tokens("abcd") == 1
        assert estimate_tokens("abcde") == 2


class TestTwinKey:
    """The language-neutral twin key (#713): equal keys = the same source paragraphs."""

    @staticmethod
    def _article(seed: str, sizes: list[list[int]]) -> str:
        return "\n\n".join(
            f"## {seed} section {s}\n\n"
            + "\n\n".join(_para(f"{seed}{s}p{p}", n) for p, n in enumerate(paragraphs))
            for s, paragraphs in enumerate(sizes)
        )

    def test_spans_name_the_packed_paragraphs(self):
        paragraphs = [_para(f"P{i}", 150) for i in range(10)]
        spans = pack_paragraph_spans(paragraphs)
        assert [text for text, _ in spans] == pack_paragraphs(paragraphs)
        bounds = [tuple(int(x) for x in span.split("-")) for _, span in spans]
        assert bounds[0][0] == 0 and bounds[-1][1] == 9
        for (_, end), (start, _) in zip(bounds, bounds[1:], strict=False):
            assert start == end + 1  # contiguous, no paragraph twice

    def test_split_paragraph_pieces_are_labelled(self):
        spans = pack_paragraph_spans([_para("Short", 50), _para("Huge", 1500)])
        labels = [span for _, span in spans]
        assert labels[0].startswith("0-")
        assert all("1." in label for label in labels[1:])  # pieces of paragraph 1
        last = labels[-1].split("-")[-1]
        piece, pieces = last.split(".")[1].split("/")
        assert int(piece) == int(pieces) - 1

    def test_twins_pair_when_the_languages_pack_differently(self):
        # Same structure, but the Spanish paragraphs run longer, so the
        # section packs into different chunks and chunk_index drifts.
        en = chunk_article("T", self._article("en", [[150] * 3, [150] * 8, [200]]), "en")
        es = chunk_article("T", self._article("es", [[150] * 3, [250] * 8, [250]]), "es")
        assert len(en) != len(es)
        en_keys = {c.meta[TWIN_KEY]: c for c in en}
        es_keys = {c.meta[TWIN_KEY]: c for c in es}
        shared = en_keys.keys() & es_keys.keys()
        assert shared  # the first and last sections pair
        for key in shared:
            assert en_keys[key].meta["section_index"] == es_keys[key].meta["section_index"]
        # The last section's chunks pair although their chunk_index differs.
        last_en, last_es = en[-1], es[-1]
        assert last_en.meta[TWIN_KEY] == last_es.meta[TWIN_KEY]
        assert last_en.chunk_index != last_es.chunk_index

    def test_identical_structure_gives_identical_keys(self):
        sizes = [[120, 80], [150] * 5]
        en = chunk_article("T", self._article("en", sizes), "en")
        es = chunk_article("T", self._article("es", sizes), "es")
        assert [c.meta[TWIN_KEY] for c in en] == [c.meta[TWIN_KEY] for c in es]

    def test_a_different_shape_never_pairs(self):
        en = chunk_article("T", self._article("en", [[100, 100], [100]]), "en")
        es = chunk_article("T", self._article("es", [[100, 100, 100], [100]]), "es")
        assert not {c.meta[TWIN_KEY] for c in en} & {c.meta[TWIN_KEY] for c in es}
