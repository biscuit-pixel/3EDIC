# TODO

Not scheduled for a release of its own: bundle these into the next version that ships anyway.

- [ ] **Credit møušn.** Add "Made by møušn" in the places people will see it: a small line in the app (footer or an About panel in Settings, SK + EN strings in `edic/ui/js/i18n.js`), the README header, and the Windows installer publisher (`AppPublisher` in `packaging/windows/3edic.iss`, currently a placeholder) plus the macOS `NSHumanReadableCopyright`. Use the møušn wordmark from the design system's `assets/Logo/` as a file, not retyped.
- [x] Logo chosen: red plate (`branding/logo/`). App icon is the matching red tile.
- [ ] Run the Windows and macOS release jobs and fix whatever breaks the first time (the installers have not been built on those systems yet).
- [ ] Test on a real GPU (DirectML on Windows, CoreML on Mac, CUDA).
- [ ] Update system: add signature verification (Authenticode / Apple notarization) so a stolen GitHub login alone cannot push a malicious update; add a "skip this version" option.
- [ ] Code signing: Windows certificate, Apple Developer ID + notarization.
- [ ] Pose at a higher resolution than 640 px for small people in wide shots.
