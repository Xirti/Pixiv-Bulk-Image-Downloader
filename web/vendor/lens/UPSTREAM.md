Lens by DayDreamInAReverie
https://github.com/ruiqichenbiec/design-systems
Commit: 7a424c93b9be13cf835b91ea67495fec2ec0584e
Package: lens/lab/design-system, public ESM entry and its complete import closure.
License: MIT; see LICENSE. Local R8 adaptation: runtime.js exposes reusable live artwork refresh, aligned artwork, brightness and tint strength; internal/renderer.js reuses a backing canvas with texSubImage2D and applies these optional settings. Original defaults, shaders and GPU lifecycle are retained.

R9 adds optional bevelWidth (default 19 CSS px) and bevelPower (default 1.65), passed from runtime to shader uniforms. The experiment uses 64 px and 1.05 for wider panel refraction; the upstream defaults stay available.

R11 defers context restoration until after the contextlost event. One owned pending task coalesces requests, checks the current viewport lease and is cancelled on suspension, restoration or teardown. Material and shader parameters are unchanged.

The local renderer also tracks layout animation duration separately from redraw duration. Pointer and live artwork updates reuse lens geometry; moving controls continue to refresh their bounds.
