# Document RAG

`local_mode/document_store.py` indexes uploaded documents and approved workspace/chat/terminal snapshots in chunks.

- **Chunks**: shared chunk-size/overlap settings apply on explicit indexing (defaults: 1500/200 characters), preferring
  whitespace boundaries; at most 512 chunks per document. Spans are deterministic
  source offsets (`start`, `end`). Chunk text is not stored again; only offsets
  and vectors are kept in the `chunks` table.
- **Citations**: semantic hits return `offset`, `excerpt` and
  `citation` (`document_id`, `name`, `chunk`, `start`, `end`). Lexical hits
  include a citation with `chunk: null`. `documents.read` accepts the offsets.
- **Long documents**: every chunk is embedded, so text beyond the first 12000
  characters is searchable. Files without extracted text (media) keep one vector
  over the uploaded file.
- **Identity**: each chunk stores provider, model and dimension. Search only
  compares vectors with the query's identity; other vectors are ignored.
- **Rebuild**: `embed(id)` computes all chunk vectors first, then replaces the
  old index in one transaction, so a failure keeps the previous index.
  `rebuild(backend, id)` re-embeds indexed documents under the current identity.
  Documents are never deleted.
- **Migration**: old whole-document vectors in `documents.embedding` remain
  searchable while their provider, model and dimension match, until the document is
  re-embedded.
- **Batching & Intra-request Dedup**: `batch_selected` batches cloud embedding transport
  (via `embed_input` with separate Content objects), while `_embed_batch` performs intra-request
  deduplication to avoid redundant transport calls without unsafe cross-request vector caching.
- **Mismatch Detection & Safe Rebuild**: Semantic searches detect when an existing index
  was generated under a different provider/model/dimensions, surfacing `index_mismatch`
  with details rather than silently returning empty hits, and supporting safe atomic `rebuild(backend)`
  or an explicit internal Python `auto_rebuild=True` call. Shared agent search never requests an automatic rebuild.
- **Shared Service `documents.rebuild`**: Exposed through `ServiceRegistry` with human approval required (`approval=True`), re-embedding indexed documents under the configured identity without file deletion.
- No raw file execution. Uploads remain content-addressed local copies; source imports and refreshes use bounded guarded readers and explicit approval.
- Tests mock embedding transport; no paid model calls.

## Explicit source refresh

The uploaded library also lists approved workspace and saved chat/terminal snapshots. **Refresh source** rereads just that original source locally. Agents discover `documents.refresh` by document ID and must obtain approval. No directory scanning, private terminal scrollback, model call or automatic embedding occurs.

Unchanged text preserves vectors. Changed text replaces the snapshot and invalidates its vectors; use explicit indexing afterward. Existing citations keep the document ID and new results report current offsets/provenance. Refresh fails without changing the old snapshot if the source disappears, changes identity/location, fails validation or is concurrently removed or changed. Saved terminal truncation metadata may update while the exact task/stream identity remains fixed. Uploaded attachments are immutable content-addressed copies: attach a changed file again rather than refreshing it.

Terminal source identity excludes mutable truncation metadata. Reimport preserves the same ID and vectors when text is unchanged, including snapshots made by older versions. If historical duplicates already exist, imports choose a deterministic existing ID; older records and citations are retained rather than silently merged or deleted.
