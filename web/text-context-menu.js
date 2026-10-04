/* One lightweight text menu; clipboard access occurs only on an explicit action. */
(() => {
  const menu = document.createElement("div");
  menu.id = "textContextMenu";
  menu.className = "text-context-menu";
  menu.setAttribute("role", "menu");
  menu.setAttribute("aria-label", "文字操作");
  menu.hidden = true;
  const actions = new Map();
  for (const [command, label, shortcut] of [["cut", "剪切", "Ctrl+X"], ["copy", "复制", "Ctrl+C"], ["paste", "粘贴", "Ctrl+V"], ["selectAll", "全选", "Ctrl+A"]]) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.textCommand = command;
    button.setAttribute("role", "menuitem");
    button.append(document.createTextNode(label));
    const hint = document.createElement("small");
    hint.textContent = shortcut;
    button.append(hint);
    button.onclick = () => run(command);
    menu.append(button);
    actions.set(command, button);
  }
  document.body.append(menu);
  const notice = document.createElement("div");
  notice.id = "textMenuNotice";
  notice.className = "text-menu-notice";
  notice.setAttribute("role", "status");
  notice.hidden = true;
  document.body.append(notice);
  let context = null, generation = 0, noticeTimer = 0;

  function capture(target) {
    if (!(target instanceof Element) || target.closest("[inert],[hidden]")) return null;
    const field = target.closest("input,textarea");
    if (field && !field.matches("input[type=checkbox],input[type=radio],input[type=button],input[type=submit],input[type=range],input[type=color],input[type=file]")) {
      const ranged = typeof field.selectionStart === "number";
      const start = ranged ? field.selectionStart : 0, end = ranged ? field.selectionEnd : field.value.length;
      return {field, node:field, ranged, start, end, value:field.value, text:field.value.slice(start, end), writable:!field.disabled && !field.readOnly, focus:document.activeElement};
    }
    if (target.closest("button,select,svg,img,canvas")) return null;
    const selection = getSelection();
    const node = target.closest("p,h1,h2,h3,h4,dd,dt,code,span,label,a,b,small,strong");
    const selected = selection?.rangeCount && selection.getRangeAt(0).intersectsNode(target) ? selection.toString() : "";
    const text = selected || node?.textContent.trim();
    return text ? {node:node || target, text, range:selected && selection.rangeCount ? selection.getRangeAt(0).cloneRange() : null, writable:false, focus:document.activeElement} : null;
  }

  function restore(current) {
    if (current.field) {
      current.field.focus({preventScroll:true});
      if (current.ranged) current.field.setSelectionRange(current.start, current.end);
    } else {
      current.focus?.focus?.({preventScroll:true});
      if (current.range) { const selection = getSelection(); selection.removeAllRanges(); selection.addRange(current.range); }
    }
  }

  function close(refocus = false) {
    const current = context;
    context = null;
    menu.hidden = true;
    generation++;
    if (refocus && current?.node.isConnected) restore(current);
  }

  function open(current, x, y) {
    close();
    context = current;
    (current.node.closest("dialog[open]") || document.body).append(menu);
    actions.get("cut").disabled = !current.writable || !current.text;
    actions.get("copy").disabled = !current.text;
    actions.get("paste").disabled = !current.writable;
    actions.get("selectAll").disabled = current.field ? !current.ranged || current.field.disabled || !current.value : !current.node.textContent;
    menu.hidden = false;
    const box = menu.getBoundingClientRect();
    menu.style.left = `${Math.max(8, Math.min(x, innerWidth - box.width - 8))}px`;
    menu.style.top = `${Math.max(8, Math.min(y, innerHeight - box.height - 8))}px`;
    [...actions.values()].find(button => !button.disabled)?.focus({preventScroll:true});
  }

  async function clipboard(command, text) {
    const api = globalThis.pywebview?.api;
    const method = command === "read" ? "read_clipboard" : "write_clipboard";
    if (typeof api?.[method] === "function") {
      const result = await api[method](...(command === "read" ? [] : [text]));
      if (result?.ok !== true || (command === "read" && typeof result.text !== "string")) throw new Error("clipboard unavailable");
      return result.text;
    }
    if (command === "read") return navigator.clipboard.readText();
    return navigator.clipboard.writeText(text);
  }

  function replace(current, text, cutting) {
    const field = current.field;
    restore(current);
    if (current.ranged) {
      if (field.maxLength >= 0) text = text.slice(0, Math.max(0, field.maxLength - (field.value.length - current.end + current.start)));
      if (document.execCommand(cutting ? "delete" : "insertText", false, text)) return;
      field.setRangeText(text, current.start, current.end, "end");
    } else {
      const old = field.value;
      field.value = text.trim();
      // Custom validation still describes the old value until input/change runs.
      const invalid = !field.value || ["rangeUnderflow", "rangeOverflow", "stepMismatch", "badInput", "typeMismatch", "patternMismatch", "tooLong", "tooShort", "valueMissing"].some(flag => field.validity[flag]);
      if (text && invalid) { field.value = old; throw new Error("invalid field value"); }
    }
    field.dispatchEvent(new Event("input", {bubbles:true}));
    if (!current.ranged) field.dispatchEvent(new Event("change", {bubbles:true}));
  }

  async function run(command) {
    const current = context;
    if (!current || actions.get(command).disabled) return;
    close(true);
    const token = generation;
    try {
      if (command === "selectAll") {
        if (current.field) current.field.select();
        else { const range = document.createRange(); range.selectNodeContents(current.node); const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range); }
        return;
      }
      if (command !== "paste") await clipboard("write", current.text);
      const text = command === "paste" ? await clipboard("read") : "";
      if (command === "copy") return;
      if (token !== generation || !current.field.isConnected || current.field.closest("[hidden],[inert]") || current.field.disabled || current.field.readOnly || current.field.value !== current.value || document.activeElement !== current.field) return;
      if (current.ranged && (current.field.selectionStart !== current.start || current.field.selectionEnd !== current.end)) return;
      if (command === "paste" && !text) return;
      replace(current, text, command === "cut");
    } catch {
      if (token !== generation) return;
      const host = current.node.closest("dialog[open]") || document.body;
      host.append(notice);
      notice.textContent = "操作未完成，请检查粘贴内容或重试，也可使用键盘快捷键。";
      notice.hidden = false;
      clearTimeout(noticeTimer);
      noticeTimer = setTimeout(() => { notice.hidden = true; }, 4000);
    }
  }

  document.addEventListener("contextmenu", event => {
    const current = capture(event.target);
    if (!current) { close(); return; }
    event.preventDefault();
    event.stopPropagation();
    open(current, event.clientX, event.clientY);
  }, true);
  document.addEventListener("pointerdown", event => { if (!menu.contains(event.target)) close(); }, true);
  window.addEventListener("scroll", () => close(true), true);
  window.addEventListener("resize", () => close(true));
  window.addEventListener("blur", () => close());
  window.addEventListener("keydown", event => {
    if (menu.hidden) {
      if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) {
        const current = capture(document.activeElement);
        if (current) { event.preventDefault(); event.stopImmediatePropagation(); const box = current.node.getBoundingClientRect(); open(current, box.left, box.bottom); }
      }
      return;
    }
    if (["Escape", "Tab", "ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault(); event.stopImmediatePropagation();
      if (event.key === "Escape" || event.key === "Tab") { close(true); return; }
      const enabled = [...actions.values()].filter(button => !button.disabled);
      const index = enabled.indexOf(document.activeElement);
      const next = event.key === "Home" ? 0 : event.key === "End" ? enabled.length - 1 : (index + (event.key === "ArrowUp" ? -1 : 1) + enabled.length) % enabled.length;
      enabled[next]?.focus();
    }
  }, true);
})();
