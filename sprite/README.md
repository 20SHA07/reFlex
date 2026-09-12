# The Ember for Slack

Chrome Manifest V3 extension for Slack web (`https://app.slack.com/*`). It is
click-through except at the orb, mounts inside a shadow root, has no permissions,
and never calls a backend.

Load this folder from `chrome://extensions` with Developer mode enabled, then
open Slack web.

## Architecture

- `ember.js` is portable. `EMBER_CONFIG` owns all tuning, `EMBER_STATES` is
  data, `update(dt)` simulates, and `render(ctx)` only calls the four replaceable
  drawing pieces.
- `slack-adapter.js` is the Slack DOM boundary. It discovers the virtualized
  message list, parses `[REFEREE]` lines, and resolves live path rectangles.
- `choreography.js` runs the cancellable reaction sequence from parsed verdicts.
- `content.js` mounts the overlay and contains the development harness.

In the Slack page console:

```js
ember.setState('searching')
ember.gazeThenGlow(500, 300, 'found')
ember.illuminate({ x: 100, y: 100, width: 240, height: 30 })
runChoreography()
injectFakeRefereeMessage()
```

`Alt+Shift+E` runs the sample choreography. `Alt+Shift+F` injects a fake
referee message. Both are local fallback tools; the extension works without a
referee backend.

The referee is a ball that pulls a card for each verdict: `ALLOW` is green,
`BLOCK` is red, and `DEFER` is yellow. The sample choreography deliberately
runs all three outcomes, even if the visible message does not contain its
sample file paths.
