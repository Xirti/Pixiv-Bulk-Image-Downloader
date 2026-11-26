"use strict";

globalThis.downloadNaming = (() => {
  const labels = {author: "作者名", uid: "作者 UID", pid: "作品 PID", date: "发布日期", title: "作品标题", page: "页码"};
  const standard = {separator: "_", dateFormat: "YYYY-MM-DD", pageWidth: 3};
  const builtins = [
    {id: "legacy", label: "现有命名", rule: null},
    {id: "pid-title", label: "PID＋标题＋页码", rule: {...standard, fields: ["pid", "title", "page"]}},
    {id: "pid-page", label: "PID＋页码", rule: {...standard, fields: ["pid", "page"]}},
    {id: "title-pid", label: "标题＋PID＋页码", rule: {...standard, fields: ["title", "pid", "page"]}},
    {id: "author", label: "按作者归档", rule: {...standard, fields: ["author", "uid", "pid", "page"]}},
    {id: "date", label: "按发布日期排列", rule: {...standard, fields: ["date", "title", "pid", "page"]}},
  ];
  const defaults = {defaultId: "legacy", visible: builtins.map(preset => preset.id), custom: []};
  const sample = {id: "123456789", title: "雨后街道", artist: "青空", userId: "246810", date: "2026-11-24", pages: 12};
  const cleanText = value => String(value || "").replace(/[<>:"/\\|?*\u0000-\u001f\uD800-\uDFFF]+/gu, "_")
    .replace(/_+/g, "_").replace(/^[ ._]+|[ ._]+$/g, "");
  const cleanPart = (value, limit, fallback) => cleanText(value).slice(0, limit)
    .replace(/[\uD800-\uDBFF]$/, "").replace(/[ ._]+$/g, "") || fallback;

  function preview(rule, item = sample, page = 0, format = "source") {
    const animation = item.workType === "ugoira";
    const extension = animation ? (format === "source" ? ".zip" : "." + format) : item === sample ? ".jpg" : "";
    if (!rule) {
      const title = Array.from(cleanText(item.title)).slice(0, 100).join("") || "pixiv";
      return title + "_" + item.id + "_" + (animation ? "ugoira" : "p" + page) + extension;
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
    let stem = parts.join(rule.separator);
    if (/^(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])$/i.test(stem.split(".", 1)[0].replace(/ +$/, ""))) stem = "_" + stem;
    return stem + extension;
  }

  function editor(root, changed) {
    const list = root.querySelector("[data-naming-presets]");
    const count = root.querySelector("[data-naming-count]");
    const add = root.querySelector("[data-naming-add]");
    const form = root.querySelector("[data-naming-form]");
    const name = root.querySelector("[data-naming-label]");
    const chosen = root.querySelector("[data-naming-chosen]");
    const available = root.querySelector("[data-naming-available]");
    const separator = root.querySelector("[data-naming-separator]");
    const date = root.querySelector("[data-naming-date]");
    const width = root.querySelector("[data-naming-width]");
    const example = root.querySelector("[data-naming-example]");
    const message = root.querySelector("[data-naming-message]");
    let config = defaults, editing = null, fields = [];

    function button(text, action, title = text) {
      const control = document.createElement("button");
      control.type = "button";
      control.textContent = text;
      control.title = title;
      control.setAttribute("aria-label", title);
      control.onclick = action;
      return control;
    }

    function apply(next) {
      config = next;
      render();
      changed();
    }

    function move(items, from, offset) {
      const next = [...items];
      [next[from], next[from + offset]] = [next[from + offset], next[from]];
      return next;
    }

    function start(preset = null) {
      editing = preset || {};
      const rule = preset?.rule || {...standard, fields: ["pid", "title", "page"]};
      name.value = preset?.label || "";
      fields = [...rule.fields];
      separator.value = rule.separator;
      date.value = rule.dateFormat;
      width.value = rule.pageWidth;
      form.hidden = false;
      paintFields();
      name.focus();
    }

    function end() {
      editing = null;
      form.hidden = true;
    }

    function render() {
      const focused = list.contains(document.activeElement) ? document.activeElement.getAttribute("aria-label") : null;
      const all = [...builtins, ...config.custom];
      const ids = [...config.visible, ...all.map(preset => preset.id).filter(id => !config.visible.includes(id))];
      list.replaceChildren(...ids.map(id => {
        const preset = all.find(row => row.id === id);
        const index = config.visible.indexOf(id);
        const isDefault = id === config.defaultId;
        const row = document.createElement("div");
        row.className = "naming-preset-row";
        const title = document.createElement("span");
        title.className = "naming-pill";
        title.textContent = preset.label + (isDefault ? " · 默认" : "");
        title.title = preview(preset.rule);
        const visibility = button(index < 0 ? "显示" : "隐藏", () => apply({...config,
          visible: index < 0 ? [...config.visible, id] : config.visible.filter(key => key !== id),
        }), (index < 0 ? "显示 " : "隐藏 ") + preset.label);
        visibility.disabled = isDefault;
        row.append(title, visibility);
        if (!isDefault) row.append(button("设为默认", () => apply({...config, defaultId: id,
          visible: index < 0 ? [...config.visible, id] : config.visible,
        }), "设为默认 " + preset.label));
        if (index >= 0) {
          const shift = offset => apply({...config, visible: move(config.visible, index, offset)});
          const up = button("↑", () => shift(-1), "上移 " + preset.label);
          const down = button("↓", () => shift(1), "下移 " + preset.label);
          up.disabled = index === 0;
          down.disabled = index === config.visible.length - 1;
          row.append(up, down);
        }
        if (id.startsWith("custom-")) {
          row.append(button("编辑", () => start(preset), "编辑 " + preset.label));
          const remove = button("删除", () => {
            if (editing?.id === id) end();
            apply({...config, visible: config.visible.filter(key => key !== id), custom: config.custom.filter(row => row.id !== id)});
          }, "删除 " + preset.label);
          remove.disabled = isDefault;
          row.append(remove);
        }
        return row;
      }));
      count.textContent = "下拉框显示 " + config.visible.length + " / " + all.length + " 个预设";
      add.disabled = config.custom.length >= 12;
      if (focused) {
        const buttons = [...list.querySelectorAll("button:not(:disabled)")];
        (buttons.find(control => control.getAttribute("aria-label") === focused) || buttons[0] || add).focus();
      }
    }

    const ruleFromForm = () => ({fields: [...fields], separator: separator.value, dateFormat: date.value, pageWidth: Number(width.value)});

    function paintExample() {
      example.textContent = fields.length ? preview(ruleFromForm()) : "请添加命名字段";
      message.textContent = fields.length && !fields.includes("pid") ? "省略作品 PID 时，不同作品可能同名。" : "";
      date.disabled = !fields.includes("date");
      width.disabled = !fields.length;
    }

    function paintFields() {
      const active = document.activeElement;
      const focused = chosen.contains(active) || available.contains(active) ? active.getAttribute("aria-label") : null;
      chosen.replaceChildren(...fields.map((key, index) => {
        const item = document.createElement("span");
        item.className = "naming-field-pill";
        const shift = offset => { fields = move(fields, index, offset); paintFields(); };
        const left = button("←", () => shift(-1), "左移 " + labels[key]);
        const right = button("→", () => shift(1), "右移 " + labels[key]);
        left.disabled = index === 0;
        right.disabled = index === fields.length - 1;
        item.append(labels[key], left, right, button("×", () => {
          fields = fields.filter(field => field !== key);
          paintFields();
        }, "移除 " + labels[key]));
        return item;
      }));
      available.replaceChildren(...Object.keys(labels).filter(key => !fields.includes(key)).map(key =>
        button("+ " + labels[key], () => { fields = [...fields, key]; paintFields(); }, "添加 " + labels[key])));
      paintExample();
      if (focused) {
        const controls = [...form.querySelectorAll(".naming-field-list button:not(:disabled)")];
        (controls.find(control => control.getAttribute("aria-label") === focused)
          || controls.find(control => control.getAttribute("aria-label").endsWith(focused.slice(3)))
          || controls[0] || name).focus();
      }
    }

    add.onclick = () => start();
    root.querySelector("[data-naming-cancel]").onclick = end;
    for (const control of [separator, date, width]) control.onchange = paintExample;
    root.querySelector("[data-naming-apply]").onclick = () => {
      const label = name.value.trim();
      if (!label || !fields.length) {
        message.textContent = !label ? "请输入预设名称。" : "请至少添加一个命名字段。";
        if (!label) name.focus();
        return;
      }
      const id = editing.id || "custom-" + crypto.randomUUID().replaceAll("-", "");
      const exists = Boolean(editing.id);
      const preset = {id, label, rule: ruleFromForm()};
      end();
      apply({...config,
        custom: exists ? config.custom.map(row => row.id === id ? preset : row) : [...config.custom, preset],
        visible: exists ? config.visible : [...config.visible, id],
      });
      add.focus();
    };
    render();
    return {
      value: () => config,
      get editing() { return editing !== null; },
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
