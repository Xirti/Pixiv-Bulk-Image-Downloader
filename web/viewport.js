"use strict";

// A synchronous repaint may temporarily shorten the scroll range.
globalThis.preserveViewport = function (surface, paint, top=null) {
  const page=surface===window,anchor=page?document.scrollingElement:surface;
  const left=page?window.scrollX:surface.scrollLeft;
  const wanted=top ?? (page?window.scrollY:surface.scrollTop);
  const previous=anchor.style.overflowAnchor;
  anchor.style.overflowAnchor="none";
  try{return paint();}
  finally{
    if((page?window.scrollY:surface.scrollTop)!==wanted || (page?window.scrollX:surface.scrollLeft)!==left){
      surface.scrollTo({top:wanted,left,behavior:"instant"});
    }
    anchor.style.overflowAnchor=previous;
  }
};
