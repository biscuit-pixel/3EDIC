# TODO

Not scheduled for a release of its own: bundle these into the next version that ships anyway.

- [ ] **Credit møušn.** Add "Made by møušn" in the places people will see it: a small line in the app (footer or an About panel in Settings, SK + EN strings in `edic/ui/js/i18n.js`), the README header, and the Windows installer publisher (`AppPublisher` in `packaging/windows/3edic.iss`, currently a placeholder) plus the macOS `NSHumanReadableCopyright`. Use the møušn wordmark from the design system's `assets/Logo/` as a file, not retyped.
- [x] Logo chosen: red plate (`branding/logo/`). App icon is the matching red tile.
- [x] Windows and macOS release jobs run green on GitHub (build, self-test of the packaged app incl. models + vision pipeline, silent install/uninstall on Windows, app run from the .dmg).
- [ ] Open the installers on real Windows and Mac machines: CI is headless, so the native window (pywebview/WebView2/WKWebView), the install wizard and the first in-app update have not been seen by a human yet.
- [ ] Verify the update path for real: install v0.1.0, tag v0.1.1, click "Update now".
- [ ] Test on a real GPU (DirectML on Windows, CoreML on Mac, CUDA).
- [ ] Update system: add signature verification (Authenticode / Apple notarization) so a stolen GitHub login alone cannot push a malicious update; add a "skip this version" option.
- [ ] Code signing: Windows certificate, Apple Developer ID + notarization.
- [ ] Pose at a higher resolution than 640 px for small people in wide shots.
