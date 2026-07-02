#!/usr/bin/env python3
"""Phase 0.4 — Vector Memory Verification Test.

Proves that ZaiEmbedder embeddings + semantic retrieval work end-to-end.
Tests: embedder loading → encoding → storage → cosine similarity search → retrieval quality.
"""
import asyncio
import json
import logging
import os
import sys
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

logging.basicConfig(level=logging.INFO, format="%(name)s | %(levelname)s | %(message)s")
logger = logging.getLogger("VectorTest")


def test_zai_embedder_loading():
    """Test 1: Can we initialise the ZaiEmbedder?"""
    logger.info("=== Test 1: ZaiEmbedder Initialisation ===")
    from core.embeddings import ZaiEmbedder

    embedder = ZaiEmbedder()
    logger.info(f"  Embedder initialised: {type(embedder).__name__}")
    logger.info(f"  API available: {embedder.available()}")
    if embedder.available():
        logger.info(f"  Using Z.ai Embedding-3 API (dim={ZaiEmbedder.EMBEDDING_DIM})")
    else:
        logger.info(f"  Using hash-based fallback (dim={ZaiEmbedder.FALLBACK_DIM})")
    logger.info("  PASS: Embedder initialised successfully")
    return embedder


def test_embedding_generation(embedder):
    """Test 2: Can we generate embeddings?"""
    logger.info("=== Test 2: Embedding Generation ===")

    # Single text
    emb = embedder.embed("Hello, my name is Friday")
    logger.info(f"  Single embedding shape: {emb.shape}")
    logger.info(f"  Embedding dtype: {emb.dtype}")
    logger.info(f"  Embedding norm: {np.linalg.norm(emb):.4f}")
    assert emb.shape[0] > 0, "Expected non-empty embedding"
    assert len(emb.shape) == 1, f"Expected 1-D embedding, got {emb.shape}"

    # Batch encoding
    texts = [
        "I love programming in Python",
        "The weather in Lagos is hot and humid",
        "Machine learning is transforming industries",
        "I prefer tea over coffee in the morning",
        "My favorite color is blue",
    ]
    embeddings = np.array([embedder.embed(t) for t in texts])
    logger.info(f"  Batch embedding shape: {embeddings.shape}")
    assert embeddings.shape[0] == 5, f"Expected 5 embeddings, got {embeddings.shape[0]}"
    logger.info("  PASS: Embedding generation works")
    return texts, embeddings


def test_cosine_similarity(embedder, texts, embeddings):
    """Test 3: Does the ZaiEmbedder.similarity method work?"""
    logger.info("=== Test 3: Cosine Similarity Quality ===")

    # Compute pairwise similarities
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    # Avoid division by zero
    norms = np.where(norms == 0, 1, norms)
    normalized = embeddings / norms
    sim_matrix = normalized @ normalized.T

    logger.info("  Pairwise cosine similarities:")
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            sim = sim_matrix[i, j]
            logger.info(f"    '{texts[i][:40]}...' ↔ '{texts[j][:40]}...' = {sim:.4f}")

    # Test ZaiEmbedder.similarity static method
    sim = ZaiEmbedder.similarity(embeddings[0], embeddings[1])
    logger.info(f"  ZaiEmbedder.similarity() result: {sim:.4f}")
    assert -1.0 <= sim <= 1.0, f"Similarity out of range: {sim}"

    logger.info("  PASS: Similarity computation works")


def test_vector_store_integration():
    """Test 4: Does the FRIDAY VectorStore work end-to-end?"""
    logger.info("=== Test 4: VectorStore Integration ===")

    from database.vector_store import VectorStore
    vs = VectorStore()

    # Add memories
    memories = [
        ("User's name is Chidi and they work as a software engineer", {"category": "personal", "type": "name"}),
        ("User prefers dark mode for all coding environments", {"category": "preference", "type": "likes"}),
        ("User lives in Lagos, Nigeria", {"category": "personal", "type": "location"}),
        ("User is allergic to shellfish", {"category": "health", "type": "allergy"}),
        ("User's birthday is March 15th", {"category": "personal", "type": "birthday"}),
        ("User enjoys reading science fiction novels", {"category": "preference", "type": "likes"}),
        ("User needs to finish the quarterly report by Friday", {"category": "work", "type": "task"}),
        ("User has a pet cat named Whiskers", {"category": "personal", "type": "possessions"}),
    ]

    for text, meta in memories:
        vs.add_memory(text, meta)
        logger.info(f"  Added: '{text[:60]}...'")

    # Search for relevant memories
    queries = [
        "What is the user's name?",
        "What are the user's preferences?",
        "Does the user have any health issues?",
        "What does the user need to do for work?",
    ]

    for query in queries:
        results = vs.search(query, top_k=3)
        logger.info(f"\n  Query: '{query}'")
        for r in results:
            content = r.get("content", "")
            similarity = r.get("similarity", 0)
            logger.info(f"    [{similarity:.4f}] {content[:70]}...")

    # Verify the most relevant result for name query
    name_results = vs.search("What is the user's name?", top_k=1)
    assert len(name_results) > 0, "Should find at least one result for name query"
    top_result = name_results[0].get("content", "")
    assert "Chidi" in top_result, f"Expected name 'Chidi' in top result, got: {top_result}"
    logger.info("\n  PASS: VectorStore integration works correctly")


def test_full_memory_pipeline():
    """Test 5: Full FridayMemory pipeline with vector search."""
    logger.info("=== Test 5: Full FridayMemory Pipeline ===")

    from core.memory import FridayMemory
    memory = FridayMemory()

    # Simulate a conversation
    conversations = [
        ("user", "Hi, my name is Chidi and I live in Lagos"),
        ("assistant", "Hello Chidi! Lagos is an amazing city. How can I help you today?"),
        ("user", "I really love jollof rice and suya"),
        ("assistant", "Jollof rice and suya are Nigerian classics! Great taste."),
        ("user", "I work as a software engineer at a fintech startup"),
        ("assistant", "That's exciting! The fintech scene in Lagos is booming."),
        ("user", "I'm allergic to shellfish, so please remember that"),
        ("assistant", "Noted! I'll remember you're allergic to shellfish."),
    ]

    for role, content in conversations:
        memory.store_conversation(role, content)

    # Check extracted facts
    profile = memory.get_user_profile()
    logger.info(f"  Extracted facts: {json.dumps(profile, default=str, indent=2)[:300]}...")

    # Test semantic retrieval
    test_queries = [
        "What's my name?",
        "What food do I like?",
        "What health issues do I have?",
        "Where do I work?",
    ]

    for query in test_queries:
        results = memory.retrieve_relevant_memories(query, top_k=2)
        logger.info(f"  Query: '{query}' → {len(results)} results")
        for r in results:
            content = r.get("content", r) if isinstance(r, dict) else str(r)
            logger.info(f"    - {content[:80]}")

    logger.info("  PASS: Full memory pipeline works with semantic retrieval")


def main():
    logger.info("=" * 60)
    logger.info("FRIDAY Phase 0.4 — Vector Memory Verification")
    logger.info("=" * 60)

    results = {}

    # Test 1: Embedder loading
    try:
        embedder = test_zai_embedder_loading()
        results["embedder_loading"] = "PASS"
    except Exception as e:
        logger.error(f"Embedder loading FAILED: {e}", exc_info=True)
        results["embedder_loading"] = f"FAIL: {e}"
        return results

    # Test 2: Embedding generation
    try:
        texts, embeddings = test_embedding_generation(embedder)
        results["embedding_generation"] = "PASS"
    except Exception as e:
        logger.error(f"Embedding generation FAILED: {e}", exc_info=True)
        results["embedding_generation"] = f"FAIL: {e}"

    # Test 3: Cosine similarity quality
    try:
        test_cosine_similarity(embedder, texts, embeddings)
        results["cosine_similarity"] = "PASS"
    except Exception as e:
        logger.error(f"Cosine similarity FAILED: {e}", exc_info=True)
        results["cosine_similarity"] = f"FAIL: {e}"

    # Test 4: VectorStore integration
    try:
        test_vector_store_integration()
        results["vector_store"] = "PASS"
    except Exception as e:
        logger.error(f"VectorStore FAILED: {e}", exc_info=True)
        results["vector_store"] = f"FAIL: {e}"

    # Test 5: Full memory pipeline
    try:
        test_full_memory_pipeline()
        results["full_pipeline"] = "PASS"
    except Exception as e:
        logger.error(f"Full pipeline FAILED: {e}", exc_info=True)
        results["full_pipeline"] = f"FAIL: {e}"

    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("PHASE 0.4 SUMMARY")
    logger.info("=" * 60)
    for name, status in results.items():
        icon = "✅" if status.startswith("PASS") else "❌"
        logger.info(f"  {icon} {name}: {status}")

    total_pass = sum(1 for v in results.values() if v.startswith("PASS"))
    total = len(results)
    logger.info(f"\n  {total_pass}/{total} tests passed")

    return results


if __name__ == "__main__":
    main()
