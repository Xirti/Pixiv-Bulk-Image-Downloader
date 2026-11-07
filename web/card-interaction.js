"use strict";
// Own the short pending action so a double click cannot commit a single click.
function createCardInteraction({interval, single, double, schedule=setTimeout, unschedule=clearTimeout}) {
  let pending = null;
  function cancel() {
    if (pending) unschedule(pending.timer);
    pending = null;
  }
  return {
    click(target, point) {
      if (pending?.target === target) {
        cancel();
        double(target);
        return;
      }
      cancel();
      pending = {target, timer:schedule(() => {
        pending = null;
        single(target, point);
      }, interval())};
    },
    cancel,
    waiting:()=>pending!==null
  };
}
if (typeof module !== "undefined") module.exports = createCardInteraction;
