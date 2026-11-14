# Security and private data

Do not include API keys, browser tokens, personal settings, uploaded documents, chats or diagnostic dumps in issues or commits. Use synthetic inputs and redact logs before sharing them.

Report suspected vulnerabilities privately using GitHub private vulnerability reporting when available. Otherwise open a minimal issue requesting a private contact, without exploit details or credentials.

## Credential exposure

Removing a file or rewriting a branch does not revoke a key. Rotate any real credential that was committed. Existing clones, forks, pull-request refs, Actions logs and GitHub caches may retain old objects; branch rewriting cannot guarantee erasure. Keep the repository private until exposed credentials and retained refs have been addressed. Use GitHub Support when host-side removal is required.

The repository ignores local credential/state locations, and the tracked-tree guard rejects them even when added with force. Example configuration files contain placeholders only. Secret scanning is an additional check, not proof that no sensitive information exists.

## After a history reset

Use a fresh clone or carefully rebase only independently reviewed changes onto the clean root. Never merge or push old branches or tags back into this repository: that can restore removed credentials even when the current files look clean. Keep any local recovery archive private and outside the repository.

The public-tree guard checks both the current index and all pathnames in reachable HEAD history, including deleted credential stores. It refuses shallow history so incomplete clones cannot claim a complete historical check. CI fetches full history and runs the pinned secret scanner separately.

## Application boundaries

Page, file, model and tool output are untrusted data. Keep canonical approval and execution linkage intact. Never replay an uncertain mutation to recover its output. Owned process cleanup is not a security sandbox, and remote cancellation cannot guarantee rollback.

Preserve TLS validation, credential-store protections and third-party notices. Do not distribute optional model weights or runtimes under the root source license without their own applicable terms.
