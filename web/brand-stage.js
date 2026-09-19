/* One bounded canvas; independent brand choreography and shared input/lifecycle. */
(() => {
  const stage = document.querySelector('.brand-stage');
  if (!stage) return;
  const play = document.querySelector('#brandPlay'), fallback = document.querySelector('#brandImage');
  const choices = [...stage.querySelectorAll('button[data-brand]')];
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const canvas = document.createElement('canvas');
  canvas.setAttribute('aria-hidden', 'true'); play.append(canvas);
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  const W = 480, H = 320, TAU = Math.PI * 2;
  const colors = { openai:'#bcf3e0', 'claude-color':'#ed9a70', grok:'#dde9ff', 'deepseek-color':'#6295ff', 'kimi-color':'#7dbbff', glm:'#b1c5ff' };
  const art = new Map(), trails = [];
  const outline = [];
  let kimiBody = null, kimiPoint = null;
  let impulse = 0, impulseVelocity = 0;
  const shine = document.createElement('canvas'); shine.width = shine.height = 256;
  const moonSurface = makeMoon();
  const lensSurface = document.createElement('canvas');
  lensSurface.width = lensSurface.height = 192;
  const lensContext = lensSurface.getContext('2d');
  const lensPixels = lensContext.createImageData(192,192);
  let grokPixels = null;
  let brand = stage.dataset.brand, frame = 0, previous = 0, time = 0, visible = false;
  let pointerId = null, down = false, dragged = false;
  let px = 0, py = 0, x = 0, y = 0, charge = 0, burst = 0;
  let angle = 0, speed = 0, dragAngle = 0, orbit = 0;
  const clamp = (n, a, b) => Math.max(a, Math.min(b, n));
  const canRun = () => visible && !document.hidden && !document.body.matches('.basket-page-open, .viewer-open');
  choices.forEach(button => {
    const id = button.dataset.brand, img = new Image();
    img.onload = () => {
      const tile = document.createElement('canvas'); tile.width = tile.height = 256;
      const g = tile.getContext('2d'); g.drawImage(img, 0, 0, 256, 256);
      if (id === 'openai' || id === 'grok') {
        g.globalCompositeOperation = 'source-in'; g.fillStyle = '#f0f5f7'; g.fillRect(0, 0, 256, 256);
      }
      art.set(id, tile);
      if (id === 'grok') grokPixels = g.getImageData(0,0,256,256).data;
      if (id === 'claude-color') buildOutline();
      if (id === 'kimi-color') {
        kimiBody = document.createElement('canvas'); kimiPoint = document.createElement('canvas');
        kimiBody.width = kimiBody.height = kimiPoint.width = kimiPoint.height = 256;
        const pixels = g.getImageData(0, 0, 256, 256), body = new ImageData(new Uint8ClampedArray(pixels.data), 256, 256);
        for (let i = 0; i < pixels.data.length; i += 4) {
          if (pixels.data[i + 2] > pixels.data[i] * 1.3 && pixels.data[i] < 160) body.data[i + 3] = 0;
          else pixels.data[i + 3] = 0;
        }
        kimiBody.getContext('2d').putImageData(body, 0, 0);
        kimiPoint.getContext('2d').putImageData(pixels, 0, 0);
      }
      if (id === brand) sync();
    };
    img.src = '/brand-logos/' + id + '.svg';
  });
  // Deform one closed contour: no overlapping sectors or patch silhouettes.
  async function buildOutline() {
    try {
      const response = await fetch('/brand-logos/claude-color.svg');
      if (!response.ok) return;
      const svg = new DOMParser().parseFromString(await response.text(), 'image/svg+xml');
      const path = svg.querySelector('path'), length = path.getTotalLength();
      for (let i = 0; i < 960; i++) {
        const p = path.getPointAtLength(i * length / 960);
        outline.push({ x: (p.x - 12) / 24, y: (p.y - 12) / 24 });
      }
      if (brand === 'claude-color') sync();
    } catch { /* Keep the original artwork if vector sampling is unavailable. */ }
  }
  function highlight(size, tint, phase, alpha) {
    const g = shine.getContext('2d');
    g.globalCompositeOperation = 'source-over'; g.clearRect(0,0,256,256); g.drawImage(art.get(brand),0,0);
    g.globalCompositeOperation = 'source-in';
    const mid = .5 + Math.sin(phase) * .32;
    const gradient = g.createLinearGradient(0,256,256,0);
    gradient.addColorStop(0,'transparent'); gradient.addColorStop(mid-.15,'transparent');
    gradient.addColorStop(mid,tint); gradient.addColorStop(mid+.15,'transparent'); gradient.addColorStop(1,'transparent');
    g.fillStyle = gradient; g.fillRect(0,0,256,256);
    ctx.save(); ctx.globalCompositeOperation = 'lighter'; ctx.globalAlpha = alpha;
    ctx.drawImage(shine,-size/2,-size/2,size,size); ctx.restore();
  }
  function resize() {
    const ratio = Math.min(devicePixelRatio || 1, 1.5);
    canvas.width = W * ratio; canvas.height = H * ratio;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0); draw();
  }
  function logo(size, rotation = 0, alpha = 1, dx = 0, dy = 0) {
    ctx.save(); ctx.translate(dx, dy); ctx.rotate(rotation); ctx.globalAlpha *= alpha;
    ctx.drawImage(art.get(brand), -size / 2, -size / 2, size, size); ctx.restore();
  }
  function line(color, width, fn) {
    ctx.beginPath(); fn(); ctx.strokeStyle = color; ctx.lineWidth = width; ctx.stroke();
  }
  function dot(dx, dy, radius, alpha, color = colors[brand]) {
    ctx.globalAlpha = alpha; ctx.fillStyle = color;
    ctx.beginPath(); ctx.arc(dx, dy, radius, 0, TAU); ctx.fill(); ctx.globalAlpha = 1;
  }
  function openai() {
    const energy = Math.min(Math.abs(speed) / 8, 1);
    ctx.save(); ctx.globalCompositeOperation = 'lighter';
    // The galaxy is tied to angular velocity, so it disappears with the spin.
    if (energy > .002) {
      ctx.save(); ctx.rotate(-.35); ctx.scale(1, .64);
      const nebula = ctx.createRadialGradient(0,0,18,0,0,182);
      nebula.addColorStop(0,'rgba(204,220,255,0)');
      nebula.addColorStop(.4,'rgba(130,151,244,'+energy*.14+')');
      nebula.addColorStop(.7,'rgba(207,150,225,'+energy*.07+')');
      nebula.addColorStop(1,'transparent');
      ctx.fillStyle=nebula;ctx.fillRect(-182,-182,364,364);
      for(let i=0;i<420;i++) {
        const t=(i+.5)/420, r=40+Math.sqrt(t)*140;
        const a=i%3*TAU/3+t*7.8+angle*.16+Math.sin(i*19.7)*.16;
        dot(Math.cos(a)*r,Math.sin(a)*r,.4+(i%11)/16,energy*(.18+(i%7)*.09),i%3?'#adcaff':'#f6d6ed');
      }
      ctx.restore();
    }
    for (let i = 0; i < 42; i++) {
      const a = i * 2.39996 + time * .18, r = 116 + Math.sin(i * 7.1 + time) * 18 + energy * (i % 5) * 9;
      dot(Math.cos(a) * r, Math.sin(a) * r * .8, i % 7 === 0 ? 1.5 : .65, .16 + energy * .45);
    }
    for (let i = 4; i > 0; i--) logo(136 + i * 2, angle - Math.sign(speed) * i * .07 * energy, energy * .07);
    ctx.restore(); logo(136 * (1 - charge * .06), angle);
    // Small masked surface keeps the moving sheen inside the original knot.
    const g = shine.getContext('2d');
    g.clearRect(0, 0, 256, 256); g.globalCompositeOperation = 'source-over';
    g.drawImage(art.get(brand), 0, 0); g.globalCompositeOperation = 'source-in';
    const tint = g.createLinearGradient(0, 0, 256, 256), mid = .5 + Math.sin(time * 1.2 + x) * .25;
    tint.addColorStop(0, 'transparent'); tint.addColorStop(mid - .18, 'transparent');
    tint.addColorStop(mid, '#9fffe0'); tint.addColorStop(mid + .18, 'transparent'); tint.addColorStop(1, 'transparent');
    g.fillStyle = tint; g.fillRect(0, 0, 256, 256);
    ctx.save(); ctx.rotate(angle); ctx.globalCompositeOperation = 'lighter'; ctx.globalAlpha = .2 + energy * .4;
    ctx.drawImage(shine, -68, -68, 136, 136); ctx.restore();
  }
  function claude() {
    const size = 146;
    const breathing = .5 + .5*Math.sin(time*2.4);
    ctx.save(); ctx.globalCompositeOperation='lighter';
    const halo=ctx.createRadialGradient(-12,-8,8,0,0,139);
    halo.addColorStop(0,'rgba(255,184,115,'+(.38+breathing*.20+charge*.16)+')');
    halo.addColorStop(.45,'rgba(245,139,76,'+(.16+breathing*.12+charge*.10)+')');
    halo.addColorStop(1,'rgba(233,142,91,0)');
    ctx.fillStyle=halo; ctx.fillRect(-139,-139,278,278);
    // Soft shafts have feathered edges, not a circular light boundary.
    ctx.save();ctx.filter='blur(7px)';
    for(let i=0;i<9;i++){
      const a=i*TAU/9+Math.sin(time*.6+i)*.12;
      const reach=115+Math.sin(time*1.2+i*2)*17;
      const light=ctx.createLinearGradient(0,0,Math.cos(a)*reach,Math.sin(a)*reach);
      light.addColorStop(0,'rgba(255,205,148,.55)');light.addColorStop(.45,'rgba(245,155,91,.28)');light.addColorStop(1,'transparent');
      ctx.globalAlpha=.65+breathing*.3;
      ctx.fillStyle=light;ctx.beginPath();ctx.moveTo(0,0);
      ctx.lineTo(Math.cos(a-.10)*reach,Math.sin(a-.10)*reach);
      ctx.lineTo(Math.cos(a+.10)*reach,Math.sin(a+.10)*reach);ctx.closePath();ctx.fill();
    }
    ctx.restore();
    for(let i=0;i<26;i++){
      const a=i*TAU/26, r=94+Math.sin(i*7.3+time)*9;
      const end=r+25+breathing*10+burst*16;
      const light=ctx.createLinearGradient(Math.cos(a)*r,Math.sin(a)*r,Math.cos(a)*end,Math.sin(a)*end);
      light.addColorStop(0,'transparent');light.addColorStop(.4,'#eda77e');light.addColorStop(1,'transparent');
      ctx.globalAlpha=.22+breathing*.2+burst*.3;
      line(light,1.3,()=>{ctx.moveTo(Math.cos(a)*r,Math.sin(a)*r);ctx.lineTo(Math.cos(a)*end,Math.sin(a)*end);});
    }
    ctx.restore();
    if (!outline.length) { logo(size); return; }
    ctx.beginPath();
    outline.forEach((p,i) => {
      const a=Math.atan2(p.y,p.x), r=Math.hypot(p.x,p.y);
      const weight=clamp((r-.10)/.30,0,1);
      const stretch=1+weight*(Math.sin(time*2.4-a*2)*.13+Math.sin(time*1.2+a*3)*.035-charge*.13+impulse*.08);
      if(i)ctx.lineTo(p.x*size*stretch,p.y*size*stretch);
      else ctx.moveTo(p.x*size*stretch,p.y*size*stretch);
    });
    ctx.closePath();ctx.fillStyle='#d97757';ctx.fill();
  }
  function grok() {
    const tilt=-.26+x*.20, flat=.34+y*.10;
    function ring(front) {
      ctx.save();ctx.rotate(tilt);ctx.globalCompositeOperation='lighter';
      for(let i=0;i<20;i++){
        const r=89+i*3.1-charge*5;
        ctx.globalAlpha=(front?.22:.13)*(1-i/27)+charge*.07;
        line(i%4===0?'#e4c7a0':'#a6bbdf',i%3===0?1.15:.6,()=>ctx.ellipse(0,0,r,r*flat,0,front?0:Math.PI,front?Math.PI:TAU));
      }
      for(let i=0;i<100;i++){
        const a=i*2.4+orbit,r=157-((i*.618+orbit*.12)%1)*63;
        if((Math.sin(a)>=0)!==front)continue;
        dot(Math.cos(a)*r,Math.sin(a)*r*flat,i%11===0?1.4:.65,.25+(i%5)*.09);
      }
      ctx.restore();
    }
    ring(false);
    ctx.save();ctx.translate(x*6,y*4);ctx.rotate(x*.06+impulse*.04);
    refractedGrok();ctx.restore();
    // Foreground stars pass in front of the mark without any enclosing disk.
    ring(true);
  }
  function refractedGrok() {
    if (!grokPixels) { logo(125); return; }
    const output=lensPixels.data;
    const cx=x*39+Math.sin(time*.7)*16, cy=y*32+Math.cos(time*.6)*12;
    const strength=.24+charge*.48+Math.abs(impulse)*.22;
    // Inverse-map the actual logo through a soft moving lens; bilinear alpha
    // sampling keeps its silhouette continuous without tile seams or extra rings.
    for(let v=0;v<192;v++)for(let u=0;u<192;u++){
      const dx=u-95.5-cx,dy=v-95.5-cy;
      const field=Math.exp(-(dx*dx+dy*dy)/1900), scale=1+strength*field;
      const twist=field*(.06+charge*.16), c=Math.cos(twist),s=Math.sin(twist);
      const sx=(cx+(dx*c-dy*s)/scale)*256/125+127.5;
      const sy=(cy+(dx*s+dy*c)/scale)*256/125+127.5;
      const index=(v*192+u)*4;
      if(sx<0||sy<0||sx>=255||sy>=255){output[index+3]=0;continue;}
      const ix=Math.floor(sx),iy=Math.floor(sy),fx=sx-ix,fy=sy-iy;
      const p=(iy*256+ix)*4+3;
      output[index]=232;output[index+1]=242;output[index+2]=255;
      output[index+3]=(grokPixels[p]*(1-fx)+grokPixels[p+4]*fx)*(1-fy)+(grokPixels[p+1024]*(1-fx)+grokPixels[p+1028]*fx)*fy;
    }
    lensContext.putImageData(lensPixels,0,0);
    ctx.drawImage(lensSurface,-96,-96);
  }
  function deepseek() {
    const lift = Math.sin(time * 1.45) * 4 - impulse * 34 + charge * 10;
    const tilt = Math.sin(time * 1.45 + .4) * .025 + x * .11 - impulse * .12;
    for (let i=0; i<9; i++) {
      ctx.globalAlpha = .13 + i*.018;
      line('#5c92ec',1,() => {
        ctx.moveTo(-150,42+i*7);
        ctx.bezierCurveTo(-75,19+y*12+Math.sin(time-i*.35)*10,50,65+lift*.2,151,36+i*5);
      });
    }
    ctx.globalAlpha=1;
    ctx.save(); ctx.translate(x*20,lift+y*9); ctx.rotate(tilt);
    const squash=charge*.16-impulse*.20;
    ctx.scale(1+squash,1/(1+squash));
    logo(155); highlight(155,'#70bdff',time*.6,.2); ctx.restore();
    for(let i=0;i<24;i++) {
      const age=(time*.18+i*.137)%1, alpha=Math.sin(age*Math.PI)*(.25+Math.abs(impulse)*.36);
      ctx.globalAlpha=alpha;
      line('#8ccaff',.7,() => ctx.arc(82+Math.sin(i*2.4+time)*15,30-age*105,1+i%3,0,TAU));
    }
    for(let i=0;i<3;i++){
      const age=(time*.28+i/3)%1;
      ctx.globalAlpha=Math.sin(age*Math.PI)*(.14+burst*.28);
      line('#6daeff',.8,()=>ctx.ellipse(x*10,60,40+age*117,6+age*16,0,0,TAU));
    }
    ctx.globalAlpha=1;
  }
  function kimi() {
    if (!kimiBody || !kimiPoint) { logo(140); return; }
    const size=112, dx=12+x*16, dy=y*11-impulse*16;
    ctx.save();ctx.translate(x*4,y*3);
    ctx.drawImage(moonSurface,-77,-77,154,154);
    for(let i=0;i<23;i++){
      const a=i*2.399+time*.025,r=105+(i%4)*9;
      dot(Math.cos(a)*r,Math.sin(a)*r*.78,i%7===0?1.2:.55,.25+.18*Math.sin(time+i));
    }
    ctx.restore();
    // Real past poses give the letter a trailing echo only while it moves.
    ctx.save();ctx.globalCompositeOperation='lighter';
    trails.forEach((past,i)=>{
      const tx=12+past.x*16,ty=past.y*11-past.impulse*16;
      const movement=Math.min(1,Math.hypot(dx-tx,dy-ty)/7);
      ctx.save();ctx.translate(tx,ty);ctx.rotate(past.x*.09-past.impulse*.035);
      ctx.globalAlpha=movement*.028*(i+1)/trails.length;
      ctx.drawImage(kimiBody,-size/2,-size/2,size,size);ctx.restore();
    });ctx.restore();
    ctx.save(); ctx.translate(dx,dy); ctx.rotate(x*.09-impulse*.035);
    ctx.scale(1+charge*.025,1-charge*.04);
    ctx.globalAlpha=.78;ctx.shadowColor='#a3b9db';ctx.shadowBlur=2;
    ctx.drawImage(kimiBody,-size/2,-size/2,size,size);
    ctx.restore();
    // The original blue dot moves independently, with a slight trailing delay.
    const lag=trails[0] || {x,y};
    const dotX=12+lag.x*24+Math.sin(time*1.5)*2+charge*8;
    const dotY=lag.y*17-impulse*22+Math.cos(time*1.5)*2-charge*9;
    ctx.save(); ctx.translate(dotX,dotY); ctx.rotate(x*.05);
    ctx.shadowColor='#1783ff'; ctx.shadowBlur=4+charge*9;
    ctx.drawImage(kimiPoint,-size/2,-size/2,size,size); ctx.restore();
  }
  function makeMoon() {
    const tile=document.createElement('canvas');tile.width=tile.height=256;
    const g=tile.getContext('2d'), pixels=g.createImageData(256,256);
    const craters=Array.from({length:34},(_,i)=>({x:Math.sin(i*17.3)*.88,y:Math.cos(i*9.7)*.88,r:.025+(i%5)*.018}));
    for(let v=0;v<256;v++)for(let u=0;u<256;u++){
      const nx=(u-127.5)/126,ny=(v-127.5)/126,r2=nx*nx+ny*ny;
      if(r2>=1)continue;
      const z=Math.sqrt(1-r2), light=Math.max(0,-nx*.77-ny*.18-z*.60);
      let texture=Math.sin(u*1.7+v*3.1)*.035+Math.sin(nx*19+Math.sin(ny*25))*Math.cos(ny*17)*.07;
      for(const c of craters){
        const d=Math.hypot(nx-c.x,ny-c.y)/c.r;
        if(d<1.3)texture+=d<.8?-.16*(1-d*.6):.13*Math.sin((d-.8)*Math.PI/.5);
      }
      const value=Math.max(0,Math.min(255,19+light*193+texture*(13+light*90)));
      const offset=(v*256+u)*4;
      pixels.data[offset]=value*.94;pixels.data[offset+1]=value*.97;pixels.data[offset+2]=value+5;
      pixels.data[offset+3]=Math.min(1,(1-r2)*100)*255;
    }
    g.putImageData(pixels,0,0);return tile;
  }
  function glm() {
    const size=144;
    for(let i=0;i<5;i++){
      const offset=charge*(i-2)*13+impulse*Math.sin(i*1.8)*12;
      ctx.save();ctx.translate(x*(i-2)*3+offset,y*(i-2)*2);
      ctx.beginPath();ctx.rect(-size/2-1,-size/2+i*size/5,size+2,size/5+.6);ctx.clip();
      logo(size);
      highlight(size,'#c2d8ff',time*.85+x+impulse,.25+charge*.45);
      ctx.restore();
    }
    ctx.globalAlpha=.15+charge*.4;
    for(const side of [-1,1])line('#9abef1',1,()=>{
      ctx.moveTo(side*103,-48);ctx.lineTo(side*103,-79);ctx.lineTo(side*80,-79);
      ctx.moveTo(side*103,48);ctx.lineTo(side*103,79);ctx.lineTo(side*80,79);
    });
    ctx.globalAlpha=1;
  }
  const painters = { openai, 'claude-color':claude, grok, 'deepseek-color':deepseek, 'kimi-color':kimi, glm };
  function draw() {
    ctx.clearRect(0, 0, W, H);
    if (!art.has(brand)) { play.classList.remove('has-canvas'); return; }
    play.classList.add('has-canvas'); ctx.save(); ctx.translate(W / 2 + x * 5, H / 2 + y * 4); ctx.scale(1.13, 1.13);
    painters[brand]();
    if (burst > .01 && brand === 'openai') {
      ctx.globalAlpha = burst * .2;
      line(colors[brand], .8, () => ctx.ellipse(0, 0, 80 + (1 - burst) * 90, 65 + (1 - burst) * 55, 0, 0, TAU));
    }
    ctx.restore();
  }
  function tick(now) {
    frame = 0;
    const dt = Math.min((now - (previous || now)) / 1000, .04);
    previous = now; time += dt;
    orbit += dt*(.3+charge*1.6+Math.abs(speed)*.15);
    // Closed-form damped response preserves position across repeated releases.
    const damping=brand==='deepseek-color'?3.6:5;
    const frequency=brand==='deepseek-color'?11:9;
    const decay=Math.exp(-damping*dt), c=Math.cos(frequency*dt), s=Math.sin(frequency*dt);
    const a=impulse, b=(impulseVelocity+damping*a)/frequency;
    impulse=decay*(a*c+b*s);
    impulseVelocity=decay*((-damping*a+frequency*b)*c+(-damping*b-frequency*a)*s);
    const follow = 1 - Math.exp(-dt * 9);
    x += (px - x) * follow; y += (py - y) * follow;
    charge += ((down ? 1 : 0) - charge) * (1 - Math.exp(-dt * 5));
    burst *= Math.exp(-dt * 2.5); speed *= Math.exp(-dt * .9); angle += speed * dt;
    trails.push({ x, y, impulse }); if (trails.length > 18) trails.shift();
    draw(); if (canRun() && !reduced.matches) frame = requestAnimationFrame(tick);
  }
  function cancelPointer() {
    const id = pointerId; pointerId = null; down = false; px = py = 0;
    if (id !== null && play.hasPointerCapture(id)) play.releasePointerCapture(id);
  }
  function sync() {
    if (frame) cancelAnimationFrame(frame);
    frame = 0; previous = 0;
    if (!canRun() || reduced.matches) {
      cancelPointer(); speed = 0; charge = burst = 0; impulse = impulseVelocity = 0; trails.length = 0;
      if (reduced.matches) { time = orbit = angle = x = y = 0; }
      draw(); return;
    }
    frame = requestAnimationFrame(tick);
  }
  function point(event) {
    const box = play.getBoundingClientRect();
    px = clamp((event.clientX - box.left) / box.width * 2 - 1, -1, 1);
    py = clamp((event.clientY - box.top) / box.height * 2 - 1, -1, 1);
  }
  function activate() {
    if (reduced.matches) return;
    burst = 1;
    impulseVelocity = clamp(impulseVelocity + 9, -18, 18);
    if (brand === 'openai') speed = clamp(speed + 9, -16, 16);
    if (brand === 'grok') speed = 6;
  }
  play.addEventListener('pointerenter', event => { point(event); });
  play.addEventListener('pointerdown', event => {
    if (pointerId !== null || event.button !== 0 || reduced.matches) return;
    point(event); down = true; dragged = false; pointerId = event.pointerId;
    dragAngle = Math.atan2(py, px); play.setPointerCapture(pointerId);
  });
  play.addEventListener('pointermove', event => {
    if (pointerId !== null && event.pointerId !== pointerId) return;
    const oldX = px, oldY = py; point(event);
    if (down) {
      if (Math.hypot(px - oldX, py - oldY) > .012) dragged = true;
      const next = Math.atan2(py, px), delta = Math.atan2(Math.sin(next - dragAngle), Math.cos(next - dragAngle));
      if (brand === 'openai' && Math.hypot(px, py) > .13) { angle += delta; speed = clamp(delta * 35, -14, 14); }
      dragAngle = next;
    }
  });
  play.addEventListener('pointerup', event => {
    if (pointerId !== event.pointerId) return;
    const moved = dragged; cancelPointer();
    if (moved) { burst = .8; impulseVelocity = clamp(impulseVelocity + 6, -18, 18); } else activate();
  });
  play.addEventListener('pointercancel', cancelPointer);
  play.addEventListener('lostpointercapture', () => { if (pointerId !== null) cancelPointer(); });
  play.addEventListener('pointerleave', () => { if (!down) { px = py = 0; } });
  play.addEventListener('click', event => { if (event.detail === 0) activate(); });
  choices.forEach(button => button.addEventListener('click', () => {
    cancelPointer(); brand = button.dataset.brand; stage.dataset.brand = brand;
    angle = speed = charge = burst = x = y = time = orbit = impulse = impulseVelocity = 0; trails.length = 0;
    fallback.src = '/brand-logos/' + brand + '.svg'; fallback.alt = button.getAttribute('aria-label');
    document.querySelector('#brandName').textContent = fallback.alt;
    play.setAttribute('aria-label', fallback.alt + ' 互动标志'); play.title = fallback.alt;
    choices.forEach(choice => choice.setAttribute('aria-pressed', String(choice === button))); sync();
  }));
  new IntersectionObserver(entries => { visible = entries[0].isIntersecting; sync(); }).observe(play);
  new MutationObserver(sync).observe(document.body, { attributes:true, attributeFilter:['class'] });
  document.addEventListener('visibilitychange', sync);
  window.addEventListener('blur', cancelPointer); window.addEventListener('resize', resize);
  reduced.addEventListener('change', sync); resize();
})();
