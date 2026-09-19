# Logo sources

Retrieved 2026-09-18. Logos remain trademarks of their respective owners.
This decorative gallery does not indicate model integration or endorsement.

- OpenAI, Claude, Grok, DeepSeek, Kimi: https://github.com/lobehub/lobe-icons/tree/master/packages/static-svg/icons
  Original SVGs, see LICENSE-lobe-icons.
- GLM: https://github.com/zai-org/GLM-4.5/blob/main/resources/logo.svg
  Original GLM model artwork (not ChatGLM), see LICENSE-glm.

Animation effects are implemented locally by MOKU.

Visual references (no source code copied):
- Paper Shaders: https://shaders.paper.design/liquid-metal and https://shaders.paper.design/heatmap
- Bruno Imbrizi / Codrops: https://tympanus.net/Tutorials/InteractiveParticles/
- Codrops motion trails: https://tympanus.net/Development/MotionTrailAnimations/

The stage uses Canvas 2D, not these projects' WebGL shaders. Its drawing area,
pixel ratio, particles, and trail history are bounded. The single animation loop
stops offscreen, behind viewers, in hidden tabs, and for reduced-motion users.
