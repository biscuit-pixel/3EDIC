# TODO

Not scheduled for a release of its own: bundle these into the next version that ships anyway.

- [x] Credit møušn: logo + "Made by" in the page footer and Settings → About, installer publisher, macOS copyright (ships with the next release).
- [x] Logo chosen: red plate (`branding/logo/`). App icon is the matching red tile.
- [x] Windows and macOS release jobs run green on GitHub (build, self-test of the packaged app incl. models + vision pipeline, silent install/uninstall on Windows, app run from the .dmg).
- [ ] Open the installers on real Windows and Mac machines: CI is headless, so the native window (pywebview/WebView2/WKWebView), the install wizard and the first in-app update have not been seen by a human yet.
- [ ] Verify the update path for real: install v0.1.0, tag v0.1.1, click "Update now".
- [ ] Test on a real GPU (DirectML on Windows, CoreML on Mac, CUDA).
- [ ] Update system: add signature verification (Authenticode / Apple notarization) so a stolen GitHub login alone cannot push a malicious update; add a "skip this version" option.
- [ ] Code signing: Windows certificate, Apple Developer ID + notarization.
- [ ] Pose at a higher resolution than 640 px for small people in wide shots.
- [ ] Try the system folder dialog, drag-and-drop of a folder onto the window, and "Change working folder → Restart" in the real Windows and macOS app (pywebview behaves differently from Chromium here).
- [ ] Tag screen: a person who is not in the model's detections cannot be boxed; add "draw a box" for that.
