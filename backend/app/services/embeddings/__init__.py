"""Vector embeddings over the catalog and parts tables, backed by pgvector.

`client` talks to the embedding provider, `text` builds the canonical source
text per entity, and `store` reconciles and searches. See store.py for why
synchronization is a content-addressed sweep rather than a create hook.
"""
