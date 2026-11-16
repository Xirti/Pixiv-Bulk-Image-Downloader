"use strict";

// Page choices are machine preferences. Pending writes survive settings repaint.
globalThis.createBrowseOptions = function ({ready,save,report}) {
  const keys={tags:"orderTags",author:"orderAuthor",uid:"orderUid",pid:"orderPid",recommended:"orderRecommended",ranking:"orderRanking"};
  const orders=Object.fromEntries(Object.keys(keys).map(kind=>[kind,"default"])),pending=new Map();
  let automatic=false,showButton=true,writes=Promise.resolve();
  return {
    ready,
    automatic:()=>automatic,
    buttonVisible:()=>!automatic || showButton,
    order:kind=>orders[kind] || "default",
    configure(settings,loaded){
      if(!loaded)return;
      automatic=settings.autoLoadPages;
      showButton=settings.showLoadButton;
      for(const [kind,key] of Object.entries(keys))orders[kind]=pending.get(key)?.order ?? settings[key];
    },
    choose(kind,order){
      const key=keys[kind];
      if(!key || orders[kind]===order)return;
      // Equal values can belong to different clicks, including popular/default/popular.
      const choice={order};
      orders[kind]=order;pending.set(key,choice);
      writes=writes.then(async()=>{
        await ready();
        if(pending.get(key)!==choice)return;
        await save({[key]:order});
        if(pending.get(key)===choice)pending.delete(key);
      }).catch(error=>report(`浏览方式暂未保存：${error.message}`));
    },
  };
};
