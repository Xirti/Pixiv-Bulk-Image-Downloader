Lattice Lorenz point set

Source: https://github.com/ruiqichenbiec/design-systems
Pinned commit: 7a424c93b9be13cf835b91ea67495fec2ec0584e
Files: lattice/src/gl/sets.js — normalise and lorenz extracted without algorithm changes.
License: MIT; upstream copyright and license are preserved in LICENSE.
MOKU uses 760 points in a bounded Canvas 2D backdrop; it does not add the full Three.js stage.

R8 also extracts the unchanged Dots class from lattice/src/dom/dotkit.js, with a local reducedMotion media-query adapter. The title uses its attract, burst, spring and damping directly.

point-sets.js: normalise, lorenz, torusKnot, crystal, klein, sphere from src/gl/sets.js and rng from src/core/motion.js; unchanged algorithms. The adapter renders and interpolates the original formations in Canvas 2D.
