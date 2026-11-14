# Files and shared documents

Use **Attach files** in chat or **Open file…** in Documents; manual paths are an advanced option. Imports use the shared native file-selection/import route, not a predefined nonexistent path. The editor's current path/content and selected Windows/WSL location must accompany file-related requests; unsaved content is distinct from disk content.

Uploads enter one local catalogue shared by chat, Files & Tools and document services. Registering/extracting a file does not automatically send it to an LLM or embedding provider. Attachments include a bounded preview/reference; `documents.read` retrieves further extracted text. Images preserve native vision parts when the chosen provider/model supports them.

Explicit embedding sends the relevant content to the selected embedding adapter. Cloud indexing/search follows the shared approval policy. Lexical search can remain local. Semantic retrieval compares compatible model/dimension identities only. See [RAG](RAG.md) for chunk/provenance/rebuild semantics.

Approved workspace files and explicit saved-chat/terminal snapshots use the same catalogue. These are bounded snapshots, not continuous harvesting. A terminal/model can use returned paths and service references; no duplicate terminal document store is needed.

**Uploaded library** inserts references into the same shared dialogue. **Remove index** removes extracted/index data while preserving source copies and existing chat messages. Previously sent content is not recalled by removing an index. Treat file contents as untrusted data and keep credentials/protected paths outside agent retrieval.

Extraction and upload sizes are bounded in the implementation. Media is not automatically transcribed, and PDF/DOCX line coordinates refer to extracted text rather than original page layout. Use returned limits/errors instead of assuming every file was read in full.
