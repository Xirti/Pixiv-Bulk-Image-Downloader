"use strict";
// Partition contiguous works into rows. Metadata fixes the geometry before decoding.
globalThis.computeAdaptiveLayout=function(rows,width,{target=240,gap=12,minWidth=120,maxWidth=560,maxHeight=380}={}){
  if(width<=0)return {boxes:[],height:0};
  const ratios=rows.map(item=>Math.max(.4,Math.min(2.5,item.width>0 && item.height>0?item.width/item.height:1)));
  minWidth=Math.min(minWidth,width*.18);maxWidth=Math.min(maxWidth,width);
  const costs=Array(rows.length+1).fill(Infinity),choices=[];costs[rows.length]=0;
  for(let start=rows.length-1;start>=0;start--){
    let sum=0,low=100,high=maxHeight;
    for(let count=1;count<=Math.min(8,rows.length-start);count++){
      const ratio=ratios[start+count-1];sum+=ratio;low=Math.max(low,minWidth/ratio);high=Math.min(high,maxWidth/ratio);
      if(low>high)continue;
      const available=width-gap*(count-1),fill=available/sum,last=start+count===rows.length;
      const height=Math.min(high,last?Math.min(fill,target):fill);
      if(height<low || available<1)continue;
      const unused=(width-height*sum-gap*(count-1))/width;
      const cost=Math.pow(Math.log(height/target),2)+unused*unused*(last?1.2:9)+.08+costs[start+count];
      if(cost<costs[start]){costs[start]=cost;choices[start]={count,height};}
    }
    if(!choices[start]){choices[start]={count:1,height:Math.min(maxHeight,maxWidth/ratios[start],Math.max(minWidth/ratios[start],target))};costs[start]=10+costs[start+1];}
  }
  const boxes=[];let y=0;
  for(let i=0;i<rows.length;){const choice=choices[i];let x=0;for(let n=0;n<choice.count;n++){const w=choice.height*ratios[i+n];boxes.push({x,y,width:w,height:choice.height});x+=w+gap;}y+=choice.height+gap;i+=choice.count;}
  return {boxes,height:Math.max(0,y-gap)};
};
if(typeof module!=="undefined")module.exports=globalThis.computeAdaptiveLayout;
