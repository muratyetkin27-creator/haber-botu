"""Cosine similarity ile tekrar eden haber tespiti (TF-IDF tabanlı)."""
import logging
import re
from typing import Any

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from config import SIMILARITY_THRESHOLD

logger = logging.getLogger(__name__)


def _normalize(text: str) -> str:
    """Metni normalize eder: küçük harf, fazla boşluk ve noktalama temizliği."""
    if not text:
        return ""
    text = text.lower()
    # Türkçe karakterleri koru ama fazla noktalamayı temizle
    text = re.sub(r"[^\w\sğüşıöçİĞÜŞÖÇ]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _build_corpus(news_items: list[dict[str, Any]]) -> list[str]:
    """Her haber için (başlık + içerik) birleşik metin döndürür."""
    corpus: list[str] = []
    for item in news_items:
        title = item.get("title") or ""
        content = item.get("content") or ""
        corpus.append(_normalize(f"{title} {content}"))
    return corpus


def deduplicate(news_items: list[dict[str, Any]],
                threshold: float = SIMILARITY_THRESHOLD) -> list[dict[str, Any]]:
    """
    Cosine similarity'ye göre tekrar eden haberleri filtreler.
    Aynı görünen haberlerden ilkini saklar, diğerlerini atar.
    """
    if not news_items or len(news_items) < 2:
        return news_items

    try:
        corpus = _build_corpus(news_items)
        # Boş metinleri filtrele
        valid_indices = [i for i, t in enumerate(corpus) if t.strip()]
        if len(valid_indices) < 2:
            return news_items

        # TF-IDF vektörleştirme
        vectorizer = TfidfVectorizer(
            ngram_range=(1, 2),
            max_features=5000,
            min_df=1,
        )
        valid_corpus = [corpus[i] for i in valid_indices]
        try:
            tfidf = vectorizer.fit_transform(valid_corpus)
        except ValueError:
            # Tüm kelimeler stopword olduysa
            return news_items

        # Cosine similarity matrisi
        sim_matrix = cosine_similarity(tfidf)

        # Greedy: ilk gelen kazanır, benzerleri ele
        keep_mask = np.ones(len(valid_indices), dtype=bool)
        for i in range(len(valid_indices)):
            if not keep_mask[i]:
                continue
            for j in range(i + 1, len(valid_indices)):
                if keep_mask[j] and sim_matrix[i, j] >= threshold:
                    keep_mask[j] = False
                    logger.debug(
                        "Tekrar tespit: '%s' ~ '%s' (sim=%.2f)",
                        news_items[valid_indices[i]].get("title", "")[:50],
                        news_items[valid_indices[j]].get("title", "")[:50],
                        float(sim_matrix[i, j]),
                    )

        # Filtrelenmiş haberler + boş içerikli olanlar (filtrelenemeyenler)
        kept_valid_idx = {valid_indices[i] for i in range(len(valid_indices)) if keep_mask[i]}
        invalid_idx = set(range(len(news_items))) - set(valid_indices)
        kept_idx = sorted(kept_valid_idx | invalid_idx)

        result = [news_items[i] for i in kept_idx]
        removed = len(news_items) - len(result)
        if removed > 0:
            logger.info("Tekrar filtresi: %d haber elendi, %d kaldı.", removed, len(result))
        return result
    except Exception as e:
        logger.exception("Tekrar filtreleme hatası: %s", e)
        return news_items
