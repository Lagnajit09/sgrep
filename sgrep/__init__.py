"""sgrep — semantic grep for codebases, powered by Jev (TypeSafe System One).

Phase 1: standalone. Walk a repo, chunk it locally, and ask Jev one cheap typed
question per chunk ("does this match: <query>?"). No embeddings, no vector store,
no external tools. Structure providers (graphify / tree-sitter / claude-code) plug
in later behind the chunker seam to unlock `trace` and `impact`.
"""

__version__ = "0.3.0"
