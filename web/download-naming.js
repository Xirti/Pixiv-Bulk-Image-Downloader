"use strict";

globalThis.downloadNaming = (() => {
  const fieldLabels = {author: "作者名", uid: "作者 UID", pid: "作品 PID", date: "发布日期", title: "标题", page: "页码"};
  const standard = {separator: "_", dateFormat: "YYYY-MM-DD", pageWidth: 3};
  const builtins = [
    {id: "legacy", label: "标题＋PID＋页码（旧版）", rule: null},
    {id: "pid-title", label: "PID＋标题＋页码", rule: {...standard, fields: ["pid", "title", "page"]}},
    {id: "pid-page", label: "PID＋页码", rule: {...standard, fields: ["pid", "page"]}},
    {id: "title-pid", label: "标题＋PID＋页码", rule: {...standard, fields: ["title", "pid", "page"]}},
    {id: "author", label: "作者名＋作者 UID＋PID＋页码", rule: {...standard, fields: ["author", "uid", "pid", "page"]}},
    {id: "date", label: "发布日期＋标题＋PID＋页码", rule: {...standard, fields: ["date", "title", "pid", "page"]}},
  ];
  const defaults = {defaultId: "legacy", visible: builtins.map(preset => preset.id), custom: []};
  const sample = {id: "123456789", title: "雨后街道", artist: "青空", userId: "246810", date: "2026-11-24", pages: 12};
  const cleanText = value => String(value || "").replace(/[<>:"/\\|?*\u0000-\u001f\uD800-\uDFFF]+/gu, "_")
    .replace(/_+/g, "_").replace(/^[ ._]+|[ ._]+$/g, "");
  const cleanPart = (value, limit, fallback) => cleanText(value).slice(0, limit)
    .replace(/[\uD800-\uDBFF]$/, "").replace(/[ ._]+$/g, "") || fallback;
  const avoidWindowsDeviceName = stem => /^(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])$/i
    .test(stem.split(".", 1)[0].replace(/ +$/, "")) ? "_" + stem : stem;

  function preview(rule, item = sample, page = 0, format = "source") {
    const animation = item.workType === "ugoira";
    const extension = animation ? (format === "source" ? ".zip" : "." + format) : item === sample ? ".jpg" : "";
    if (!rule) {
      const title = Array.from(cleanText(item.title)).slice(0, 100).join("") || "pixiv";
      const stem = title + "_" + item.id + "_" + (animation ? "ugoira" : "p" + page);
      return avoidWindowsDeviceName(stem) + extension;
    }
    let date = item.date || "日期未知";
    if (rule.dateFormat === "YYYYMMDD") date = date.replaceAll("-", "");
    if (rule.dateFormat === "YYYY/MM/DD") date = date.replaceAll("-", "／");
    const uid = String(item.userId || "");
    const pageLabel = animation ? "ugoira" : "p" + String(page + 1).padStart(Math.max(rule.pageWidth, String(item.pages).length), "0");
    const values = {
      author: cleanPart(item.artist, 50, "未知画师"),
      uid: /^[0-9]{1,30}$/.test(uid) ? uid : "未知UID",
      pid: item.id,
      date,
      title: cleanPart(item.title, 90, "未命名作品"),
      page: pageLabel,
    };
    const parts = rule.fields.map(field => values[field]);
    if (!rule.fields.includes("page") && (animation || item.pages > 1)) parts.push(pageLabel);
    return avoidWindowsDeviceName(parts.join(rule.separator)) + extension;
  }

  function editor(root, changed) {
    const list = root.querySelector("[data-naming-presets]");
    const count = root.querySelector("[data-naming-count]");
    const defaultPreset = root.querySelector("[data-naming-default]");
    const add = root.querySelector("[data-naming-add]");
    const form = root.querySelector("[data-naming-form]");
    const name = root.querySelector("[data-naming-label]");
    const fieldGroup = root.querySelector("[data-naming-fields]");
    const separator = root.querySelector("[data-naming-separator]");
    const date = root.querySelector("[data-naming-date]");
    const width = root.querySelector("[data-naming-width]");
    const example = root.querySelector("[data-naming-example]");
    const message = root.querySelector("[data-naming-message]");
    const remove = root.querySelector("[data-naming-remove]");
    let config = defaults, editing = null;
    let fields = [], originalDraft = "";
    const fieldButtons = Object.entries(fieldLabels).map(([field, label]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.namingField = field;
      const order = document.createElement("span");
      order.setAttribute("aria-hidden", "true");
      button.append(order, label);
      button.onclick = () => {
        fields = fields.includes(field) ? fields.filter(value => value !== field) : [...fields, field];
        paintFields();
      };
      fieldGroup.append(button);
      return {field, button, order};
    });

    function apply(next) {
      config = next;
      render();
      changed();
    }

    function start(preset = null) {
      editing = preset || {};
      const rule = preset?.rule || {...standard, fields: []};
      name.value = preset?.label || "";
      fields = [...rule.fields];
      separator.value = rule.separator;
      date.value = rule.dateFormat;
      width.value = rule.pageWidth;
      originalDraft = draftValue();
      remove.hidden = !preset;
      remove.disabled = preset?.id === config.defaultId;
      form.hidden = false;
      paintFields();
      name.focus();
    }

    function end() {
      editing = null;
      form.hidden = true;
    }

    function render() {
      const focused = list.contains(document.activeElement) ? document.activeElement : null;
      const all = [...builtins, ...config.custom];
      defaultPreset.replaceChildren(...all.map(preset => new Option(preset.label, preset.id)));
      defaultPreset.value = config.defaultId;
      let focusTarget;
      list.replaceChildren(...all.map(preset => {
        const {id, label, rule} = preset;
        const row = document.createElement("div");
        row.className = "naming-preset-row";
        const copy = document.createElement("label");
        copy.className = "settings-copy";
        copy.htmlFor = "naming-visible-" + id;
        const title = document.createElement("b");
        title.textContent = label;
        const sampleName = document.createElement("small");
        sampleName.textContent = preview(rule);
        copy.append(title, sampleName);
        const visibility = document.createElement("input");
        visibility.type = "checkbox";
        visibility.id = copy.htmlFor;
        visibility.value = id;
        visibility.setAttribute("aria-label", "在下载选项中显示 " + label);
        visibility.checked = config.visible.includes(id);
        visibility.disabled = id === config.defaultId;
        visibility.onchange = () => apply({...config, visible: visibility.checked
          ? [...config.visible, id] : config.visible.filter(key => key !== id)});
        if (focused?.value === id && focused.type === "checkbox") focusTarget = visibility;
        row.append(copy);
        if (id.startsWith("custom-")) {
          const edit = document.createElement("button");
          edit.type = "button";
          edit.value = id;
          edit.textContent = "编辑";
          edit.setAttribute("aria-label", "编辑 " + label);
          edit.onclick = () => start(preset);
          if (focused?.value === id && focused.type === "button") focusTarget = edit;
          row.append(edit);
        }
        row.append(visibility);
        return row;
      }));
      count.textContent = `${config.visible.length} / ${all.length} 个预设`;
      add.disabled = config.custom.length >= 12;
      remove.disabled = editing?.id === config.defaultId;
      if (focused) (focusTarget && !focusTarget.disabled ? focusTarget : defaultPreset).focus();
    }

    function ruleFromForm() {
      return {fields, separator: separator.value, dateFormat: date.value, pageWidth: Number(width.value)};
    }

    function draftValue() {
      return JSON.stringify([name.value.trim(), ruleFromForm()]);
    }

    function paintFields() {
      for (const {field, button, order} of fieldButtons) {
        const position = fields.indexOf(field) + 1;
        button.setAttribute("aria-pressed", String(position > 0));
        order.textContent = position || "";
      }
      paintExample();
    }

    function paintExample() {
      example.textContent = fields.length ? preview(ruleFromForm()) : "请选择命名字段";
      message.textContent = fields.length && !fields.includes("pid") ? "省略作品 PID 时，不同作品可能同名。" : "";
      date.disabled = !fields.includes("date");
    }

    defaultPreset.onchange = () => {
      const id = defaultPreset.value;
      apply({...config, defaultId: id, visible: config.visible.includes(id) ? config.visible : [...config.visible, id]});
    };
    add.onclick = () => start();
    root.querySelector("[data-naming-cancel]").onclick = () => { end(); defaultPreset.focus(); };
    remove.onclick = () => {
      const {id} = editing;
      end();
      apply({...config, visible: config.visible.filter(key => key !== id), custom: config.custom.filter(row => row.id !== id)});
      defaultPreset.focus();
    };
    for (const control of [separator, date, width]) control.onchange = paintExample;
    function applyDraft() {
      const label = name.value.trim();
      if (!label || !fields.length) {
        message.textContent = !label ? "请输入预设名称。" : "请至少选择一个命名字段。";
        (label ? fieldButtons[0].button : name).focus();
        return false;
      }
      const id = editing.id || "custom-" + crypto.randomUUID().replaceAll("-", "");
      const exists = Boolean(editing.id);
      const preset = {id, label, rule: ruleFromForm()};
      end();
      apply({...config,
        custom: exists ? config.custom.map(row => row.id === id ? preset : row) : [...config.custom, preset],
        visible: exists ? config.visible : [...config.visible, id],
      });
      defaultPreset.focus();
      return true;
    }
    root.querySelector("[data-naming-apply]").onclick = applyDraft;
    render();
    return {
      value: () => config,
      get editing() { return editing !== null; },
      get dirty() { return editing !== null && draftValue() !== originalDraft; },
      applyDraft,
      update(next) {
        if (JSON.stringify(config) !== JSON.stringify(next)) {
          config = next;
          render();
        }
      },
      reset: end,
    };
  }

  return {defaults, presets: config => [...builtins, ...config.custom], preview, editor};
})();
