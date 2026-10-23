"""Current pgvector embedding space dimensions.

A single source of truth: the pgvector column type, the initial schema and the
runtime validation all derive from this value, so changing it requires
rebuilding the database rather than migrating one.
"""

VECTOR_DIMENSIONS = 1024
