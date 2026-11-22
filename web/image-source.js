"use strict";

// The proxy source identifies the image independently of its access token.
globalThis.imageSourceKey=function(source=""){
  const route="/api/pixiv/image?";
  if(!source.startsWith(route))return source;
  const params=new URLSearchParams(source.slice(route.length));
  const version=params.get("source");
  if(version)return route+"source="+version;
  params.delete("warm");params.delete("requestId");
  return route+params;
};
