# Lens Design System

一套可复制、可扩展的原生 Web 设计系统。默认「厚润 / Thick」，包含真实背景折射、弹性手势、18 类语义化控件和 13 种原创合成交互音效。运行时无依赖、无 CDN、无音频下载。

所有固定名称和展示入口见 [组件清单](COMPONENTS.md)，页面入口为 `examples/optics.html#component-index`。`componentCatalog` 从公共入口导出中英文名称、工厂标识和示例参数；新增组件时同步这份清单。

系统范围与复用规则见 [SCOPE.md](SCOPE.md)，核心术语见 [CONTEXT.md](CONTEXT.md)：所有视觉内容拆解为背景、图标（文字）、透镜、凹槽、发光效果、液体、反光变体；所有交互由复用的透镜组件承载，材质反馈通过光、反光和液体表达。公共组件名称描述用途，不是七类之外的新元素。该规则是后续准入与对齐标准，当前实现差距已在 SCOPE 单列。

当前版本 `1.1.0-showcase.1` 在 `1.0.0` 之上新增的内容及 API 见 [光学交互说明](INTERACTIONS.md) 和 [完整展示页说明](SHOWCASE.md)。

A portable native-web system for glass materials, elastic interaction and quiet sound. Semantic HTML remains responsible for input, focus and accessible state. No runtime dependencies.

## Run / 运行

从随附 `lab` 项目执行 `npm start`，打开 `http://127.0.0.1:4173/design-system/index.html`。目录可切换中文 / EN；独立集成示例在 `examples/basic.html`，完整展示页在 `examples/optics.html`，包含材质、控件、声音、光学交互、图表和表格。

也可把整个 `design-system/` 文件夹复制到任何静态网站。通过 HTTP 提供 ES modules；直接双击模块页面会受到浏览器的 `file:` 导入限制。材质实验室和完整展示页各附中英文单文件 HTML，构建时内联全部运行代码。

## Install / 接入

```html
<link rel="stylesheet" href="./design-system/tokens.css">
<link rel="stylesheet" href="./design-system/components.css">
<div id="stage" style="min-height:280px;padding:32px"></div>
<script type="module">
  import {createComponent, createGlassSystem} from './design-system/index.js';
  const stage = document.querySelector('#stage');
  const focus = createComponent('switch', {
    label: 'Focus', checked: false,
    onChange: checked => console.log(checked)
  });
  stage.append(focus.element);
  const system = createGlassSystem(stage); // Thick, quiet sound, no idle movement
  // On application unmount:
  // system.destroy(); focus.destroy();
</script>
```

也可用 `npm install ./design-system` 作为本地 ESM 包安装，导入 `@lens/design-system` 及其两个 CSS 入口。包自带 `index.d.ts`，类型化示例见 `examples/typed.ts`；没有 React、Vue 或构建工具绑定。框架组件在 effect / mounted 中创建，在 cleanup / unmounted 中销毁。

## Layers / 组织

| 文件 | 职责 |
| --- | --- |
| `tokens.js` | 不可变的颜色、字号、间距、圆角、材质、动效和音量规则，唯一变量来源 |
| `tokens.css`, `tokens.json` | 由 `node build-tokens.mjs` 生成，供样式和其他工具使用 |
| `components.css` | `lg-` 命名空间的材质、控件、选择、禁用、焦点和兼容状态 |
| `components.js` | 组件工厂与可增强既有 HTML 的开关、胶囊行为 |
| `card-list.js` | 横向透镜卡片列表、原生滚动、吸附、导航与子组件生命周期 |
| `component-patterns.js` | 图标按钮、连接开关、分段标签、浮动导航和操作菜单 |
| `component-catalog.js` | 统一中英文名称、固定标识、展示页索引和使用示例 |
| `sound.js` | 原创 PCM 合成、共享音频上下文、节流、音量、静音和偏好存储 |
| `showcase.js`, `showcase.css`, `showcase-charts.js` | 完整展示页面与示例数据图表；可独立挂载、读取图表状态和卸载 |
| `internal/chart-glass.js` | 图表的彩色玻璃、液面、光源与真实刻度折射；DOM / SVG 保留语义与兼容显示 |
| `runtime.js` | WebGL、弹性和声音的统一挂载与销毁 |
| `internal/` | 渲染与手势实现；优先通过公共入口使用 |
| `index.html`, `examples/` | 实际运行上述组件的双语目录与最小接入示例 |

材质实验室也使用这一目录的渲染器、物理控制器、变量、声音、开关和胶囊行为。项目根部的 `glass.js`、`jelly.js`、`jelly-physics.js` 只是旧导入路径的兼容入口。页面特有的布局仍留在实验室 `style.css`。

## Components / 控件

调用 `createComponent(name, props)` 得到 `{element, destroy, ...}`。先把 `element` 插入 stage，再创建系统；后来增加的组件用 `system.mount(element)` 注册。可见文案和无障碍名称可以由调用者本地化，不能把标签作为业务值。组件的 `setValue()` 默认不触发业务回调；新光学控件在 `{emit:true}` 提交选择或完成进度时也发出对应的语义音效事件，由所在系统的音效设置决定是否播放。

| 名称 | 主要属性 | 返回值 / 行为 |
| --- | --- | --- |
| `button` | `label`, `variant`, `disabled`, `sound`, `onPress` | `setDisabled()`；原生按钮、按住拖动、语义音效 |
| `icon-button` | `label`, `icon`, `pressed`, `disabled`, `onChange`, `onPress` | `value`, `setValue()`, `setDisabled()`；传入 `pressed` 时可切换，保留可访问名称 |
| `toggle-tile` | `label`, `icon`, `checked`, `onLabel`, `offLabel`, `disabled`, `onChange` | `value`, `setValue()`, `setDisabled()`；连接状态、绿色反光、switch 语义 |
| `tabs` | `label`, `options:[{value,label,content,disabled}]`, `value`, `onChange` | `value`, `setValue()`；标签 / 面板关联、拖动玻璃选中层、方向键、Home / End |
| `dock` | `label`, `options:[{value,label,icon,disabled}]`, `value`, `onChange` | `value`, `setValue()`；图标导航、拖动选中层、键盘、`aria-current` |
| `menu` | `label`, `options:[{value,label,icon,disabled}]`, `onAction` | `open()`, `close()`；展开 / 收起动画，方向键跳过禁用项，Escape 回到触发器 |
| `switch` | `label`, `checked`, `onChange` | `value`, `setValue()`；点击、拖动、空格、左右与 Home/End |
| `choices` | `label`, `options:[{value,label,disabled}]`, `value`, `selectionColor`, `onChange` | `value`, `setValue()`；互斥选择、方向键、勾选、选中边缘变色与一次流光 |
| `slider` | `label`, `min`, `max`, `step`, `value`, `onChange` | `value`, `setValue()`；原生 range 语义和玻璃滑钮 |
| `stepper` | `label`, `min`, `max`, `step`, `value`, `unit`, `decreaseLabel`, `increaseLabel`, `onChange` | `value`, `setValue()`；边界按钮禁用，live output |
| `notification` | `title`, `description`, `dismissLabel`, `onDismiss` | `show()`；调用者在关闭 / 恢复后管理焦点 |
| `search` | `label`, `placeholder`, `onChange` | `value`, `setValue()`；原生 search，忽略输入法组合过程的声音 |
| `lens` | `label`, `caption` | 自由拖动、方向键、Home 居中；容器需有明确高度 |
| `panel` | `label`, `children:Node[]`, `motion:'tether'|'press'|'none'` | 组合插槽；默认拖动回弹，或仅按压／保持静态；不抢夺内部控件的操作 |
| `card-list` | `label`, `items:[{id,label,title?,description?,icon?,children?}]`, `previousLabel`, `nextLabel`, `emptyLabel`, `autoScroll`, `pauseLabel`, `playLabel` | `scrollTo(id,{behavior})`, `pause()`, `play()`, `playing`；横向原生滚动与吸附、可选自动循环、导航与子组件生命周期 |
| `progress` | `label`, `value`, `max`, `completionColor`, `wave`, `motion`, `onComplete` | `setValue()`, `setMotion()`, `setCompletionColor()`；已完成区域折射与光纤波动，完成时流光渐变为绿色或金色 |
| `drop-select` | `label`, `options:[{value,label,icon,disabled}]`, `variant`, `selectionColor`, `onChange` | `setValue()`, `setVariant()`, `setDisabled()`；松手吸附最近可用凹槽；玻璃到达后连续汇入，落点由凹变凸；点击、键盘与取消也沿当前姿态运动 |
| `xy-slider` | `label`, `xLabel`, `yLabel`, `value:{x,y}`, `variant`, `magnification`, `onChange`, `onCommit` | `setValue()`, `setVariant()`, `setDisabled()`；二维调节、普通触点 / 放大透镜，两轴原生 range 备选 |

用 `bindSwitch(existingButton, options)` 或 `bindChoiceGroup(existingGroup, options)` 增强已有语义 DOM。胶囊按钮使用 `data-choice="stable-id"`。实例销毁时先 `system.destroy()` 再销毁每个组件 handle；`mount()` 可重复调用而不会重复注册现有手势。

### Card list / 透镜卡片列表

`card-list` 组合现有面板、按钮、滑杆和进度组件。所有卡片默认使用 16:9 横版比例。每项 `id` 与 `label` 必须非空且 `id` 唯一；`title` 默认使用 `label`，传 `title:''` 可隐藏可见标题而保留卡片的无障碍名称。`icon` 接受调用者创建的扁平图标节点。`children` 可混合组件 handle、DOM 节点和文字：列表接管传入 handle 的销毁责任，原始 DOM 节点仍由调用者管理；同一个节点或 handle 不能放入多个卡片。

```html
<link rel="stylesheet" href="./design-system/tokens.css">
<link rel="stylesheet" href="./design-system/components.css">
<div id="cards" style="min-height:380px;padding:24px"></div>
<script type="module">
  import {createComponent, createGlassSystem} from './design-system/index.js';

  const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  icon.setAttribute('viewBox', '0 0 48 48');
  const tile = document.createElementNS(icon.namespaceURI, 'rect');
  for (const [key, value] of Object.entries({x:4, y:4, width:40, height:40, rx:10, fill:'#72c9b5'})) tile.setAttribute(key, value);
  const mark = document.createElementNS(icon.namespaceURI, 'path');
  for (const [key, value] of Object.entries({d:'M15 16h18v4H15zm0 7h14v4H15zm0 7h18v4H15z', fill:'#173f39'})) mark.setAttribute(key, value);
  icon.append(tile, mark);
  const brightness = createComponent('slider', {
    label: 'Brightness', value: 64, onChange: console.log
  });
  const progress = createComponent('progress', {
    label: 'Focus progress', value: 42, completionColor: 'gold'
  });
  const save = createComponent('button', {
    label: 'Save', sound: 'success', onPress: () => console.log('Saved')
  });
  const cards = createComponent('card-list', {
    label: 'Focus tools',
    previousLabel: 'Previous tools',
    nextLabel: 'Next tools',
    autoScroll: true,
    pauseLabel: 'Pause cards',
    playLabel: 'Play cards',
    items: [
      {id: 'adjust', label: 'Adjust brightness', icon, children: [brightness]},
      {id: 'session', label: 'Focus session', description: 'Keep the current rhythm.', children: [progress, save]}
    ]
  });
  const stage = document.querySelector('#cards');
  stage.append(cards.element);
  const system = createGlassSystem(stage);

  // true uses 24 px/s; a positive number selects another px/s speed.
  // cards.pause(); cards.play(); console.log(cards.playing);
  // cards.scrollTo('session', {behavior: 'smooth'});
  // On unmount, destroy the system first. cards.destroy() also destroys
  // brightness, progress and save because their handles were transferred.
  // system.destroy(); cards.destroy();
</script>
```

触控板、触摸和滚轮使用原生横向滚动与 scroll snap；鼠标或笔可以从卡片空白处拖动，触摸继续使用原生 pan。列表获得焦点时，方向键和 Home / End 移动卡片，嵌套控件处理自己的键盘和指针手势而不会带动列表。前后按钮使用相同导航。

`autoScroll` 默认为 `false`；`true` 使用每秒 24 CSS 像素，正数指定每秒像素速度。只有内容足够溢出、可让至少一张卡片完整离开视口时才启用无缝循环；少量内容保留普通手动滚动。循环移动原有卡片 DOM 与 handle，不创建克隆。播放／暂停控件复用公共 `button` 透镜。

`playing` 表示该列表当前的用户播放意愿。普通环境中，鼠标悬停、列表内键盘焦点、拖动、其他手动操作、页面隐藏或列表离屏只会暂时停止实际移动，不改变 `playing`；`pause()` 将其设为 `false`，`play()` 将其设为 `true`。如果列表初始化时系统已启用减弱动态，播放意愿从 `false` 开始，自动滚动保持静止，但播放按钮仍然可见；用户点击播放或调用 `play()` 可只为该列表主动开启动效。系统偏好之后每次切换为减弱动态都会再次暂停并清除这次主动选择。减弱动态下拖拽惯性始终关闭，编程滚动也强制使用 `auto`，即使调用者请求 `smooth`。`scrollTo()` 找不到 ID 或实例已销毁时返回 `false`。

## Keyboard focus / 键盘焦点

`createGlassSystem()` 自动为 stage 管理焦点框。Tab、方向键或其他非修饰键操作后显示；鼠标 / 触控板移动、按下、滚轮或双指滚动，以及触摸操作立即隐藏。再次使用键盘时恢复。只改变焦点提示样式，不调用 `blur()`，因此输入光标、选中值和当前 DOM 焦点都会保留。

要覆盖页面中 stage 之外的工具栏与链接，可显式绑定页面；同一文档共用事件监听，支持多个组件系统独立卸载：

```js
import {bindKeyboardFocus} from './design-system/index.js';
const unbindFocus = bindKeyboardFocus(document);
// On page/app unmount:
unbindFocus();
```

`data-lg-input="keyboard|pointer"` 由该服务维护。自定义控件可沿用这个状态显示自己的焦点代理元素；不要把实际选中态绑定到它。

## Material and theme / 材质与主题

```js
system.setMaterial('clear');
system.setMaterial({refraction: .78, blur: .18, dispersion: .32});
system.setScene('grid'); // aurora | sunset | ocean | grid
system.jelly.setIntensity(.55);
```

材质参数为 0–1，缺省值来自 Thick，越界有限数值会夹到合法范围，NaN 和未知预设会报错。

应用也可以提供自己的画布背景。回调绘制的像素会上传到场景纹理，玻璃会真实采样、折射这张背景；它不是叠在玻璃上方的装饰层。`width` 和 `height` 是纹理画布的像素尺寸，`dpr` 可用于把尺寸换算为 CSS 像素。回调会在首次创建、尺寸变化、WebGL 上下文恢复以及 `setScene()` 切换（或刷新同名场景）时再次调用。提供回调后不会绘制内置场景或 `hello.`；不提供时原有场景保持不变。

```js
const system = createGlassSystem(stage, {
  scene: 'aurora',
  artwork(ctx, {width, height, dpr, scene}) {
    ctx.fillStyle = scene === 'ocean' ? '#173c55' : '#d9e8df';
    ctx.fillRect(0, 0, width, height);
    ctx.fillStyle = '#24445c';
    ctx.font = `${24 * dpr}px sans-serif`;
    ctx.fillText('Custom sampled background', 24 * dpr, 48 * dpr);
  }
});

// Redraw after application state used by the callback changes.
system.setScene('aurora');
```

```css
.my-surface {
  --lg-radius-panel: 28px;
  --lg-color-focus: #235b85;
  --lg-font-control: 15px;
}
```

CSS 变量用于类型、布局、控件和 CSS 兼容材质。WebGL 中的折射 / 雾化 / 色散由 `setMaterial()` 设置；不要以为只改 CSS 背景色就会改变 GPU 光学。

## Sound / 音效

默认音量 28%，不自动播放，不响应 hover，不循环背景音。声音只补充视觉反馈。首次操作时才创建 AudioContext；浏览器不支持或拒绝音频时，控件继续工作。音效偏好使用 `lens.sound.v1`，受限存储会自动退回内存。传 `audio:{storage:null}` 可关闭偏好持久化。

```js
system.sound.set({enabled:false});       // 立即停止已播放与等待中的声音
system.sound.set({enabled:true, volume:.35});
await system.sound.play('success');      // 从用户操作回调中调用
```

透镜的纸面摩擦音复用同一个音量和静音设置。自由透镜、拖放透明胶囊、二维透镜在按下后解锁声音，移动速度控制音量、滤波与采样速度；停住 90ms 后淡出，松手 / 取消 / Escape / 失焦 / 隐藏 / 卸载时结束。普通二维触点不触发该音效。自定义图表透镜用 `data-lg-friction` 显式加入相同的拖动声音委托，悬停仍保持安静。这是声音能力的共享，不代表这些控件已统一复用透镜组件。

自定义手势可在用户按下回调中调用 `await sound.startFriction()`，移动时调用 `sound.updateFriction(speed)`（CSS 像素 / 秒），结束时调用 `sound.endFriction()`；`endFriction(true)` 立即释放音源。`synthesizeFriction(sampleRate)` 导出原创纸面噪声 PCM。摩擦声是手势期间的循环音源，不属于 13 种短提示音；页面启动或鼠标悬停不播放。

| 语义 | Cue |
| --- | --- |
| 抓起 / 松手 | `grab` / `release` |
| 开启 / 关闭 | `switchOn` / `switchOff` |
| 选择 / 调节 / 输入 | `select` / `tick` / `type` |
| 确认 / 收藏 | `success` / `favorite` |
| 收起 / 恢复 / 重置 | `dismiss` / `restore` / `reset` |
| 自定义轻按场景 | `press` |

`bindControlSounds(root, sound)` 为已有 HTML 提供委托绑定：按钮 `data-lg-sound="success"`，开关 `data-lg-sound="toggle"`，range 用 `tick`，输入框用 `type`。`lg:gesture` 由 JellyController 发出；移动超过阈值才触发抓起，取消手势不会播放完成音。默认每 cue 最少间隔 70ms，滑杆 90ms、输入 100ms；最多 6 个声音并发。过期 220ms 的待播放事件丢弃，静音 / 卸载不会延迟补播。

## Extend / 扩展

新控件先遵守 [元素归属与透镜复用规则](SCOPE.md)，优先组合已有组件并返回同样的 handle；仅有相同 handle 或玻璃外观不足以通过准入。不得覆盖内置名字：

```js
import {defineComponent, createComponent} from './design-system/index.js';
defineComponent('approve', ({label, onPress}) =>
  createComponent('button', {label, variant:'success', sound:'success', onPress})
);
const approve = createComponent('approve', {label:'Approve', onPress: save});
```

声音定义使用秒、Hz 和 0–1 振幅；单个 cue 最长 1 秒，避免长音阻碍界面：

```js
system.sound.register('complete', {
  duration:.22,
  tones:[[520,520,.15],[780,780,.08,.07]]
});
// 在用户确认操作里：
system.sound.play('complete');
```

现有高级材质接入支持 `.lg-surface`、`data-glass="panel|button|chip|small|notification|free"` 和 `data-lg-drag="tether|free"`；特殊 range / toggle / segment 可用导出的 `JellyController.register()`。这些兼容入口继续保留，但属于底层能力，不能单凭这些标记认定已复用透镜组件。新交互应组合已有控件，缺少能力时集中扩展共享透镜基础，不在使用方复制材质或手势实现；当前自由 `lens` 工厂尚不是通用透镜基础 API。使用 `textContent` 或 DOM 节点传入内容，组件不解析用户 HTML。

## Limits / 边界与验证

折射采样系统画布内的背景与已绘制玻璃，不自动捕获任意 DOM、iframe 或视频。玻璃背后的文字依然使用真实 DOM 保证可读和可访问。WebGL2 不可用时退回 CSS 毛玻璃。系统尊重 `prefers-reduced-motion`，背景默认不自动移动。语义色和重要文字需要在最终项目的背景上复查对比度。

项目中执行 `npm test`、`npm run build`、`npm run check`。测试覆盖真实声音 PCM、懒加载、节流、并发限制、静音取消、存储、扩展注册、包内导入闭合，以及既有弹簧与双语规则。组件目录用于真实键盘 / 指针与移动端检查。不要用“没有 JS 报错”代替可视和交互检查。
