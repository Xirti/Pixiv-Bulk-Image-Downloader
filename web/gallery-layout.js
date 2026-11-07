"use strict";
// Balance visible area, then cap height so portraits do not dominate the viewport.
function mokuPreviewSize({width,height,ratio,maxWidth=Infinity,maxHeight=Infinity}) {
  maxWidth=Math.max(1,maxWidth);maxHeight=Math.max(1,maxHeight);
  const area=Math.min(250000,Math.max(170000,maxWidth*Math.min(maxHeight,700)*.28));
  const desiredWidth=Math.max(Math.sqrt(area*ratio),width*1.08,height*ratio*1.08);
  const heightLimit=Math.max(height*1.08,Math.min(520,maxHeight*.82));
  const w=Math.min(desiredWidth,maxWidth,maxHeight*ratio,heightLimit*ratio);
  return {width:w,height:w/ratio};
}

// Insert space around one enlarged image. Every other card keeps its base size.
function mokuLayout({columns, rows, count=columns*rows, cellWidth, cellHeight, gap, focusCol, focusRow, centerX, centerY, imageRatio, maxAspect=2.5, bounds=null}) {
  const ratio=Math.max(1/maxAspect,Math.min(maxAspect,imageRatio));
  const {width:photoWidth,height:photoHeight}=mokuPreviewSize({width:cellWidth-2,height:cellHeight-54,ratio,
    ...(bounds?{maxWidth:bounds.right-bounds.left-2,maxHeight:bounds.bottom-bounds.top-54}:{})});
  if(bounds){
    centerX=Math.min(Math.max(centerX,bounds.left+photoWidth/2+1),bounds.right-photoWidth/2-1);
    centerY=Math.min(Math.max(centerY,bounds.top+photoHeight/2+1),bounds.bottom-photoHeight/2-53);
  }
  const defaultLeft=centerX-cellWidth/2;
  const defaultTop=centerY-(cellHeight-54)/2-1;
  const focus={left:centerX-photoWidth/2-1,top:centerY-photoHeight/2-1,width:photoWidth+2,height:photoHeight+54};
  const cards=Array.from({length:count},(_,index)=>{
    const col=index%columns,row=Math.floor(index/columns),selected=col===focusCol && row===focusRow;
    if(selected)return {...focus};
    const left=col<focusCol
      ? focus.left-gap-cellWidth-(focusCol-col-1)*(cellWidth+gap)
      : col>focusCol
        ? focus.left+focus.width+gap+(col-focusCol-1)*(cellWidth+gap)
        : defaultLeft;
    const top=row<focusRow
      ? focus.top-gap-cellHeight-(focusRow-row-1)*(cellHeight+gap)
      : row>focusRow
        ? focus.top+focus.height+gap+(row-focusRow-1)*(cellHeight+gap)
        : defaultTop;
    return {left,top,width:cellWidth,height:cellHeight};
  });
  return {cards,crop:ratio!==imageRatio,ratio,photoWidth,photoHeight,
    bottom:Math.max(...cards.map(card=>card.top+card.height))};
}
if(typeof module!=="undefined"){module.exports=mokuLayout;module.exports.previewSize=mokuPreviewSize;}
