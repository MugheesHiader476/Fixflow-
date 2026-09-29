---
type: Reference
title: Current knowledge pipeline
description: The implemented document ingestion and keyword retrieval path in FixFlow.
tags: [architecture, ingestion, retrieval]
---

# Current knowledge pipeline

FixFlow accepts uploaded documents and pasted Markdown through `POST /api/documents`. The API stores uploads in private, server-generated paths and records a source in PostgreSQL. A database-backed worker extracts text, writes documents and chunks transactionally, then marks the source `ready_for_embedding`.

The application currently retrieves chunks with PostgreSQL full-text search. The vector column is nullable, and no embedding model, reranker, or AI diagnosis provider is configured. Debug sessions, chat messages, and saved solutions are persisted in PostgreSQL.

