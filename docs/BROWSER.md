# Chrome and media

Configure the Chrome profile and connection in **Settings → Browser**. Existing-profile automation requires the supported extension/token connection; profile selection must not silently attach to another personal browser. Tokens are private credentials. Opening an ordinary URL without an automation connection does not prove page content was inspected.

Browser tools operate through the shared capability/approval pipeline. Returned page content is untrusted. Use observed URLs and actual playback state; do not report music as playing merely because navigation succeeded. Advertisements, muted tabs, loading players and authentication can prevent verification.

Jarvis's media queue stores observed video URLs. Empty URL lists are invalid. Queue Next and selected-player controls are separate from global multimedia hotkeys. Speech Pause/Stop controls only Jarvis synthesis.

Chrome is the supported browser automation target. Zephium is not part of the product. During development use an isolated Chrome/Chromium profile and fixture page, never incidental control of the user's current tabs or music.

Connection errors belong in browser diagnostics; changing global networking or installing unrelated browsers is not a valid automatic recovery action.
