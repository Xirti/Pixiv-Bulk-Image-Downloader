Lattice point formations and dot physics

Source: https://github.com/ruiqichenbiec/design-systems
Pinned commit: 7a424c93b9be13cf835b91ea67495fec2ec0584e
License: MIT; original copyright and license are preserved in LICENSE.

point-sets.js contains normalise, lorenz, torusKnot, crystal, klein and sphere from src/gl/sets.js, plus rng from src/core/motion.js. The algorithms are unchanged. The background interpolates four 1600-point formations in Canvas 2D and supplies the live canvas to the glass renderer.

dots.js extracts Dots from lattice/src/dom/dotkit.js, with a local reduced-motion media-query adapter. The title uses attract, burst, spring and damping directly.

The earlier standalone lorenz.js duplicated the point-set module and was removed for R11 publication.
