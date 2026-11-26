# Pi coding agent on Render Workflows

The Pi coding agent example is TypeScript only: [Pi](https://github.com/earendil-works/pi)'s SDK (`@earendil-works/pi-coding-agent`) runs in Node.js and has no Python package.

See [`pi-agent` in the TypeScript examples repository](https://github.com/render-examples/render-workflows-examples-ts/tree/main/pi-agent). It runs Pi inside a Render Workflows task: it clones a repository, fixes the bug behind a support ticket, and returns the change as a patch or a pushed branch.

To scaffold it with the Render CLI:

```bash
render workflows init --language node --template pi-agent
```
