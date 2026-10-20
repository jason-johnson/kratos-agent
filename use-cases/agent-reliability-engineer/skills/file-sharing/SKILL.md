---
name: file-sharing
description: Deliver incident evidence bundles and reports as ephemeral downloadable files
enabled: true
---

# Incident file delivery

When a retained workflow produces a file:

1. Write it under `/tmp` with a stable incident-aware name such as
   `/tmp/INC7002-command-brief.pdf`.
2. Reference the absolute path in the response so the frontend can provide a
   download link.
3. Do not base64-encode the file into chat.

Files are ephemeral and do not survive pod replacement. Include only data
authorized for the intended audience, and never describe a file as durable
incident storage.
